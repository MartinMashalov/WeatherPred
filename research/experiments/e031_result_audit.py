"""Registered E031 result/failure audit: retained arrays only, no model or network."""

import argparse
import fcntl
import gzip
import importlib.metadata
import json
import math
import signal
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np

from research.experiments.e022_audit import (
    check_case,
    compare_scores,
    observations_from_rows,
    prepared_summary,
    reconstruct_scores,
)
from research.experiments.e026_nbh_comparison import merge_pins, read_record, verify_files
from research.experiments.e026_result_audit import assert_pin, exact_tree, pin, sha
from research.experiments.e031_guided_forecasts import load_sources, validate_config
from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import parse_time, utcnow

CONFIG = Path("config/e031_result_audit.json")
OWN_FILES = [Path(__file__), CONFIG, Path("tests/test_e031_result_audit.py")]
SHARED_FILES = [
    Path("research/experiments/e022_audit.py"),
    Path("research/experiments/e026_result_audit.py"),
    Path("research/experiments/e026_nbh_comparison.py"),
    Path("research/experiments/e031_guided_forecasts.py"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
]
UNIVARIATE = "chronos_pretrained_univariate_matched"
GUIDED = "chronos_observed_then_guided"
VARIANTS = [UNIVARIATE, GUIDED]
REFERENCES = ["nbh_original", "nbh_chronos_w025", "nbh_chronos_w050", "nbh_chronos_w075"]
LEVELS = [0.01, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]
ROLES = ["first", "repeat", "appended", "permuted"]
KINDS = [
    "e031_model_started",
    "e031_model_batch_gzip",
    "e031_model_integrity_gzip",
    "e031_model_predictions_gzip",
    "e031_model_resources",
    "e031_model_log",
    "e031_model_report_gzip",
    "e031_model_failed",
]
SETTINGS = {
    "block_days": 7,
    "resamples": 10000,
    "seed": 6203101,
    "confidence": 0.95,
    "comparison_count": 5,
    "metric": "pinball_loss_f",
}


def validate_audit(config):
    expected = {
        "experiment": "E031-independent-result-or-failure-audit-v1",
        "model_registration_id": 166868,
        "wall_seconds": 600,
        "cpu_threads": 2,
        "attempts": 1,
        "model_inferences": 0,
        "model_fits": 0,
        "network_requests": 0,
        "expected_cases": 9870,
        "expected_calibration_cases": 3271,
        "expected_development_cases": 6599,
        "stop_file": "data/STOP_E031_AUDIT",
        "result_bodies_read_before_registration": False,
    }
    if any(config.get(k) != v for k, v in expected.items()):
        raise ValueError("Audit's fixed target, census or budget changed")


def census(archive, run_id):
    """Only record metadata: never read any new prediction, resource, log or score body."""
    rows = []
    for kind in KINDS:
        found = archive.db.execute(
            "SELECT * FROM records WHERE kind=? AND (key=? OR key LIKE ?) ORDER BY id",
            (kind, str(run_id), f"{run_id}:%"),
        ).fetchall()
        rows.extend(dict(row) for row in found)
    rows.sort(key=lambda row: row["id"])
    counts = Counter(row["kind"] for row in rows)
    if (
        counts["e031_model_started"] != 1
        or any(
            counts[kind] > 1
            for kind in (
                "e031_model_predictions_gzip",
                "e031_model_resources",
                "e031_model_report_gzip",
                "e031_model_failed",
            )
        )
        or not counts["e031_model_report_gzip"] + counts["e031_model_failed"]
    ):
        raise ValueError("Require one terminal attempt, never an incomplete or repeated model run")
    if len({(row["kind"], row["key"]) for row in rows}) != len(rows):
        raise ValueError("Repeated model artifact key")
    for row in rows:
        fields = [
            row[k] for k in ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")
        ]
        previous = archive.db.execute(
            "SELECT record_sha256 FROM records WHERE id<? ORDER BY id DESC LIMIT 1", (row["id"],)
        ).fetchone()
        if sha(canonical(fields).encode()) != row["record_sha256"] or row["previous_sha256"] != (
            previous[0] if previous else "0" * 64
        ):
            raise ValueError("Attempt record metadata chain changed")
    return [pin(row) for row in rows]


def register(archive):
    config = json.loads(CONFIG.read_bytes())
    validate_audit(config)
    identifier = config["model_registration_id"]
    if archive.latest("e031_result_audit_protocol", str(identifier)):
        raise ValueError("Audit already registered")
    run_row, run = read_record(archive, identifier, "e031_model_protocol")
    validate_config(run["config"])
    verify_files(run)
    if (
        sys.executable != run["python_executable"]
        or np.__version__ != run["numpy_version"]
        or {name: importlib.metadata.version(name) for name in run["runtime_versions"]}
        != run["runtime_versions"]
    ):
        raise ValueError("Use the exact registered model Python/package runtime; no model loads are needed")
    records = census(archive, identifier)
    hashes = merge_pins(run["source_hashes"], {str(p): sha(p.read_bytes()) for p in OWN_FILES + SHARED_FILES})
    source_ids = {
        str(p): archive.append("e031_audit_source", str(p), utcnow(), {}, p.read_bytes()) for p in OWN_FILES
    }
    protocol = {
        "config": config,
        "run": pin(run_row),
        "records": records,
        "source_hashes": hashes,
        "source_record_ids": source_ids,
        "numpy_version": np.__version__,
        "python_executable": sys.executable,
        "cwd": str(Path.cwd().resolve()),
        "model_runtime_versions": run["runtime_versions"],
        "registered_before_result_body": True,
        "automatic_retry": False,
    }
    return archive.append(
        "e031_result_audit_protocol", str(identifier), utcnow(), {}, canonical(protocol).encode()
    )


def bootstrap_replay(differences, days, settings):
    if settings != SETTINGS:
        raise ValueError("Five pinball bootstrap controls changed")
    calendar = [(date(2026, 7, 20) + timedelta(days=i)).isoformat() for i in range(28)]
    matrix = np.asarray(differences, dtype=np.float64)
    if days != calendar or matrix.shape != (28, 5) or not np.isfinite(matrix).all():
        raise ValueError("Complete 28-day, five-contrast bootstrap matrix required")
    starts = np.random.default_rng(6203101).integers(0, 28, size=(10000, 4))
    indices = np.empty((10000, 28), dtype=np.int64)
    for block in range(4):
        for offset in range(7):
            indices[:, 7 * block + offset] = (starts[:, block] + offset) % 28
    samples = np.stack([matrix[index].mean(axis=0) for index in indices])
    means, errors = matrix.mean(axis=0), samples.std(axis=0, ddof=1)
    active = [bool(np.any(matrix[:, j] != matrix[0, j])) and errors[j] > 1e-14 for j in range(5)]
    maxima = np.zeros(10000)
    for j in range(5):
        if active[j]:
            maxima = np.maximum(maxima, np.abs(samples[:, j] - means[j]) / errors[j])
    critical = float(sorted(maxima)[math.ceil(0.95 * 9999)])
    return {
        "status": "descriptive_reused_development_intervals",
        "means": means.tolist(),
        "standard_errors": errors.tolist(),
        "active": [bool(v) for v in active],
        "critical_value": critical,
        "intervals": [
            [float(means[j] - critical * errors[j]), float(means[j] + critical * errors[j])]
            if active[j]
            else None
            for j in range(5)
        ],
        "indices_sha256": sha(canonical(indices.tolist()).encode()),
        "resampled_means_sha256": sha(canonical(samples.tolist()).encode()),
        "settings": settings,
        "independent_significance_proven": False,
    }


def input_metadata(case, observation_summary, trajectory, variant):
    """Independent mapping from E022's independently reconstructed historical context."""
    guided = variant == GUIDED and trajectory["available"]
    return {
        **{k: case[k] for k in ("case_id", "station_id", "decision_ms", "target_ms", "horizon_hours")},
        "context_end_ms": case["decision_ms"] - 3_600_000,
        "selected_step": case["horizon_hours"] + 1,
        "context_sha256": observation_summary["input_sha256"],
        "trajectory_sha256": sha(canonical(trajectory).encode()),
        "uses_guidance": guided,
        "fallback_reason": trajectory["reason"] if variant == GUIDED and not guided else None,
        "finite_context_points": observation_summary["finite_context_points"],
    }


def output_table(rows, expected_ids, cases):
    if [row["case_id"] for row in rows] != expected_ids:
        raise ValueError("Retained output case order or census changed")
    table = {}
    for row in rows:
        array = np.asarray(row["all_quantiles_f"], dtype=float)
        if array.shape != (13, 7) or not np.isfinite(array).all():
            raise ValueError("Invalid 13-by-7 raw output array")
        selected = array[:, cases[row["case_id"]]["horizon_hours"]].tolist()
        if row["quantiles_f"] != selected or row["point_f"] != selected[6]:
            raise ValueError("Selected horizon step or median differs from raw output")
        table[row["case_id"]] = row
    return table


def difference(left, right, ids, tolerance):
    maximum = 0.0
    for key in ids:
        a, b = (np.asarray(table[key]["all_quantiles_f"], dtype=float) for table in (left, right))
        if a.shape != (13, 7) or b.shape != (13, 7) or not np.isfinite(a).all() or not np.isfinite(b).all():
            raise ValueError("Malformed integrity array")
        for actual, expected in zip(a.flat, b.flat, strict=True):
            maximum = max(maximum, abs(float(actual) - float(expected)))
    return {"maximum_difference_f": maximum, "tolerance_f": tolerance, "passed": maximum <= tolerance}


def audit_calls(entries, cases, trajectories, expected_meta, require_complete=False):
    """Rebuild full batch order and every retained integrity comparison, including failures."""
    ids = [case["case_id"] for case in cases]
    by_case = {case["case_id"]: case for case in cases}
    if len(by_case) != len(cases):
        raise ValueError("Duplicate original case ID")
    selected = {UNIVARIATE: ids, GUIDED: [key for key in ids if trajectories[key]["available"]]}
    expected_batches = [
        (variant, offset, selected[variant][offset : offset + size])
        for variant, size in ((UNIVARIATE, 64), (GUIDED, 32))
        for offset in range(0, len(selected[variant]), size)
    ]
    main = {variant: {} for variant in VARIANTS}
    batches = [(row, body) for row, body in entries if row["kind"] == "e031_model_batch_gzip"]
    if len(batches) > len(expected_batches):
        raise ValueError("Extra unregistered main batch")
    parameter = None
    for (row, body), (variant, offset, wanted) in zip(batches, expected_batches, strict=False):
        if (
            body["variant"] != variant
            or body["offset"] != offset
            or not row["key"].endswith(f":{variant}:{offset}")
        ):
            raise ValueError("Main batch sequence differs from registered order")
        if body["labels_supplied"] is not False:
            raise ValueError("Inference input label-access flag changed")
        if parameter is None:
            parameter = body["parameter_sha256"]
        if body["parameter_sha256"] != parameter or len(parameter) != 64:
            raise ValueError("Model parameter hash changed between calls")
        exact_tree(body["inputs"], [expected_meta[variant][key] for key in wanted], "main input metadata")
        main[variant].update(output_table(body["predictions"], wanted, by_case))
    integrity_entries = [(r, b) for r, b in entries if r["kind"] == "e031_model_integrity_gzip"]
    if integrity_entries and len(batches) != len(expected_batches):
        raise ValueError("Integrity calls preceded completion of the original main batch census")
    if batches and integrity_entries and batches[-1][0]["id"] >= integrity_entries[0][0]["id"]:
        raise ValueError("Integrity archival precedes main output completion")
    ordered_roles = [(variant, role) for variant in VARIANTS for role in ROLES]
    if len(integrity_entries) > 8:
        raise ValueError("Extra integrity calls exceed the fixed task plan")
    checks, variants, tasks = [], {}, 0
    for variant in VARIANTS:
        ordered = sorted(selected[variant], key=lambda key: (by_case[key]["decision_ms"], key))
        first = ordered[:4]
        if len(first) != 4:
            raise ValueError("Original integrity selection has insufficient cases")
        later = [
            key
            for key in ordered
            if by_case[key]["decision_ms"] > max(by_case[k]["decision_ms"] for k in first)
        ][-4:]
        if len(later) != 4:
            raise ValueError("Original integrity selection lacks later origins")
        variants[variant] = {"first_ids": first, "later_ids": later, "outputs": {}, "record_ids": {}}
    for (record, body), (variant, role) in zip(integrity_entries, ordered_roles, strict=False):
        evidence = variants[variant]
        wanted = (
            evidence["first_ids"]
            if role in ("first", "repeat")
            else evidence["first_ids"] + evidence["later_ids"]
        )
        if role == "permuted":
            wanted = list(reversed(wanted))
        if (
            body["variant"] != variant
            or body["role"] != role
            or record["key"] != f"{body['registration_id']}:{variant}:{role}"
            or body["parameter_sha256"] != parameter
            or body["tasks"] != len(wanted)
            or body["comparison_started"] is not False
            or body["labels_supplied"] is not False
        ):
            raise ValueError("Integrity role/task/provenance metadata changed")
        exact_tree(
            body["inputs"], [expected_meta[variant][key] for key in wanted], "integrity input metadata"
        )
        evidence["outputs"][role] = output_table(body["predictions"], wanted, by_case)
        evidence["record_ids"][role] = record["id"]
        tasks += len(wanted)
    for variant, evidence in variants.items():
        saved, first = evidence["outputs"], evidence["first_ids"]
        pairs = [
            ("exact_repeat", "first", "repeat", first, 0),
            ("main_batch", "main", "first", first, 1e-5),
            ("append_later_origin", "first", "appended", first, 1e-5),
            ("permutation", "appended", "permuted", first + evidence["later_ids"], 1e-5),
        ]
        for name, left, right, wanted, tolerance in pairs:
            if right not in saved or (left != "main" and left not in saved):
                continue
            value = difference(
                main[variant] if left == "main" else saved[left], saved[right], wanted, tolerance
            )
            checks.append({"variant": variant, "check": name, **value})
    # Frozen execution compares first/repeat/main before attempting appended;
    # append/permutation are both retained before either comparison is evaluated.
    for index, variant in enumerate(VARIANTS):
        existing = variants[variant]["outputs"]
        if "repeat" not in existing:
            continue
        for checked in (row for row in checks if row["variant"] == variant and not row["passed"]):
            maximum_calls = index * 4 + (2 if checked["check"] in ("exact_repeat", "main_batch") else 4)
            if len(integrity_entries) > maximum_calls:
                raise ValueError("Model continued after a failing registered integrity comparison")
    complete = len(batches) == len(expected_batches) and len(integrity_entries) == 8 and tasks == 48
    if require_complete and (not complete or not all(row["passed"] for row in checks)):
        raise ValueError("Successful result lacks complete passing integrity evidence")
    return {
        "main": main,
        "variants": variants,
        "parameter_sha256": parameter,
        "main_batches": len(batches),
        "expected_main_batches": len(expected_batches),
        "main_tasks": sum(len(value) for value in main.values()),
        "integrity_calls": len(integrity_entries),
        "integrity_tasks": tasks,
        "checks": checks,
        "complete": complete,
    }


def verify_scores(result, predictions, sources, observations):
    cases, parent = sources["cases"], sources["parent_config"]
    if [row["id"] for row in result["candidates"]] != [*VARIANTS, *REFERENCES]:
        raise ValueError("Six-candidate score family changed")
    observed = {case["case_id"]: check_case(case, parent, observations) for case in cases}
    reconstructed, maximum = {}, 0.0
    for candidate in result["candidates"]:
        model = candidate["id"]
        evaluation, _ = reconstruct_scores(predictions[model], cases, observed, LEVELS, parent)
        maximum = max(maximum, compare_scores(candidate["evaluation"], evaluation))
        expected_evaluation = {
            **evaluation,
            "metric_weighting": "Equal UTC target-day weight, equal case weight within each day; RMSE is the square root of day-weighted squared error.",
            "point_metric_basis": "Raw baseline point forecasts or pretrained/fitted neural medians before quantile calibration.",
        }
        maximum = max(maximum, exact_tree(candidate["evaluation"], expected_evaluation, "complete scores"))
        lookup = {row["case_id"]: row for row in predictions[model]}
        bias = {}
        for case in cases:
            if case["split"] == "development":
                day = datetime.fromtimestamp(case["target_ms"] / 1000, UTC).date().isoformat()
                bias.setdefault(day, []).append(
                    lookup[case["case_id"]]["point_f"] - observed[case["case_id"]]
                )
        daily_bias = [
            {"day": day, "bias_f": math.fsum(values) / len(values), "cases": len(values)}
            for day, values in sorted(bias.items())
        ]
        maximum = max(maximum, exact_tree(candidate["daily_bias"], daily_bias, "daily bias"))
        maximum = max(
            maximum,
            exact_tree(
                candidate["mean_day_weighted_bias_f"],
                math.fsum(r["bias_f"] for r in daily_bias) / len(daily_bias),
            ),
        )
        reconstructed[model] = evaluation
        if model in REFERENCES:
            original = next(
                row for row in sources["reference_report"]["result"]["candidates"] if row["id"] == model
            )
            maximum = max(maximum, compare_scores(original["evaluation"], evaluation))
    guided = reconstructed[GUIDED]
    days = [row["day"] for row in guided["daily"]]
    contrasts = []
    for model in [UNIVARIATE, *REFERENCES]:
        reference = reconstructed[model]
        if [(r["day"], r["cases"]) for r in reference["daily"]] != [
            (r["day"], r["cases"]) for r in guided["daily"]
        ]:
            raise ValueError("Paired model calendars differ")
        baseline = reference["metrics"]["pinball_loss_f"]
        contrasts.append(
            {
                "candidate": GUIDED,
                "reference": model,
                "pinball_difference_f": guided["metrics"]["pinball_loss_f"] - baseline,
                "mae_difference_f": guided["metrics"]["mae_f"] - reference["metrics"]["mae_f"],
                "relative_pinball_improvement": (baseline - guided["metrics"]["pinball_loss_f"]) / baseline
                if baseline > 0
                else None,
            }
        )
    matrix = [
        [
            guided["daily"][i]["pinball_loss_f"] - reconstructed[model]["daily"][i]["pinball_loss_f"]
            for model in [UNIVARIATE, *REFERENCES]
        ]
        for i in range(28)
    ]
    maximum = max(maximum, exact_tree(result["comparisons"], contrasts, "five contrasts"))
    maximum = max(maximum, exact_tree(result["daily_paired_pinball"], matrix, "daily pinball"))
    exact_tree(result["daily_paired_days"], days)
    # Raw retained differences first agree with independently recomputed values above.
    # Replay their exact float bytes so bootstrap content hashes remain comparable.
    replay = bootstrap_replay(result["daily_paired_pinball"], days, SETTINGS)
    maximum = max(maximum, exact_tree(result["bootstrap"], replay, "bootstrap"))
    coverage = sum(row["available"] for row in sources["trajectories"].values())
    advance = coverage / len(cases) >= 0.9 and all(
        row["relative_pinball_improvement"] is not None and row["relative_pinball_improvement"] >= 0.05
        for row in contrasts
    )
    for key, expected in {
        "guidance_count": coverage,
        "guidance_denominator": len(cases),
        "guidance_fraction": coverage / len(cases),
        "advance_to_prospective_research_only": advance,
        "promotion_eligible": False,
        "profitability_proven": False,
    }.items():
        maximum = max(maximum, exact_tree(result[key], expected, key))
    if not 0 <= result["independent_score_max_error"] <= 1e-10:
        raise ValueError("Original scorer arithmetic self-check failed")
    return {
        "models_reconstructed": 6,
        "comparisons_reconstructed": 5,
        "frozen_references_reproduced": 4,
        "maximum_arithmetic_error": maximum,
        "advance_gate_reproduced": advance,
        "bootstrap_indices_sha256": replay["indices_sha256"],
        "bootstrap_resampled_means_sha256": replay["resampled_means_sha256"],
    }


def ordered_records(records, run_row):
    previous = run_row
    for row in records:
        if row["id"] <= previous["id"] or parse_time(row["available_at"]) < parse_time(
            previous["available_at"]
        ):
            raise ValueError("Model artifact chronology changed")
        previous = row
    if records[0]["kind"] != "e031_model_started":
        raise ValueError("First model artifact is not the unique attempt start")
    terminal = [row for row in records if row["kind"] in ("e031_model_report_gzip", "e031_model_failed")]
    if not terminal or records[-1]["id"] != terminal[-1]["id"]:
        raise ValueError("Artifacts were added after the terminal attempt")


def audit_payload(archive, protocol, run, sources, entries, observations):
    cases, trajectories = sources["cases"], sources["trajectories"]
    by_kind = {}
    for row, body in entries:
        by_kind.setdefault(row["kind"], []).append((row, body))
    config = protocol["config"]
    if len(cases) != config["expected_cases"] or Counter(c["split"] for c in cases) != {
        "calibration": config["expected_calibration_cases"],
        "development": config["expected_development_cases"],
    }:
        raise ValueError("Original 9,870-case calibration/development census changed")
    summaries = {
        case["case_id"]: prepared_summary(case, observations, sources["parent_config"]) for case in cases
    }
    expected_meta = {
        variant: {
            case["case_id"]: input_metadata(
                case, summaries[case["case_id"]], trajectories[case["case_id"]], variant
            )
            for case in cases
        }
        for variant in VARIANTS
    }
    reports = by_kind.get("e031_model_report_gzip", [])
    failures = by_kind.get("e031_model_failed", [])
    call_audit = audit_calls(entries, cases, trajectories, expected_meta, require_complete=bool(reports))
    for row, body in entries:
        if row["kind"] == "e031_model_integrity_gzip" and body["registration_id"] != protocol["run"]["id"]:
            raise ValueError("Integrity call registration differs")
    started_row, started = by_kind["e031_model_started"][0]
    expected_directory = f"reports/E031-model-{protocol['run']['id']}"
    if started != {"directory": expected_directory, "budgets": run["config"]["budgets"]}:
        raise ValueError("Started attempt changed its directory or resource budget")
    resource = by_kind.get("e031_model_resources", [])
    if resource:
        resource_row, resources = resource[0]
        expected_command = [
            run["python_executable"],
            "-m",
            "research.experiments.e031_guided_forecasts",
            "--worker-record-id",
            str(protocol["run"]["id"]),
        ]
        if (
            resources["command"] != expected_command
            or resources["cwd"] != run["cwd"]
            or resources["wall_seconds_limit"] != 1200
            or resources["rss_bytes_limit"] != 4294967296
            or resources["automatic_retry"] is not False
        ):
            raise ValueError("Supervised command/budget differs")
        logs = {row["key"].rsplit(":", 1)[-1]: (row, body) for row, body in by_kind.get("e031_model_log", [])}
        if set(logs) != set(resources["logs"]):
            raise ValueError("Recorded process logs missing from immutable archive")
        for name, expected in resources["logs"].items():
            row, body = logs[name]
            if expected != {"bytes": len(body), "sha256": sha(body)} or row["id"] <= resource_row["id"]:
                raise ValueError("Process log content/chronology differs from resources")
    elif reports:
        raise ValueError("Successful report has no resource evidence")
    elif not failures:
        raise ValueError("Attempt has neither publication nor retained failure")
    for _, failed in failures:
        if failed["automatic_retry"] is not False or failed["resource_record_id"] != (
            resource[0][0]["id"] if resource else None
        ):
            raise ValueError("Retained failure lost its exact resource link")
    predictions_entries = by_kind.get("e031_model_predictions_gzip", [])
    score_audit = None
    if predictions_entries:
        prediction_row, payload = predictions_entries[0]
        if payload["registration_id"] != protocol["run"]["id"] or payload["cases"] != cases:
            raise ValueError("Complete prediction artifact changed case/protocol bindings")
        if (
            not call_audit["complete"]
            or any(not row["passed"] for row in call_audit["checks"])
            or any(
                row["id"] >= prediction_row["id"]
                for row, _ in entries
                if row["kind"] in ("e031_model_batch_gzip", "e031_model_integrity_gzip")
            )
        ):
            raise ValueError("Full prediction artifact preceded complete passing call evidence")
        ids = [case["case_id"] for case in cases]
        for variant in VARIANTS:
            expected = call_audit["main"][variant].copy()
            for key in ids:
                if key not in expected:
                    if variant != GUIDED or trajectories[key]["available"]:
                        raise ValueError("Missing inference has no declared fallback")
                    expected[key] = call_audit["main"][UNIVARIATE][key]
            if payload["predictions"][variant] != [expected[key] for key in ids]:
                raise ValueError("Full predictions differ from retained batches or exact fallback")
            exact_tree(
                payload["input_metadata"][variant],
                [expected_meta[variant][key] for key in ids],
                "full input metadata",
            )
            saved = payload["integrity"][variant]
            evidence = call_audit["variants"][variant]
            if (
                saved["artifact_record_ids"] != evidence["record_ids"]
                or saved["first_case_ids"] != evidence["first_ids"]
                or saved["later_case_ids"] != evidence["later_ids"]
            ):
                raise ValueError("Integrity evidence links/identities changed")
            for role, table in evidence["outputs"].items():
                if saved["predictions"][role] != list(table.values()):
                    raise ValueError("Complete integrity arrays differ from their immediate receipt")
            for check in (row for row in call_audit["checks"] if row["variant"] == variant):
                field = check["check"] + "_max_difference_f"
                exact_tree(saved[field], check["maximum_difference_f"], "integrity difference")
        fixed = {
            "main_tasks": call_audit["main_tasks"],
            "integrity_tasks": 48,
            "parameters_unchanged": True,
            "parameter_sha256": call_audit["parameter_sha256"],
            "training_mode": False,
            "dropout_active": False,
            "cross_learning": False,
            "cpu_threads": 2,
            "torch_interop_threads": 1,
            "labels_supplied_to_models": False,
            "score_label_lookup_started": False,
        }
        for key, expected in fixed.items():
            exact_tree(payload[key], expected, key)
        expected_weights = {
            name: {
                "sha256": expected,
                "bytes": (Path(run["config"]["checkpoint_directory"]) / name).stat().st_size,
            }
            for name, expected in run["config"]["checkpoint_files"].items()
        }
        exact_tree(payload["checkpoint_files"], expected_weights, "checkpoint evidence")
    if reports:
        report_row, report = reports[0]
        if (
            not predictions_entries
            or not started_row["id"] < prediction_row["id"] < resource_row["id"] < report_row["id"]
        ):
            raise ValueError("Predictions/resources were not archived before published scoring report")
        if (
            report["registration_id"] != protocol["run"]["id"]
            or report["prediction_record_id"] != prediction_row["id"]
            or report["prediction_body_sha256"] != prediction_row["body_sha256"]
            or report["prediction_record_sha256"] != prediction_row["record_sha256"]
            or report["resource_record_id"] != resource_row["id"]
            or report["source_records"] != run["source_records"]
        ):
            raise ValueError("Score publication/source/hash links changed")
        if (
            resources["status"] != "completed"
            or resources["returncode"] != 0
            or resources["reason"] is not None
            or resources["elapsed_seconds"] > 1200
            or resources["maximum_sampled_rss_bytes"] > 4294967296
            or report["worker_peak_rss_bytes"] > 4294967296
        ):
            raise ValueError("Successful report lacks completed in-budget process evidence")
        for key, expected in {
            "main_tasks": call_audit["main_tasks"],
            "integrity_tasks": 48,
            "all_four_frozen_reference_scores_reproduced": True,
            "model_fits": 0,
            "network_requests": 0,
            "weight_downloads": 0,
            "historical_public_availability_verified": False,
            "untouched_validation": False,
            "promotion_eligible": False,
        }.items():
            exact_tree(report[key], expected, key)
        exact_tree(
            report["guidance_counts_by_split"],
            dict(Counter(case["split"] for case in cases if trajectories[case["case_id"]]["available"])),
        )
        score_audit = verify_scores(
            report["result"], {**payload["predictions"], **sources["references"]}, sources, observations
        )
    failed_checks = [row for row in call_audit["checks"] if not row["passed"]]
    stderr = b"\n".join(
        body for row, body in by_kind.get("e031_model_log", []) if row["key"].endswith(":stderr.log")
    )
    return {
        "status": "published_scores_reproduced" if reports else "retained_failed_attempt_audited",
        "execution_failed": bool(failures),
        "published_score_audit": score_audit,
        **{
            key: call_audit[key]
            for key in (
                "main_batches",
                "expected_main_batches",
                "main_tasks",
                "integrity_calls",
                "integrity_tasks",
                "complete",
            )
        },
        "integrity_comparisons": call_audit["checks"],
        "failed_integrity_comparisons": failed_checks,
        "failure_message_mentions_invariance": b"Origin/batch output invariance failed" in stderr,
        "failure_records": [{"id": row["id"], **body} for row, body in failures],
        "resource_evidence": resource[0][1] if resource else None,
        "complete_parameter_hash_consistency": call_audit["parameter_sha256"],
        "case_counts": dict(Counter(case["split"] for case in cases)),
        "no_model_replay": True,
        "no_claim_unfinished_calls_passed": True,
    }


def execute(archive, identifier):
    _, protocol = read_record(archive, identifier, "e031_result_audit_protocol")
    config = protocol["config"]
    validate_audit(config)
    verify_files(protocol)
    if (
        protocol["numpy_version"] != np.__version__
        or protocol["cwd"] != str(Path.cwd().resolve())
        or protocol["python_executable"] != sys.executable
    ):
        raise ValueError("Audit runtime changed")
    if archive.latest("e031_result_audit_started", str(identifier)):
        raise ValueError("Audit already attempted; no hidden retry")
    if Path(config["stop_file"]).exists():
        raise InterruptedError("Audit stop file present")
    for path, source_id in protocol["source_record_ids"].items():
        row, body = read_record(archive, source_id, "e031_audit_source", False)
        if source_id >= identifier or row["key"] != path or sha(body) != protocol["source_hashes"][path]:
            raise ValueError("Archived audit source differs")
    archive.append("e031_result_audit_started", str(identifier), utcnow(), {}, b"{}")

    def expired(_number, _frame):
        raise TimeoutError("Audit600-second wall deadline")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, config["wall_seconds"])
    try:
        run_row, run = read_record(archive, protocol["run"]["id"], "e031_model_protocol")
        assert_pin(run_row, protocol["run"])
        verify_files(run)
        if run["runtime_versions"] != protocol["model_runtime_versions"]:
            raise ValueError("Original model runtime binding changed")
        if {name: importlib.metadata.version(name) for name in run["runtime_versions"]} != run[
            "runtime_versions"
        ]:
            raise ValueError("Installed registered package versions changed")
        actual_census = census(archive, run_row["id"])
        if actual_census != protocol["records"] or any(row["id"] >= identifier for row in actual_census):
            raise ValueError("Attempt artifact census changed after audit registration")
        ordered_records(actual_census, run_row)
        sources = load_sources(archive, run["config"])
        if sources["source_records"] != run["source_records"]:
            raise ValueError("Shared source checker returned different reference bindings")
        entries = []
        for expected in actual_census:
            if Path(config["stop_file"]).exists():
                raise InterruptedError("Audit stop file present")
            row, body = read_record(
                archive, expected["id"], expected["kind"], expected["kind"] != "e031_model_log"
            )
            assert_pin(row, expected)
            entries.append((row, body))
        with Path(run["config"]["observations_path"]).open() as handle:
            observations = observations_from_rows(
                (json.loads(line) for line in handle if line.strip()), sources["parent_config"]
            )
        result = audit_payload(archive, protocol, run, sources, entries, observations)
        verify_files(protocol)
        report = {
            "audit_registration_id": identifier,
            "model_registration_id": run_row["id"],
            "result": result,
            "records_checked": actual_census,
            "shared_helpers": config["shared_helpers"],
            "model_inferences": 0,
            "model_fits": 0,
            "network_requests": 0,
            "historical_public_availability_verified": False,
            "promotion_eligible": False,
        }
        rid = archive.append(
            "e031_result_audit_report_gzip",
            str(identifier),
            utcnow(),
            {},
            gzip.compress(canonical(report).encode(), mtime=0),
        )
        report["report_record_id"] = rid
        destination = Path("reports") / f"E031_audit_{identifier}.json"
        with destination.open("x") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
        return {"report_record_id": rid, "report_path": str(destination), "status": result["status"]}
    except BaseException as error:
        archive.append(
            "e031_result_audit_failed",
            str(identifier),
            utcnow(),
            {},
            canonical({"error": f"{type(error).__name__}: {error}", "automatic_retry": False}).encode(),
        )
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--register", action="store_true")
    group.add_argument("--run-record-id", type=int)
    args = parser.parse_args()
    archive = Archive("data")
    try:
        with (archive.root / "e031_result_audit.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(
                canonical(
                    {"registration_id": register(archive)}
                    if args.register
                    else execute(archive, args.run_record_id)
                )
            )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
