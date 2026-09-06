"""Registered eight-candidate station development study, without trading actions.

Register and review before invoking any model. Child processes are supervised
with hard deadlines; this is process control, not an operating-system sandbox.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import importlib.metadata
import json
import os
import signal
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from research.experiments.e022_prepare import validate_protocol
from research.probes.station_transformer import (
    FIXED_FIT,
    HOUR,
    canonical,
    digest,
    fit_fixed,
    index_observations,
    infer_prepared,
    prepare_manifest,
    timestamp_ms,
    validate_training_series,
    verify_artifacts,
    verify_checkpoint,
)
from weatherpred.archive import Archive
from weatherpred.station_forecasts import (
    calibrated_quantiles,
    describe_case,
    fit_ridge,
    predict_ridge,
    quantile_offsets,
)
from weatherpred.timeutil import utcnow

CONFIG = Path("config/e022_station_forecasts.json")
MANIFEST = Path("reports/E022_manifest.json")
TRAINING = Path("reports/E022_training_series.json")
SOURCES = [
    Path(__file__),
    CONFIG,
    MANIFEST,
    TRAINING,
    Path("reports/station_history_rows.jsonl"),
    Path("reports/station_registry.json"),
    Path("research/experiments/e022_prepare.py"),
    Path("research/probes/station_transformer.py"),
    Path("weatherpred/station_forecasts.py"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
]
FIT_WALL_SECONDS = 300
INFERENCE_WALL_SECONDS = 1800
STOP_FILE = Path("data/STOP_E022")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(canonical(value))
        handle.flush()
        os.fsync(handle.fileno())


def versions():
    result = {}
    for name in ("numpy", "torch", "transformers", "chronos-forecasting", "weatherpred"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def register(archive):
    config = json.loads(CONFIG.read_bytes())
    validate_protocol(config)
    checkpoint = config["station_transformer"]
    verify_checkpoint(checkpoint["checkpoint_directory"], checkpoint["checkpoint_files"])
    training = json.loads(TRAINING.read_bytes())
    manifest = json.loads(MANIFEST.read_bytes())
    if manifest["protocol_canonical_sha256"] != digest(config):
        raise ValueError("Prepared manifest belongs to a different protocol")
    validate_cases(manifest, config)
    if validate_training_series(training["series"]) != training["training_series_sha256"]:
        raise ValueError("Prepared training series hash changed")
    files = {str(path): sha(path) for path in SOURCES}
    if files[config["observations_path"]] != config["observations_sha256"]:
        raise ValueError("Observation bytes differ from the preparation protocol")
    if files[config["registry_path"]] != config["registry_sha256"]:
        raise ValueError("Station directory bytes differ from the preparation protocol")
    artifacts = {
        "protocol_sha256": files[str(CONFIG)],
        "manifest_sha256": files[str(MANIFEST)],
        "observations_sha256": files[config["observations_path"]],
        "adapter_sha256": files["research/probes/station_transformer.py"],
        "training_artifact_sha256": files[str(TRAINING)],
        "training_series_sha256": training["training_series_sha256"],
    }
    quantiles = json.loads((Path(checkpoint["checkpoint_directory"]) / "config.json").read_text())[
        "chronos_config"
    ]["quantiles"]
    if not all(level in quantiles for level in (0.05, 0.1, 0.5, 0.9, 0.95)):
        raise ValueError("Checkpoint lacks the registered interval/median quantiles")
    source_ids = {
        str(path): archive.append("research_source", str(path), utcnow(), {}, path.read_bytes())
        for path in SOURCES
    }
    protocol = {
        "experiment": config["experiment"],
        "config": config,
        "source_hashes": files,
        "source_record_ids": source_ids,
        "artifacts": artifacts,
        "checkpoint_files": checkpoint["checkpoint_files"],
        "quantile_levels": quantiles,
        "python_executable": sys.executable,
        "working_directory": str(Path.cwd()),
        "runtime_versions": versions(),
        "registered_before_model_execution": True,
        "execution": {
            "candidate_count": 8,
            "chronos_fit_attempts": 1,
            "fit_hard_wall_seconds": FIT_WALL_SECONDS,
            "neural_inference_hard_wall_seconds_per_candidate": INFERENCE_WALL_SECONDS,
            "preparation_batch_size": 64,
            "cpu_threads": 2,
            "automatic_retry": False,
            "stopping_file": str(STOP_FILE),
            "scope": "supervised_processes_without_OS_sandbox",
        },
        "point_metric": "Uncalibrated point forecasts / neural medians; quantile calibration is reported separately.",
        "incomplete_fit_policy": "Only exactly 200 completed steps qualify as the fixed-fit candidate; timeout or fewer steps is reported failed with no retry or alternate checkpoint.",
        "comparison": "All eight predeclared candidates on identical eligible development case IDs; failed candidates retain their row without reduced-case metrics.",
        "promotion_eligible": False,
        "profitability_proven": False,
    }
    return archive.append("experiment_protocol", config["experiment"], utcnow(), {}, canonical(protocol))


def verify_registration(archive, identifier):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
    if row is None or row["kind"] != "experiment_protocol":
        raise ValueError("Missing experiment registration")
    body = archive.body(row)
    if hashlib.sha256(body).hexdigest() != row["body_sha256"]:
        raise ValueError("Registration body hash changed")
    protocol = json.loads(body)
    if protocol.get("experiment") != "E022-station-forecast-development-v1":
        raise ValueError("Registration belongs to a different experiment")
    if protocol["source_hashes"] != {str(path): sha(path) for path in SOURCES}:
        raise ValueError("Registered E022 source or artifact changed")
    if (
        sys.executable != protocol["python_executable"]
        or str(Path.cwd()) != protocol["working_directory"]
        or versions() != protocol["runtime_versions"]
    ):
        raise ValueError("Registered execution environment changed")
    config = protocol["config"]
    validate_protocol(config)
    verify_artifacts(
        protocol,
        CONFIG.read_bytes(),
        MANIFEST.read_bytes(),
        Path(config["observations_path"]).read_bytes(),
        Path("research/probes/station_transformer.py").read_bytes(),
    )
    verify_checkpoint(config["station_transformer"]["checkpoint_directory"], protocol["checkpoint_files"])
    evidence = {
        "record_id": row["id"],
        "record_sha256": row["record_sha256"],
        "available_at": row["available_at"],
        "artifact_hashes": protocol["artifacts"],
    }
    return protocol, evidence


def terminate_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=3)


def supervised_once(command, directory, wall_seconds):
    """Exclusive attempt, persistent stdout/stderr and no retry after interruption."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    started, result_path = directory / "attempt_started.json", directory / "attempt_result.json"
    if result_path.exists():
        return json.loads(result_path.read_bytes())
    if started.exists():
        result = {
            "status": "failed",
            "reason": "previous_attempt_incomplete_no_retry",
            "automatic_retry": False,
        }
        write_new(result_path, result)
        return result
    write_new(started, {"command": command, "wall_seconds": wall_seconds, "started_at": utcnow().isoformat()})
    begin, process = time.monotonic(), None
    environment = dict(
        os.environ,
        OMP_NUM_THREADS="2",
        OPENBLAS_NUM_THREADS="2",
        MKL_NUM_THREADS="2",
        HF_HUB_OFFLINE="1",
        HF_DATASETS_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        WANDB_DISABLED="true",
        WANDB_MODE="disabled",
    )
    previous = {}

    def interrupted(signum, _frame):
        raise InterruptedError(f"Supervisor received signal {signum}")

    result = {"status": "failed", "reason": "unstarted", "automatic_retry": False}
    try:
        previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
        for number in previous:
            signal.signal(number, interrupted)
        with (directory / "stdout.log").open("xb") as out, (directory / "stderr.log").open("xb") as err:
            process = subprocess.Popen(
                command,
                shell=False,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                env=environment,
            )
            try:
                code = process.wait(timeout=max(0.001, wall_seconds - (time.monotonic() - begin)))
                result.update(
                    status="completed" if code == 0 else "failed",
                    reason=None if code == 0 else "child_exit_nonzero",
                    exit_code=code,
                )
            except subprocess.TimeoutExpired:
                result.update(status="timeout", reason="hard_wall_deadline")
    except BaseException as error:
        result.update(reason=f"{type(error).__name__}: {error}")
        raise
    finally:
        if process is not None:
            terminate_group(process)
        for number, handler in previous.items():
            signal.signal(number, handler)
        result["elapsed_seconds"] = time.monotonic() - begin
        result["stdout_sha256"] = (
            sha(directory / "stdout.log") if (directory / "stdout.log").exists() else None
        )
        result["stderr_sha256"] = (
            sha(directory / "stderr.log") if (directory / "stderr.log").exists() else None
        )
        result["payload_sha256"] = (
            sha(directory / "payload.json") if (directory / "payload.json").exists() else None
        )
        write_new(result_path, result)
    return result


def load_observations(config):
    with Path(config["observations_path"]).open() as handle:
        indexed, audit = index_observations(json.loads(line) for line in handle if line.strip())
    return indexed, audit


def validate_cases(manifest, config):
    ids = set()
    fit, calibration, end = (
        timestamp_ms(config[key])
        for key in ("fit_cutoff", "calibration_fit_available", "target_end_exclusive")
    )
    for case in manifest["cases"]:
        if case["case_id"] in ids or not case["eligible"]:
            raise ValueError("The common prediction panel must contain unique eligible IDs")
        ids.add(case["case_id"])
        decision, target, horizon = case["decision_ms"], case["target_ms"], case["horizon_hours"]
        if horizon not in (1, 3, 6) or target - decision != horizon * HOUR:
            raise ValueError("Prediction horizon changed")
        if case["split"] == "calibration":
            valid = fit <= decision < target < calibration and target + 15 * 60_000 <= calibration
        elif case["split"] == "development":
            valid = calibration <= decision < target < end
        else:
            valid = False
        if not valid:
            raise ValueError("Prediction case crosses its registered fit/calibration boundary")
    return ids


def baseline_predictions(candidate, cases, ridge_cases, observations, config, levels):
    histories = defaultdict(dict)
    for (station, when), point in observations.items():
        if point["status"] == "settled":
            histories[station][when] = point["temperature_f"]
    described = [
        {**case, **describe_case(histories[case["station_id"]], case, case["station_timezone"])}
        for case in cases
    ]
    identifier, models = candidate["id"], {}
    if "penalty" in candidate:
        for horizon in config["station_transformer"]["horizons_hours"]:
            training = [case for case in ridge_cases if case["horizon_hours"] == horizon]
            if any(
                case["target_assumed_available_ms"] > timestamp_ms(config["fit_cutoff"]) for case in training
            ):
                raise ValueError("A ridge label was unavailable by the registered fit cutoff")
            models[str(horizon)] = fit_ridge(
                training, candidate["penalty"], timestamp_ms(config["fit_cutoff"])
            )
        points = {}
        for horizon in config["station_transformer"]["horizons_hours"]:
            selected = [case for case in described if case["horizon_hours"] == horizon]
            if selected:
                points.update(
                    {
                        case["case_id"]: float(value)
                        for case, value in zip(
                            selected, predict_ridge(models[str(horizon)], selected), strict=True
                        )
                    }
                )
    else:
        if identifier not in ("persistence", "seasonal_24h", "blend_50_50"):
            raise ValueError("Unknown baseline candidate")
        points = {
            case["case_id"]: case["last_f"]
            if identifier == "persistence"
            else case["seasonal_f"]
            if identifier == "seasonal_24h"
            else 0.5 * (case["last_f"] + case["seasonal_f"])
            for case in described
        }
    predictions = [
        {
            "case_id": case["case_id"],
            "point_f": points[case["case_id"]],
            "quantiles_f": [points[case["case_id"]]] * len(levels),
        }
        for case in described
    ]
    return predictions, models


def score_predictions(predictions, manifest, observations, config, levels):
    """Only calibration labels determine offsets; no development outcome changes predictions."""
    ids = validate_cases(manifest, config)
    mapped = {row["case_id"]: row for row in predictions}
    if len(mapped) != len(predictions) or set(mapped) != ids:
        raise ValueError("Candidate did not forecast the entire fixed common panel")
    cases = manifest["cases"]
    for row in predictions:
        values = np.asarray([row["point_f"], *row["quantiles_f"]], dtype=float)
        if len(row["quantiles_f"]) != len(levels) or not np.isfinite(values).all():
            raise ValueError("Nonfinite or incomplete candidate prediction")

    def label(case):
        point = observations.get((case["station_id"], case["target_ms"]))
        if point is None or point["status"] != "settled":
            raise ValueError("The frozen common panel lost a settled target")
        return point["temperature_f"]

    offsets = {}
    for horizon in config["station_transformer"]["horizons_hours"]:
        calibration = [
            case for case in cases if case["split"] == "calibration" and case["horizon_hours"] == horizon
        ]
        if (
            len(calibration) < config["minimum_calibration_cases_per_horizon"]
            or len({c["target_ms"] // (24 * HOUR) for c in calibration})
            < config["minimum_calibration_days_per_horizon"]
        ):
            raise ValueError("Registered common calibration support is insufficient")
        offsets[str(horizon)] = quantile_offsets(
            np.asarray([mapped[c["case_id"]]["quantiles_f"] for c in calibration]),
            np.asarray([label(c) for c in calibration]),
            levels,
        ).tolist()
    levels_array = np.asarray(levels)
    indexes = {q: levels.index(q) for q in (0.05, 0.1, 0.9, 0.95)}
    scores = []
    for case in cases:
        if case["split"] != "development":
            continue
        prediction, observed = mapped[case["case_id"]], label(case)
        quantiles = calibrated_quantiles([prediction["quantiles_f"]], offsets[str(case["horizon_hours"])])[0]
        error, residual = prediction["point_f"] - observed, observed - quantiles
        row = {
            "case_id": case["case_id"],
            "station_id": case["station_id"],
            "horizon_hours": case["horizon_hours"],
            "target_ms": case["target_ms"],
            "day": datetime.fromtimestamp(case["target_ms"] / 1000, UTC).date().isoformat(),
            "absolute_error_f": abs(error),
            "squared_error_f2": error * error,
            "pinball_loss_f": float(
                np.maximum(levels_array * residual, (levels_array - 1) * residual).mean()
            ),
        }
        for percent, low, high in ((80, 0.1, 0.9), (90, 0.05, 0.95)):
            left, right = quantiles[indexes[low]], quantiles[indexes[high]]
            row[f"coverage_{percent}"] = float(left <= observed <= right)
            row[f"width_{percent}_f"] = float(right - left)
        scores.append(row)
    if not scores:
        raise ValueError("No common development cases")
    keys = [
        key for key in scores[0] if key not in ("case_id", "station_id", "horizon_hours", "target_ms", "day")
    ]
    grouped = defaultdict(list)
    for row in scores:
        grouped[row["day"]].append(row)
    daily = [
        {"day": day, "cases": len(rows), **{key: float(np.mean([row[key] for row in rows])) for key in keys}}
        for day, rows in sorted(grouped.items())
    ]
    aggregate = {key: float(np.mean([row[key] for row in daily])) for key in keys}
    aggregate["mae_f"] = aggregate.pop("absolute_error_f")
    aggregate["rmse_f"] = float(np.sqrt(aggregate.pop("squared_error_f2")))
    return {
        "metrics": aggregate,
        "development_cases": len(scores),
        "development_utc_days": len(daily),
        "calibration_offsets_f": offsets,
        "daily": daily,
        "station_case_counts": dict(sorted(Counter(row["station_id"] for row in scores).items())),
        "horizon_case_counts": dict(sorted(Counter(str(row["horizon_hours"]) for row in scores).items())),
        "metric_weighting": "Equal UTC target-day weight, equal case weight within each day; RMSE is the square root of day-weighted squared error.",
        "point_metric_basis": "Raw baseline point forecasts or pretrained/fitted neural medians before quantile calibration.",
    }, scores


def paired_differences(candidate, persistence):
    reference = {row["case_id"]: row for row in persistence}
    if (
        len(reference) != len(persistence)
        or len(candidate) != len(reference)
        or {r["case_id"] for r in candidate} != set(reference)
    ):
        raise ValueError("Paired comparison requires identical case IDs")
    days = defaultdict(list)
    for row in candidate:
        base = reference[row["case_id"]]
        if row["day"] != base["day"]:
            raise ValueError("Paired comparison calendar changed")
        days[row["day"]].append(row["absolute_error_f"] - base["absolute_error_f"])
    daily = [
        {"day": day, "cases": len(values), "mae_difference_f": float(np.mean(values))}
        for day, values in sorted(days.items())
    ]
    return {
        "candidate_minus_persistence_mae_f": float(np.mean([row["mae_difference_f"] for row in daily])),
        "case_pairs": len(candidate),
        "utc_day_groups": len(daily),
        "daily": daily,
        "interpretation": "Negative favors the candidate on reused development data; no independent significance or profitability claim.",
    }


def worker(args):
    archive = Archive(args.archive_root)
    registered_fit = None
    try:
        protocol, evidence = verify_registration(archive, args.run_record_id)
        if args.worker == "chronos_fixed_fit":
            fit_record = archive.latest("e022_fixed_fit_result", str(args.run_record_id))
            if fit_record is None:
                raise ValueError("Fitted weights require the archived completed fit result")
            registered_fit = archive.json(fit_record)
    finally:
        archive.close()
    config, directory = protocol["config"], Path(args.worker_directory)
    expected = Path(f"reports/E022-run-{args.run_record_id}") / (
        "single_fit_attempt" if args.worker == "fit" else args.worker
    )
    if directory.resolve() != expected.resolve() or not (directory / "attempt_started.json").exists():
        raise ValueError("Worker requires its one registered supervised-attempt directory")
    checkpoint = Path(config["station_transformer"]["checkpoint_directory"])
    files = protocol["checkpoint_files"]
    if args.worker == "fit":
        training = json.loads(TRAINING.read_bytes())
        result = fit_fixed(
            training["series"],
            checkpoint,
            files,
            directory / "model",
            evidence,
            protocol["artifacts"]["training_series_sha256"],
        )
        if result["training_steps_completed"] != FIXED_FIT["num_steps"]:
            raise ValueError("Fixed 200-step candidate incomplete; no alternate checkpoint or retry")
        write_new(directory / "payload.json", result)
        return
    if args.worker == "chronos_fixed_fit":
        fit_dir = Path(args.fit_directory)
        fit_result = checked_payload(fit_dir)
        if fit_result != registered_fit:
            raise ValueError("Fitted checkpoint metadata changed after archival")
        if fit_result["training_steps_completed"] != 200:
            raise ValueError("Incomplete fitted checkpoint")
        checkpoint, files = fit_dir / "model/finetuned-ckpt", fit_result["saved_checkpoint_files"]
    observations, audit = load_observations(config)
    manifest = json.loads(MANIFEST.read_bytes())
    validate_cases(manifest, config)
    batches, hashes = [], set()
    for offset in range(0, len(manifest["cases"]), 64):
        if STOP_FILE.exists():
            raise InterruptedError("E022 stop file present")
        selected = manifest["cases"][offset : offset + 64]
        prepared = prepare_manifest({"schema_version": 1, "cases": selected}, observations, config)
        if not all(case["eligible"] for case in prepared):
            raise ValueError("A frozen common case became ineligible")
        result = infer_prepared(prepared, config, checkpoint, files, evidence)
        if result["quantile_levels"] != protocol["quantile_levels"]:
            raise ValueError("Model quantile levels changed")
        hashes.add(result["parameter_sha256"])
        if len(hashes) != 1:
            raise ValueError("Model parameters differ between prediction batches")
        predictions = [
            {
                "case_id": row["case_id"],
                "point_f": row["median_f"],
                "quantiles_f": row["quantile_forecasts_f"],
                "input_sha256": row["input_sha256"],
                "last_input_ms": row["last_input_ms"],
                "historical_availability_verified": False,
            }
            for row in result["cases"]
        ]
        batch = directory / f"predictions_{offset // 64:04d}.json"
        write_new(
            batch,
            {
                "predictions": predictions,
                "evidence": {
                    key: value for key, value in result.items() if key not in ("cases", "excluded_cases")
                },
            },
        )
        batches.append({"path": str(batch), "sha256": sha(batch), "cases": len(predictions)})
        print(
            json.dumps({"candidate": args.worker, "completed_cases": offset + len(predictions)}), flush=True
        )
    write_new(
        directory / "payload.json",
        {
            "batches": batches,
            "parameter_sha256": next(iter(hashes)),
            "data_audit": audit,
            "labels_supplied_to_inference": False,
        },
    )


def checked_payload(directory):
    path = Path(directory) / "payload.json"
    if not path.exists():
        raise ValueError("Completed child did not produce its payload")
    status = json.loads((Path(directory) / "attempt_result.json").read_bytes())
    if status["status"] != "completed" or status.get("payload_sha256") != sha(path):
        raise ValueError("Child payload differs from its completed supervised attempt")
    return json.loads(path.read_bytes())


def run(args):
    archive = Archive(args.archive_root)
    try:
        if args.register_only:
            identifier = register(archive)
            print(
                json.dumps({"registration_record_id": identifier, "registered_before_model_execution": True}),
                flush=True,
            )
            return
        if not args.run_record_id:
            raise ValueError("An externally reviewed registration is required before model execution")
        protocol, _ = verify_registration(archive, args.run_record_id)
        config, root = protocol["config"], Path(f"reports/E022-run-{args.run_record_id}")
        root.mkdir(parents=True, exist_ok=True)
        manifest, training = json.loads(MANIFEST.read_bytes()), json.loads(TRAINING.read_bytes())
        validate_cases(manifest, config)
        observations, audit = load_observations(config)
        reports, scores, prediction_artifacts = [], {}, []
        for candidate in config["candidates"]:
            identifier = candidate["id"]
            if STOP_FILE.exists():
                print(json.dumps({"stopped": True, "completed_candidates": len(reports)}), flush=True)
                return
            directory = root / identifier
            directory.mkdir(parents=True, exist_ok=True)
            archived = archive.latest("e022_candidate_result", f"{args.run_record_id}:{identifier}")
            scored = detail = None
            if archived:
                card = archive.json(archived)
                if card["status"] == "completed":
                    path = Path(card["predictions_file"])
                    if sha(path) != card["predictions_sha256"]:
                        raise ValueError("Archived candidate predictions changed")
                    predictions = json.loads(path.read_bytes())["predictions"]
                else:
                    reports.append(card)
                    continue
            else:
                card = {
                    "candidate": candidate,
                    "status": "failed",
                    "automatic_retry": False,
                    "artifact_directory": str(directory),
                }
                try:
                    if identifier.startswith("chronos_"):
                        fit_dir = root / "single_fit_attempt"
                        if identifier == "chronos_fixed_fit":
                            command = [
                                sys.executable,
                                "-m",
                                "research.experiments.e022_run",
                                "--run-record-id",
                                str(args.run_record_id),
                                "--archive-root",
                                str(Path(args.archive_root).resolve()),
                                "--worker",
                                "fit",
                                "--worker-directory",
                                str(fit_dir),
                            ]
                            fit_status = supervised_once(command, fit_dir, FIT_WALL_SECONDS)
                            card["fit_attempt"] = fit_status
                            if fit_status["status"] != "completed":
                                raise ValueError("The single fixed fit failed or timed out")
                            fitted_result = checked_payload(fit_dir)
                            if fitted_result["training_steps_completed"] != 200:
                                raise ValueError("The fixed fit did not complete 200 steps")
                            card["fit_result"] = {
                                key: value
                                for key, value in fitted_result.items()
                                if key != "training_history"
                            }
                            fit_record = archive.latest("e022_fixed_fit_result", str(args.run_record_id))
                            if fit_record is None:
                                archive.append(
                                    "e022_fixed_fit_result",
                                    str(args.run_record_id),
                                    utcnow(),
                                    {},
                                    canonical(fitted_result),
                                )
                            elif archive.json(fit_record) != fitted_result:
                                raise ValueError(
                                    "The fitted checkpoint result differs from its archived record"
                                )
                        command = [
                            sys.executable,
                            "-m",
                            "research.experiments.e022_run",
                            "--run-record-id",
                            str(args.run_record_id),
                            "--archive-root",
                            str(Path(args.archive_root).resolve()),
                            "--worker",
                            identifier,
                            "--worker-directory",
                            str(directory),
                            "--fit-directory",
                            str(fit_dir),
                        ]
                        execution = supervised_once(command, directory, INFERENCE_WALL_SECONDS)
                        card["execution"] = execution
                        if execution["status"] != "completed":
                            raise ValueError("Neural inference failed or timed out")
                        payload = checked_payload(directory)
                        card["neural_inference"] = {
                            "parameter_sha256": payload["parameter_sha256"],
                            "labels_supplied_to_inference": payload["labels_supplied_to_inference"],
                            "batch_artifacts": payload["batches"],
                        }
                        predictions = []
                        for batch in payload["batches"]:
                            if sha(batch["path"]) != batch["sha256"]:
                                raise ValueError("Prediction batch bytes changed")
                            predictions.extend(json.loads(Path(batch["path"]).read_bytes())["predictions"])
                    else:
                        # A baseline start marker also prevents silent refitting after interruption.
                        write_new(
                            directory / "baseline_started.json",
                            {"candidate": candidate, "started_at": utcnow().isoformat()},
                        )
                        predictions, fitted = baseline_predictions(
                            candidate,
                            manifest["cases"],
                            training["ridge_cases"],
                            observations,
                            config,
                            protocol["quantile_levels"],
                        )
                        write_new(directory / "ridge_models.json", fitted)
                    prediction_file = directory / "predictions.json"
                    write_new(prediction_file, {"predictions": predictions})
                    scored, detail = score_predictions(
                        predictions, manifest, observations, config, protocol["quantile_levels"]
                    )
                    card.update(
                        status="completed",
                        predictions_file=str(prediction_file),
                        predictions_sha256=sha(prediction_file),
                        evaluation=scored,
                    )
                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    OSError,
                    RuntimeError,
                    np.linalg.LinAlgError,
                ) as error:
                    card.update(status="failed", error=f"{type(error).__name__}: {error}")
                archive.append(
                    "e022_candidate_result",
                    f"{args.run_record_id}:{identifier}",
                    utcnow(),
                    {},
                    canonical(card),
                )
                if card["status"] != "completed":
                    reports.append(card)
                    continue
            if scored is None:
                scored, detail = score_predictions(
                    predictions, manifest, observations, config, protocol["quantile_levels"]
                )
            card["evaluation"] = scored
            scores[identifier] = detail
            reports.append(card)
            prediction_artifacts.append(
                {
                    "candidate": identifier,
                    "path": card["predictions_file"],
                    "sha256": card["predictions_sha256"],
                }
            )
            print(
                json.dumps(
                    {"candidate": identifier, "status": card["status"], "development_cases": len(detail)}
                ),
                flush=True,
            )
        baseline = scores.get("persistence")
        for card in reports:
            identifier = card["candidate"]["id"]
            if baseline is not None and identifier in scores:
                card["paired_against_persistence"] = paired_differences(scores[identifier], baseline)
        report = {
            "experiment": config["experiment"],
            "registration_record_id": args.run_record_id,
            "candidates_registered": len(config["candidates"]),
            "candidates": reports,
            "completed_candidates": sum(card["status"] == "completed" for card in reports),
            "failed_candidates": sum(card["status"] != "completed" for card in reports),
            "common_calibration_cases": sum(c["split"] == "calibration" for c in manifest["cases"]),
            "common_development_cases": sum(c["split"] == "development" for c in manifest["cases"]),
            "common_panel_sha256": digest([c["case_id"] for c in manifest["cases"]]),
            "quantile_levels": protocol["quantile_levels"],
            "prediction_artifacts": prediction_artifacts,
            "data_audit": audit,
            "historical_availability_verified": False,
            "model_comparisons_are_independent_trials": False,
            "development_is_untouched_validation": False,
            "profitability_proven": False,
            "trading_actions": 0,
            "interpretation": config["evaluation"],
            "limitations": [config[key] for key in ("availability", "station_universe", "fit_policy")],
            "source_hashes": protocol["source_hashes"],
        }
        report_id = archive.append(
            "experiment_report_gzip",
            f"E022:{args.run_record_id}",
            utcnow(),
            {},
            gzip.compress(canonical(report), mtime=0),
        )
        path = root / "report.json"
        if not path.exists():
            write_new(path, {**report, "report_record_id": report_id})
        print(
            json.dumps(
                {
                    "report_record_id": report_id,
                    "completed": report["completed_candidates"],
                    "failed": report["failed_candidates"],
                }
            ),
            flush=True,
        )
    finally:
        archive.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register-only", action="store_true")
    parser.add_argument("--run-record-id", type=int)
    parser.add_argument("--archive-root", default="data")
    parser.add_argument("--worker", choices=("fit", "chronos_pretrained", "chronos_fixed_fit"))
    parser.add_argument("--worker-directory")
    parser.add_argument("--fit-directory")
    args = parser.parse_args()
    if args.worker:
        worker(args)
    else:
        with Path("data/E022.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            run(args)


if __name__ == "__main__":
    main()
