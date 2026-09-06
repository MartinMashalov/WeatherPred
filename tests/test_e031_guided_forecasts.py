import copy
import gzip
import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from research.experiments.e031_guided_forecasts import (
    BOOTSTRAP,
    REFERENCES,
    integrity_checks,
    research_gain_gate,
    score_all,
    shared_intervals,
    validate_config,
)
from research.probes.e031_guided_adapter import (
    GUIDED,
    KEY,
    LEVELS,
    UNIVARIATE,
    HistoryStore,
    build_input,
    compare_outputs,
    integrity_selection,
    validate_trajectory,
)
from research.probes.e031_supervisor import supervise
from research.probes.station_transformer import HOUR
from tests.test_e026_nbh_comparison import panel_fixture
from weatherpred.archive import Archive


def config():
    return json.loads(Path("config/e031_guided_forecasts.json").read_bytes())


def trajectory(case):
    run = case["decision_ms"] - 2 * HOUR
    stamp = datetime.fromtimestamp(run / 1000, UTC).isoformat()
    cells = []
    future = [None] * 7
    for lead in range(2, case["horizon_hours"] + 3):
        valid = run + lead * HOUR
        cell = {
            "forecast_hour": lead,
            "valid_ms": valid,
            "tmp_f": 70 + lead,
            "missing_reason": None,
            "raw_lexeme": f"{70 + lead:3d}",
            "utc_raw_lexeme": f"{(valid // HOUR) % 24:3d}",
            "response_id": 123,
            "response_sha256": "a" * 64,
            "response_record_sha256": "b" * 64,
            "card_sha256": "c" * 64,
            "card_global_offset": 100,
            "field_line_offset": 200,
            "cell_offset": 200 + 3 * lead,
            "utc_cell_offset": 150 + 3 * lead,
            "station_id": case["station_id"],
            "run_ms": run,
            "conditional_eligible_at_ms": run,
            "conditional_eligible_at": stamp,
            "object_last_modified": stamp,
            "object_last_modified_ms": run,
            "actual_received_at": "2026-09-06T19:00:00.123456+00:00",
            "historical_public_availability_verified": False,
        }
        cells.append(cell)
        future[lead - 2] = 70 + lead
    return {
        **{
            key: case[key]
            for key in ("case_id", "station_id", "split", "decision_ms", "target_ms", "horizon_hours")
        },
        "run_ms": run,
        "required_leads": list(range(2, case["horizon_hours"] + 3)),
        "cells": cells,
        "future_tmp_f": future,
        "available": True,
        "reason": None,
        "historical_public_availability_verified": False,
    }


class NoTemperature(dict):
    def __getitem__(self, key):
        if key == "temperature_f":
            raise AssertionError("A future target temperature was consulted")
        return super().__getitem__(key)


def histories(case):
    rows = []
    for i in range(169):
        at = case["decision_ms"] - (168 - i) * HOUR
        dt = datetime.fromtimestamp(at / 1000, UTC)
        row = {
            "station_id": case["station_id"],
            "observed_at": dt.isoformat(),
            "station_timezone": "UTC",
            "local_date": dt.date().isoformat(),
            "local_hour": dt.hour,
            "temperature_f": 70 + np.sin(i / 5),
            "status": "settled",
            "source_record_id": i + 1,
            "received_at": "2026-09-06T19:00:00.123456+00:00",
        }
        rows.append(row if at < case["decision_ms"] else NoTemperature(row))
    return rows


def test_history_is_gated_before_numeric_reads_and_keeps_missing_last_slot():
    cases, _, _, _, _, parent = panel_fixture()
    case = next(c for c in cases if c["split"] == "development")
    rows = histories(case)
    rows = [
        row
        for row in rows
        if datetime.fromisoformat(row["observed_at"]).timestamp() * 1000 != case["decision_ms"] - HOUR
    ]
    prepared = HistoryStore(rows).prepare(case, parent)
    assert prepared["context_end_ms"] == case["decision_ms"] - HOUR
    assert prepared["last_input_ms"] == case["decision_ms"] - 2 * HOUR
    assert prepared["history_values_f"][-1] is None
    assert prepared["forecast_steps_from_grid_end"] == case["horizon_hours"] + 1
    guided = validate_trajectory(case, trajectory(case))
    baseline, _ = build_input(prepared, guided, UNIVARIATE)
    item, meta = build_input(prepared, guided, GUIDED)
    np.testing.assert_array_equal(item["target"], baseline["target"])
    np.testing.assert_array_equal(item["past_covariates"][KEY], item["target"])
    assert meta["uses_guidance"] and meta["fallback_reason"] is None
    assert np.isnan(item["future_covariates"][KEY][meta["selected_step"] :]).all()


@pytest.mark.parametrize("horizon,step", [(1, 2), (3, 4), (6, 7)])
def test_exact_trajectory_target_and_tail_mask(horizon, step):
    cases, _, _, _, _, parent = panel_fixture()
    case = next(c for c in cases if c["split"] == "development" and c["horizon_hours"] == horizon)
    path = validate_trajectory(case, trajectory(case))
    prepared = HistoryStore(histories(case)).prepare(case, parent)
    item, meta = build_input(prepared, path, GUIDED)
    assert meta["selected_step"] == step
    assert item["future_covariates"][KEY][step - 1] == 70 + horizon + 2
    path["future_tmp_f"][-1] = 999 if horizon < 6 else None
    with pytest.raises(ValueError):
        validate_trajectory(case, path)


class NoTMP(dict):
    def __getitem__(self, key):
        if key in ("tmp_f", "raw_lexeme"):
            raise AssertionError("TMP values read before every cell publication gate")
        return super().__getitem__(key)


@pytest.mark.parametrize(
    "change", [{"station_id": "KWRONG"}, {"run_ms": 0}, {"conditional_eligible_at_ms": 10**15}]
)
def test_all_cell_metadata_is_checked_before_guidance_values(change):
    cases, *_ = panel_fixture()
    case = cases[0]
    path = trajectory(case)
    path["cells"] = [NoTMP(cell) for cell in path["cells"]]
    path["cells"][-1].update(change)
    with pytest.raises(ValueError):
        validate_trajectory(case, path)


def test_missing_or_failed_sources_retain_exact_univariate_schema():
    cases, _, _, _, _, parent = panel_fixture()
    case = cases[0]
    path = trajectory(case)
    path["cells"][0].update(tmp_f=None, raw_lexeme="-99", missing_reason="missing_tmp")
    path["future_tmp_f"][0] = None
    path.update(available=False, reason="missing_required_tmp_cells")
    validate_trajectory(case, path)
    prepared = HistoryStore(histories(case)).prepare(case, parent)
    baseline, _ = build_input(prepared, path, UNIVARIATE)
    item, meta = build_input(prepared, path, GUIDED)
    assert set(item) == {"target"} and meta["fallback_reason"] == "missing_required_tmp_cells"
    np.testing.assert_array_equal(item["target"], baseline["target"])
    failed = {**path, "reason": "original_object_failed", "cells": [], "future_tmp_f": [None] * 7}
    assert validate_trajectory(case, failed) == failed
    failed["future_tmp_f"][0] = 70
    with pytest.raises(ValueError, match="Failed source"):
        validate_trajectory(case, failed)


def test_fixed_integrity_selection_uses_later_origins_and_reassembled_case_ids():
    cases, *_ = panel_fixture()
    first, later = integrity_selection(list(reversed(cases)), {c["case_id"] for c in cases})
    assert len(first) == len(later) == 4
    assert max(c["decision_ms"] for c in first) < min(c["decision_ms"] for c in later)
    rows = [
        {"case_id": c["case_id"], "all_quantiles_f": [[float(i)] * 7 for _ in LEVELS]}
        for i, c in enumerate(first + later)
    ]
    ids = [r["case_id"] for r in rows]
    assert compare_outputs(rows, list(reversed(rows)), ids, 0) == 0
    changed = copy.deepcopy(rows)
    changed[0]["all_quantiles_f"][0][0] += 0.1
    with pytest.raises(ValueError, match="invariance failed"):
        compare_outputs(rows, changed, ids, 1e-5)


@pytest.mark.parametrize("failed_role", [None, "repeat", "appended", "permuted"])
def test_integrity_calls_survive_comparison_failure_in_real_archive(tmp_path, failed_role):
    cases = [{"case_id": f"synthetic-{index}"} for index in range(8)]
    roles = ["first", "repeat", "appended", "permuted"]
    calls = []

    def output(selected):
        return [
            {"case_id": case["case_id"], "all_quantiles_f": [[float(index)] * 7 for _ in LEVELS]}
            for case in selected
            for index in [int(case["case_id"].split("-")[-1])]
        ]

    def inputs(selected, variant):
        assert variant == GUIDED
        return selected, [{**case, "context_sha256": "a" * 64} for case in selected]

    def synthetic_prediction(selected, metadata):
        assert [row["case_id"] for row in selected] == [row["case_id"] for row in metadata]
        role = roles[len(calls)]
        calls.append(role)
        predictions = output(selected)
        if role == failed_role:
            predictions[0]["all_quantiles_f"][0][0] += 0.1
        return predictions

    archive = Archive(tmp_path / "synthetic_archive")
    try:
        arguments = (
            archive,
            123,
            GUIDED,
            cases[:4],
            cases[4:],
            output(cases),
            inputs,
            synthetic_prediction,
            "b" * 64,
        )
        if failed_role is None:
            result = integrity_checks(*arguments)
            assert list(result["artifact_record_ids"]) == roles
        else:
            with pytest.raises(ValueError, match="invariance failed"):
                integrity_checks(*arguments)
        expected_roles = roles[:2] if failed_role == "repeat" else roles
        assert calls == expected_roles
        records = list(archive.db.execute("SELECT * FROM records ORDER BY id"))
        assert len(records) == len(expected_roles)
        total_tasks = 0
        for record, role in zip(records, expected_roles, strict=True):
            assert record["kind"] == "e031_model_integrity_gzip"
            assert record["key"] == f"123:{GUIDED}:{role}"
            retained = json.loads(gzip.decompress(archive.body(record)))
            selected = cases[:4] if role in ("first", "repeat") else cases
            if role == "permuted":
                selected = list(reversed(selected))
            expected = output(selected)
            if role == failed_role:
                expected[0]["all_quantiles_f"][0][0] += 0.1
            assert retained["predictions"] == expected
            assert retained["inputs"] == inputs(selected, GUIDED)[1]
            assert retained["registration_id"] == 123 and retained["role"] == role
            assert not retained["comparison_started"] and not retained["labels_supplied"]
            assert retained["parameter_sha256"] == "b" * 64
            assert retained["tasks"] == len(selected)
            total_tasks += retained["tasks"]
        assert total_tasks == (8 if failed_role == "repeat" else 24)
        assert archive.verify()["records_verified"] == len(expected_roles)
    finally:
        archive.close()


def test_registered_resource_and_reference_gates_cannot_be_weakened():
    value = config()
    validate_config(value, False)
    with pytest.raises(ValueError, match="completed registered extraction"):
        validate_config({**value, "trajectory_registration_id": None, "trajectory_record_id": None})
    for key, new in [("references", REFERENCES[:-1]), ("cross_learning", True), ("prediction_length", 6)]:
        with pytest.raises(ValueError, match="fixed family"):
            validate_config({**value, key: new}, False)
    modified = copy.deepcopy(value)
    modified["budgets"]["integrity_tasks"] = 19740
    with pytest.raises(ValueError):
        validate_config(modified, False)


def scoring_fixture():
    cases, _, _base, observations, _, parent = panel_fixture()
    cal = [case for case in cases if case["split"] == "calibration"]
    day_cases = [
        case
        for case in cases
        if case["split"] == "development"
        and datetime.fromtimestamp(case["target_ms"] / 1000, UTC).date() == date(2026, 7, 20)
    ]
    development = []
    for offset in range(28):
        for original in day_cases:
            case = {
                **original,
                "target_ms": original["target_ms"] + offset * 24 * HOUR,
                "decision_ms": original["decision_ms"] + offset * 24 * HOUR,
            }
            case["case_id"] = f"{case['station_id']}:{case['target_ms']}:{case['horizon_hours']}"
            development.append(case)
            observations[case["station_id"], case["target_ms"]] = {
                "temperature_f": 70 + (offset % 4),
                "status": "settled",
                "source_record_ids": [123],
            }
    cases = cal + development

    def predictions(point):
        return [{"case_id": case["case_id"], "point_f": point, "quantiles_f": [point] * 13} for case in cases]

    neural = {UNIVARIATE: predictions(74), GUIDED: predictions(72)}
    refs = {ref: predictions(70) for ref in REFERENCES}
    return cases, neural, refs, observations, parent


def test_all_five_references_and_daily_pinball_family_retained():
    cases, neural, refs, observations, parent = scoring_fixture()
    result = score_all(cases, neural, refs, observations, parent, config(), len(cases))
    assert len(result["candidates"]) == 6 and len(result["comparisons"]) == 5
    assert [row["reference"] for row in result["comparisons"]] == [UNIVARIATE, *REFERENCES]
    assert len(result["bootstrap"]["intervals"]) == 5
    assert not result["advance_to_prospective_research_only"]
    assert result["independent_score_max_error"] < 1e-10
    assert all(len(c["daily_bias"]) == 28 for c in result["candidates"])
    # With fixed constant raw boundaries, calibration offsets reproduce the same boundaries.
    assert all(abs(c["pinball_difference_f"]) < 1e-10 for c in result["comparisons"])
    assert not result["promotion_eligible"]


def test_shared_bootstrap_rejects_calendar_gaps_and_hyperparameter_changes():
    days = [(date(2026, 7, 20) + timedelta(days=i)).isoformat() for i in range(28)]
    values = np.arange(28)[:, None] * np.arange(5)[None, :] / 100
    result = shared_intervals(values, days, BOOTSTRAP)
    assert result == shared_intervals(values, days, BOOTSTRAP)
    assert result["intervals"][0] is None and result["active"] == [False, True, True, True, True]
    with pytest.raises(ValueError, match="calendar"):
        shared_intervals(values, days[:-1], BOOTSTRAP)
    with pytest.raises(ValueError, match="changed"):
        shared_intervals(values, days, {**BOOTSTRAP, "seed": 9})


def test_real_process_supervisor_retains_success_and_failure_without_retry(tmp_path):
    result = supervise(
        [sys.executable, "-c", "import sys; print('output'); print('error', file=sys.stderr)"],
        str(tmp_path),
        tmp_path / "good",
        5,
        128 * 1024 * 1024,
        tmp_path / "STOP",
    )
    assert result["status"] == "completed"
    assert (tmp_path / "good/stdout.log").read_text() == "output\n"
    assert (tmp_path / "good/stderr.log").read_text() == "error\n"
    with pytest.raises(FileExistsError):
        supervise(
            [sys.executable, "-c", "pass"],
            str(tmp_path),
            tmp_path / "good",
            5,
            128 * 1024 * 1024,
            tmp_path / "STOP",
        )
    failed = supervise(
        [sys.executable, "-c", "raise ValueError('retained failure')"],
        str(tmp_path),
        tmp_path / "bad",
        5,
        128 * 1024 * 1024,
        tmp_path / "STOP",
    )
    assert failed["status"] == "failed" and failed["reason"] == "nonzero_exit"
    assert "retained failure" in (tmp_path / "bad/stderr.log").read_text()


def test_real_process_supervisor_stops_timeout_and_memory(tmp_path):
    timeout = supervise(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        str(tmp_path),
        tmp_path / "timeout",
        0.3,
        128 * 1024 * 1024,
        tmp_path / "STOP",
    )
    assert timeout["status"] == "failed" and timeout["reason"] == "wall_deadline"
    assert timeout["elapsed_seconds"] < 4 and timeout["process_group_termination_sent"]
    memory = supervise(
        [
            sys.executable,
            "-c",
            'import time; x=bytearray(64*1024*1024); print("allocated",flush=True); time.sleep(20)',
        ],
        str(tmp_path),
        tmp_path / "memory",
        5,
        32 * 1024 * 1024,
        tmp_path / "STOP",
    )
    assert memory["status"] == "failed" and memory["reason"] == "rss_limit"
    assert memory["maximum_sampled_rss_bytes"] > 32 * 1024 * 1024
    with pytest.raises(ProcessLookupError):
        os.kill(memory["pid"], 0)


def test_gain_gate_requires_every_blend_and_ninety_percent_guidance():
    comparisons = [
        {"reference": name, "relative_pinball_improvement": 0.05} for name in [UNIVARIATE, *REFERENCES]
    ]
    assert research_gain_gate(comparisons, 90, 100)
    assert not research_gain_gate(comparisons, 89, 100)
    comparisons[-1]["relative_pinball_improvement"] = 0.049
    assert not research_gain_gate(comparisons, 100, 100)
    with pytest.raises(ValueError, match="five fixed"):
        research_gain_gate(comparisons[:-1], 100, 100)
