"""Registered read-only E026 result replay; no forecast inference or network.

Shared provenance checking: E026's source/lineage and raw-card extraction helpers.
Independent score arithmetic: the previously separate E022 arithmetic auditor.
Independent bootstrap implementation below does not call E026 shared_intervals.
"""

import argparse
import fcntl
import hashlib
import json
import math
import signal
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from research.experiments.e022_audit import (
    check_case,
    compare_scores,
    observations_from_rows,
    reconstruct_scores,
)
from research.experiments.e026_nbh_comparison import (
    original_sources,
    read_record,
    validate_acquisition,
    validate_bindings,
    verify_files,
)
from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import utcnow

RUN_ID = 117527
EXPERIMENT = "E026-independent-result-audit-v1"
FILES = [
    Path(__file__),
    Path("tests/test_e026_result_audit.py"),
    Path("research/experiments/e026_nbh_comparison.py"),
    Path("research/experiments/e022_audit.py"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
]
PIN_FIELDS = ("id", "kind", "key", "available_at", "body_sha256", "record_sha256")


def sha(body):
    return hashlib.sha256(body).hexdigest()


def pin(row):
    return {key: row[key] for key in PIN_FIELDS}


def assert_pin(row, expected):
    if any(row.get(key) != value for key, value in expected.items()):
        raise ValueError("Pinned source/report link changed")


def exact_tree(actual, expected, path="result"):
    """All fields retained; tight arithmetic tolerance never permits omission."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(actual) != set(expected):
            raise ValueError("Field set changed: " + path)
        return max((exact_tree(actual[k], v, path + "." + str(k)) for k, v in expected.items()), default=0.0)
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise ValueError("Array length changed: " + path)
        return max(
            (exact_tree(a, b, f"{path}[{i}]") for i, (a, b) in enumerate(zip(actual, expected, strict=True))),
            default=0.0,
        )
    if isinstance(expected, float):
        if (
            isinstance(actual, bool)
            or not isinstance(actual, (int, float))
            or not math.isfinite(actual)
            or abs(actual - expected) > 1e-10
        ):
            raise ValueError("Arithmetic value changed: " + path)
        return abs(actual - expected)
    if actual != expected or (isinstance(expected, bool) and type(actual) is not bool):
        raise ValueError("Exact value changed: " + path)
    return 0.0


def bootstrap_replay(differences, days, settings):
    fixed = {"block_days": 7, "resamples": 10000, "seed": 6202601, "confidence": 0.95, "comparison_count": 8}
    if settings != fixed:
        raise ValueError("Bootstrap registration changed")
    expected_days = [(date(2026, 7, 20) + timedelta(days=i)).isoformat() for i in range(28)]
    if days != expected_days:
        return {"status": "unsupported_missing_calendar_days", "days": days}
    matrix = np.asarray(differences, dtype=float)
    if matrix.shape != (28, 8) or not np.isfinite(matrix).all():
        raise ValueError("Missing comparison or invalid paired day matrix")
    rng = np.random.default_rng(6202601)
    starts = rng.integers(0, 28, size=(10000, 4))
    indices = np.empty((10000, 28), dtype=np.int64)
    for block in range(4):
        for offset in range(7):
            indices[:, block * 7 + offset] = (starts[:, block] + offset) % 28
    # Replay each draw independently, retaining the exact original row summation order.
    samples = np.stack([matrix[index].mean(axis=0) for index in indices])
    means = matrix.mean(axis=0)
    errors = samples.std(axis=0, ddof=1)
    active = np.array(
        [
            bool(np.any(matrix[:, column] != matrix[0, column])) and errors[column] > 1e-14
            for column in range(8)
        ]
    )
    maxima = np.zeros(10000)
    if active.any():
        for column in np.flatnonzero(active):
            maxima = np.maximum(maxima, np.abs(samples[:, column] - means[column]) / errors[column])
    critical = float(np.sort(maxima)[math.ceil(0.95 * (len(maxima) - 1))])
    return {
        "status": "descriptive_development_intervals",
        "means": means.tolist(),
        "standard_errors": errors.tolist(),
        "active": active.tolist(),
        "critical_value": critical,
        "intervals": [
            [float(mean - critical * error), float(mean + critical * error)] if usable else None
            for mean, error, usable in zip(means, errors, active, strict=True)
        ],
        "indices_sha256": sha(canonical(indices.tolist()).encode()),
        "resampled_means_sha256": sha(canonical(samples.tolist()).encode()),
        "settings": settings,
        "independent_significance_proven": False,
    }


def verify_result(result, cases, physical, predictions, observations, config, parent_config, old_report):
    validate_bindings(cases, physical)
    ids = {case["case_id"] for case in cases}
    physical_map = {case["case_id"]: case for case in physical}
    if len(physical) != len(ids) or set(physical_map) != ids:
        raise ValueError("Physical case census changed")
    available = {key for key, row in physical_map.items() if row["available"]}
    panel = [case for case in cases if case["case_id"] in available]
    expected_support = {
        "full_panel_complete": available == ids,
        "classification": "full_original_panel" if available == ids else "common_available_subset_only",
        "original_case_count": len(cases),
        "common_case_count": len(panel),
        "missing": [
            {"case_id": row["case_id"], "reason": row["reason"]} for row in physical if not row["available"]
        ],
        "case_ids_sha256": sha(canonical(sorted(available)).encode()),
    }
    for key, value in expected_support.items():
        exact_tree(result[key], value, key)
    support = []
    for horizon in (1, 3, 6):
        cal = [case for case in panel if case["split"] == "calibration" and case["horizon_hours"] == horizon]
        support.append(
            len(cal) >= parent_config["minimum_calibration_cases_per_horizon"]
            and len({case["target_ms"] // 86400000 for case in cal})
            >= parent_config["minimum_calibration_days_per_horizon"]
        )
    if not all(support) or not any(case["split"] == "development" for case in panel):
        status = "insufficient_common_calibration" if not all(support) else "no_common_development_cases"
        exact_tree(result["status"], status)
        exact_tree(result["candidates"], [])
        exact_tree(result["comparisons"], [])
        return {**expected_support, "status": status, "audited_models": 0, "audited_comparisons": 0}
    model_ids = [*config["model_ids"], config["new_model_id"]]
    if result["status"] != "scored" or [c["id"] for c in result["candidates"]] != model_ids:
        raise ValueError("Missing or reordered model evaluation")
    if [c["candidate"] for c in result["comparisons"]] != config["model_ids"]:
        raise ValueError("Missing or reordered comparison")
    observed = {case["case_id"]: check_case(case, parent_config, observations) for case in panel}
    audited, maximum = [], 0.0
    for model, reported in zip(model_ids, result["candidates"], strict=True):
        if model == config["new_model_id"]:
            rows = [
                {
                    "case_id": case["case_id"],
                    "point_f": physical_map[case["case_id"]]["point_f"],
                    "quantiles_f": [physical_map[case["case_id"]]["point_f"]]
                    * len(config["quantile_levels"]),
                }
                for case in panel
            ]
        else:
            saved = predictions[model]
            if len(saved) != len(ids) or {row["case_id"] for row in saved} != ids:
                raise ValueError("Saved prediction census changed")
            rows = [row for row in saved if row["case_id"] in available]
        evaluation, _ = reconstruct_scores(rows, panel, observed, config["quantile_levels"], parent_config)
        maximum = max(maximum, compare_scores(reported["evaluation"], evaluation))
        # The original scorer appends two static interpretation labels; verify
        # them explicitly while requiring every computed field from the auditor.
        complete_evaluation = {
            **evaluation,
            "metric_weighting": "Equal UTC target-day weight, equal case weight within each day; RMSE is the square root of day-weighted squared error.",
            "point_metric_basis": "Raw baseline point forecasts or pretrained/fitted neural medians before quantile calibration.",
        }
        exact_tree(reported["evaluation"], complete_evaluation, model)
        audited.append(evaluation)
    if available == ids:
        if [row["candidate"]["id"] for row in old_report["candidates"]] != config["model_ids"]:
            raise ValueError("Original E022 candidate family changed")
        for current, original in zip(audited[:-1], old_report["candidates"], strict=True):
            maximum = max(maximum, compare_scores(original["evaluation"], current))
    nbh = audited[-1]
    days = [row["day"] for row in nbh["daily"]]
    paired = []
    for i, row in enumerate(nbh["daily"]):
        if any(
            (other["daily"][i]["day"], other["daily"][i]["cases"]) != (row["day"], row["cases"])
            for other in audited[:-1]
        ):
            raise ValueError("Paired daily support differs")
        paired.append(
            [row["absolute_error_f"] - other["daily"][i]["absolute_error_f"] for other in audited[:-1]]
        )
    maximum = max(maximum, exact_tree(result["daily_paired_mae"], paired, "paired_mae"))
    for i, comparison in enumerate(result["comparisons"]):
        maximum = max(
            maximum,
            exact_tree(
                comparison["nbh_minus_candidate_mae_f"],
                nbh["metrics"]["mae_f"] - audited[i]["metrics"]["mae_f"],
                "comparison_mean",
            ),
        )
    # Validate the stored matrix against independent arithmetic first; replay its
    # exact bytes to distinguish rounding tolerance from wrong draw hashes.
    boot = bootstrap_replay(result["daily_paired_mae"], days, config["bootstrap"])
    maximum = max(maximum, exact_tree(result["bootstrap"], boot, "bootstrap"))
    return {
        **expected_support,
        "status": "all_registered_arithmetic_reproduced",
        "audited_models": 9,
        "audited_comparisons": 8,
        "original_e022_models_reproduced": 8 if available == ids else 0,
        "maximum_arithmetic_error": maximum,
        "bootstrap_status": boot["status"],
        "bootstrap_indices_sha256": boot.get("indices_sha256"),
        "bootstrap_resampled_means_sha256": boot.get("resampled_means_sha256"),
        "common_counts_by_split": dict(Counter(case["split"] for case in panel)),
        "common_counts_by_station": dict(Counter(case["station_id"] for case in panel)),
        "common_counts_by_horizon": dict(Counter(str(case["horizon_hours"]) for case in panel)),
    }


def register(archive):
    if archive.latest("e026_result_audit_protocol", str(RUN_ID)):
        raise ValueError("Result audit already registered")
    run_row, run = read_record(archive, RUN_ID, "e026_protocol")
    verify_files(run)
    output = archive.latest("e026_report_gzip", str(RUN_ID))
    started = archive.latest("e026_started", str(RUN_ID))
    if output is None or started is None or not RUN_ID < started["id"] < output["id"]:
        raise ValueError("A completed E026 report and earlier source binding are required")
    # Neither result nor source-binding body is opened during this registration.
    hashes = {str(path): sha(path.read_bytes()) for path in FILES}
    source_records = {
        str(path): archive.append("research_source", str(path), utcnow(), {}, path.read_bytes())
        for path in FILES
    }
    protocol = {
        "experiment": EXPERIMENT,
        "run": pin(run_row),
        "report": pin(dict(output)),
        "started": pin(dict(started)),
        "source_hashes": hashes,
        "source_record_ids": source_records,
        "parent_source_hashes": run["source_hashes"],
        "python_numpy_version": np.__version__,
        "cwd": str(Path.cwd().resolve()),
        "budget_seconds": 600,
        "stop_file": "data/STOP_E026_AUDIT",
        "registered_before_result_body": True,
        "root_reported_aggregate_results_known": True,
        "known_context": "Root communicated NBH MAE1.8345127, fixed-fit1.88720755, pretrained1.90722714 and neural-comparison intervals including zero before this audit implementation finished. Audit has not read result bodies before its own registration.",
        "shared_helpers": "E026 original_sources/read_record/validate_acquisition/validate_bindings validate raw lineage and card bytes; independent E022 reconstruct_scores/compare_scores provide score arithmetic. Bootstrap implementation is separate.",
        "model_fits": 0,
        "model_inferences": 0,
        "network_requests": 0,
        "trading_actions": 0,
        "automatic_retry": False,
    }
    return archive.append(
        "e026_result_audit_protocol", str(RUN_ID), utcnow(), {}, canonical(protocol).encode()
    )


def execute(archive, identifier):
    _, protocol = read_record(archive, identifier, "e026_result_audit_protocol")
    if (
        protocol["experiment"] != EXPERIMENT
        or protocol["run"]["id"] != RUN_ID
        or protocol["budget_seconds"] != 600
    ):
        raise ValueError("Wrong audit declaration")
    if (
        protocol["source_hashes"] != {str(path): sha(path.read_bytes()) for path in FILES}
        or protocol["python_numpy_version"] != np.__version__
        or protocol["cwd"] != str(Path.cwd().resolve())
    ):
        raise ValueError("Audit sources/runtime changed")
    for path, source_id in protocol["source_record_ids"].items():
        row, raw = read_record(archive, source_id, "research_source", decode=False)
        if (
            source_id >= identifier
            or row["key"] != path
            or row["body_sha256"] != protocol["source_hashes"][path]
            or sha(raw) != protocol["source_hashes"][path]
        ):
            raise ValueError("Audit source archive changed")
    if archive.latest("e026_result_audit_started", str(identifier)):
        raise ValueError("Audit already attempted; no automatic retry")
    archive.append("e026_result_audit_started", str(identifier), utcnow(), {}, b"{}")

    def timeout(_number, _frame):
        raise TimeoutError("Original 600-second audit budget reached")

    old_handler = signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 600)
    try:
        run_row, run = read_record(archive, RUN_ID, "e026_protocol")
        assert_pin(run_row, protocol["run"])
        if run["source_hashes"] != protocol["parent_source_hashes"]:
            raise ValueError("Parent source pins changed")
        verify_files(run)
        config = run["config"]
        started_row, started = read_record(archive, protocol["started"]["id"], "e026_started")
        assert_pin(started_row, protocol["started"])
        report_row, report = read_record(archive, protocol["report"]["id"], "e026_report_gzip")
        assert_pin(report_row, protocol["report"])
        if report["registration_id"] != RUN_ID or report["source_binding_record_id"] != started_row["id"]:
            raise ValueError("Report source binding changed")
        if any(
            report[field] != 0
            for field in ("model_fits", "model_inferences", "network_requests", "trading_actions")
        ) or any(
            report[field] is not False
            for field in (
                "profitability_proven",
                "historical_public_availability_verified",
                "untouched_validation",
            )
        ):
            raise ValueError("Result exceeds registered development interpretation")
        acquisition_row, acquisition = read_record(
            archive, config["acquisition_registration_id"], config["acquisition_protocol_kind"]
        )
        if acquisition_row["record_sha256"] != run["acquisition_record_sha256"]:
            raise ValueError("Acquisition registration link changed")
        cases = json.loads(Path(config["manifest_path"]).read_bytes())["cases"]
        validate_acquisition(acquisition, config, cases)
        objects = started["object_sources"]
        if len(objects) != 498 or len({item["id"] for item in objects}) != 498:
            raise ValueError("Started source census is not all498objects")
        physical, raw_ids = [], set()
        for pinned, entry in zip(objects, acquisition["manifest"]["objects"], strict=True):
            if Path(protocol["stop_file"]).exists():
                raise InterruptedError("Audit stop file present")
            row, value = read_record(archive, pinned["id"], config["acquisition_object_kind"])
            assert_pin(row, pinned)
            if not config["acquisition_registration_id"] < row["id"] < started_row["id"]:
                raise ValueError("Object was not fixed before comparison start")
            rows, sources = original_sources(archive, value, entry, config, row)
            physical.extend(rows)
            raw_ids.update(sources)
        if report["raw_source_ids"] != sorted(raw_ids):
            raise ValueError("Final raw source list differs from validated objects")
        parent_config = json.loads(Path(config["parent_config_path"]).read_bytes())
        with Path(parent_config["observations_path"]).open() as handle:
            observations = observations_from_rows(
                (json.loads(line) for line in handle if line.strip()), parent_config
            )
        predictions = {
            model: json.loads(Path(f"reports/E022-run-105314/{model}/predictions.json").read_bytes())[
                "predictions"
            ]
            for model in config["model_ids"]
        }
        old_row, old = read_record(archive, config["parent_report_record_id"], "experiment_report_gzip")
        if old_row["body_sha256"] != run["report_body_sha256"]:
            raise ValueError("Original E022 report link changed")
        outcome = verify_result(
            report["result"], cases, physical, predictions, observations, config, parent_config, old
        )
        local = json.loads(Path(f"reports/E026_comparison_{RUN_ID}.json").read_bytes())
        if local != {**report, "report_record_id": report_row["id"]}:
            raise ValueError("Local E026 report differs from archive")
        verify_files(run)
        if protocol["source_hashes"] != {str(path): sha(path.read_bytes()) for path in FILES}:
            raise ValueError("Audit source changed during replay")
        result = {
            "experiment": EXPERIMENT,
            "audit_protocol_id": identifier,
            "audited_run_id": RUN_ID,
            "audited_report_id": report_row["id"],
            "audited_report_body_sha256": report_row["body_sha256"],
            "objects_verified": len(objects),
            "raw_sources_verified": len(raw_ids),
            "result": outcome,
            "shared_helpers": protocol["shared_helpers"],
            "bootstrap_independently_replayed": outcome.get("bootstrap_status")
            == "descriptive_development_intervals",
            "model_fits": 0,
            "model_inferences": 0,
            "network_requests": 0,
            "trading_actions": 0,
            "profitability_proven": False,
            "historical_public_availability_verified": False,
            "untouched_validation": False,
        }
        record_id = archive.append(
            "e026_result_audit_report", str(identifier), utcnow(), {}, canonical(result).encode()
        )
        path = Path(f"reports/E026_audit_{identifier}.json")
        with path.open("x") as handle:
            json.dump({**result, "audit_report_id": record_id}, handle, indent=2, allow_nan=False)
        return {"audit_report_id": record_id, "path": str(path), "result": outcome}
    except BaseException as error:
        archive.append(
            "e026_result_audit_failed",
            str(identifier),
            utcnow(),
            {},
            canonical({"error": f"{type(error).__name__}: {error}", "automatic_retry": False}).encode(),
        )
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--register", action="store_true")
    modes.add_argument("--run-record-id", type=int)
    args = parser.parse_args()
    archive = Archive("data")
    try:
        with (archive.root / "E026.result_audit.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = (
                {"audit_protocol_id": register(archive), "result_bodies_read": 0}
                if args.register
                else execute(archive, args.run_record_id)
            )
            print(json.dumps(result, sort_keys=True))
    finally:
        archive.close()


if __name__ == "__main__":
    main()
