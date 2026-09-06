"""Independent E029 raw-blend, score and bootstrap audit, with explicit source reuse.

No fits, inference, orders or network. Shared E026 lineage/card extraction and
E022 pure score arithmetic are disclosed; blend and bootstrap code are separate.
"""

import argparse
import fcntl
import json
import math
import signal
from collections import Counter
from datetime import date, timedelta
from itertools import pairwise
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
    validate_bindings,
    verify_files,
)
from research.experiments.e026_result_audit import assert_pin, exact_tree, pin, sha
from research.experiments.e029_fixed_combinations import parents
from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import parse_time, utcnow

RUN_ID = 129863
EXPERIMENT = "E029-independent-result-audit-v1"
FILES = [
    Path(__file__),
    Path("tests/test_e029_result_audit.py"),
    Path("research/experiments/e026_result_audit.py"),
    Path("research/experiments/e026_nbh_comparison.py"),
    Path("research/experiments/e029_fixed_combinations.py"),
    Path("research/experiments/e022_audit.py"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
]
DISCLOSURE = "Shared E029 parents checks only design/acquisition lineage, E026 original_sources checks raw headers/times/cards, E022 reconstruct_scores performs independent arithmetic. E026 audit pin/tree utilities are reused. Raw blends use new NumPy broadcasting; bootstrap is separately replayed. No production prediction_panel, score_panel or shared_intervals calls."


def bootstrap_replay(differences, days, settings):
    fixed = {"block_days": 7, "resamples": 10000, "seed": 6202901, "confidence": 0.95, "comparison_count": 12}
    if settings != fixed:
        raise ValueError("Bootstrap registration changed")
    expected_days = [(date(2026, 7, 20) + timedelta(days=i)).isoformat() for i in range(28)]
    if days != expected_days:
        return {
            "status": "unsupported_missing_calendar_days",
            "days": days,
            "independent_significance_proven": False,
        }
    matrix = np.asarray(differences, dtype=float)
    if matrix.shape != (28, 12) or not np.isfinite(matrix).all():
        raise ValueError("Missing comparison or invalid paired day matrix")
    rng = np.random.default_rng(6202901)
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
            for column in range(12)
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


def reconstruct_panel(cases, physical, predictions, config):
    """Accept no labels; reconstruct every raw point and all13quantiles."""
    validate_bindings(cases, physical)
    identities = {case["case_id"] for case in cases}
    if len(identities) != len(cases) or len(physical) != len(cases):
        raise ValueError("Raw case census changed")
    mapping = {row["case_id"]: row for row in physical}
    if set(mapping) != identities or len(mapping) != len(physical):
        raise ValueError("Physical case identities changed")
    if set(predictions) != set(config["model_ids"]):
        raise ValueError("Original model family changed")
    selected = [case for case in cases if mapping[case["case_id"]]["available"]]
    selected_ids = [case["case_id"] for case in selected]
    arrays = {}
    levels = config["quantile_levels"]
    for row in physical:
        if type(row["available"]) is not bool or (not row["available"] and row["point_f"] is not None):
            raise ValueError("Physical missingness changed")
        if row["available"] and (
            isinstance(row["point_f"], bool)
            or not isinstance(row["point_f"], (int, float))
            or not math.isfinite(row["point_f"])
        ):
            raise ValueError("Invalid physical raw point")
    for model in config["model_ids"]:
        rows = predictions[model]
        if len(rows) != len(cases) or {row["case_id"] for row in rows} != identities:
            raise ValueError("Saved prediction census changed")
        table = {}
        for row in rows:
            if len(row["quantiles_f"]) != len(levels) or any(
                isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
                for v in [row["point_f"], *row["quantiles_f"]]
            ):
                raise ValueError("Invalid saved raw point or quantiles")
            table[row["case_id"]] = {
                "case_id": row["case_id"],
                "point_f": row["point_f"],
                "quantiles_f": list(row["quantiles_f"]),
            }
        arrays[model] = [table[key] for key in selected_ids]
    arrays[config["physical_model_id"]] = [
        {
            "case_id": key,
            "point_f": mapping[key]["point_f"],
            "quantiles_f": [mapping[key]["point_f"]] * len(levels),
        }
        for key in selected_ids
    ]
    # One N-by-14 raw matrix (point +13levels) per parent; no calibration,
    # interpolation, labels or weight fitting enters these broadcasts.
    neural = np.asarray(
        [[row["point_f"], *row["quantiles_f"]] for row in arrays[config["neural_model_id"]]], dtype=np.float64
    ).reshape(-1, len(levels) + 1)
    physical_values = np.asarray([mapping[key]["point_f"] for key in selected_ids], dtype=np.float64)[:, None]
    if config["hybrids"] != [
        {"id": "nbh_chronos_w025", "neural_weight": 0.25},
        {"id": "nbh_chronos_w050", "neural_weight": 0.5},
        {"id": "nbh_chronos_w075", "neural_weight": 0.75},
    ]:
        raise ValueError("Predeclared raw blend weights changed")
    for hybrid in config["hybrids"]:
        weight = hybrid["neural_weight"]
        combined = np.add(np.multiply(physical_values, 1.0 - weight), np.multiply(neural, weight))
        arrays[hybrid["id"]] = [
            {"case_id": key, "point_f": values[0], "quantiles_f": values[1:]}
            for key, values in zip(selected_ids, combined.tolist(), strict=True)
        ]
    return {
        "cases": selected,
        "predictions": arrays,
        "candidate_order": [
            *config["model_ids"],
            config["physical_model_id"],
            *[h["id"] for h in config["hybrids"]],
        ],
        "original_case_count": len(cases),
        "common_case_count": len(selected),
        "full_panel_complete": len(selected) == len(cases),
        "classification": "full_original_panel"
        if len(selected) == len(cases)
        else "common_available_subset_only",
        "missing": [
            {"case_id": row["case_id"], "reason": row["reason"]} for row in physical if not row["available"]
        ],
        "case_ids_sha256": sha(canonical(sorted(selected_ids)).encode()),
        "calibration_applied": False,
        "labels_accessed": False,
        "formula": "(1-w)*NBH_TMP+w*Chronos_raw_point_or_quantile; w in .25,.50,.75",
    }


def exact_raw_panel(stored, expected):
    if canonical(stored) != canonical(expected):
        raise ValueError("Retained raw blends/panel differ from independent reconstruction")
    return sha(canonical(expected).encode())


def check_chronology(run_row, start_row, prediction_row, report_row):
    rows = [run_row, start_row, prediction_row, report_row]
    if not all(
        a["id"] < b["id"] and parse_time(a["available_at"]) <= parse_time(b["available_at"])
        for a, b in pairwise(rows)
    ):
        raise ValueError("Prediction was not archived between registration/start and scored report")


def verify_scores(result, panel, observations, config, parent_config, originals):
    support_keys = (
        "original_case_count",
        "common_case_count",
        "full_panel_complete",
        "classification",
        "missing",
        "case_ids_sha256",
    )
    for key in support_keys:
        exact_tree(result[key], panel[key], key)
    cases = panel["cases"]
    adequate = True
    for horizon in (1, 3, 6):
        cal = [case for case in cases if case["split"] == "calibration" and case["horizon_hours"] == horizon]
        adequate &= (
            len(cal) >= parent_config["minimum_calibration_cases_per_horizon"]
            and len({c["target_ms"] // 86400000 for c in cal})
            >= parent_config["minimum_calibration_days_per_horizon"]
        )
    if not adequate or not any(case["split"] == "development" for case in cases):
        exact_tree(
            result["status"],
            "insufficient_common_calibration" if not adequate else "no_common_development_cases",
        )
        exact_tree(result["candidates"], [])
        exact_tree(result["comparisons"], [])
        return {"status": result["status"], "audited_models": 0, "audited_comparisons": 0}
    if (
        result["status"] != "scored"
        or [row["id"] for row in result["candidates"]] != panel["candidate_order"]
    ):
        raise ValueError("Missing/reordered candidate evaluation")
    contrasts = [(h["id"], reference) for h in config["hybrids"] for reference in config["reference_ids"]]
    if (
        len(contrasts) != 12
        or [(row["hybrid"], row["reference"]) for row in result["comparisons"]] != contrasts
    ):
        raise ValueError("Missing/reordered fixed contrast")
    observed = {case["case_id"]: check_case(case, parent_config, observations) for case in cases}
    audited, maximum = {}, 0.0
    for reported in result["candidates"]:
        model = reported["id"]
        evaluation, _ = reconstruct_scores(
            panel["predictions"][model], cases, observed, config["quantile_levels"], parent_config
        )
        maximum = max(maximum, compare_scores(reported["evaluation"], evaluation))
        complete = {
            **evaluation,
            "metric_weighting": "Equal UTC target-day weight, equal case weight within each day; RMSE is the square root of day-weighted squared error.",
            "point_metric_basis": "Raw baseline point forecasts or pretrained/fitted neural medians before quantile calibration.",
        }
        maximum = max(maximum, exact_tree(reported["evaluation"], complete, model))
        audited[model] = evaluation
    original_count = 0
    if panel["full_panel_complete"]:
        for model in [*config["model_ids"], config["physical_model_id"]]:
            maximum = max(maximum, compare_scores(originals[model], audited[model]))
            original_count += 1
    reference_days = audited[config["physical_model_id"]]["daily"]
    for value in audited.values():
        if [(row["day"], row["cases"]) for row in value["daily"]] != [
            (row["day"], row["cases"]) for row in reference_days
        ]:
            raise ValueError("Daily case support differs between candidates")
    differences = [
        [
            audited[hybrid]["daily"][i]["absolute_error_f"]
            - audited[reference]["daily"][i]["absolute_error_f"]
            for hybrid, reference in contrasts
        ]
        for i in range(len(reference_days))
    ]
    days = [row["day"] for row in reference_days]
    maximum = max(maximum, exact_tree(result["daily_paired_mae"], differences, "paired_daily_mae"))
    exact_tree(result["daily_paired_mae_days"], days)
    for (hybrid, reference), reported in zip(contrasts, result["comparisons"], strict=True):
        maximum = max(
            maximum,
            exact_tree(
                reported["hybrid_minus_reference_mae_f"],
                audited[hybrid]["metrics"]["mae_f"] - audited[reference]["metrics"]["mae_f"],
                "contrast",
            ),
        )
    bootstrap = bootstrap_replay(result["daily_paired_mae"], days, config["bootstrap"])
    maximum = max(maximum, exact_tree(result["bootstrap"], bootstrap, "bootstrap"))
    return {
        "status": "all_registered_arithmetic_reproduced",
        "audited_models": 12,
        "audited_comparisons": 12,
        "original_models_reproduced": original_count,
        "maximum_arithmetic_error": maximum,
        "bootstrap_status": bootstrap["status"],
        "bootstrap_indices_sha256": bootstrap.get("indices_sha256"),
        "bootstrap_resampled_means_sha256": bootstrap.get("resampled_means_sha256"),
        "common_counts_by_split": dict(Counter(case["split"] for case in cases)),
        "common_counts_by_station": dict(Counter(case["station_id"] for case in cases)),
        "common_counts_by_horizon": dict(Counter(str(case["horizon_hours"]) for case in cases)),
    }


def register(archive):
    if archive.latest("e029_result_audit_protocol", str(RUN_ID)):
        raise ValueError("Audit already registered")
    run_row, run = read_record(archive, RUN_ID, "e029_protocol")
    verify_files(run)
    found = {}
    for key, kind in (
        ("report", "e029_report_gzip"),
        ("predictions", "e029_predictions_gzip"),
        ("started", "e029_started"),
    ):
        rows = archive.db.execute(
            "SELECT * FROM records WHERE kind=? AND key=?", (kind, str(RUN_ID))
        ).fetchall()
        if len(rows) != 1:
            raise ValueError("Expected one complete report/prediction/start receipt")
        found[key] = dict(rows[0])
    check_chronology(run_row, found["started"], found["predictions"], found["report"])
    physical_report = archive.latest(
        "e026_report_gzip", str(run["config"]["physical_comparison_registration_id"])
    )
    if physical_report is None or physical_report["id"] >= RUN_ID:
        raise ValueError("Expected the inherited earlier physical comparison report")
    hashes = {str(path): sha(path.read_bytes()) for path in FILES}
    source_ids = {
        str(path): archive.append("research_source", str(path), utcnow(), {}, path.read_bytes())
        for path in FILES
    }
    protocol = {
        "experiment": EXPERIMENT,
        "run": pin(run_row),
        **{key: pin(row) for key, row in found.items()},
        "physical_report": pin(dict(physical_report)),
        "source_hashes": hashes,
        "source_record_ids": source_ids,
        "parent_source_hashes": run["source_hashes"],
        "numpy_version": np.__version__,
        "cwd": str(Path.cwd().resolve()),
        "budget_seconds": 600,
        "stop_file": "data/STOP_E029_AUDIT",
        "registered_before_result_body": True,
        "known_aggregates": "Root communicated three blend MAEs1.6607612607/1.6022569788/1.6865554322 versus NBH1.8345 and all12 descriptive intervals negative before audit implementation. No audit result/prediction body has been read before this registration.",
        "shared_helpers": DISCLOSURE,
        "automatic_retry": False,
    }
    return archive.append(
        "e029_result_audit_protocol", str(RUN_ID), utcnow(), {}, canonical(protocol).encode()
    )


def execute(archive, identifier):
    _, protocol = read_record(archive, identifier, "e029_result_audit_protocol")
    if (
        protocol["experiment"] != EXPERIMENT
        or protocol["run"]["id"] != RUN_ID
        or protocol["budget_seconds"] != 600
    ):
        raise ValueError("Wrong declared audit")
    if (
        protocol["source_hashes"] != {str(path): sha(path.read_bytes()) for path in FILES}
        or protocol["numpy_version"] != np.__version__
        or protocol["cwd"] != str(Path.cwd().resolve())
    ):
        raise ValueError("Audit source/runtime changed")
    for path, source_id in protocol["source_record_ids"].items():
        row, body = read_record(archive, source_id, "research_source", decode=False)
        if source_id >= identifier or row["key"] != path or sha(body) != protocol["source_hashes"][path]:
            raise ValueError("Audit archived source changed")
    if archive.latest("e029_result_audit_started", str(identifier)):
        raise ValueError("Audit already attempted; no automatic retry")
    archive.append("e029_result_audit_started", str(identifier), utcnow(), {}, b"{}")

    def timeout(_number, _frame):
        raise TimeoutError("Original600-second audit budget reached")

    old = signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 600)
    try:
        run_row, run = read_record(archive, RUN_ID, "e029_protocol")
        assert_pin(run_row, protocol["run"])
        if run["source_hashes"] != protocol["parent_source_hashes"]:
            raise ValueError("Inherited source pins changed")
        verify_files(run)
        config = run["config"]
        comparison, acquisition, cases, census = parents(archive, config)
        start_row, started = read_record(archive, protocol["started"]["id"], "e029_started")
        assert_pin(start_row, protocol["started"])
        prediction_row, predictions_record = read_record(
            archive, protocol["predictions"]["id"], "e029_predictions_gzip"
        )
        assert_pin(prediction_row, protocol["predictions"])
        check_chronology(run_row, start_row, prediction_row, protocol["report"])
        if (
            started["object_sources"] != run["object_sources"]
            or census != run["object_sources"]
            or len(census) != 498
        ):
            raise ValueError("498-object source census changed")
        settings = comparison["config"]
        physical, sources = [], set()
        for pinned, entry in zip(census, acquisition["manifest"]["objects"], strict=True):
            if Path(protocol["stop_file"]).exists():
                raise InterruptedError("Audit stop file present")
            row, value = read_record(archive, pinned["id"], settings["acquisition_object_kind"])
            assert_pin(row, pinned)
            if row["id"] >= RUN_ID:
                raise ValueError("Physical acquisition was not fixed before blend registration")
            rows, raw_ids = original_sources(archive, value, entry, settings, row)
            physical.extend(rows)
            sources.update(raw_ids)
        saved = {
            model: json.loads(Path(path).read_bytes())["predictions"]
            for model, path in run["prediction_paths"].items()
        }
        panel = reconstruct_panel(cases, physical, saved, config)
        panel_sha = exact_raw_panel(predictions_record["panel"], panel)
        if (
            predictions_record["registration_id"] != RUN_ID
            or predictions_record["source_binding_record_id"] != start_row["id"]
            or predictions_record["raw_source_ids"] != sorted(sources)
            or predictions_record["weather_scores_computed"] != 0
            or predictions_record["labels_accessed"] is not False
        ):
            raise ValueError("Immutable prediction provenance/labels claim changed")
        # Raw panel reconstruction and chronology are complete before score-body
        # or original target-label decoding begins.
        report_row, report = read_record(archive, protocol["report"]["id"], "e029_report_gzip")
        assert_pin(report_row, protocol["report"])
        if (
            report["registration_id"] != RUN_ID
            or report["source_binding_record_id"] != start_row["id"]
            or report["prediction_record_id"] != prediction_row["id"]
            or report["prediction_body_sha256"] != prediction_row["body_sha256"]
            or report["prediction_record_sha256"] != prediction_row["record_sha256"]
            or report["raw_source_ids"] != sorted(sources)
        ):
            raise ValueError("Report prediction/source linkage changed")
        if (
            report["selected_weight"] is not None
            or any(
                report[key] is not False
                for key in (
                    "promotion_eligible",
                    "profitability_proven",
                    "historical_public_availability_verified",
                    "untouched_validation",
                )
            )
            or any(
                report[key] != 0
                for key in ("model_fits", "model_inferences", "network_requests", "trading_actions")
            )
        ):
            raise ValueError("Report exceeds descriptive no-selection scope")
        physical_row, physical_report = read_record(
            archive, protocol["physical_report"]["id"], "e026_report_gzip"
        )
        assert_pin(physical_row, protocol["physical_report"])
        if physical_report["raw_source_ids"] != sorted(sources):
            raise ValueError("E026 raw-source inheritance changed")
        original_row, original = read_record(
            archive, config["parent_report_record_id"], "experiment_report_gzip"
        )
        if original_row["body_sha256"] != run["parent_report_body_sha256"]:
            raise ValueError("Original E022 report changed")
        originals = {row["candidate"]["id"]: row["evaluation"] for row in original["candidates"]}
        originals[config["physical_model_id"]] = next(
            row["evaluation"]
            for row in physical_report["result"]["candidates"]
            if row["id"] == config["physical_model_id"]
        )
        parent_config = json.loads(Path(settings["parent_config_path"]).read_bytes())
        with Path(parent_config["observations_path"]).open() as handle:
            observations = observations_from_rows(
                (json.loads(line) for line in handle if line.strip()), parent_config
            )
        outcome = verify_scores(report["result"], panel, observations, config, parent_config, originals)
        expected_reproduction = (
            "all_eight_original_full_panel_scores_reproduced"
            if panel["full_panel_complete"] and report["result"]["status"] == "scored"
            else "not_applicable_common_subset_or_insufficient_support"
        )
        if report["original_E022_reproduction"] != expected_reproduction:
            raise ValueError("Original model reproduction claim changed")
        local = json.loads(Path(f"reports/E029_combinations_{RUN_ID}.json").read_bytes())
        if local != {**report, "report_record_id": report_row["id"]}:
            raise ValueError("Local report differs from archived result")
        verify_files(run)
        if protocol["source_hashes"] != {str(path): sha(path.read_bytes()) for path in FILES}:
            raise ValueError("Audit source changed during replay")
        result = {
            "experiment": EXPERIMENT,
            "audit_protocol_id": identifier,
            "audited_run_id": RUN_ID,
            "audited_report_id": report_row["id"],
            "audited_report_body_sha256": report_row["body_sha256"],
            "prediction_record_id": prediction_row["id"],
            "prediction_body_sha256": prediction_row["body_sha256"],
            "raw_panel_sha256": panel_sha,
            "raw_predictions_exact": True,
            "hybrid_forecast_rows_checked": len(panel["cases"]) * 3,
            "hybrid_numeric_values_checked": len(panel["cases"]) * 3 * 14,
            "objects_verified": len(census),
            "raw_sources_verified": len(sources),
            "result": outcome,
            "shared_helpers": DISCLOSURE,
            "model_fits": 0,
            "model_inferences": 0,
            "network_requests": 0,
            "trading_actions": 0,
            "selected_weight": None,
            "profitability_proven": False,
            "untouched_validation": False,
            "historical_public_availability_verified": False,
        }
        record_id = archive.append(
            "e029_result_audit_report", str(identifier), utcnow(), {}, canonical(result).encode()
        )
        path = Path(f"reports/E029_audit_{identifier}.json")
        with path.open("x") as handle:
            json.dump({**result, "audit_report_id": record_id}, handle, indent=2, allow_nan=False)
        return {"audit_report_id": record_id, "path": str(path), "result": outcome}
    except BaseException as error:
        archive.append(
            "e029_result_audit_failed",
            str(identifier),
            utcnow(),
            {},
            canonical({"error": f"{type(error).__name__}: {error}", "automatic_retry": False}).encode(),
        )
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--register", action="store_true")
    modes.add_argument("--run-record-id", type=int)
    args = parser.parse_args()
    archive = Archive("data")
    try:
        with (archive.root / "E029.result_audit.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            value = (
                {"audit_protocol_id": register(archive), "result_bodies_read": 0}
                if args.register
                else execute(archive, args.run_record_id)
            )
            print(json.dumps(value, sort_keys=True))
    finally:
        archive.close()


if __name__ == "__main__":
    main()
