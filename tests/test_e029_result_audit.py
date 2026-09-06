"""Only synthetic raw forecasts and arithmetic, never real E029 result bodies."""

import copy
from datetime import date, timedelta

import numpy as np
import pytest

from research.experiments.e026_result_audit import exact_tree
from research.experiments.e029_fixed_combinations import (
    BOOTSTRAP,
    prediction_panel,
    score_panel,
    shared_intervals,
)
from research.experiments.e029_result_audit import (
    bootstrap_replay,
    check_chronology,
    exact_raw_panel,
    reconstruct_panel,
    verify_scores,
)
from tests.test_e029_fixed_combinations import fixture


def test_broadcast_reconstructs_all_point_and_thirteen_quantiles_exactly():
    cases, physical, predictions, _, config, parent = fixture()
    # Non-integer values exercise floating operation order, not only easy integer sums.
    predictions["chronos_pretrained"][0]["point_f"] = 81.234567891234
    predictions["chronos_pretrained"][0]["quantiles_f"] = [71.123456789 + i * 0.876543219 for i in range(13)]
    expected = prediction_panel(cases, physical, predictions, config, parent)
    independently = reconstruct_panel(cases, physical, predictions, config)
    assert exact_raw_panel(expected, independently)
    assert len(independently["candidate_order"]) == 12
    for weight, model in [(0.25, "nbh_chronos_w025"), (0.5, "nbh_chronos_w050"), (0.75, "nbh_chronos_w075")]:
        row = independently["predictions"][model][1]
        assert row["point_f"] == 70 + weight * 10
        assert row["quantiles_f"] == [70 + weight * (4 + i) for i in range(13)]
    assert independently["labels_accessed"] is False and independently["calibration_applied"] is False


@pytest.mark.parametrize("mutation", ["point", "quantile", "lost_case", "calibrated", "weight"])
def test_raw_prediction_corruption_cannot_enter_label_scoring(mutation):
    cases, physical, predictions, _, config, _ = fixture()
    expected = reconstruct_panel(cases, physical, predictions, config)
    changed = copy.deepcopy(expected)
    rows = changed["predictions"]["nbh_chronos_w050"]
    if mutation == "point":
        rows[0]["point_f"] += 1e-12
    elif mutation == "quantile":
        rows[-1]["quantiles_f"][12] += 1e-12
    elif mutation == "lost_case":
        rows.pop()
    elif mutation == "calibrated":
        changed["calibration_applied"] = True
    else:
        changed["formula"] = "fit-selected weight"
    with pytest.raises(ValueError, match="raw blends/panel differ"):
        exact_raw_panel(changed, expected)


def test_all_twelve_calibration_daily_aggregate_and_contrast_values_reproduce():
    cases, physical, predictions, observations, config, parent = fixture()
    panel = reconstruct_panel(cases, physical, predictions, config)
    result = score_panel(panel, observations, config, parent)
    originals = {row["id"]: row["evaluation"] for row in result["candidates"][:9]}
    audited = verify_scores(result, panel, observations, config, parent, originals)
    assert audited["audited_models"] == 12 and audited["audited_comparisons"] == 12
    assert audited["original_models_reproduced"] == 9
    assert audited["maximum_arithmetic_error"] < 1e-10
    result["comparisons"].pop()
    with pytest.raises(ValueError, match="Missing/reordered fixed contrast"):
        verify_scores(result, panel, observations, config, parent, originals)


@pytest.mark.parametrize("field", ["calibration_offsets_f", "daily", "metrics"])
def test_changed_scored_layer_rejected(field):
    cases, physical, predictions, observations, config, parent = fixture()
    panel = reconstruct_panel(cases, physical, predictions, config)
    result = score_panel(panel, observations, config, parent)
    originals = {row["id"]: copy.deepcopy(row["evaluation"]) for row in result["candidates"][:9]}
    evaluation = result["candidates"][-1]["evaluation"]
    if field == "calibration_offsets_f":
        evaluation[field]["1"][0] += 0.1
    elif field == "daily":
        evaluation[field][0]["absolute_error_f"] += 0.1
    else:
        evaluation[field]["mae_f"] += 0.1
    with pytest.raises(ValueError):
        verify_scores(result, panel, observations, config, parent, originals)


def test_missing_physical_case_preserved_for_every_candidate():
    cases, physical, predictions, observations, config, parent = fixture()
    omitted = next(row for row in physical if row["split"] == "development")
    omitted.update(available=False, point_f=None, reason="absent_tmp")
    panel = reconstruct_panel(cases, physical, predictions, config)
    assert exact_raw_panel(prediction_panel(cases, physical, predictions, config, parent), panel)
    result = score_panel(panel, observations, config, parent)
    audit = verify_scores(result, panel, observations, config, parent, {})
    assert audit["original_models_reproduced"] == 0
    assert panel["missing"] == [{"case_id": omitted["case_id"], "reason": "absent_tmp"}]
    assert len({len(rows) for rows in panel["predictions"].values()}) == 1


def bootstrap_fixture():
    matrix = np.arange(28)[:, None] * np.arange(12)[None, :] / 100
    days = [(date(2026, 7, 20) + timedelta(days=i)).isoformat() for i in range(28)]
    return matrix, days, BOOTSTRAP


def test_independent_twelve_contrast_bootstrap_replays_both_hashes_and_bounds():
    data = bootstrap_fixture()
    reported = shared_intervals(*data)
    expected = bootstrap_replay(*data)
    assert exact_tree(reported, expected) == 0.0
    assert expected["active"] == [False] + [True] * 11
    assert expected["intervals"][0] is None
    assert len(expected["intervals"]) == 12


@pytest.mark.parametrize("field", ["means", "indices_sha256", "resampled_means_sha256"])
def test_bootstrap_mean_or_draw_tampering_rejected(field):
    data = bootstrap_fixture()
    reported, expected = shared_intervals(*data), bootstrap_replay(*data)
    if field == "means":
        reported[field][1] += 0.1
    else:
        reported[field] = "0" * 64
    with pytest.raises(ValueError):
        exact_tree(reported, expected)


def test_prediction_record_must_precede_report_in_ids_and_receipt_times():
    rows = [{"id": i, "available_at": f"2026-09-06T20:00:0{i}Z"} for i in range(1, 5)]
    check_chronology(*rows)
    for bad in [
        {**rows[2], "id": 5},
        {**rows[2], "available_at": "2026-09-06T20:00:05Z"},
        {**rows[2], "id": 2},
    ]:
        with pytest.raises(ValueError, match="Prediction was not archived"):
            check_chronology(rows[0], rows[1], bad, rows[3])
