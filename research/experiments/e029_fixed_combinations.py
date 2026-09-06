"""One registered arithmetic evaluation of three already-designed forecast blends.

No model or network execution. E026's frozen physical-source checks precede
an immutable prediction artifact; only then may the original labels be read.
"""

import argparse
import fcntl
import gzip
import json
import math
import signal
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from research.experiments.e022_audit import (
    check_case,
    compare_scores,
    observations_from_rows,
    reconstruct_scores,
)
from research.experiments.e022_run import score_predictions, validate_cases
from research.experiments.e026_nbh_comparison import (
    digest,
    merge_pins,
    original_sources,
    read_record,
    validate_acquisition,
    validate_bindings,
    verify_files,
)
from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import utcnow

CONFIG = Path("config/e029_fixed_combinations.json")
FILES = [Path(__file__), CONFIG, Path("tests/test_e029_fixed_combinations.py")]
MODELS = [
    "persistence",
    "seasonal_24h",
    "blend_50_50",
    "ridge_1",
    "ridge_10",
    "ridge_100",
    "chronos_pretrained",
    "chronos_fixed_fit",
]
HYBRIDS = [
    {"id": "nbh_chronos_w025", "neural_weight": 0.25},
    {"id": "nbh_chronos_w050", "neural_weight": 0.5},
    {"id": "nbh_chronos_w075", "neural_weight": 0.75},
]
REFERENCES = ["nbh_original", "chronos_pretrained", "chronos_fixed_fit", "ridge_100"]
LEVELS = [0.01, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]
BOOTSTRAP = {"block_days": 7, "resamples": 10000, "seed": 6202901, "confidence": 0.95, "comparison_count": 12}


def fixed_configuration(config):
    expected = {
        "experiment": "E029-fixed-physical-transformer-combinations-v1",
        "design_registration_id": 124922,
        "design_record_sha256": "0c05f6e16b1f17a493c0848ee256be9664aae87867f80d03ed44d4294fd0142a",
        "design_path": "config/e029_fixed_combinations_design.json",
        "physical_comparison_registration_id": 117527,
        "physical_comparison_record_sha256": "b4f8fbd5363df5ec122a84f3cb0ec70138b3bffa0f9e3f6a0b43a78f08ca09b5",
        "parent_registration_id": 105314,
        "parent_report_record_id": 105811,
        "acquisition_registration_id": 113137,
        "expected_cases": 9870,
        "expected_objects": 498,
        "model_ids": MODELS,
        "physical_model_id": "nbh_original",
        "neural_model_id": "chronos_pretrained",
        "hybrids": HYBRIDS,
        "reference_ids": REFERENCES,
        "quantile_levels": LEVELS,
        "bootstrap": BOOTSTRAP,
        "execution_budget_seconds": 600,
        "maximum_cpu_threads": 2,
        "attempts": 1,
        "stop_file": "data/STOP_E029",
        "lock_file": "e029.lock",
        "model_fits": 0,
        "model_inferences": 0,
        "network_requests": 0,
        "trading_actions": 0,
    }
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError("Configuration differs from the fixed E029 design and budgets")


def attempt_census(archive, acquisition, settings):
    """Read identities only; every registered object must have one terminal attempt."""
    entries = acquisition["manifest"]["objects"]
    expected = [f"{settings['acquisition_registration_id']}:{entry['key']}" for entry in entries]
    rows = archive.db.execute(
        "SELECT id,kind,key,available_at,body_sha256,record_sha256 FROM records "
        "WHERE kind=? AND key LIKE ? ORDER BY id",
        (settings["acquisition_object_kind"], f"{settings['acquisition_registration_id']}:%"),
    ).fetchall()
    by_key = {row["key"]: dict(row) for row in rows}
    if (
        len(entries) != settings["expected_objects"]
        or len(set(expected)) != len(expected)
        or len(rows) != len(expected)
        or set(by_key) != set(expected)
        or any(row["id"] <= settings["acquisition_registration_id"] for row in rows)
    ):
        raise ValueError("Acquisition incomplete, duplicated or outside the exact attempt census")
    return [by_key[key] for key in expected]


def parents(archive, config):
    fixed_configuration(config)
    design_record, design = read_record(archive, config["design_registration_id"], "e029_design_protocol")
    comparison_record, comparison = read_record(
        archive, config["physical_comparison_registration_id"], "e026_protocol"
    )
    if (
        design_record["record_sha256"] != config["design_record_sha256"]
        or design != json.loads(Path(config["design_path"]).read_bytes())
        or comparison_record["record_sha256"] != config["physical_comparison_record_sha256"]
        or design["candidate_neural_weights"] != [h["neural_weight"] for h in HYBRIDS]
        or design["neural_model"] != config["neural_model_id"]
    ):
        raise ValueError("Immutable design or physical comparison registration changed")
    settings = comparison["config"]
    for key in (
        "parent_registration_id",
        "parent_report_record_id",
        "acquisition_registration_id",
        "expected_cases",
        "expected_objects",
        "model_ids",
        "quantile_levels",
    ):
        if settings[key] != config[key]:
            raise ValueError("Comparison differs from the original E022/E026 family")
    acquisition_record, acquisition = read_record(
        archive, settings["acquisition_registration_id"], settings["acquisition_protocol_kind"]
    )
    if acquisition_record["record_sha256"] != comparison["acquisition_record_sha256"]:
        raise ValueError("Pinned physical acquisition changed")
    cases = json.loads(Path(settings["manifest_path"]).read_bytes())["cases"]
    validate_acquisition(acquisition, settings, cases)
    census = attempt_census(archive, acquisition, settings)
    parent_record, _ = read_record(
        archive, config["parent_registration_id"], "experiment_protocol", decode=False
    )
    if parent_record["record_sha256"] != comparison["parent_record_sha256"]:
        raise ValueError("Original E022 registration changed")
    return comparison, acquisition, cases, census


def register(archive):
    config = json.loads(CONFIG.read_bytes())
    if archive.latest("e029_protocol", config["experiment"]):
        raise ValueError("E029 already registered; no second registration or attempt")
    comparison, _, _, census = parents(archive, config)
    verify_files(comparison)
    report_record, report = read_record(archive, config["parent_report_record_id"], "experiment_report_gzip")
    if report_record["body_sha256"] != comparison["report_body_sha256"]:
        raise ValueError("Original E022 report changed")
    if [item["candidate"]["id"] for item in report["candidates"]] != MODELS:
        raise ValueError("Original report lost a fixed candidate")
    pins = dict(comparison["source_hashes"])
    for path in FILES + [Path(config["design_path"])]:
        pins = merge_pins(pins, {str(path): digest(path.read_bytes())})
    # Record exact saved-prediction paths from the original report, rather than guessing filenames.
    prediction_paths = {}
    for model, item in zip(MODELS, report["prediction_artifacts"], strict=True):
        path = Path(item["path"])
        if item["candidate"] != model or path.parent.name != model or pins.get(str(path)) != item["sha256"]:
            raise ValueError("Original prediction path/hash/candidate binding changed")
        prediction_paths[model] = str(path)
    source_ids = {
        str(path): archive.append("e029_source", str(path), utcnow(), {}, path.read_bytes())
        for path in FILES + [Path(config["design_path"])]
    }
    protocol = {
        "config": config,
        "source_hashes": pins,
        "source_record_ids": source_ids,
        "prediction_paths": prediction_paths,
        "object_sources": census,
        "inherited_source_registration_id": config["physical_comparison_registration_id"],
        "design_record_sha256": config["design_record_sha256"],
        "physical_comparison_record_sha256": config["physical_comparison_record_sha256"],
        "numpy_version": np.__version__,
        "cwd": str(Path.cwd().resolve()),
        "known_E022_results_acknowledged": True,
        "E026_scores_known_before_implementation_registration": True,
        "parent_record_sha256": comparison["parent_record_sha256"],
        "parent_report_body_sha256": comparison["report_body_sha256"],
        "acquisition_record_sha256": comparison["acquisition_record_sha256"],
        "E026_reports_existing_at_implementation_registration": [
            dict(row)
            for row in archive.db.execute(
                "SELECT id,body_sha256,record_sha256 FROM records WHERE kind='e026_report_gzip' AND key=?",
                (str(config["physical_comparison_registration_id"]),),
            )
        ],
        "weights_predeclared_before_NBH_scoring_in_design_record": config["design_registration_id"],
        "registered_at": utcnow().isoformat(),
        "execution": {
            "wall_seconds": 600,
            "automatic_retry": False,
            "array_arithmetic_only": True,
            "no_neural_or_BLAS_operations": True,
            "parallel_workers": 1,
            "maximum_cpu_threads": 2,
            "deadline": "POSIX real-time alarm, checked stop file between units; no OS sandbox",
        },
    }
    verify_files(protocol)
    return archive.append("e029_protocol", config["experiment"], utcnow(), {}, canonical(protocol).encode())


def numeric_prediction(row, levels):
    values = [row["point_f"], *row["quantiles_f"]]
    if len(row["quantiles_f"]) != len(levels) or any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in values
    ):
        raise ValueError("Saved point/quantiles must be finite and retain all thirteen raw levels")
    return {"case_id": row["case_id"], "point_f": row["point_f"], "quantiles_f": list(row["quantiles_f"])}


def prediction_panel(cases, physical, predictions, config, parent_config):
    """Identity/missingness and fixed arithmetic only: accepts no observation labels."""
    fixed_configuration(config)
    ids = validate_cases({"cases": cases}, parent_config)
    validate_bindings(cases, physical)
    mapped = {row["case_id"]: row for row in physical}
    for row in physical:
        if type(row["available"]) is not bool or (not row["available"] and row["point_f"] is not None):
            raise ValueError("Physical forecast missingness is inconsistent")
        if row["available"]:
            numeric_prediction({**row, "quantiles_f": [row["point_f"]] * len(LEVELS)}, LEVELS)
    if set(predictions) != set(MODELS):
        raise ValueError("All eight saved candidate arrays are required")
    complete = {}
    for model in MODELS:
        rows = predictions[model]
        if len(rows) != len(ids) or {row["case_id"] for row in rows} != ids:
            raise ValueError("Saved candidate does not match every unique original case")
        complete[model] = {row["case_id"]: numeric_prediction(row, LEVELS) for row in rows}
    panel = [case for case in cases if mapped[case["case_id"]]["available"]]
    panel_ids = [case["case_id"] for case in panel]
    arrays = {model: [complete[model][key] for key in panel_ids] for model in MODELS}
    arrays[config["physical_model_id"]] = [
        {
            "case_id": key,
            "point_f": mapped[key]["point_f"],
            "quantiles_f": [mapped[key]["point_f"]] * len(LEVELS),
        }
        for key in panel_ids
    ]
    for hybrid in HYBRIDS:
        weight = hybrid["neural_weight"]
        values = []
        for key in panel_ids:
            physical_point, neural = mapped[key]["point_f"], complete[config["neural_model_id"]][key]
            values.append(
                {
                    "case_id": key,
                    "point_f": (1 - weight) * physical_point + weight * neural["point_f"],
                    "quantiles_f": [
                        (1 - weight) * physical_point + weight * q for q in neural["quantiles_f"]
                    ],
                }
            )
        arrays[hybrid["id"]] = values
    return {
        "cases": panel,
        "predictions": arrays,
        "candidate_order": [*MODELS, config["physical_model_id"], *[h["id"] for h in HYBRIDS]],
        "original_case_count": len(cases),
        "common_case_count": len(panel),
        "full_panel_complete": len(panel) == len(cases),
        "classification": "full_original_panel"
        if len(panel) == len(cases)
        else "common_available_subset_only",
        "missing": [
            {"case_id": row["case_id"], "reason": row["reason"]} for row in physical if not row["available"]
        ],
        "case_ids_sha256": digest(canonical(sorted(panel_ids)).encode()),
        "calibration_applied": False,
        "labels_accessed": False,
        "formula": "(1-w)*NBH_TMP+w*Chronos_raw_point_or_quantile; w in .25,.50,.75",
    }


def shared_intervals(differences, days, settings):
    if settings != BOOTSTRAP:
        raise ValueError("Bootstrap differs from the fixed twelve-comparison design")
    expected = [(date(2026, 7, 20) + timedelta(days=i)).isoformat() for i in range(28)]
    if days != expected:
        return {
            "status": "unsupported_missing_calendar_days",
            "days": days,
            "independent_significance_proven": False,
        }
    x = np.asarray(differences, dtype=float)
    if x.shape != (28, 12) or not np.isfinite(x).all():
        raise ValueError("Wrong paired 28-day/twelve-comparison matrix")
    rng = np.random.default_rng(settings["seed"])
    starts = rng.integers(0, 28, size=(settings["resamples"], 4))
    indices = ((starts[:, :, None] + np.arange(7)) % 28).reshape(-1, 28)
    samples = x[indices].mean(axis=1)
    means, errors = x.mean(axis=0), samples.std(axis=0, ddof=1)
    active = np.any(x != x[0], axis=0) & (errors > 1e-14)
    maxima = (
        np.max(np.abs(samples[:, active] - means[active]) / errors[active], axis=1)
        if active.any()
        else np.zeros(len(samples))
    )
    critical = float(np.quantile(maxima, settings["confidence"], method="higher"))
    return {
        "status": "descriptive_development_intervals",
        "means": means.tolist(),
        "standard_errors": errors.tolist(),
        "active": active.tolist(),
        "critical_value": critical,
        "intervals": [
            [float(m - critical * s), float(m + critical * s)] if a else None
            for m, s, a in zip(means, errors, active, strict=True)
        ],
        "indices_sha256": digest(canonical(indices.tolist()).encode()),
        "resampled_means_sha256": digest(canonical(samples.tolist()).encode()),
        "settings": settings,
        "independent_significance_proven": False,
    }


def score_panel(panel, observations, config, parent_config, check_stop=lambda: None):
    fixed_configuration(config)
    cases = panel["cases"]
    ids = validate_cases({"cases": cases}, parent_config)
    expected_models = [*MODELS, config["physical_model_id"], *[h["id"] for h in HYBRIDS]]
    if panel["candidate_order"] != expected_models or set(panel["predictions"]) != set(expected_models):
        raise ValueError("Prepared panel lost the fixed twelve-candidate order")
    for rows in panel["predictions"].values():
        if len(rows) != len(ids) or {row["case_id"] for row in rows} != ids:
            raise ValueError("Candidate differs from the prepared common case grid")
        for row in rows:
            numeric_prediction(row, LEVELS)
    metadata = {
        key: panel[key]
        for key in (
            "original_case_count",
            "common_case_count",
            "full_panel_complete",
            "classification",
            "missing",
            "case_ids_sha256",
        )
    }
    for horizon in (1, 3, 6):
        calibration = [c for c in cases if c["split"] == "calibration" and c["horizon_hours"] == horizon]
        if (
            len(calibration) < parent_config["minimum_calibration_cases_per_horizon"]
            or len({c["target_ms"] // 86400000 for c in calibration})
            < parent_config["minimum_calibration_days_per_horizon"]
        ):
            return {
                **metadata,
                "status": "insufficient_common_calibration",
                "candidates": [],
                "comparisons": [],
            }
    if not any(c["split"] == "development" for c in cases):
        return {**metadata, "status": "no_common_development_cases", "candidates": [], "comparisons": []}
    check_stop()
    observed = {case["case_id"]: check_case(case, parent_config, observations) for case in cases}
    candidates, max_error = [], 0.0
    for model in expected_models:
        check_stop()
        rows = panel["predictions"][model]
        evaluation, _ = score_predictions(rows, {"cases": cases}, observations, parent_config, LEVELS)
        independent, _ = reconstruct_scores(rows, cases, observed, LEVELS, parent_config)
        max_error = max(max_error, compare_scores(evaluation, independent))
        candidates.append({"id": model, "evaluation": evaluation})
    by_model = {row["id"]: row["evaluation"] for row in candidates}
    reference_days = by_model[config["physical_model_id"]]["daily"]
    for model in expected_models:
        if [(r["day"], r["cases"]) for r in by_model[model]["daily"]] != [
            (r["day"], r["cases"]) for r in reference_days
        ]:
            raise ValueError("Candidates do not share identical daily case support")
    contrasts = [
        {
            "hybrid": h["id"],
            "reference": ref,
            "hybrid_minus_reference_mae_f": by_model[h["id"]]["metrics"]["mae_f"]
            - by_model[ref]["metrics"]["mae_f"],
        }
        for h in HYBRIDS
        for ref in REFERENCES
    ]
    differences = [
        [
            by_model[c["hybrid"]]["daily"][i]["absolute_error_f"]
            - by_model[c["reference"]]["daily"][i]["absolute_error_f"]
            for c in contrasts
        ]
        for i in range(len(reference_days))
    ]
    check_stop()
    return {
        **metadata,
        "status": "scored",
        "candidates": candidates,
        "comparisons": contrasts,
        "daily_paired_mae": differences,
        "daily_paired_mae_days": [r["day"] for r in reference_days],
        "bootstrap": shared_intervals(differences, [r["day"] for r in reference_days], config["bootstrap"]),
        "independent_score_max_error": max_error,
    }


def archive_panel(archive, identifier, panel, source_ids, source_binding_id):
    """Persist forecast arithmetic and provenance before any label/scoring call."""
    value = {
        "registration_id": identifier,
        "source_binding_record_id": source_binding_id,
        "raw_source_ids": sorted(source_ids),
        "panel": panel,
        "weather_scores_computed": 0,
        "labels_accessed": False,
    }
    return archive.append(
        "e029_predictions_gzip",
        str(identifier),
        utcnow(),
        {},
        gzip.compress(canonical(value).encode(), mtime=0),
    )


def execute(archive, identifier):
    _, protocol = read_record(archive, identifier, "e029_protocol")
    config = protocol["config"]
    fixed_configuration(config)
    verify_files(protocol)
    if archive.latest("e029_started", str(identifier)):
        raise ValueError("E029 attempt already exists; no silent retry, even after interruption")
    if Path(config["stop_file"]).exists():
        raise InterruptedError("Registered stop file present before execution")
    comparison, acquisition, cases, census = parents(archive, config)
    if census != protocol["object_sources"] or any(row["id"] >= identifier for row in census):
        raise ValueError("Physical attempt census differs from the pre-inference registration")
    binding_id = archive.append(
        "e029_started", str(identifier), utcnow(), {}, canonical({"object_sources": census}).encode()
    )

    def check_stop():
        if Path(config["stop_file"]).exists():
            raise InterruptedError("Registered stop file present")

    def deadline(_number, _frame):
        raise TimeoutError("Registered 600-second arithmetic budget reached")

    old_handler = signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, config["execution_budget_seconds"])
    prediction_id = None
    try:
        physical, source_ids = [], set()
        settings = comparison["config"]
        for item, entry in zip(census, acquisition["manifest"]["objects"], strict=True):
            check_stop()
            row, value = read_record(archive, item["id"], settings["acquisition_object_kind"])
            if any(row[key] != expected for key, expected in item.items()):
                raise ValueError("Registered acquisition attempt changed")
            rows, raw_ids = original_sources(archive, value, entry, settings, row)
            physical.extend(rows)
            source_ids.update(raw_ids)
        if len(physical) != config["expected_cases"]:
            raise ValueError("Physical case census changed")
        parent_config = json.loads(Path(settings["parent_config_path"]).read_bytes())
        predictions = {
            model: json.loads(Path(path).read_bytes())["predictions"]
            for model, path in protocol["prediction_paths"].items()
        }
        panel = prediction_panel(cases, physical, predictions, config, parent_config)
        verify_files(protocol)
        prediction_id = archive_panel(archive, identifier, panel, source_ids, binding_id)
        check_stop()
        # This is the first observation decoding/label access; prior hashing did not parse labels.
        with Path(parent_config["observations_path"]).open() as handle:
            observations = observations_from_rows(
                (json.loads(line) for line in handle if line.strip()), parent_config
            )
        result = score_panel(panel, observations, config, parent_config, check_stop)
        reproduction = "not_applicable_common_subset_or_insufficient_support"
        if result["full_panel_complete"] and result["status"] == "scored":
            parent_report = json.loads(Path(settings["parent_report_path"]).read_bytes())
            for current, old in zip(result["candidates"][:8], parent_report["candidates"], strict=True):
                if current["id"] != old["candidate"]["id"]:
                    raise ValueError("Original E022 report candidate order changed")
                compare_scores(current["evaluation"], old["evaluation"])
            reproduction = "all_eight_original_full_panel_scores_reproduced"
        check_stop()
        verify_files(protocol)
        prediction_record = archive.db.execute(
            "SELECT * FROM records WHERE id=?", (prediction_id,)
        ).fetchone()
        report = {
            "experiment": config["experiment"],
            "registration_id": identifier,
            "design_registration_id": config["design_registration_id"],
            "source_binding_record_id": binding_id,
            "prediction_record_id": prediction_id,
            "prediction_body_sha256": prediction_record["body_sha256"],
            "prediction_record_sha256": prediction_record["record_sha256"],
            "raw_source_ids": sorted(source_ids),
            "result": result,
            "original_E022_reproduction": reproduction,
            "model_fits": 0,
            "model_inferences": 0,
            "network_requests": 0,
            "trading_actions": 0,
            "selected_weight": None,
            "promotion_eligible": False,
            "profitability_proven": False,
            "historical_public_availability_verified": False,
            "untouched_validation": False,
            "interpretation": config["interpretation"],
        }
        record_id = archive.append(
            "e029_report_gzip",
            str(identifier),
            utcnow(),
            {},
            gzip.compress(canonical(report).encode(), mtime=0),
        )
        report["report_record_id"] = record_id
        path = Path("reports") / f"E029_combinations_{identifier}.json"
        with path.open("x") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
        return {
            "report_record_id": record_id,
            "report_path": str(path),
            "status": result["status"],
            "full_panel_complete": result["full_panel_complete"],
        }
    except BaseException as exc:
        archive.append(
            "e029_failed",
            str(identifier),
            utcnow(),
            {},
            canonical(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "automatic_retry": False,
                    "prediction_record_id": prediction_id,
                }
            ).encode(),
        )
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--register", action="store_true")
    group.add_argument("--run-record-id", type=int)
    args = parser.parse_args()
    archive = Archive("data")
    try:
        with (archive.root / "e029.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(
                canonical(
                    {"registration_id": register(archive), "scores_computed": 0}
                    if args.register
                    else execute(archive, args.run_record_id)
                )
            )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
