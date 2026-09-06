"""Synthetic arithmetic and provenance tests; no actual forecast result reads."""

import copy
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from research.experiments.e026_nbh_comparison import (
    case_binding,
    compare_panel,
    read_record,
    shared_intervals,
)
from research.experiments.e026_result_audit import (
    assert_pin,
    bootstrap_replay,
    exact_tree,
    pin,
    verify_result,
)
from weatherpred.archive import Archive


def panel_fixture():
    config = json.loads(Path("config/e026_nbh_comparison.json").read_bytes())
    parent = json.loads(Path(config["parent_config_path"]).read_bytes())
    cases, physical, observations = [], [], {}
    for split, days in (
        ("calibration", [date(2026, 7, 6) + timedelta(days=i) for i in range(8)]),
        ("development", [date(2026, 7, 20), date(2026, 7, 21)]),
    ):
        for day in days:
            for hour in (6, 12):
                at = int(datetime(day.year, day.month, day.day, hour, tzinfo=UTC).timestamp() * 1000)
                for station in ("KMIA", "KNYC"):
                    observations[station, at] = {
                        "temperature_f": 70 + hour / 10,
                        "status": "settled",
                        "source_record_ids": [123],
                    }
                    for horizon in (1, 3, 6):
                        identity = f"{station}:{at}:{horizon}"
                        case = {
                            "case_id": identity,
                            "station_id": station,
                            "target_ms": at,
                            "decision_ms": at - horizon * 3600000,
                            "horizon_hours": horizon,
                            "eligible": True,
                            "split": split,
                            "target_source_record_ids": [123],
                        }
                        cases.append(case)
                        physical.append(
                            {**case_binding(case), "available": True, "point_f": 70.0, "reason": None}
                        )
    predictions = {
        model: [
            {"case_id": c["case_id"], "point_f": 69.0 + i / 10, "quantiles_f": [69.0 + i / 10] * 13}
            for c in cases
        ]
        for i, model in enumerate(config["model_ids"])
    }
    return cases, physical, predictions, observations, config, parent


def test_all_nine_models_reproduce_each_calibration_daily_and_aggregate_field():
    data = panel_fixture()
    result = compare_panel(*data)
    old = {
        "candidates": [
            {"candidate": {"id": row["id"]}, "evaluation": row["evaluation"]}
            for row in result["candidates"][:-1]
        ]
    }
    audit = verify_result(result, *data, old)
    assert audit["audited_models"] == 9 and audit["audited_comparisons"] == 8
    assert audit["original_e022_models_reproduced"] == 8
    assert audit["maximum_arithmetic_error"] < 1e-10
    assert audit["bootstrap_status"] == "unsupported_missing_calendar_days"
    result["comparisons"].pop()
    with pytest.raises(ValueError, match="Missing or reordered comparison"):
        verify_result(result, *data, old)


@pytest.mark.parametrize("part", ["daily", "metrics", "calibration_offsets_f"])
def test_changed_score_in_any_of_three_layers_rejected(part):
    data = panel_fixture()
    result = compare_panel(*data)
    old = {
        "candidates": [
            {"candidate": {"id": row["id"]}, "evaluation": copy.deepcopy(row["evaluation"])}
            for row in result["candidates"][:-1]
        ]
    }
    evaluation = result["candidates"][0]["evaluation"]
    if part == "daily":
        evaluation[part][0]["absolute_error_f"] += 0.01
    elif part == "metrics":
        evaluation[part]["mae_f"] += 0.01
    else:
        evaluation[part]["1"][0] += 0.01
    with pytest.raises(ValueError):
        verify_result(result, *data, old)


def test_missing_physical_case_restricts_all_nine_models_and_preserves_reason():
    data = panel_fixture()
    case = next(row for row in data[0] if row["split"] == "development")
    next(row for row in data[1] if row["case_id"] == case["case_id"]).update(
        available=False, point_f=None, reason="synthetic_missing"
    )
    result = compare_panel(*data)
    audit = verify_result(result, *data, {"candidates": []})
    assert audit["audited_models"] == 9
    assert audit["full_panel_complete"] is False and audit["original_e022_models_reproduced"] == 0
    assert audit["missing"] == [{"case_id": case["case_id"], "reason": "synthetic_missing"}]
    result["missing"] = []
    with pytest.raises(ValueError, match="Array length changed"):
        verify_result(result, *data, {"candidates": []})


def bootstrap_fixture():
    days = [(date(2026, 7, 20) + timedelta(days=i)).isoformat() for i in range(28)]
    settings = {
        "block_days": 7,
        "resamples": 10000,
        "seed": 6202601,
        "confidence": 0.95,
        "comparison_count": 8,
    }
    matrix = [[(i % (j + 2)) * 0.17 - j * 0.3 for j in range(8)] for i in range(28)]
    return matrix, days, settings


def test_independent_bootstrap_replays_exact_draw_and_sample_hashes():
    fixture = bootstrap_fixture()
    expected = shared_intervals(*fixture)
    audited = bootstrap_replay(*fixture)
    assert exact_tree(expected, audited) == 0.0
    assert len(audited["intervals"]) == 8
    assert all(audited["active"])
    assert audited["indices_sha256"] == expected["indices_sha256"]
    assert audited["resampled_means_sha256"] == expected["resampled_means_sha256"]


@pytest.mark.parametrize("field", ["means", "indices_sha256", "resampled_means_sha256", "intervals"])
def test_tampered_bootstrap_means_draws_or_intervals_rejected(field):
    fixture = bootstrap_fixture()
    reported = shared_intervals(*fixture)
    expected = bootstrap_replay(*fixture)
    if field == "means":
        reported[field][0] += 0.01
    elif field == "intervals":
        reported[field][0][0] -= 0.01
    else:
        reported[field] = "0" * 64
    with pytest.raises(ValueError):
        exact_tree(reported, expected, "bootstrap")


def test_bootstrap_missing_comparison_and_missing_day_are_not_hidden():
    matrix, days, settings = bootstrap_fixture()
    with pytest.raises(ValueError, match="Missing comparison"):
        bootstrap_replay([row[:-1] for row in matrix], days, settings)
    result = bootstrap_replay(matrix[:-1], days[:-1], settings)
    assert result == {"status": "unsupported_missing_calendar_days", "days": days[:-1]}
    result = bootstrap_replay([[0.0] * 8 for _ in days], days, settings)
    assert result["means"] == [0.0] * 8
    assert result["standard_errors"] == [0.0] * 8
    assert result["intervals"] == [None] * 8
    assert result["critical_value"] == 0.0


def test_report_source_links_require_exact_record_hash_identity_and_receipt(tmp_path):
    archive = Archive(tmp_path)
    try:
        identifier = archive.append("e026_started", "117527", "2026-09-06T20:00:00Z", {}, b"{}")
        row, _ = read_record(archive, identifier, "e026_started")
        expected = pin(row)
        assert_pin(row, expected)
        for field, bad in [
            ("id", identifier + 1),
            ("key", "different"),
            ("body_sha256", "0" * 64),
            ("record_sha256", "0" * 64),
            ("available_at", "2026-07-01T00:00:00Z"),
        ]:
            with pytest.raises(ValueError, match="Pinned source/report link changed"):
                assert_pin(row, {**expected, field: bad})
    finally:
        archive.close()
