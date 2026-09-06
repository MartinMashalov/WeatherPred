"""Registered, supervised two-variant Chronos/NBH experiment; no fitting or network."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import importlib.metadata
import json
import resource
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path

import numpy as np

from research.experiments.e022_audit import (
    check_case,
    compare_scores,
    observations_from_rows,
    reconstruct_scores,
)
from research.experiments.e022_run import score_predictions, validate_cases
from research.experiments.e026_nbh_comparison import digest, merge_pins, read_record, verify_files
from research.probes.e031_guided_adapter import (
    GUIDED,
    LEVELS,
    UNIVARIATE,
    VARIANTS,
    HistoryStore,
    build_input,
    call_model,
    compare_outputs,
    integrity_selection,
    validate_trajectory,
)
from research.probes.e031_supervisor import supervise
from research.probes.station_transformer import load_local_pipeline, parameter_digest, verify_checkpoint
from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import utcnow

CONFIG = Path("config/e031_guided_forecasts.json")
FILES = [
    Path(__file__),
    CONFIG,
    Path("research/probes/e031_guided_adapter.py"),
    Path("research/probes/e031_supervisor.py"),
    Path("tests/test_e031_guided_forecasts.py"),
]
REFERENCES = ["nbh_original", "nbh_chronos_w025", "nbh_chronos_w050", "nbh_chronos_w075"]
BOOTSTRAP = {
    "block_days": 7,
    "resamples": 10000,
    "seed": 6203101,
    "confidence": 0.95,
    "comparison_count": 5,
    "metric": "pinball_loss_f",
}


def versions():
    return {
        name: importlib.metadata.version(name)
        for name in ("numpy", "torch", "transformers", "chronos-forecasting")
    }


def validate_config(config, require_sources=True):
    expected = {
        "experiment": "E031B-observed-then-guided-station-forecasts-v1",
        "design_path": "config/c1_nbh_covariate_design.json",
        "design_sha256": "8779e79dc26362c0678002101068641ce780702d79cf886b76c9611aaf3f97ca",
        "trajectory_path": "reports/E031_trajectories.json",
        "e022_registration_id": 105314,
        "e029_registration_id": 129863,
        "e029_report_record_id": 131549,
        "e029_predictions_record_id": 131498,
        "parent_config_path": "config/e022_station_forecasts.json",
        "manifest_path": "reports/E022_manifest.json",
        "observations_path": "reports/station_history_rows.jsonl",
        "expected_cases": 9870,
        "expected_calibration_cases": 3271,
        "expected_development_cases": 6599,
        "variants": VARIANTS,
        "references": REFERENCES,
        "context_hours": 168,
        "prediction_length": 7,
        "batch_size_series": 64,
        "cross_learning": False,
        "checkpoint_directory": "tmp/chronos2-small-pinned",
        "checkpoint_files": {
            "config.json": "f2780468adc8b16322aa0b6e28b26476c9401ecf64ab53292b6d2b0d3f423722",
            "model.safetensors": "492290ae82bb89f9769e3479ce90b3179de1f33e600c34daa0352531538b23cd",
        },
        "quantile_levels": LEVELS,
        "bootstrap": BOOTSTRAP,
        "advance_gate": {
            "minimum_guidance_fraction": 0.9,
            "minimum_pinball_relative_improvement_every_reference": 0.05,
            "promotion_eligible": False,
        },
        "budgets": {
            "wall_seconds": 1200,
            "cpu_threads": 2,
            "rss_bytes": 4294967296,
            "main_tasks": 19740,
            "integrity_tasks": 48,
            "maximum_integrity_tasks": 64,
            "attempts": 1,
            "model_fits": 0,
            "network_requests": 0,
            "weight_downloads": 0,
        },
        "stop_file": "data/STOP_E031_MODEL",
        "lock_file": "e031_model.lock",
    }
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError("E031 configuration differs from its fixed family, inputs or resource limits")
    integrity = config["integrity"]
    if any(
        integrity[key] != value
        for key, value in {
            "first_cases": 4,
            "later_cases": 4,
            "exact_repeat_atol_f": 0,
            "changed_batch_atol_f": 1e-5,
            "relative_tolerance": 0,
        }.items()
    ):
        raise ValueError("Fixed 48-task output-integrity design changed")
    if require_sources and any(
        type(config[key]) is not int or config[key] <= 0
        for key in ("trajectory_registration_id", "trajectory_record_id")
    ):
        raise ValueError("Root must bind the completed registered extraction before model registration")


def check_stop(config):
    if Path(config["stop_file"]).exists():
        raise InterruptedError("Registered E031 model stop file present")


def trajectory_panel(value, cases, config):
    ids = {case["case_id"] for case in cases}
    rows = value["cases"]
    if (
        value["schema_version"] != 1
        or len(rows) != len(ids)
        or {row["case_id"] for row in rows} != ids
        or value["historical_public_availability_verified"] is not False
    ):
        raise ValueError("Extraction must retain every original case and conditional source status")
    mapped = {row["case_id"]: row for row in rows}
    for case in cases:
        validate_trajectory(case, mapped[case["case_id"]])
    counts = Counter(case["split"] for case in cases)
    if len(cases) != config["expected_cases"] or counts != {
        "calibration": config["expected_calibration_cases"],
        "development": config["expected_development_cases"],
    }:
        raise ValueError("Original calibration/development census changed")
    return mapped


def load_sources(archive, config):
    validate_config(config)
    design_bytes = Path(config["design_path"]).read_bytes()
    if digest(design_bytes) != config["design_sha256"]:
        raise ValueError("The independently reviewed C1B design changed")
    design = json.loads(design_bytes)
    parent_row, parent = read_record(archive, config["e022_registration_id"], "experiment_protocol")
    e029_row, e029 = read_record(archive, config["e029_registration_id"], "e029_protocol")
    report_row, report = read_record(archive, config["e029_report_record_id"], "e029_report_gzip")
    forecasts_row, forecasts = read_record(
        archive, config["e029_predictions_record_id"], "e029_predictions_gzip"
    )
    extraction_row, extraction = read_record(
        archive, config["trajectory_registration_id"], "e031_trajectory_protocol"
    )
    trajectory_row, trajectory = read_record(
        archive, config["trajectory_record_id"], "e031_trajectories_gzip"
    )
    binding_row, binding = read_record(
        archive, trajectory["source_binding_record_id"], "e031_extraction_started"
    )
    if (
        binding_row["key"] != str(extraction_row["id"])
        or binding["object_sources"] != extraction["object_sources"]
        or binding["object_sources"] != e029["object_sources"]
        or extraction["parent_registration_record_sha256"] != e029_row["record_sha256"]
        or trajectory_row["key"] != str(extraction_row["id"])
        or trajectory["raw_source_ids"] != report["raw_source_ids"]
    ):
        raise ValueError("Extraction census/source binding differs from its frozen parent")
    raw_ids = trajectory["raw_source_ids"]
    if raw_ids != sorted(set(raw_ids)):
        raise ValueError("Raw extraction source census contains duplicates")
    raw_records = {}
    for source_id in raw_ids:
        raw = archive.db.execute("SELECT * FROM records WHERE id=?", (source_id,)).fetchone()
        if raw is None:
            raise ValueError("A pinned original response record is missing")
        fields = [
            raw[k] for k in ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")
        ]
        if digest(canonical(fields).encode()) != raw["record_sha256"]:
            raise ValueError("Original source record metadata hash changed")
        raw_records[source_id] = raw
    for row in trajectory["cases"]:
        for cell in row["cells"]:
            if cell["response_id"] is None:
                continue
            raw = raw_records.get(cell["response_id"])
            if (
                raw is None
                or raw["body_sha256"] != cell["response_sha256"]
                or raw["record_sha256"] != cell["response_record_sha256"]
                or json.loads(raw["metadata"])["actual_received_at"] != cell["actual_received_at"]
            ):
                raise ValueError("Trajectory cell no longer belongs to the original response census")
    local = json.loads(Path(config["trajectory_path"]).read_bytes())
    if (
        report["registration_id"] != config["e029_registration_id"]
        or report["prediction_record_id"] != forecasts_row["id"]
        or report["prediction_body_sha256"] != forecasts_row["body_sha256"]
        or report["prediction_record_sha256"] != forecasts_row["record_sha256"]
        or forecasts["registration_id"] != config["e029_registration_id"]
        or e029["parent_record_sha256"] != parent_row["record_sha256"]
        or local.get("record_id") != trajectory_row["id"]
        or {k: v for k, v in local.items() if k != "record_id"} != trajectory
        or trajectory["registration_id"] != extraction_row["id"]
        or trajectory["extraction_registration_id"] != extraction_row["id"]
        or not extraction_row["id"] < trajectory["source_binding_record_id"] < trajectory_row["id"]
        or trajectory["manifest_sha256"] != digest(Path(config["manifest_path"]).read_bytes())
        or trajectory["design_sha256"] != config["design_sha256"]
        or any(
            trajectory[key] != 0
            for key in ("model_fits", "model_inferences", "network_requests", "weather_scores_computed")
        )
    ):
        raise ValueError("Registered extraction/reference lineage or local artifact differs")
    manifest = json.loads(Path(config["manifest_path"]).read_bytes())
    parent_config = json.loads(Path(config["parent_config_path"]).read_bytes())
    validate_cases(manifest, parent_config)
    trajectories = trajectory_panel(trajectory, manifest["cases"], config)
    selected_ids = {case["case_id"] for case in manifest["cases"]}
    references = {model: forecasts["panel"]["predictions"][model] for model in REFERENCES}
    for rows in references.values():
        if len(rows) != len(selected_ids) or {row["case_id"] for row in rows} != selected_ids:
            raise ValueError("Frozen references lost the identical original case grid")
    pins = merge_pins(
        parent["source_hashes"], e029["source_hashes"], extraction["source_hashes"], design["source_sha256"]
    )
    records = [
        {k: row[k] for k in ("id", "kind", "body_sha256", "record_sha256")}
        for row in (
            parent_row,
            e029_row,
            report_row,
            forecasts_row,
            extraction_row,
            binding_row,
            trajectory_row,
        )
    ]
    return {
        "cases": manifest["cases"],
        "parent_config": parent_config,
        "trajectories": trajectories,
        "references": references,
        "reference_report": report,
        "source_hashes": pins,
        "source_records": records,
        "design": design,
    }


def register(archive):
    config = json.loads(CONFIG.read_bytes())
    if archive.latest("e031_model_protocol", config["experiment"]):
        raise ValueError("E031 model experiment already registered; no second attempt")
    sources = load_sources(archive, config)
    if versions() != sources["design"]["versions"]:
        raise ValueError("Use the exact independently checked optional model environment")
    verify_checkpoint(config["checkpoint_directory"], config["checkpoint_files"])
    pins = sources["source_hashes"]
    paths = [*FILES, Path(config["design_path"]), Path(config["trajectory_path"])]
    for path in paths:
        pins = merge_pins(pins, {str(path): digest(path.read_bytes())})
    for name, expected in config["checkpoint_files"].items():
        pins = merge_pins(pins, {str(Path(config["checkpoint_directory"]) / name): expected})
    protocol = {
        "config": config,
        "source_hashes": pins,
        "source_records": sources["source_records"],
        "numpy_version": np.__version__,
        "runtime_versions": versions(),
        "python_executable": sys.executable,
        "cwd": str(Path.cwd().resolve()),
        "known_E022_E026_E029_results_acknowledged": True,
        "prediction_before_scoring_labels": True,
        "fits_authorized": 0,
        "main_neural_tasks_max": 19740,
        "integrity_tasks": 48,
        "source_record_ids": {
            str(path): archive.append("e031_model_source", str(path), utcnow(), {}, path.read_bytes())
            for path in paths
        },
    }
    verify_files(protocol)
    return archive.append(
        "e031_model_protocol", config["experiment"], utcnow(), {}, canonical(protocol).encode()
    )


def registration(archive, identifier):
    record, protocol = read_record(archive, identifier, "e031_model_protocol")
    validate_config(protocol["config"])
    verify_files(protocol)
    if protocol["python_executable"] != sys.executable or protocol["runtime_versions"] != versions():
        raise ValueError("Registered Python or package runtime changed")
    sources = load_sources(archive, protocol["config"])
    if sources["source_records"] != protocol["source_records"] or any(
        row["id"] >= identifier for row in sources["source_records"]
    ):
        raise ValueError("Model source record bindings changed")
    return record, protocol, sources


def shared_intervals(differences, days, settings):
    if settings != BOOTSTRAP:
        raise ValueError("Fixed five-comparison pinball bootstrap changed")
    calendar = [(date(2026, 7, 20) + timedelta(days=i)).isoformat() for i in range(28)]
    if days != calendar:
        raise ValueError("Every original development calendar day is required; do not concatenate gaps")
    x = np.asarray(differences, dtype=float)
    if x.shape != (28, 5) or not np.isfinite(x).all():
        raise ValueError("Incorrect paired day/comparison matrix")
    rng = np.random.default_rng(6203101)
    starts = rng.integers(0, 28, size=(10000, 4))
    indices = ((starts[:, :, None] + np.arange(7)) % 28).reshape(-1, 28)
    samples = x[indices].mean(axis=1)
    means, errors = x.mean(axis=0), samples.std(axis=0, ddof=1)
    active = np.any(x != x[0], axis=0) & (errors > 1e-14)
    maxima = (
        np.max(np.abs(samples[:, active] - means[active]) / errors[active], axis=1)
        if active.any()
        else np.zeros(len(samples))
    )
    critical = float(np.quantile(maxima, 0.95, method="higher"))
    return {
        "status": "descriptive_reused_development_intervals",
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


def research_gain_gate(comparisons, guidance_count, case_count):
    if [row["reference"] for row in comparisons] != [UNIVARIATE, *REFERENCES]:
        raise ValueError("Every one of five fixed reference gains must be present")
    if type(guidance_count) is not int or not 0 <= guidance_count <= case_count or case_count <= 0:
        raise ValueError("Invalid complete-case guidance denominator")
    return guidance_count / case_count >= 0.9 and all(
        row["relative_pinball_improvement"] is not None
        and np.isfinite(row["relative_pinball_improvement"])
        and row["relative_pinball_improvement"] >= 0.05
        for row in comparisons
    )


def score_all(cases, predictions, references, observations, parent_config, config, guidance_count):
    if set(predictions) != set(VARIANTS) or set(references) != set(REFERENCES):
        raise ValueError("Both neural variants and every frozen reference are required")
    ids = validate_cases({"cases": cases}, parent_config)
    observed = {case["case_id"]: check_case(case, parent_config, observations) for case in cases}
    models = [*VARIANTS, *REFERENCES]
    arrays = {**predictions, **references}
    candidates, max_error = [], 0.0
    for model in models:
        rows = arrays[model]
        if len(rows) != len(ids) or {row["case_id"] for row in rows} != ids:
            raise ValueError("Candidate lost a required common case")
        evaluation, _ = score_predictions(rows, {"cases": cases}, observations, parent_config, LEVELS)
        audited, _ = reconstruct_scores(rows, cases, observed, LEVELS, parent_config)
        max_error = max(max_error, compare_scores(evaluation, audited))
        mapped = {row["case_id"]: row for row in rows}
        bias = {}
        for case in cases:
            if case["split"] == "development":
                day = datetime.fromtimestamp(case["target_ms"] / 1000, UTC).date().isoformat()
                bias.setdefault(day, []).append(
                    mapped[case["case_id"]]["point_f"] - observed[case["case_id"]]
                )
        daily_bias = [
            {"day": day, "bias_f": float(np.mean(values)), "cases": len(values)}
            for day, values in sorted(bias.items())
        ]
        candidates.append(
            {
                "id": model,
                "evaluation": evaluation,
                "daily_bias": daily_bias,
                "mean_day_weighted_bias_f": float(np.mean([row["bias_f"] for row in daily_bias])),
            }
        )
    by_model = {row["id"]: row["evaluation"] for row in candidates}
    guided = by_model[GUIDED]
    days = [row["day"] for row in guided["daily"]]
    comparisons = []
    for model in [UNIVARIATE, *REFERENCES]:
        other = by_model[model]
        if [(r["day"], r["cases"]) for r in other["daily"]] != [
            (r["day"], r["cases"]) for r in guided["daily"]
        ]:
            raise ValueError("Candidate daily support differs")
        baseline = other["metrics"]["pinball_loss_f"]
        gain = (baseline - guided["metrics"]["pinball_loss_f"]) / baseline if baseline > 0 else None
        comparisons.append(
            {
                "candidate": GUIDED,
                "reference": model,
                "pinball_difference_f": guided["metrics"]["pinball_loss_f"] - baseline,
                "mae_difference_f": guided["metrics"]["mae_f"] - other["metrics"]["mae_f"],
                "relative_pinball_improvement": gain,
            }
        )
    differences = [
        [
            guided["daily"][i]["pinball_loss_f"]
            - by_model[comparison["reference"]]["daily"][i]["pinball_loss_f"]
            for comparison in comparisons
        ]
        for i in range(len(days))
    ]
    guidance_fraction = guidance_count / len(cases)
    advance = research_gain_gate(comparisons, guidance_count, len(cases))
    return {
        "candidates": candidates,
        "comparisons": comparisons,
        "daily_paired_pinball": differences,
        "daily_paired_days": days,
        "bootstrap": shared_intervals(differences, days, config["bootstrap"]),
        "guidance_count": guidance_count,
        "guidance_denominator": len(cases),
        "guidance_fraction": guidance_fraction,
        "advance_to_prospective_research_only": advance,
        "promotion_eligible": False,
        "profitability_proven": False,
        "independent_score_max_error": max_error,
    }


def integrity_checks(
    archive, identifier, variant, first, later, main_predictions, prepare_inputs, predict, parameter_sha256
):
    """Retain every integrity call before any comparison can terminate the attempt."""
    records = {}

    def retained_call(role, values, metadata):
        output = predict(values, metadata)
        artifact = {
            "registration_id": identifier,
            "variant": variant,
            "role": role,
            "inputs": metadata,
            "predictions": output,
            "tasks": len(metadata),
            "labels_supplied": False,
            "parameter_sha256": parameter_sha256,
            "comparison_started": False,
        }
        records[role] = archive.append(
            "e031_model_integrity_gzip",
            f"{identifier}:{variant}:{role}",
            utcnow(),
            {},
            gzip.compress(canonical(artifact).encode(), mtime=0),
        )
        return output

    first_inputs, first_metadata = prepare_inputs(first, variant)
    first_output = retained_call("first", first_inputs, first_metadata)
    repeat = retained_call("repeat", first_inputs, first_metadata)
    ids = [case["case_id"] for case in first]
    exact = compare_outputs(first_output, repeat, ids, 0)
    original = compare_outputs(main_predictions, first_output, ids, 1e-5)
    appended_inputs, appended_metadata = prepare_inputs(first + later, variant)
    appended = retained_call("appended", appended_inputs, appended_metadata)
    permuted_inputs, permuted_metadata = prepare_inputs(list(reversed(first + later)), variant)
    permuted = retained_call("permuted", permuted_inputs, permuted_metadata)
    append_difference = compare_outputs(first_output, appended, ids, 1e-5)
    permutation_difference = compare_outputs(appended, permuted, [c["case_id"] for c in first + later], 1e-5)
    return {
        "first_case_ids": ids,
        "later_case_ids": [case["case_id"] for case in later],
        "exact_repeat_max_difference_f": exact,
        "main_batch_max_difference_f": original,
        "append_later_origin_max_difference_f": append_difference,
        "permutation_max_difference_f": permutation_difference,
        "artifact_record_ids": records,
        "predictions": {
            "first": first_output,
            "repeat": repeat,
            "appended": appended,
            "permuted": permuted,
        },
    }


def worker(archive, identifier, directory):
    _, protocol, sources = registration(archive, identifier)
    config = protocol["config"]
    started = archive.latest("e031_model_started", str(identifier))
    if started is None or started["id"] <= identifier:
        raise ValueError("Worker requires the one externally supervised attempt")
    expected = Path("reports") / f"E031-model-{identifier}"
    if directory.resolve() != expected.resolve() or not (directory / "started.json").exists():
        raise ValueError("Worker output must belong to its externally supervised directory")
    with (directory / "worker_started.json").open("x") as handle:
        json.dump({"registration_id": identifier}, handle)
    cases, parent_config, trajectories = sources["cases"], sources["parent_config"], sources["trajectories"]
    with Path(config["observations_path"]).open() as handle:
        history = HistoryStore(json.loads(line) for line in handle if line.strip())
    available = {key for key, row in trajectories.items() if row["available"]}
    selections = {
        variant: integrity_selection(
            cases, {c["case_id"] for c in cases} if variant == UNIVARIATE else available
        )
        for variant in VARIANTS
    }
    pipeline, checkpoint_evidence = load_local_pipeline(
        config["checkpoint_directory"], config["checkpoint_files"]
    )
    import torch

    torch.set_num_interop_threads(1)
    if torch.get_num_threads() != 2 or torch.get_num_interop_threads() != 1:
        raise ValueError("Model thread limits differ")
    initial_parameters = parameter_digest(pipeline.model)
    predictions, metadata, main_tasks = {}, {}, 0

    def inputs(selected, variant):
        values, meta = [], []
        for case in selected:
            item, info = build_input(
                history.prepare(case, parent_config), trajectories[case["case_id"]], variant
            )
            values.append(item)
            meta.append(info)
        return values, meta

    for variant in VARIANTS:
        check_stop(config)
        verify_files(protocol)
        selected = (
            cases if variant == UNIVARIATE else [case for case in cases if case["case_id"] in available]
        )
        batch_size = 64 if variant == UNIVARIATE else 32
        result, descriptors = {}, {}
        for offset in range(0, len(selected), batch_size):
            check_stop(config)
            batch_inputs, batch_meta = inputs(selected[offset : offset + batch_size], variant)
            batch_rows = call_model(pipeline, batch_inputs, batch_meta)
            main_tasks += len(batch_rows)
            if main_tasks > config["budgets"]["main_tasks"]:
                raise ValueError("Main model task budget exceeded")
            artifact = {
                "variant": variant,
                "offset": offset,
                "predictions": batch_rows,
                "inputs": batch_meta,
                "labels_supplied": False,
                "parameter_sha256": initial_parameters,
            }
            archive.append(
                "e031_model_batch_gzip",
                f"{identifier}:{variant}:{offset}",
                utcnow(),
                {},
                gzip.compress(canonical(artifact).encode(), mtime=0),
            )
            result.update({row["case_id"]: row for row in batch_rows})
            descriptors.update({row["case_id"]: row for row in batch_meta})
        if variant == GUIDED:
            baseline = {row["case_id"]: row for row in predictions[UNIVARIATE]}
            old_meta = {row["case_id"]: row for row in metadata[UNIVARIATE]}
            for case in cases:
                key = case["case_id"]
                if key not in available:
                    result[key] = baseline[key]
                    descriptors[key] = {
                        **old_meta[key],
                        "uses_guidance": False,
                        "fallback_reason": trajectories[key]["reason"],
                    }
        predictions[variant] = [result[case["case_id"]] for case in cases]
        metadata[variant] = [descriptors[case["case_id"]] for case in cases]
        if parameter_digest(pipeline.model) != initial_parameters:
            raise ValueError("Model parameters changed during main inference")
    integrity, extra_tasks = {}, 0
    for variant in VARIANTS:
        first, later = selections[variant]
        integrity[variant] = integrity_checks(
            archive,
            identifier,
            variant,
            first,
            later,
            predictions[variant],
            inputs,
            partial(call_model, pipeline),
            initial_parameters,
        )
        extra_tasks += 2 * len(first) + 2 * len(first + later)
        check_stop(config)
    if extra_tasks != 48 or parameter_digest(pipeline.model) != initial_parameters or pipeline.model.training:
        raise ValueError("Integrity task count, fixed parameters or evaluation mode changed")
    verify_files(protocol)
    payload = {
        "registration_id": identifier,
        "cases": cases,
        "predictions": predictions,
        "input_metadata": metadata,
        "integrity": integrity,
        "main_tasks": main_tasks,
        "integrity_tasks": extra_tasks,
        "parameters_unchanged": True,
        "parameter_sha256": initial_parameters,
        "checkpoint_files": checkpoint_evidence,
        "training_mode": False,
        "dropout_active": False,
        "cross_learning": False,
        "cpu_threads": 2,
        "torch_interop_threads": 1,
        "labels_supplied_to_models": False,
        "score_label_lookup_started": False,
    }
    prediction_id = archive.append(
        "e031_model_predictions_gzip",
        str(identifier),
        utcnow(),
        {},
        gzip.compress(canonical(payload).encode(), mtime=0),
    )
    history.rows.clear()
    history.cache.clear()
    del pipeline
    check_stop(config)
    with Path(config["observations_path"]).open() as handle:
        observations = observations_from_rows(
            (json.loads(line) for line in handle if line.strip()), parent_config
        )
    result = score_all(
        cases, predictions, sources["references"], observations, parent_config, config, len(available)
    )
    for candidate in result["candidates"]:
        if candidate["id"] in REFERENCES:
            old = next(
                row
                for row in sources["reference_report"]["result"]["candidates"]
                if row["id"] == candidate["id"]
            )
            compare_scores(candidate["evaluation"], old["evaluation"])
    verify_files(protocol)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_bytes = int(peak if sys.platform == "darwin" else peak * 1024)
    report = {
        "experiment": config["experiment"],
        "registration_id": identifier,
        "prediction_record_id": prediction_id,
        "source_records": protocol["source_records"],
        "result": result,
        "guidance_counts_by_split": dict(
            Counter(case["split"] for case in cases if case["case_id"] in available)
        ),
        "main_tasks": main_tasks,
        "integrity_tasks": extra_tasks,
        "worker_peak_rss_bytes": peak_bytes,
        "all_four_frozen_reference_scores_reproduced": True,
        "model_fits": 0,
        "network_requests": 0,
        "weight_downloads": 0,
        "historical_public_availability_verified": False,
        "untouched_validation": False,
        "promotion_eligible": False,
        "interpretation": config["interpretation"],
    }
    with (directory / "worker_report.json").open("x") as handle:
        json.dump(report, handle, allow_nan=False)
    return {"prediction_record_id": prediction_id, "worker_report": str(directory / "worker_report.json")}


def execute(archive, identifier):
    _, protocol, _ = registration(archive, identifier)
    config = protocol["config"]
    if archive.latest("e031_model_started", str(identifier)):
        raise ValueError("An E031 model attempt already exists; no hidden retry")
    check_stop(config)
    directory = Path("reports") / f"E031-model-{identifier}"
    started = archive.append(
        "e031_model_started",
        str(identifier),
        utcnow(),
        {},
        canonical({"directory": str(directory), "budgets": config["budgets"]}).encode(),
    )
    command = [
        sys.executable,
        "-m",
        "research.experiments.e031_guided_forecasts",
        "--worker-record-id",
        str(identifier),
    ]
    resource_id = None
    try:
        result = supervise(command, protocol["cwd"], directory, 1200, 4294967296, config["stop_file"])
        resource_id = archive.append(
            "e031_model_resources", str(identifier), utcnow(), {}, canonical(result).encode()
        )
        for path in (directory / "stdout.log", directory / "stderr.log"):
            if path.exists():
                archive.append("e031_model_log", f"{identifier}:{path.name}", utcnow(), {}, path.read_bytes())
        if result["status"] != "completed":
            raise RuntimeError("Supervised model attempt failed: " + str(result["reason"]))
        report = json.loads((directory / "worker_report.json").read_bytes())
        if report["worker_peak_rss_bytes"] > 4294967296:
            raise ValueError("Worker's recorded peak exceeded the 4-GiB limit between supervisor samples")
        if report["registration_id"] != identifier:
            raise ValueError("Worker result belongs to another registration")
        prediction_row, _ = read_record(
            archive, report["prediction_record_id"], "e031_model_predictions_gzip", False
        )
        if not started < prediction_row["id"] < resource_id:
            raise ValueError("Model predictions were not retained before final scoring publication")
        verify_files(protocol)
        report.update(
            resource_record_id=resource_id,
            prediction_body_sha256=prediction_row["body_sha256"],
            prediction_record_sha256=prediction_row["record_sha256"],
        )
        rid = archive.append(
            "e031_model_report_gzip",
            str(identifier),
            utcnow(),
            {},
            gzip.compress(canonical(report).encode(), mtime=0),
        )
        report["report_record_id"] = rid
        path = Path("reports") / f"E031_guided_forecasts_{identifier}.json"
        with path.open("x") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
        return {
            "report_record_id": rid,
            "report_path": str(path),
            "advance_to_prospective_research_only": report["result"]["advance_to_prospective_research_only"],
        }
    except BaseException as exc:
        archive.append(
            "e031_model_failed",
            str(identifier),
            utcnow(),
            {},
            canonical(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "resource_record_id": resource_id,
                    "automatic_retry": False,
                }
            ).encode(),
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--register", action="store_true")
    group.add_argument("--run-record-id", type=int)
    group.add_argument("--worker-record-id", type=int)
    args = parser.parse_args()
    archive = Archive("data")
    try:
        if args.worker_record_id:
            print(
                canonical(
                    worker(
                        archive,
                        args.worker_record_id,
                        Path("reports") / f"E031-model-{args.worker_record_id}",
                    )
                )
            )
        else:
            with (archive.root / "e031_model.lock").open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                print(
                    canonical(
                        {"registration_id": register(archive), "model_inferences": 0}
                        if args.register
                        else execute(archive, args.run_record_id)
                    )
                )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
