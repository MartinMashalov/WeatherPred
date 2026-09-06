"""Isolated, read-only August pilot for a pinned small forecasting transformer.

Run with the optional environment documented in research/model_candidates.md.
Nothing here registers a trading policy or accesses September/held-out labels.
"""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import resource
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import torch
from chronos import Chronos2Pipeline
from huggingface_hub import HfApi, snapshot_download

MODEL_ID = "autogluon/chronos-2-small"
REVISION = "ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a"
STATIONS = ("KFLL1M", "KFXE1M", "KMIA1M", "KOPF1M", "KPMP1M")
MINUTE = 60_000
START = int(datetime(2026, 8, 20, tzinfo=UTC).timestamp() * 1000)
STOP = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000)
CONTEXT = 512
PREDICTION_LENGTH = 40


def stamp():
    return datetime.now(UTC).isoformat()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def read_august(root):
    sources = []
    with sqlite3.connect(f"file:{root / 'data/archive.sqlite'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row

        def read(identifier):
            record = db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
            data = (root / "data/blobs" / record["body_sha256"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == record["body_sha256"]
            sources.append({"record_id": identifier, "sha256": record["body_sha256"]})
            return json.loads(data)

        rows = read(12322)
        model = read(12323)
        points = {}
        for identifier in model["history_record_ids"]:
            for point in read(identifier)["timeseries"]:
                if point["t"] < STOP:
                    if point["t"] in points:
                        assert point == points[point["t"]], "Conflicting historical index versions"
                    points[point["t"]] = point
    assert len(rows) == 864
    assert all(START <= row["settlement_ms"] < STOP for row in rows)
    selected = [row for row in rows if (row["settlement_ms"] // (60 * MINUTE)) % 6 == 0]
    assert len(selected) == 144 and len({r["settlement_ms"] for r in selected}) == 48
    return selected, points, sources


def inputs_for(row, points, covariates):
    cutoff = row["decision_ms"] - 5 * MINUTE
    end = row["features"]["last_point_ms"]
    assert end <= cutoff < row["settlement_ms"]
    times = np.arange(end - (CONTEXT - 1) * MINUTE, end + MINUTE, MINUTE)
    values = np.full(CONTEXT, np.nan, dtype=np.float32)
    station_values = {station: values.copy() for station in STATIONS}
    for index, timestamp in enumerate(times):
        point = points.get(int(timestamp))
        if point is None or point.get("status") != "normal":
            continue
        values[index] = point["v"]
        for station in point.get("stations", []):
            if (
                station["station_id"] in station_values
                and station.get("code") == "ok"
                and station.get("received_at_ms", row["decision_ms"] + 1) <= row["decision_ms"]
            ):
                station_values[station["station_id"]][index] = station["temp_f"]
    assert np.isfinite(values[-1])
    assert abs(float(values[-1]) - row["features"]["persistence"]) < 1e-5
    steps = (row["settlement_ms"] - end) // MINUTE
    assert (
        steps * MINUTE == row["settlement_ms"] - end
        and row["horizon_minutes"] + 5 <= steps <= row["horizon_minutes"] + 10
    )
    result = {"target": values}
    if covariates:
        phase = 2 * np.pi * (times / (24 * 60 * MINUTE))
        future_times = end + MINUTE * np.arange(1, PREDICTION_LENGTH + 1)
        future_phase = 2 * np.pi * (future_times / (24 * 60 * MINUTE))
        result["past_covariates"] = {
            **station_values,
            "clock_sine": np.sin(phase).astype(np.float32),
            "clock_cosine": np.cos(phase).astype(np.float32),
        }
        result["future_covariates"] = {
            "clock_sine": np.sin(future_phase).astype(np.float32),
            "clock_cosine": np.cos(future_phase).astype(np.float32),
        }
    return result, int(steps), int(np.isfinite(values).sum())


def main(root, model_path):
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.manual_seed(62026)
    np.random.seed(62026)
    rows, points, sources = read_august(root)
    protocol = {
        "created_at": stamp(),
        "model_id": MODEL_ID,
        "revision": REVISION,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "training_record_id": 12322,
        "training_parent_record_id": 12323,
        "targets": "All August 20-31 hourly targets with UTC hour divisible by six",
        "target_events": 48,
        "horizons_minutes": [30, 15, 5],
        "cases_per_variant": 144,
        "variants": ["index_only", "index_stations_clock"],
        "context_points": CONTEXT,
        "prediction_steps": PREDICTION_LENGTH,
        "max_last_input_age_minutes": 10,
        "publication_lag_minutes_assumed": 5,
        "missing_points": "NaN, no interpolation or forward-fill",
        "source_ids_and_hashes": sources,
        "training": "None; fixed pretrained weights",
        "accuracy_interpretation": "Exploratory August training-period diagnostic, not independent validation",
        "september_or_final_holdout_accessed": False,
    }
    dump(root / "reports/model_candidate_protocol.json", protocol)
    api_info = HfApi(token=False).model_info(MODEL_ID, revision=REVISION)
    assert api_info.sha == REVISION
    snapshot_download(
        MODEL_ID,
        revision=REVISION,
        token=False,
        allow_patterns=["config.json", "model.safetensors", "README.md", "LICENSE*"],
        local_dir=model_path,
    )
    weights = {
        path.name: {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in model_path.iterdir()
        if path.is_file()
    }
    start = time.perf_counter()
    pipeline = Chronos2Pipeline.from_pretrained(str(model_path), device_map="cpu", dtype=torch.float32)
    load_seconds = time.perf_counter() - start
    synthetic = (80 + 3 * np.sin(np.arange(CONTEXT) / 30)).astype(np.float32)
    timings = []
    for _ in range(4):
        start = time.perf_counter()
        synthetic_result = pipeline.predict([synthetic], prediction_length=PREDICTION_LENGTH, batch_size=16)[
            0
        ]
        timings.append(time.perf_counter() - start)
        assert torch.isfinite(synthetic_result).all()
    quantiles = np.array(pipeline.quantiles)
    median_index = int(np.where(np.isclose(quantiles, 0.5))[0][0])
    all_predictions = []
    run_times = {}
    for variant in protocol["variants"]:
        prepared = [inputs_for(row, points, variant == "index_stations_clock") for row in rows]
        start = time.perf_counter()
        outputs = pipeline.predict(
            [item[0] for item in prepared],
            prediction_length=PREDICTION_LENGTH,
            batch_size=16,
            cross_learning=False,
        )
        run_times[variant] = time.perf_counter() - start
        assert len(outputs) == len(rows)
        for row, (_, steps, finite_points), output in zip(rows, prepared, outputs, strict=True):
            forecast = output[0, :, steps - 1].numpy().astype(float)
            assert np.isfinite(forecast).all()
            residual = row["observed"] - forecast
            pinball = np.maximum(quantiles * residual, (quantiles - 1) * residual)
            all_predictions.append(
                {
                    "variant": variant,
                    "decision_ms": row["decision_ms"],
                    "settlement_ms": row["settlement_ms"],
                    "horizon_minutes": row["horizon_minutes"],
                    "forecast_steps_after_last_input": steps,
                    "last_input_ms": row["features"]["last_point_ms"],
                    "finite_context_points": finite_points,
                    "observed_f": row["observed"],
                    "median_f": float(forecast[median_index]),
                    "persistence_f": row["features"]["persistence"],
                    "quantile_forecasts_f": forecast.tolist(),
                    "mean_pinball_loss_f": float(pinball.mean()),
                    "quantile_crossing": bool(np.any(np.diff(forecast) < 0)),
                }
            )
        print(json.dumps({"variant_completed": variant, "seconds": run_times[variant]}), flush=True)
    summaries = []
    for variant in protocol["variants"]:
        for horizon in (None, 30, 15, 5):
            subset = [
                row
                for row in all_predictions
                if row["variant"] == variant and (horizon is None or row["horizon_minutes"] == horizon)
            ]
            observed = np.array([row["observed_f"] for row in subset])
            predicted = np.array([row["median_f"] for row in subset])
            baseline = np.array([row["persistence_f"] for row in subset])
            summaries.append(
                {
                    "variant": variant,
                    "horizon_minutes": horizon,
                    "cases": len(subset),
                    "days": len({r["settlement_ms"] // (24 * 60 * MINUTE) for r in subset}),
                    "rmse_f": float(np.sqrt(np.mean((predicted - observed) ** 2))),
                    "mae_f": float(np.mean(np.abs(predicted - observed))),
                    "persistence_rmse_f": float(np.sqrt(np.mean((baseline - observed) ** 2))),
                    "persistence_mae_f": float(np.mean(np.abs(baseline - observed))),
                    "mean_pinball_loss_f": float(np.mean([r["mean_pinball_loss_f"] for r in subset])),
                    "quantile_crossing_cases": sum(r["quantile_crossing"] for r in subset),
                }
            )
    result = {
        "generated_at": stamp(),
        "protocol": protocol,
        "model_card_last_modified": api_info.last_modified.isoformat(),
        "checkpoint_files": weights,
        "parameter_count": sum(p.numel() for p in pipeline.model.parameters()),
        "platform": platform.platform(),
        "cpu": subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip(),
        "physical_memory_bytes": int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)),
        "cpu_threads": torch.get_num_threads(),
        "device": str(pipeline.model.device),
        "dtype": str(next(pipeline.model.parameters()).dtype),
        "package_versions": {
            name: importlib.metadata.version(name)
            for name in ("chronos-forecasting", "torch", "transformers", "numpy", "huggingface-hub")
        },
        "load_seconds_excluding_download": load_seconds,
        "synthetic_single_series_inference_seconds": timings,
        "synthetic_output_shape": list(synthetic_result.shape),
        "august_batch_seconds": run_times,
        "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "quantile_levels": quantiles.tolist(),
        "summaries": summaries,
        "input_time_and_identical_persistence_checks": True,
        "historical_publication_verified": False,
        "september_or_final_holdout_accessed": False,
        "independent_validation": False,
        "profitability_proven": False,
        "limits": [
            "One city, 12 previously used training days, 48 dependent hourly targets per variant.",
            "No historical executable prices or fills; accuracy cannot imply trading profit.",
            "Five-minute source lag is assumed; archived historical payloads were acquired September 6.",
            "Pinned checkpoint repository predates August 2026, but training corpus provenance is not fully audited.",
            "Quantile scores are descriptive; no forecast calibration or threshold-probability test was run.",
            "UTC clock sine/cosine is known in advance; no future weather covariates were supplied.",
            "Univariate and station-covariate results are both retained, with no parameter tuning.",
        ],
        "predictions": all_predictions,
    }
    dump(root / "reports/model_candidate_chronos2_small.json", result)
    print(
        json.dumps(
            {key: value for key, value in result.items() if key not in ("predictions", "protocol")}, indent=2
        )
    )


def parameter_digest(model):
    digest = hashlib.sha256()
    for name, parameter in sorted(model.named_parameters()):
        digest.update(name.encode())
        digest.update(parameter.detach().cpu().numpy().tobytes())
    return digest.hexdigest()


def finetune(root, model_path):
    """One fixed supervised fit; all scores are already-used development data."""
    from transformers import TrainerCallback

    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    torch.manual_seed(62026)
    np.random.seed(62026)
    rows, points, sources = read_august(root)
    cutoff = int(datetime(2026, 8, 24, tzinfo=UTC).timestamp() * 1000)
    training_times = np.arange(START, cutoff - 5 * MINUTE, MINUTE)
    training = np.array(
        [
            points[int(t)]["v"] if int(t) in points and points[int(t)].get("status") == "normal" else np.nan
            for t in training_times
        ],
        dtype=np.float32,
    )
    assert training_times[-1] + 5 * MINUTE < cutoff
    validation = [row for row in rows if row["decision_ms"] >= cutoff]
    assert len(validation) == 93
    excluded = [row for row in rows if row["settlement_ms"] >= cutoff > row["decision_ms"]]
    assert len(excluded) == 3
    protocol = {
        "experiment": "E021-v1",
        "registered_at": stamp(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "model_id": MODEL_ID,
        "revision": REVISION,
        "source_ids_and_hashes": sources,
        "fit_cutoff_ms": cutoff,
        "training_start_ms": int(training_times[0]),
        "training_last_point_ms": int(training_times[-1]),
        "training_finite_points": int(np.isfinite(training).sum()),
        "training_total_points": len(training),
        "training_array_sha256": hashlib.sha256(training.tobytes()).hexdigest(),
        "training_mode": "full supervised quantile fine-tuning; no reinforcement learning",
        "context_length": 512,
        "prediction_length": 40,
        "min_past": 60,
        "num_steps": 100,
        "batch_size": 8,
        "learning_rate": 1e-5,
        "optimizer": "adamw_torch",
        "learning_rate_schedule": "linear",
        "seed": 62026,
        "cpu_threads": 2,
        "wall_budget_seconds": 300,
        "evaluation_cases": len(validation),
        "excluded_midnight_cases_with_pre_fit_decisions": excluded,
        "evaluation": "August 24-31 development targets, fixed six-hourly cases decided after fit cutoff",
        "selection": "One configuration, no checkpoint selection, no evaluation passed to fit",
        "september_or_final_holdout_accessed": False,
        "independent_validation": False,
    }
    dump(root / "reports/model_candidate_E021_protocol.json", protocol)
    pipeline = Chronos2Pipeline.from_pretrained(str(model_path), device_map="cpu", dtype=torch.float32)
    original_digest = parameter_digest(pipeline.model)
    training_started = time.perf_counter()
    history = []

    class BudgetCallback(TrainerCallback):
        timed_out = False
        steps = 0

        def on_step_end(self, args, state, control, **kwargs):
            self.steps = state.global_step
            if time.perf_counter() - training_started >= protocol["wall_budget_seconds"]:
                self.timed_out = True
                control.should_training_stop = True
            return control

        def on_log(self, args, state, control, logs=None, **kwargs):
            if logs:
                history.append({"step": state.global_step, **logs})

    callback = BudgetCallback()
    trained = pipeline.fit(
        [training],
        prediction_length=protocol["prediction_length"],
        context_length=protocol["context_length"],
        learning_rate=protocol["learning_rate"],
        num_steps=protocol["num_steps"],
        batch_size=protocol["batch_size"],
        min_past=protocol["min_past"],
        finetune_mode="full",
        output_dir=root / "tmp/E021-chronos2-small",
        callbacks=[callback],
        optim=protocol["optimizer"],
        seed=protocol["seed"],
        data_seed=protocol["seed"],
        logging_steps=20,
        disable_tqdm=True,
        dataloader_pin_memory=False,
    )
    fit_seconds = time.perf_counter() - training_started
    trained_digest = parameter_digest(trained.model)
    assert parameter_digest(pipeline.model) == original_digest
    assert trained_digest != original_digest
    trained.model.eval()
    assert not trained.model.training
    prepared = [inputs_for(row, points, False) for row in validation]
    outputs = trained.predict(
        [item[0] for item in prepared], prediction_length=40, batch_size=16, cross_learning=False
    )
    quantiles = np.array(trained.quantiles)
    median_index = int(np.where(np.isclose(quantiles, 0.5))[0][0])
    zero_shot = json.loads((root / "reports/model_candidate_chronos2_small.json").read_text())
    comparators = {
        (row["decision_ms"], row["settlement_ms"]): row
        for row in zero_shot["predictions"]
        if row["variant"] == "index_only"
    }
    predictions = []
    for row, (_, steps, finite), output in zip(validation, prepared, outputs, strict=True):
        forecast = output[0, :, steps - 1].numpy().astype(float)
        assert np.isfinite(forecast).all()
        baseline = comparators[(row["decision_ms"], row["settlement_ms"])]
        assert baseline["observed_f"] == row["observed"]
        error = row["observed"] - forecast
        predictions.append(
            {
                "decision_ms": row["decision_ms"],
                "settlement_ms": row["settlement_ms"],
                "horizon_minutes": row["horizon_minutes"],
                "finite_context_points": finite,
                "observed_f": row["observed"],
                "median_f": float(forecast[median_index]),
                "zero_shot_median_f": baseline["median_f"],
                "persistence_f": baseline["persistence_f"],
                "quantile_forecasts_f": forecast.tolist(),
                "mean_pinball_loss_f": float(np.maximum(quantiles * error, (quantiles - 1) * error).mean()),
                "zero_shot_mean_pinball_loss_f": baseline["mean_pinball_loss_f"],
                "quantile_crossing": bool(np.any(np.diff(forecast) < 0)),
            }
        )
    summaries = []
    for horizon in (None, 30, 15, 5):
        subset = [p for p in predictions if horizon is None or p["horizon_minutes"] == horizon]
        result = {"horizon_minutes": horizon, "cases": len(subset)}
        for label, field in (
            ("finetuned", "median_f"),
            ("zero_shot", "zero_shot_median_f"),
            ("persistence", "persistence_f"),
        ):
            error = np.array([p[field] - p["observed_f"] for p in subset])
            result[label + "_rmse_f"] = float(np.sqrt(np.mean(error**2)))
            result[label + "_mae_f"] = float(np.mean(np.abs(error)))
        result["finetuned_mean_pinball_loss_f"] = float(np.mean([p["mean_pinball_loss_f"] for p in subset]))
        result["zero_shot_mean_pinball_loss_f"] = float(
            np.mean([p["zero_shot_mean_pinball_loss_f"] for p in subset])
        )
        summaries.append(result)
    result = {
        "generated_at": stamp(),
        "protocol": protocol,
        "training_steps_completed": callback.steps,
        "wall_budget_reached": callback.timed_out,
        "fit_seconds_including_checkpoint_save": fit_seconds,
        "original_parameter_sha256": original_digest,
        "trained_parameter_sha256": trained_digest,
        "original_pipeline_parameters_unchanged": True,
        "inference_training_mode": trained.model.training,
        "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "training_history": history,
        "summaries": summaries,
        "predictions": predictions,
        "checkpoint_directory": "tmp/E021-chronos2-small/finetuned-ckpt",
        "profitability_proven": False,
        "independent_validation": False,
        "historical_publication_verified": False,
        "september_or_final_holdout_accessed": False,
    }
    dump(root / "reports/model_candidate_E021_finetune.json", result)
    print(json.dumps({k: v for k, v in result.items() if k not in ("protocol", "predictions")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--finetune", action="store_true")
    args = parser.parse_args()
    action = finetune if args.finetune else main
    action(args.root, args.root / "tmp/chronos2-small-pinned")
