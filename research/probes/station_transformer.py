"""Registered, optional Chronos adapter for an hourly station development study.

The pure preparation helpers do not import a neural library. Inference requires
an externally registered manifest and local hashed weights. No outcome labels
are supplied to neural inference; no network requests or trading actions are made.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import sqlite3
import time
from collections import defaultdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

HOUR = 3_600_000
MINUTE = 60_000
MODEL_ID = "autogluon/chronos-2-small"
REVISION = "ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a"
DATA_START = int(datetime(2026, 5, 4, tzinfo=UTC).timestamp() * 1000)
TRAIN_START = int(datetime(2026, 5, 11, tzinfo=UTC).timestamp() * 1000)
FIT_CUTOFF = int(datetime(2026, 7, 6, tzinfo=UTC).timestamp() * 1000)
BENCHMARK_END = int(datetime(2026, 8, 17, tzinfo=UTC).timestamp() * 1000)
FIXED_CONFIG = {
    "context_hours": 168,
    "horizons_hours": [1, 3, 6],
    "target_utc_hours": [0, 6, 12, 18],
    "lag_minutes": 15,
    "minimum_finite_context": 120,
    "maximum_last_input_age_minutes": 120,
    "availability_mode": "retrospective_assumed_lag",
    "prediction_length_hours": 7,
    "model_fit_cutoff_ms": FIT_CUTOFF,
    "target_end_exclusive_ms": BENCHMARK_END,
    "inference_batch_size": 64,
    "cpu_threads": 2,
    "seed": 62027,
}
FIXED_FIT = {
    "num_steps": 200,
    "batch_size": 16,
    "context_length": 168,
    "prediction_length": 7,
    "min_past": 24,
    "learning_rate": 1e-5,
    "seed": 62027,
    "wall_budget_seconds": 300,
    "optimizer": "adamw_torch",
    "training_start_ms": TRAIN_START,
    "fit_cutoff_ms": FIT_CUTOFF,
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp_ms(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise TypeError("Timestamp must be an integer millisecond count or timezone-aware ISO text")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Naive timestamps are ambiguous")
    exact = parsed.timestamp() * 1000
    if not exact.is_integer():
        raise ValueError("Sub-millisecond timestamps are unsupported")
    return int(exact)


def receipt_timestamp_ms(value):
    """Round a real receipt up to the next millisecond, never backdate it.

    Observation and decision grids still use strict timestamp_ms. The original
    receipt text remains attached to each source alongside this conservative
    millisecond representation.
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise TypeError("Receipt timestamp must be integer milliseconds or timezone-aware ISO text")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Naive receipt timestamps are ambiguous")
    delta = parsed.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    microseconds = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    return (microseconds + 999) // 1000


def finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Temperature must be finite and numeric")
    return float(value)


def validate_config(protocol):
    config = protocol.get("station_transformer", protocol)
    for key, expected in FIXED_CONFIG.items():
        if config.get(key) != expected:
            raise ValueError(f"The registered station adapter requires {key}={expected!r}")
    return config


def index_observations(rows):
    """Preserve actual receipts; quarantine conflicting versions of an hour.

    Received-at times are acquisition evidence, never the assumed historical
    eligibility times. No observation on/after August 17 contributes a value.
    """
    grouped = defaultdict(list)
    excluded = defaultdict(int)
    for row in rows:
        observed = timestamp_ms(row["observed_at"])
        if not DATA_START <= observed < BENCHMARK_END:
            excluded["outside_registered_data_window"] += 1
            continue
        if observed % HOUR:
            raise ValueError("Station observations must lie on the exact UTC hourly grid")
        station = row["station_id"]
        if not isinstance(station, str) or not station:
            raise ValueError("Missing station ID")
        local = datetime.fromtimestamp(observed / 1000, UTC).astimezone(ZoneInfo(row["station_timezone"]))
        if (
            row["local_date"] != local.date().isoformat()
            or row["local_hour"] != local.hour
            or row.get("local_fold", local.fold) != local.fold
        ):
            raise ValueError("Local station time disagrees with the UTC timestamp/timezone")
        received = receipt_timestamp_ms(row["received_at"])
        if received < observed:
            raise ValueError("An archived observation receipt precedes its observation time")
        source_id = row["source_record_id"]
        if isinstance(source_id, bool) or not isinstance(source_id, int) or source_id <= 0:
            raise ValueError("Each observation needs a positive source record ID")
        value = finite_number(row["temperature_f"])
        grouped[(station, observed)].append(
            {
                "station_id": station,
                "observed_ms": observed,
                "temperature_f": value,
                "status": row["status"],
                "source_record_id": source_id,
                "source_row_index": row.get("source_row_index"),
                "received_ms": received,
                "received_at_original": row["received_at"],
                "historical_availability_verified": row.get("historical_availability_verified") is True,
            }
        )
    indexed = {}
    conflicts = []
    for key, versions in sorted(grouped.items()):
        signatures = {(v["temperature_f"], v["status"]) for v in versions}
        if len(signatures) != 1:
            conflicts.append({"station_id": key[0], "observed_ms": key[1], "versions": versions})
            continue
        indexed[key] = {
            **versions[0],
            "source_record_ids": sorted({v["source_record_id"] for v in versions}),
            "actual_received_ms": sorted({v["received_ms"] for v in versions}),
            "actual_received_at_original": sorted({v["received_at_original"] for v in versions}),
        }
    return indexed, {"excluded_rows": dict(excluded), "conflicting_hours": conflicts}


def prepare_case(case, observations, protocol):
    """Construct only the history available under the registered lag assumption."""
    config = validate_config(protocol)
    identifier = case["case_id"]
    station = case["station_id"]
    decision = timestamp_ms(case["decision_ms"])
    target = timestamp_ms(case["target_ms"])
    horizon = case["horizon_hours"]
    if not isinstance(identifier, str) or not identifier or not isinstance(station, str) or not station:
        raise ValueError("Case and station IDs must be nonempty strings")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon not in config["horizons_hours"]:
        raise ValueError("Unregistered forecast horizon")
    if decision % HOUR or target % HOUR or target - decision != horizon * HOUR:
        raise ValueError("Case horizon must equal its exact hourly target minus decision timestamp")
    if (target // HOUR) % 24 not in config["target_utc_hours"]:
        raise ValueError("Target is outside the registered six-hourly sampling schedule")
    if not DATA_START <= decision < target < config["target_end_exclusive_ms"]:
        raise ValueError("Case is outside the registered development window")
    result = {
        "case_id": identifier,
        "station_id": station,
        "decision_ms": decision,
        "target_ms": target,
        "horizon_hours": horizon,
        "eligible": False,
        "availability_basis": config["availability_mode"],
        "historical_availability_verified": False,
    }
    if decision < config["model_fit_cutoff_ms"]:
        return {**result, "exclusion_reason": "decision_precedes_model_fit_cutoff"}
    end = ((decision - config["lag_minutes"] * MINUTE) // HOUR) * HOUR
    grid = [end - (config["context_hours"] - 1 - i) * HOUR for i in range(config["context_hours"])]
    values = []
    provenance = []
    for observed in grid:
        point = observations.get((station, observed))
        if point is None or point["status"] != "settled":
            values.append(None)
            provenance.append(None)
            continue
        # This is the declared historical assumption, not an actual receipt.
        assumed_eligible = observed + config["lag_minutes"] * MINUTE
        if observed > end or assumed_eligible > decision:
            raise ValueError("Future observation entered the model context")
        values.append(point["temperature_f"])
        provenance.append(
            {
                "observed_ms": observed,
                "assumed_eligible_ms": assumed_eligible,
                "status": point["status"],
                "source_record_ids": point["source_record_ids"],
                "actual_received_ms": point["actual_received_ms"],
                "actual_received_at_original": point["actual_received_at_original"],
            }
        )
    finite = [i for i, value in enumerate(values) if value is not None]
    steps = (target - end) // HOUR
    if target - end != steps * HOUR or not 1 <= steps <= config["prediction_length_hours"]:
        raise ValueError("Target is not covered by the registered prediction length")
    result.update(
        context_end_ms=end,
        forecast_steps_from_grid_end=steps,
        finite_context_points=len(finite),
        input_sha256=digest({"grid_ms": grid, "values_f": values, "provenance": provenance}),
    )
    if len(finite) < config["minimum_finite_context"]:
        return {**result, "exclusion_reason": "insufficient_finite_history"}
    last = finite[-1]
    if decision - grid[last] > config["maximum_last_input_age_minutes"] * MINUTE:
        return {**result, "exclusion_reason": "last_input_too_old"}
    seasonal_ms = target - 24 * HOUR
    seasonal_index = (seasonal_ms - grid[0]) // HOUR
    seasonal = values[seasonal_index] if 0 <= seasonal_index < len(values) else None
    return {
        **result,
        "eligible": True,
        "history_values_f": values,
        "history_grid_ms": grid,
        "history_source_provenance": provenance,
        "history_missing_mask": [value is None for value in values],
        "last_input_ms": grid[last],
        "persistence_f": values[last],
        "seasonal_24h_f": seasonal,
        "seasonal_reference_ms": seasonal_ms,
    }


def prepare_manifest(manifest, observations, protocol):
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("cases"), list):
        raise ValueError("Manifest requires schema_version=1 and a cases list")
    prepared = []
    identifiers = set()
    decision_targets = set()
    for case in manifest["cases"]:
        key = (case["station_id"], timestamp_ms(case["decision_ms"]), timestamp_ms(case["target_ms"]))
        if case["case_id"] in identifiers or key in decision_targets:
            raise ValueError("Duplicate manifest case or station/decision/target")
        identifiers.add(case["case_id"])
        decision_targets.add(key)
        prepared.append(prepare_case(case, observations, protocol))
    return prepared


def verify_artifacts(registration, protocol_bytes, manifest_bytes, observations_bytes, adapter_bytes):
    """Registration comes from the caller's append-only archive, not this adapter."""
    artifacts = registration.get("artifacts", {})
    actual = {
        "protocol_sha256": hashlib.sha256(protocol_bytes).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "observations_sha256": hashlib.sha256(observations_bytes).hexdigest(),
        "adapter_sha256": hashlib.sha256(adapter_bytes).hexdigest(),
    }
    if any(artifacts.get(key) != value for key, value in actual.items()):
        raise ValueError("Input artifacts do not match the externally registered hashes")
    return actual


def verify_checkpoint(checkpoint, expected_files):
    if not isinstance(expected_files, dict) or not {"config.json", "model.safetensors"} <= set(
        expected_files
    ):
        raise ValueError("Registration must pin config.json and model.safetensors")
    result = {}
    for name, expected in expected_files.items():
        if Path(name).name != name:
            raise ValueError("Checkpoint hash keys must be simple filenames")
        content = (Path(checkpoint) / name).read_bytes()
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected:
            raise ValueError(f"Checkpoint hash mismatch for {name}")
        result[name] = {"sha256": actual, "bytes": len(content)}
    return result


def parameter_digest(model):
    value = hashlib.sha256()
    for name, parameter in sorted(model.named_parameters()):
        value.update(name.encode())
        value.update(parameter.detach().cpu().numpy().tobytes())
    return value.hexdigest()


def load_local_pipeline(checkpoint, expected_files):
    files = verify_checkpoint(checkpoint, expected_files)
    try:
        import torch
        from chronos import Chronos2Pipeline
    except ImportError as error:
        raise RuntimeError(
            "Use the isolated tmp/forecast-model-env Python; production dependencies stay unchanged"
        ) from error
    torch.set_num_threads(2)
    torch.manual_seed(62027)
    torch.use_deterministic_algorithms(True)
    pipeline = Chronos2Pipeline.from_pretrained(
        str(Path(checkpoint).resolve()), device_map="cpu", dtype=torch.float32, local_files_only=True
    )
    pipeline.model.eval()
    if pipeline.model.training:
        raise ValueError("Inference model must have training-mode dropout disabled")
    return pipeline, files


def infer_prepared(prepared, protocol, checkpoint, expected_files, registration_evidence):
    """Evaluate the fixed cases only; no targets/labels or training are accepted."""
    import numpy as np

    config = validate_config(protocol)
    required = {"record_id", "record_sha256", "artifact_hashes", "available_at"}
    if not required <= set(registration_evidence):
        raise ValueError("Inference requires archive-backed external registration evidence")
    pipeline, files = load_local_pipeline(checkpoint, expected_files)
    initial_parameters = parameter_digest(pipeline.model)
    eligible = [case for case in prepared if case["eligible"]]
    contexts = [
        np.asarray([np.nan if v is None else v for v in case["history_values_f"]], dtype=np.float32)
        for case in eligible
    ]
    started = time.perf_counter()
    kwargs = {
        "prediction_length": config["prediction_length_hours"],
        "batch_size": config["inference_batch_size"],
        "cross_learning": False,
    }
    outputs = pipeline.predict(contexts, **kwargs) if contexts else []
    repeated = pipeline.predict(contexts, **kwargs) if contexts else []
    quantiles = [float(q) for q in pipeline.quantiles]
    if quantiles != sorted(set(quantiles)) or not all(0 < q < 1 for q in quantiles) or 0.5 not in quantiles:
        raise ValueError("Unexpected model quantile levels")
    predictions = []
    maximum_replay_difference = 0.0
    for case, first, second in zip(eligible, outputs, repeated, strict=True):
        expected_shape = (1, len(quantiles), config["prediction_length_hours"])
        if tuple(first.shape) != expected_shape or tuple(second.shape) != expected_shape:
            raise ValueError("Unexpected forecast tensor shape")
        first_values = first.detach().cpu().numpy()
        second_values = second.detach().cpu().numpy()
        if not np.isfinite(first_values).all() or not np.isfinite(second_values).all():
            raise ValueError("Model returned a non-finite forecast")
        maximum_replay_difference = max(
            maximum_replay_difference, float(np.abs(first_values - second_values).max())
        )
        values = first_values[0, :, case["forecast_steps_from_grid_end"] - 1].astype(float).tolist()
        predictions.append(
            {
                **{key: value for key, value in case.items() if not key.startswith("history_")},
                "quantile_forecasts_f": values,
                "median_f": values[quantiles.index(0.5)],
                "quantile_crossing": any(a > b for a, b in pairwise(values)),
            }
        )
    if maximum_replay_difference != 0:
        raise ValueError("Identical evaluation-mode predictions did not reproduce exactly")
    if parameter_digest(pipeline.model) != initial_parameters:
        raise ValueError("Model parameters changed during inference")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "registration": registration_evidence,
        "checkpoint_files": files,
        "parameter_sha256": initial_parameters,
        "parameters_unchanged": True,
        "parameter_count": sum(p.numel() for p in pipeline.model.parameters()),
        "inference_training_mode": pipeline.model.training,
        "device": str(pipeline.model.device),
        "package_versions": {
            name: importlib.metadata.version(name)
            for name in ("chronos-forecasting", "torch", "numpy", "transformers")
        },
        "inference_and_repeat_seconds": time.perf_counter() - started,
        "quantile_levels": quantiles,
        "maximum_replay_difference_f": maximum_replay_difference,
        "replayed_cases": len(predictions),
        "cases": predictions,
        "predictions_sha256": digest(predictions),
        "excluded_cases": [case for case in prepared if not case["eligible"]],
        "historical_availability_verified": False,
        "availability_basis": config["availability_mode"],
        "labels_supplied_to_inference": False,
        "network_requests": 0,
        "profitability_proven": False,
    }


def validate_training_series(series):
    """Validate root-supplied hourly target arrays without importing a model."""
    if not series:
        raise ValueError("No training series supplied")
    identifiers = set()
    for item in series:
        station = item["station_id"]
        if not isinstance(station, str) or not station or station in identifiers:
            raise ValueError("Training stations must be unique nonempty IDs")
        identifiers.add(station)
        times, values, sources = item["timestamps_ms"], item["values_f"], item["source_record_ids"]
        if not (len(times) == len(values) == len(sources)) or len(times) < FIXED_FIT["min_past"] + 7:
            raise ValueError("Training arrays have inconsistent or insufficient lengths")
        for index, (timestamp, value, source) in enumerate(zip(times, values, sources, strict=True)):
            timestamp = timestamp_ms(timestamp)
            if timestamp % HOUR or not TRAIN_START <= timestamp or timestamp + 15 * MINUTE > FIT_CUTOFF:
                raise ValueError("Training target is outside the registered label/availability cutoff")
            if index and timestamp != timestamp_ms(times[index - 1]) + HOUR:
                raise ValueError(
                    "Training timestamps must retain a regular hourly grid, including missing slots"
                )
            if value is not None:
                finite_number(value)
                if isinstance(source, bool) or not isinstance(source, int) or source <= 0:
                    raise ValueError("Finite training values need archived source record IDs")
        if sum(value is not None for value in values) < FIXED_FIT["min_past"] + 7:
            raise ValueError("Training series has insufficient finite data")
    return digest(series)


def fit_fixed(
    series, checkpoint, expected_files, output_dir, registration_evidence, registered_training_sha256
):
    """Optional single supervised fit. Root must register arrays before calling.

    There is intentionally no validation/evaluation input or checkpoint-choice
    argument. This function is not invoked by the inference command-line entry.
    """
    training_sha256 = validate_training_series(series)
    if training_sha256 != registered_training_sha256:
        raise ValueError("Training arrays differ from the registered immutable hash")
    if not {"record_id", "record_sha256", "artifact_hashes", "available_at"} <= set(registration_evidence):
        raise ValueError("Training needs external registration evidence")
    import numpy as np
    from transformers import TrainerCallback

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    protocol = {
        "registered_input_sha256": training_sha256,
        "fixed_fit": FIXED_FIT,
        "registration": registration_evidence,
        "created_at": datetime.now(UTC).isoformat(),
    }
    with (output_dir / "training_protocol.json").open("x") as handle:
        json.dump(protocol, handle, indent=2)
    pipeline, files = load_local_pipeline(checkpoint, expected_files)
    initial_parameters = parameter_digest(pipeline.model)
    values = [
        np.asarray([np.nan if v is None else v for v in item["values_f"]], dtype=np.float32)
        for item in series
    ]
    started = time.perf_counter()
    history = []

    class Budget(TrainerCallback):
        steps = 0
        reached = False

        def on_step_end(self, args, state, control, **kwargs):
            self.steps = state.global_step
            if time.perf_counter() - started >= FIXED_FIT["wall_budget_seconds"]:
                self.reached = True
                control.should_training_stop = True
            return control

        def on_log(self, args, state, control, logs=None, **kwargs):
            if logs:
                history.append({"step": state.global_step, **logs})

    budget = Budget()
    trained = pipeline.fit(
        values,
        prediction_length=7,
        context_length=168,
        min_past=24,
        num_steps=200,
        batch_size=16,
        learning_rate=1e-5,
        finetune_mode="full",
        optim="adamw_torch",
        seed=62027,
        data_seed=62027,
        output_dir=output_dir,
        callbacks=[budget],
        logging_steps=20,
        disable_tqdm=True,
        dataloader_pin_memory=False,
    )
    trained.model.eval()
    if trained.model.training or parameter_digest(pipeline.model) != initial_parameters:
        raise ValueError("Training mutated the original model or retained training mode for inference")
    trained_sha256 = parameter_digest(trained.model)
    if trained_sha256 == initial_parameters:
        raise ValueError("Training did not change any model parameter")
    saved_dir = output_dir / "finetuned-ckpt"
    saved_files = {
        name: hashlib.sha256((saved_dir / name).read_bytes()).hexdigest()
        for name in ("config.json", "model.safetensors")
    }
    reloaded, _ = load_local_pipeline(saved_dir, saved_files)
    if parameter_digest(reloaded.model) != trained_sha256:
        raise ValueError("Saved trained weights did not reproduce their parameter hash")
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "training_protocol": protocol,
        "original_checkpoint_files": files,
        "original_parameter_sha256": initial_parameters,
        "trained_parameter_sha256": trained_sha256,
        "saved_checkpoint_files": saved_files,
        "training_steps_completed": budget.steps,
        "wall_budget_reached": budget.reached,
        "fit_and_save_seconds": time.perf_counter() - started,
        "training_history": history,
        "inference_training_mode": trained.model.training,
        "saved_parameter_hash_reproduced": True,
        "evaluation_supplied_to_fit": False,
        "network_requests": 0,
        "profitability_proven": False,
    }
    with (output_dir / "training_result.json").open("x") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
    return result


def main(args):
    output = Path(args.output)
    if output.exists():
        raise ValueError("Refusing to overwrite an existing inference result")
    protocol_bytes = Path(args.protocol).read_bytes()
    manifest_bytes = Path(args.manifest).read_bytes()
    observation_bytes = Path(args.observations).read_bytes()
    with sqlite3.connect(f"file:{Path(args.archive_root) / 'archive.sqlite'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM records WHERE id=?", (args.registration_record_id,)).fetchone()
        if row is None:
            raise ValueError("Registration record does not exist")
        body = (Path(args.archive_root) / "blobs" / row["body_sha256"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != row["body_sha256"]:
            raise ValueError("Registration source blob hash mismatch")
        registration = json.loads(body)
        hashes = verify_artifacts(
            registration, protocol_bytes, manifest_bytes, observation_bytes, Path(__file__).read_bytes()
        )
        evidence = {
            "record_id": row["id"],
            "record_sha256": row["record_sha256"],
            "available_at": row["available_at"],
            "artifact_hashes": hashes,
        }
    protocol, manifest = json.loads(protocol_bytes), json.loads(manifest_bytes)
    indexed, data_audit = index_observations(
        json.loads(line) for line in observation_bytes.splitlines() if line.strip()
    )
    prepared = prepare_manifest(manifest, indexed, protocol)
    result = infer_prepared(
        prepared, protocol, args.checkpoint, validate_config(protocol)["checkpoint_files"], evidence
    )
    result["data_audit"] = data_audit
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
    print(
        json.dumps(
            {
                "output": str(output),
                "replayed_cases": result["replayed_cases"],
                "excluded_cases": len(result["excluded_cases"]),
                "maximum_replay_difference_f": result["maximum_replay_difference_f"],
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registration-record-id", type=int, required=True)
    parser.add_argument("--archive-root", default="data")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", required=True)
    main(parser.parse_args())
