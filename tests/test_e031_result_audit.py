import copy
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from research.experiments.e022_audit import prepared_summary
from research.experiments.e031_guided_forecasts import score_all, shared_intervals
from research.experiments.e031_result_audit import (
    GUIDED,
    SETTINGS,
    UNIVARIATE,
    audit_calls,
    audit_payload,
    bootstrap_replay,
    census,
    difference,
    input_metadata,
    ordered_records,
    output_table,
    validate_audit,
    verify_scores,
)
from tests.test_e031_guided_forecasts import config, scoring_fixture
from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import utcnow


def call_fixture():
    cases = [
        {
            "case_id": f"case-{i}",
            "station_id": "KSYN",
            "decision_ms": 100000000 + i * 3600000,
            "target_ms": 103600000 + i * 3600000,
            "horizon_hours": 1,
            "split": "development",
        }
        for i in range(8)
    ]
    trajectories = {case["case_id"]: {"available": True, "reason": None} for case in cases}
    metadata = {
        variant: {
            case["case_id"]: input_metadata(
                case,
                {"input_sha256": "a" * 64, "finite_context_points": 168},
                trajectories[case["case_id"]],
                variant,
            )
            for case in cases
        }
        for variant in (UNIVARIATE, GUIDED)
    }

    def outputs(selected):
        rows = []
        for case in selected:
            number = float(int(case["case_id"].split("-")[-1]))
            rows.append(
                {
                    "case_id": case["case_id"],
                    "point_f": number,
                    "quantiles_f": [number] * 13,
                    "all_quantiles_f": [[number] * 7 for _ in range(13)],
                }
            )
        return rows

    entries = []
    for variant in (UNIVARIATE, GUIDED):
        entries.append(
            (
                {"id": len(entries) + 2, "kind": "e031_model_batch_gzip", "key": f"1:{variant}:0"},
                {
                    "variant": variant,
                    "offset": 0,
                    "predictions": outputs(cases),
                    "inputs": list(metadata[variant].values()),
                    "labels_supplied": False,
                    "parameter_sha256": "b" * 64,
                },
            )
        )
    for variant in (UNIVARIATE, GUIDED):
        for role in ("first", "repeat", "appended", "permuted"):
            selected = cases[:4] if role in ("first", "repeat") else cases
            if role == "permuted":
                selected = list(reversed(selected))
            entries.append(
                (
                    {
                        "id": len(entries) + 2,
                        "kind": "e031_model_integrity_gzip",
                        "key": f"1:{variant}:{role}",
                    },
                    {
                        "variant": variant,
                        "role": role,
                        "registration_id": 1,
                        "predictions": outputs(selected),
                        "inputs": [metadata[variant][case["case_id"]] for case in selected],
                        "tasks": len(selected),
                        "labels_supplied": False,
                        "comparison_started": False,
                        "parameter_sha256": "b" * 64,
                    },
                )
            )
    return entries, cases, trajectories, metadata


def test_independent_batch_and_full_48_task_integrity_census():
    entries, cases, trajectories, metadata = call_fixture()
    result = audit_calls(entries, cases, trajectories, metadata, True)
    assert result["complete"] and result["main_tasks"] == 16
    assert result["integrity_tasks"] == 48 and len(result["checks"]) == 8
    assert all(row["passed"] and row["maximum_difference_f"] == 0 for row in result["checks"])
    changed = copy.deepcopy(entries)
    changed[1][1]["inputs"][0]["context_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="Exact value changed"):
        audit_calls(changed, cases, trajectories, metadata, True)
    with pytest.raises(ValueError, match="sequence"):
        audit_calls([entries[1], entries[0], *entries[2:]], cases, trajectories, metadata)


def test_failed_repeat_preserves_partial_counts_and_actual_violation():
    entries, cases, trajectories, metadata = call_fixture()
    partial = copy.deepcopy(entries[:4])  # Both main batches, first four and repeated four only.
    partial[-1][1]["predictions"][0]["all_quantiles_f"][0][0] += 0.25
    result = audit_calls(partial, cases, trajectories, metadata)
    assert not result["complete"] and result["integrity_calls"] == 2 and result["integrity_tasks"] == 8
    failed = [row for row in result["checks"] if not row["passed"]]
    assert failed == [
        {
            "variant": UNIVARIATE,
            "check": "exact_repeat",
            "maximum_difference_f": 0.25,
            "tolerance_f": 0,
            "passed": False,
        }
    ]
    with pytest.raises(ValueError, match="complete passing"):
        audit_calls(partial, cases, trajectories, metadata, True)
    early = audit_calls(entries[:1], cases, trajectories, metadata)
    assert early["main_tasks"] == 8 and early["integrity_tasks"] == 0 and early["checks"] == []
    impossible = copy.deepcopy(entries)
    impossible[3][1]["predictions"][0]["all_quantiles_f"][0][0] += 0.25
    with pytest.raises(ValueError, match="continued after"):
        audit_calls(impossible, cases, trajectories, metadata)


def test_missing_guidance_changes_expected_batch_census_without_dropping_case():
    entries, cases, trajectories, metadata = call_fixture()
    trajectories[cases[-1]["case_id"]]["available"] = False
    with pytest.raises(ValueError, match="input metadata|output case order"):
        audit_calls(entries, cases, trajectories, metadata)


def test_raw_output_step_and_integrity_tolerances_are_independent_and_strict():
    entries, cases, *_ = call_fixture()
    rows = entries[0][1]["predictions"]
    lookup = {case["case_id"]: case for case in cases}
    table = output_table(rows, list(lookup), lookup)
    changed = copy.deepcopy(table)
    changed[cases[0]["case_id"]]["all_quantiles_f"][0][0] += 1e-4
    assert not difference(table, changed, list(lookup), 1e-5)["passed"]
    rows = copy.deepcopy(rows)
    rows[0]["quantiles_f"][0] = 42
    with pytest.raises(ValueError, match="horizon step"):
        output_table(rows, list(lookup), lookup)


def test_independent_bootstrap_exact_draw_hashes_and_zero_variance():
    days = [(date(2026, 7, 20) + timedelta(days=i)).isoformat() for i in range(28)]
    x = np.arange(28)[:, None] * np.arange(5)[None, :] / 100
    actual = bootstrap_replay(x, days, SETTINGS)
    assert actual == shared_intervals(x, days, SETTINGS)
    assert actual["intervals"][0] is None
    with pytest.raises(ValueError, match="controls"):
        bootstrap_replay(x, days, {**SETTINGS, "seed": 12})
    with pytest.raises(ValueError, match="28-day"):
        bootstrap_replay(x, days[:-1], SETTINGS)


def test_independent_six_score_reconstruction_rejects_tampered_gain_or_calibration():
    cases, neural, refs, observations, parent = scoring_fixture()
    result = score_all(cases, neural, refs, observations, parent, config(), len(cases))
    sources = {
        "cases": cases,
        "parent_config": parent,
        "trajectories": {case["case_id"]: {"available": True} for case in cases},
        "reference_report": {"result": {"candidates": result["candidates"][2:]}},
    }
    actual = verify_scores(result, {**neural, **refs}, sources, observations)
    assert actual["models_reconstructed"] == 6 and actual["comparisons_reconstructed"] == 5
    assert actual["maximum_arithmetic_error"] < 1e-10
    for path in ("gain", "calibration"):
        corrupted = copy.deepcopy(result)
        if path == "gain":
            corrupted["advance_to_prospective_research_only"] = True
        else:
            corrupted["candidates"][0]["evaluation"]["calibration_offsets_f"]["1"][0] += 1
        with pytest.raises(ValueError):
            verify_scores(corrupted, {**neural, **refs}, sources, observations)
    missing = copy.deepcopy(result)
    missing["candidates"] = missing["candidates"][:-1]
    with pytest.raises(ValueError, match="Six-candidate"):
        verify_scores(missing, {**neural, **refs}, sources, observations)


def test_registration_census_uses_metadata_only_and_retains_failure(tmp_path):
    archive = Archive(tmp_path)
    try:
        run = archive.append("e031_model_protocol", "synthetic", utcnow(), {}, b"{}")
        archive.append("e031_model_started", str(run), utcnow(), {}, b"{}")
        with pytest.raises(ValueError, match="terminal attempt"):
            census(archive, run)
        record = archive.append("e031_model_failed", str(run), utcnow(), {}, b"not-json-intentionally")
        body_sha = archive.db.execute("SELECT body_sha256 FROM records WHERE id=?", (record,)).fetchone()[0]
        (tmp_path / "blobs" / body_sha).unlink()  # Census must not attempt to open any outcome body.
        records = census(archive, run)
        assert [row["kind"] for row in records] == ["e031_model_started", "e031_model_failed"]
        archive.append("e031_model_failed", str(run), utcnow(), {}, canonical({"again": True}).encode())
        with pytest.raises(ValueError, match="terminal attempt"):
            census(archive, run)
    finally:
        archive.close()


def test_chronology_and_fixed_audit_budget_are_not_weakened():
    settings = json.loads(Path("config/e031_result_audit.json").read_bytes())
    validate_audit(settings)
    with pytest.raises(ValueError, match="budget"):
        validate_audit({**settings, "wall_seconds": 601})
    rows = [
        {"id": 2, "kind": "e031_model_started", "available_at": "2026-09-06T00:00:01Z"},
        {"id": 3, "kind": "e031_model_failed", "available_at": "2026-09-06T00:00:02Z"},
    ]
    run = {"id": 1, "available_at": "2026-09-06T00:00:00Z"}
    ordered_records(rows, run)
    with pytest.raises(ValueError, match="chronology"):
        ordered_records(list(reversed(rows)), run)


def test_failure_artifact_integration_audits_inputs_resources_logs_and_partial_checks():
    entries, cases, trajectories, _ = call_fixture()
    parent = {"station_transformer": {"availability_mode": "retrospective_15_minute_assumption"}}
    observations = {}
    hour = 3_600_000
    for case in cases:
        # The fixture reuses the production case grid semantics, without model or archive reads.
        case["split"] = "calibration" if int(case["case_id"].split("-")[-1]) < 4 else "development"
        case["decision_ms"] = (case["decision_ms"] // hour) * hour
        case["target_ms"] = case["decision_ms"] + hour
        end = case["decision_ms"] - hour
        for at in range(end - 167 * hour, end + hour, hour):
            observations[case["station_id"], at] = {
                "temperature_f": 70.0,
                "status": "settled",
                "source_record_ids": [99],
                "actual_received_ms": [at + hour],
                "actual_received_at_original": ["2026-09-06T00:00:00Z"],
            }
    expected_meta = {
        variant: {
            case["case_id"]: input_metadata(
                case, prepared_summary(case, observations, parent), trajectories[case["case_id"]], variant
            )
            for case in cases
        }
        for variant in (UNIVARIATE, GUIDED)
    }
    retained = copy.deepcopy(entries[:4])
    for row, body in retained:
        row["id"] += 1
        body["inputs"] = [expected_meta[body["variant"]][item["case_id"]] for item in body["predictions"]]
    retained[-1][1]["predictions"][0]["all_quantiles_f"][0][0] += 0.25
    run = {"config": config(), "python_executable": "synthetic-python", "cwd": "/synthetic"}
    budget = run["config"]["budgets"]
    start = (
        {"id": 2, "kind": "e031_model_started", "key": "1"},
        {"directory": "reports/E031-model-1", "budgets": budget},
    )
    stderr = b"ValueError: Origin/batch output invariance failed: 0.25 > 0\n"
    from research.experiments.e026_result_audit import sha

    resources = {
        "command": [
            "synthetic-python",
            "-m",
            "research.experiments.e031_guided_forecasts",
            "--worker-record-id",
            "1",
        ],
        "cwd": "/synthetic",
        "wall_seconds_limit": 1200,
        "rss_bytes_limit": 4294967296,
        "automatic_retry": False,
        "status": "failed",
        "reason": "nonzero_exit",
        "logs": {"stderr.log": {"bytes": len(stderr), "sha256": sha(stderr)}},
    }
    tail = [
        ({"id": 7, "kind": "e031_model_resources", "key": "1"}, resources),
        ({"id": 8, "kind": "e031_model_log", "key": "1:stderr.log"}, stderr),
        (
            {"id": 9, "kind": "e031_model_failed", "key": "1"},
            {
                "error": "RuntimeError: Supervised model attempt failed: nonzero_exit",
                "resource_record_id": 7,
                "automatic_retry": False,
            },
        ),
    ]
    protocol = {
        "run": {"id": 1},
        "config": {"expected_cases": 8, "expected_calibration_cases": 4, "expected_development_cases": 4},
    }
    sources = {"cases": cases, "trajectories": trajectories, "parent_config": parent}
    result = audit_payload(None, protocol, run, sources, [start, *retained, *tail], observations)
    assert result["status"] == "retained_failed_attempt_audited"
    assert result["execution_failed"] and result["failure_message_mentions_invariance"]
    assert result["integrity_tasks"] == 8 and result["published_score_audit"] is None
    assert result["resource_evidence"]["reason"] == "nonzero_exit"
    corrupted = copy.deepcopy(tail)
    corrupted[0][1]["logs"]["stderr.log"]["bytes"] -= 1
    with pytest.raises(ValueError, match="log content"):
        audit_payload(None, protocol, run, sources, [start, *retained, *corrupted], observations)
