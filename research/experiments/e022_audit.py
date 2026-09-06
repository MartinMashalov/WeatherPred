"""Independent E022 arithmetic/provenance audit; no production scorer imports.

Reads immutable predictions and observations. Never loads a forecasting model,
fits parameters, or generates forecasts. Neural replay assertions are checked
against retained metadata, not rerun by this audit.
"""

import argparse
import gzip
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

HOUR = 3_600_000
DAY = 24 * HOUR
TOLERANCE = 1e-10


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def timestamp(value, receipt=False):
    if isinstance(value, int):
        return value
    time = datetime.fromisoformat(value)
    if time.tzinfo is None:
        raise ValueError("Naive source time")
    delta = time.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    micros = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    if not receipt and micros % 1000:
        raise ValueError("Grid timestamp is not an exact millisecond")
    return (micros + (999 if receipt else 0)) // 1000


def mean(values):
    return math.fsum(values) / len(values)


def linear_quantile(values, level):
    ordered = sorted(values)
    location = (len(ordered) - 1) * level
    lower, upper = math.floor(location), math.ceil(location)
    return ordered[lower] + (location - lower) * (ordered[upper] - ordered[lower])


def close(actual, expected, message):
    if not math.isfinite(actual) or abs(actual - expected) > TOLERANCE:
        raise ValueError(f"{message}: observed={actual}, independent={expected}")
    return abs(actual - expected)


def observations_from_rows(rows, config):
    versions = defaultdict(list)
    start = timestamp("2026-05-04T00:00:00Z")
    end = timestamp(config["target_end_exclusive"])
    for row in rows:
        at = timestamp(row["observed_at"])
        if not start <= at < end:
            continue
        local = datetime.fromtimestamp(at / 1000, ZoneInfo(row["station_timezone"]))
        if at % HOUR or (local.date().isoformat(), local.hour, local.fold) != (
            row["local_date"],
            row["local_hour"],
            row.get("local_fold", local.fold),
        ):
            raise ValueError("Raw observation UTC/local hour mismatch")
        if not math.isfinite(row["temperature_f"]) or timestamp(row["received_at"], True) < at:
            raise ValueError("Invalid raw temperature or receipt chronology")
        versions[(row["station_id"], at)].append(row)
    result = {}
    for key, items in versions.items():
        if len({(r["temperature_f"], r["status"]) for r in items}) != 1:
            continue
        result[key] = {
            "temperature_f": float(items[0]["temperature_f"]),
            "status": items[0]["status"],
            "source_record_ids": sorted({r["source_record_id"] for r in items}),
            "actual_received_ms": sorted({timestamp(r["received_at"], True) for r in items}),
            "actual_received_at_original": sorted({r["received_at"] for r in items}),
        }
    return result


def check_case(case, config, observations):
    decision, target, horizon = case["decision_ms"], case["target_ms"], case["horizon_hours"]
    fit, calibration, end = (
        timestamp(config[k]) for k in ("fit_cutoff", "calibration_fit_available", "target_end_exclusive")
    )
    if not case["eligible"] or horizon not in (1, 3, 6) or target - decision != horizon * HOUR:
        raise ValueError("Invalid common case")
    if target % (6 * HOUR) or decision % HOUR:
        raise ValueError("Case left the declared hourly/six-hourly grid")
    if case["split"] == "calibration":
        valid = fit <= decision < target and target + 900_000 <= calibration
    elif case["split"] == "development":
        valid = calibration <= decision < target < end
    else:
        valid = False
    if not valid:
        raise ValueError("Prediction or calibration crosses its fixed cutoff")
    label = observations.get((case["station_id"], target))
    if label is None or label["status"] != "settled":
        raise ValueError("Common case lost its exact settled raw target")
    if case["target_source_record_ids"] != label["source_record_ids"]:
        raise ValueError("Target provenance differs from raw observations")
    return label["temperature_f"]


def prepared_summary(case, observations, config):
    """Rebuild only input provenance and retained case fields; no predictions."""
    decision, target = case["decision_ms"], case["target_ms"]
    end = (decision - 900_000) // HOUR * HOUR
    times = list(range(end - 167 * HOUR, end + HOUR, HOUR))
    values, provenance = [], []
    for at in times:
        point = observations.get((case["station_id"], at))
        if point is None or point["status"] != "settled":
            values.append(None)
            provenance.append(None)
        else:
            values.append(point["temperature_f"])
            provenance.append(
                {
                    "observed_ms": at,
                    "assumed_eligible_ms": at + 900_000,
                    "status": point["status"],
                    **{
                        key: point[key]
                        for key in ("source_record_ids", "actual_received_ms", "actual_received_at_original")
                    },
                }
            )
    finite = [i for i, value in enumerate(values) if value is not None]
    if len(finite) < 120 or decision - times[finite[-1]] > 2 * HOUR:
        raise ValueError("Registered eligible context fails independent historical gates")
    seasonal_at = target - 24 * HOUR
    seasonal = values[times.index(seasonal_at)]
    return {
        "case_id": case["case_id"],
        "station_id": case["station_id"],
        "decision_ms": decision,
        "target_ms": target,
        "horizon_hours": case["horizon_hours"],
        "eligible": True,
        "availability_basis": config["station_transformer"]["availability_mode"],
        "historical_availability_verified": False,
        "context_end_ms": end,
        "forecast_steps_from_grid_end": (target - end) // HOUR,
        "finite_context_points": len(finite),
        "input_sha256": digest({"grid_ms": times, "values_f": values, "provenance": provenance}),
        "last_input_ms": times[finite[-1]],
        "persistence_f": values[finite[-1]],
        "seasonal_24h_f": seasonal,
        "seasonal_reference_ms": seasonal_at,
    }


def reconstruct_scores(predictions, cases, observed, levels, config):
    mapped = {row["case_id"]: row for row in predictions}
    if len(mapped) != len(predictions) or set(mapped) != {c["case_id"] for c in cases}:
        raise ValueError("Candidate does not have the identical complete case panel")
    for row in predictions:
        if len(row["quantiles_f"]) != len(levels) or not all(
            math.isfinite(v) for v in [row["point_f"], *row["quantiles_f"]]
        ):
            raise ValueError("Invalid candidate point or quantile forecast")
    offsets = {}
    for horizon in (1, 3, 6):
        calibration = [c for c in cases if c["split"] == "calibration" and c["horizon_hours"] == horizon]
        if (
            len(calibration) < config["minimum_calibration_cases_per_horizon"]
            or len({c["target_ms"] // DAY for c in calibration})
            < config["minimum_calibration_days_per_horizon"]
        ):
            raise ValueError("Common calibration support is insufficient")
        offsets[str(horizon)] = [
            linear_quantile(
                [observed[c["case_id"]] - mapped[c["case_id"]]["quantiles_f"][j] for c in calibration], q
            )
            for j, q in enumerate(levels)
        ]
    rows = []
    for case in cases:
        if case["split"] != "development":
            continue
        prediction, actual = mapped[case["case_id"]], observed[case["case_id"]]
        calibrated = sorted(
            p + d for p, d in zip(prediction["quantiles_f"], offsets[str(case["horizon_hours"])], strict=True)
        )
        error = prediction["point_f"] - actual
        losses = []
        for q, forecast in zip(levels, calibrated, strict=True):
            residual = actual - forecast
            losses.append(q * residual if residual >= 0 else (q - 1) * residual)
        row = {
            "case_id": case["case_id"],
            "station_id": case["station_id"],
            "horizon_hours": case["horizon_hours"],
            "day": datetime.fromtimestamp(case["target_ms"] / 1000, UTC).date().isoformat(),
            "absolute_error_f": abs(error),
            "squared_error_f2": error**2,
            "pinball_loss_f": mean(losses),
        }
        for percent, left, right in ((80, 0.1, 0.9), (90, 0.05, 0.95)):
            low, high = calibrated[levels.index(left)], calibrated[levels.index(right)]
            row[f"coverage_{percent}"] = float(low <= actual <= high)
            row[f"width_{percent}_f"] = high - low
        rows.append(row)
    groups = defaultdict(list)
    for row in rows:
        groups[row["day"]].append(row)
    fields = (
        "absolute_error_f",
        "squared_error_f2",
        "pinball_loss_f",
        "coverage_80",
        "width_80_f",
        "coverage_90",
        "width_90_f",
    )
    daily = [
        {"day": day, "cases": len(items), **{field: mean([r[field] for r in items]) for field in fields}}
        for day, items in sorted(groups.items())
    ]
    metrics = {field: mean([d[field] for d in daily]) for field in fields}
    metrics["mae_f"] = metrics.pop("absolute_error_f")
    metrics["rmse_f"] = math.sqrt(metrics.pop("squared_error_f2"))
    return {
        "calibration_offsets_f": offsets,
        "daily": daily,
        "metrics": metrics,
        "development_cases": len(rows),
        "development_utc_days": len(daily),
        "station_case_counts": dict(Counter(r["station_id"] for r in rows)),
        "horizon_case_counts": dict(Counter(str(r["horizon_hours"]) for r in rows)),
    }, rows


def compare_scores(reported, expected):
    errors = []
    for key in ("development_cases", "development_utc_days", "station_case_counts", "horizon_case_counts"):
        if reported[key] != expected[key]:
            raise ValueError("Score support differs: " + key)
    if set(reported["metrics"]) != set(expected["metrics"]):
        raise ValueError("Unexpected aggregate metric set")
    for key, value in expected["metrics"].items():
        errors.append(close(reported["metrics"][key], value, key))
    for horizon, values in expected["calibration_offsets_f"].items():
        for actual, expected_value in zip(reported["calibration_offsets_f"][horizon], values, strict=True):
            errors.append(close(actual, expected_value, "calibration quantile"))
    for actual, expected_day in zip(reported["daily"], expected["daily"], strict=True):
        if (actual["day"], actual["cases"]) != (expected_day["day"], expected_day["cases"]):
            raise ValueError("Daily score calendar differs")
        for key, value in expected_day.items():
            if key not in ("day", "cases"):
                errors.append(close(actual[key], value, "daily " + key))
    return max(errors, default=0.0)


def audit_neural(card, protocol, registration, cases, prepared, read_fit):
    directory = Path(card["artifact_directory"])
    payload_path = directory / "payload.json"
    attempt = json.loads((directory / "attempt_result.json").read_bytes())
    if attempt["status"] != "completed" or attempt["payload_sha256"] != file_hash(payload_path):
        raise ValueError("Neural payload is not the completed attempt's immutable result")
    for name in ("stdout", "stderr"):
        if file_hash(directory / (name + ".log")) != attempt[name + "_sha256"]:
            raise ValueError("Neural attempt log hash changed")
    payload = json.loads(payload_path.read_bytes())
    if payload["labels_supplied_to_inference"] is not False:
        raise ValueError("Inference claims access to labels")
    expected_files = protocol["checkpoint_files"]
    expected_parameter = payload["parameter_sha256"]
    if card["candidate"]["id"] == "chronos_fixed_fit":
        fitted = read_fit()
        if (
            fitted["training_steps_completed"] != 200
            or fitted["inference_training_mode"]
            or not fitted["saved_parameter_hash_reproduced"]
            or fitted["evaluation_supplied_to_fit"]
        ):
            raise ValueError("Fixed fit is incomplete or lacks saved evaluation-mode evidence")
        if (
            fitted["training_protocol"]["registered_input_sha256"]
            != protocol["artifacts"]["training_series_sha256"]
        ):
            raise ValueError("Fixed fit used unregistered arrays")
        expected_files = fitted["saved_checkpoint_files"]
        if expected_parameter != fitted["trained_parameter_sha256"]:
            raise ValueError("Inference parameter hash differs from the single fixed fit")
    merged, position = [], 0
    for batch in payload["batches"]:
        if file_hash(batch["path"]) != batch["sha256"]:
            raise ValueError("Neural prediction batch hash changed")
        content = json.loads(Path(batch["path"]).read_bytes())
        evidence, predictions = content["evidence"], content["predictions"]
        selected = cases[position : position + 64]
        if len(predictions) != len(selected) or batch["cases"] != len(selected):
            raise ValueError("Neural batch has missing or extra cases")
        for key in ("inference_training_mode", "labels_supplied_to_inference"):
            if evidence[key] is not False:
                raise ValueError("Neural inference was not evaluation-only")
        if (
            evidence["maximum_replay_difference_f"] != 0
            or evidence["replayed_cases"] != len(selected)
            or not evidence["parameters_unchanged"]
        ):
            raise ValueError("Neural exact-replay metadata failed")
        if (
            evidence["parameter_sha256"] != expected_parameter
            or evidence["quantile_levels"] != protocol["quantile_levels"]
        ):
            raise ValueError("Neural weights or quantile levels changed across batches")
        reg = evidence["registration"]
        if (
            reg["record_id"] != registration["id"]
            or reg["record_sha256"] != registration["record_sha256"]
            or reg["artifact_hashes"] != protocol["artifacts"]
        ):
            raise ValueError("Neural inference is detached from its prior registration")
        if {name: item["sha256"] for name, item in evidence["checkpoint_files"].items()} != expected_files:
            raise ValueError("Neural checkpoint is not the pinned original or fixed trained checkpoint")
        full_rows = []
        for case, prediction in zip(selected, predictions, strict=True):
            base = prepared[case["case_id"]]
            if (
                prediction["case_id"] != case["case_id"]
                or prediction["input_sha256"] != base["input_sha256"]
                or prediction["last_input_ms"] != base["last_input_ms"]
            ):
                raise ValueError("Neural case input provenance differs from raw data")
            quantiles = prediction["quantiles_f"]
            if prediction["point_f"] != quantiles[protocol["quantile_levels"].index(0.5)]:
                raise ValueError("Neural point forecast is not its raw median")
            full_rows.append(
                {
                    **base,
                    "quantile_forecasts_f": quantiles,
                    "median_f": prediction["point_f"],
                    "quantile_crossing": any(a > b for a, b in pairwise(quantiles)),
                }
            )
        if digest(full_rows) != evidence["predictions_sha256"]:
            raise ValueError(
                "Full adapter prediction hash differs from independently reconstructed case evidence"
            )
        merged.extend(predictions)
        position += len(selected)
    if position != len(cases):
        raise ValueError("Neural batch chain omitted registered cases")
    if (
        card["neural_inference"]["batch_artifacts"] != payload["batches"]
        or card["neural_inference"]["parameter_sha256"] != expected_parameter
    ):
        raise ValueError("Candidate neural metadata differs from its payload")
    return merged, {
        "batches": len(payload["batches"]),
        "replay_metadata_cases": position,
        "input_and_adapter_prediction_hashes_reproduced": position,
        "model_reexecuted_by_audit": False,
    }


def self_check():
    close(linear_quantile([30.0, 0.0, 20.0, 10.0], 0.25), 7.5, "linear quantile fixture")
    close(mean([mean([2.0] * 3), mean([6.0] * 9)]), 4.0, "equal-day mean fixture")
    close(math.sqrt(mean([mean([4.0] * 3), mean([36.0] * 9)])), math.sqrt(20), "equal-day RMSE fixture")
    close(mean([0.1 * 2, (0.9 - 1) * -3]), 0.25, "pinball fixture")
    return {"independent_arithmetic_fixtures": 4, "model_loads": 0, "model_fits": 0}


def run(run_id, archive_root, output, allow_partial):
    root = Path(f"reports/E022-run-{run_id}")
    report_path = root / "report.json"
    if not report_path.exists() and not allow_partial:
        raise ValueError(
            "Final E022 report is not available; use --allow-partial only for an explicitly partial audit"
        )
    source_ids = set()
    with sqlite3.connect(f"file:{archive_root / 'archive.sqlite'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row

        def read(row, compressed=False):
            if row is None:
                raise ValueError("Missing archived audit source")
            data = (archive_root / "blobs" / row["body_sha256"]).read_bytes()
            fields = [
                row[k] for k in ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")
            ]
            if (
                hashlib.sha256(data).hexdigest() != row["body_sha256"]
                or digest(fields) != row["record_sha256"]
            ):
                raise ValueError("Archive source integrity failure")
            source_ids.add(row["id"])
            return json.loads(gzip.decompress(data) if compressed else data)

        registration = db.execute("SELECT * FROM records WHERE id=?", (run_id,)).fetchone()
        protocol = read(registration)
        if (
            registration["kind"] != "experiment_protocol"
            or protocol["experiment"] != "E022-station-forecast-development-v1"
        ):
            raise ValueError("Wrong registration")
        for path, expected in protocol["source_hashes"].items():
            if file_hash(path) != expected:
                raise ValueError("Frozen E022 input or code changed: " + path)
        config = protocol["config"]
        manifest = json.loads(Path("reports/E022_manifest.json").read_bytes())
        cases = manifest["cases"]
        if digest(config) != manifest["protocol_canonical_sha256"] or len(
            {c["case_id"] for c in cases}
        ) != len(cases):
            raise ValueError("Manifest identity or case uniqueness mismatch")
        with Path(config["observations_path"]).open() as handle:
            observations = observations_from_rows(
                (json.loads(line) for line in handle if line.strip()), config
            )
        observed = {case["case_id"]: check_case(case, config, observations) for case in cases}
        prepared = {case["case_id"]: prepared_summary(case, observations, config) for case in cases}
        report = None
        if report_path.exists():
            report = json.loads(report_path.read_bytes())
            archived_report = read(
                db.execute("SELECT * FROM records WHERE id=?", (report["report_record_id"],)).fetchone(), True
            )
            if {k: v for k, v in report.items() if k != "report_record_id"} != archived_report or report[
                "registration_record_id"
            ] != run_id:
                raise ValueError("Local report differs from archived report")
            cards = report["candidates"]
            if [c["candidate"] for c in cards] != config["candidates"]:
                raise ValueError("Final report omitted or changed a registered candidate")
            if (
                report["common_panel_sha256"] != digest([c["case_id"] for c in cases])
                or report["quantile_levels"] != protocol["quantile_levels"]
            ):
                raise ValueError("Final common case panel or quantiles differ")
        else:
            cards = [
                read(row)
                for row in db.execute(
                    "SELECT * FROM records WHERE kind='e022_candidate_result' AND key LIKE ? ORDER BY id",
                    (f"{run_id}:%",),
                ).fetchall()
            ]

        def read_fit():
            row = db.execute(
                "SELECT * FROM records WHERE kind='e022_fixed_fit_result' AND key=? ORDER BY id DESC LIMIT 1",
                (str(run_id),),
            ).fetchone()
            fit = read(row)
            local_path = root / "single_fit_attempt/payload.json"
            attempt = json.loads((root / "single_fit_attempt/attempt_result.json").read_bytes())
            if (
                json.loads(local_path.read_bytes()) != fit
                or attempt["status"] != "completed"
                or file_hash(local_path) != attempt["payload_sha256"]
            ):
                raise ValueError("Archived fit differs from the sole successful supervised fit")
            for filename, expected in fit["saved_checkpoint_files"].items():
                if file_hash(root / "single_fit_attempt/model/finetuned-ckpt" / filename) != expected:
                    raise ValueError("Saved trained checkpoint bytes changed")
            return fit

        results, details = [], {}
        maximum_error = 0.0
        seen_candidates = set()
        for card in cards:
            identifier = card["candidate"]["id"]
            if identifier in seen_candidates or card["candidate"] not in config["candidates"]:
                raise ValueError("Duplicate or unregistered candidate result")
            seen_candidates.add(identifier)
            records = db.execute(
                "SELECT * FROM records WHERE kind='e022_candidate_result' AND key=? ORDER BY id",
                (f"{run_id}:{identifier}",),
            ).fetchall()
            if len(records) != 1:
                raise ValueError("Candidate was missing or published multiple times")
            archived = read(records[0])
            if {k: v for k, v in card.items() if k != "paired_against_persistence"} != archived:
                raise ValueError("Candidate report differs from its immutable result")
            if card["status"] != "completed":
                results.append(
                    {
                        "candidate": identifier,
                        "status": "failed_retained",
                        "error": card.get("error"),
                        "scored_by_audit": False,
                    }
                )
                continue
            prediction_path = Path(card["predictions_file"])
            if (
                prediction_path.resolve() != (root / identifier / "predictions.json").resolve()
                or file_hash(prediction_path) != card["predictions_sha256"]
            ):
                raise ValueError("Candidate prediction file is detached from its registered artifact")
            predictions = json.loads(prediction_path.read_bytes())["predictions"]
            neural = None
            if identifier.startswith("chronos_"):
                batch_predictions, neural = audit_neural(
                    card, protocol, registration, cases, prepared, read_fit
                )
                if batch_predictions != predictions:
                    raise ValueError("Merged forecasts differ from retained neural batches")
            else:
                if any(
                    row["quantiles_f"] != [row["point_f"]] * len(protocol["quantile_levels"])
                    for row in predictions
                ):
                    raise ValueError("Baseline raw quantiles differ from its point forecast")
                if identifier in ("persistence", "seasonal_24h", "blend_50_50"):
                    for row in predictions:
                        base = prepared[row["case_id"]]
                        seasonal = (
                            base["seasonal_24h_f"]
                            if base["seasonal_24h_f"] is not None
                            else base["persistence_f"]
                        )
                        expected = (
                            base["persistence_f"]
                            if identifier == "persistence"
                            else seasonal
                            if identifier == "seasonal_24h"
                            else 0.5 * (seasonal + base["persistence_f"])
                        )
                        close(row["point_f"], expected, "Direct baseline input-to-point mapping")
            expected, scores = reconstruct_scores(
                predictions, cases, observed, protocol["quantile_levels"], config
            )
            maximum_error = max(maximum_error, compare_scores(card["evaluation"], expected))
            details[identifier] = scores
            results.append(
                {
                    "candidate": identifier,
                    "status": "scores_reproduced",
                    "prediction_cases": len(predictions),
                    "development_cases": expected["development_cases"],
                    "development_days": expected["development_utc_days"],
                    "independent_metrics": expected["metrics"],
                    "neural_evidence": neural,
                }
            )
        if report is not None:
            if report["completed_candidates"] != len(details) or report["failed_candidates"] != len(
                cards
            ) - len(details):
                raise ValueError("Final candidate completion counts differ")
            for split, key in (
                ("calibration", "common_calibration_cases"),
                ("development", "common_development_cases"),
            ):
                if report[key] != sum(c["split"] == split for c in cases):
                    raise ValueError("Final case counts differ")
            if "persistence" in details:
                baseline = {r["case_id"]: r for r in details["persistence"]}
                for card in cards:
                    identifier = card["candidate"]["id"]
                    if identifier not in details:
                        continue
                    days = defaultdict(list)
                    for row in details[identifier]:
                        days[row["day"]].append(
                            row["absolute_error_f"] - baseline[row["case_id"]]["absolute_error_f"]
                        )
                    paired = card["paired_against_persistence"]
                    close(
                        paired["candidate_minus_persistence_mae_f"],
                        mean([mean(v) for v in days.values()]),
                        "Paired mean daily MAE difference",
                    )
                    if paired["case_pairs"] != len(baseline) or paired["utc_day_groups"] != len(days):
                        raise ValueError("Paired comparison support differs")
                    for item in paired["daily"]:
                        close(
                            item["mae_difference_f"], mean(days[item["day"]]), "Paired daily MAE difference"
                        )
                        if item["cases"] != len(days[item["day"]]):
                            raise ValueError("Paired day case count differs")
    result = {
        "generated_at": datetime.now(UTC).isoformat(),
        "run_record_id": run_id,
        "audit_source_sha256": file_hash(__file__),
        "registration_record_sha256": registration["record_sha256"],
        "final_report_audited": report is not None,
        "report_record_id": report["report_record_id"] if report else None,
        "registered_candidates": len(config["candidates"]),
        "candidate_records_audited": len(cards),
        "completed_scores_reproduced": len(details),
        "failed_candidates_retained": len(cards) - len(details),
        "maximum_score_or_calibration_difference": maximum_error,
        "numerical_tolerance": TOLERANCE,
        "common_prediction_cases": len(cases),
        "candidates": results,
        "archive_source_ids": sorted(source_ids),
        "original_checkpoints_reloaded": 0,
        "models_fitted": 0,
        "model_predictions_reexecuted": 0,
        "archive_writes": 0,
        "network_requests": 0,
        "profitability_proven": False,
        "limits": "Independent score arithmetic, exact input/prediction hashes and retained neural replay metadata. No model execution replay, historical publication proof, untouched validation, or profitability inference.",
    }
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int, default=105314)
    parser.add_argument("--archive-root", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("reports/E022_audit.json"))
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(self_check()))
    else:
        run(args.run_record_id, args.archive_root, args.output, args.allow_partial)
