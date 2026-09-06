import copy
import gzip
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from research.experiments.e029_fixed_combinations import (
    BOOTSTRAP,
    HYBRIDS,
    LEVELS,
    MODELS,
    REFERENCES,
    archive_panel,
    attempt_census,
    fixed_configuration,
    prediction_panel,
    score_panel,
    shared_intervals,
)
from tests.test_e026_nbh_comparison import NoLabels, panel_fixture
from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import utcnow


def fixture():
    cases, physical, predictions, observations, _, parent = panel_fixture()
    config = json.loads(Path("config/e029_fixed_combinations.json").read_bytes())
    for row in predictions["chronos_pretrained"]:
        row["point_f"] = 80.0
        row["quantiles_f"] = [74.0 + i for i in range(13)]
    return cases, physical, predictions, observations, config, parent


def test_fixed_raw_quantile_formula_and_all_twelve_share_exact_grid():
    cases, physical, predictions, observations, config, parent = fixture()
    panel = prediction_panel(cases, physical, predictions, config, parent)
    assert panel["candidate_order"] == [*MODELS, "nbh_original", *[h["id"] for h in HYBRIDS]]
    assert panel["predictions"]["nbh_original"][0]["quantiles_f"] == [70.0] * 13
    for hybrid in HYBRIDS:
        row = panel["predictions"][hybrid["id"]][0]
        w = hybrid["neural_weight"]
        assert row["point_f"] == (1 - w) * 70 + w * 80
        assert row["quantiles_f"] == [(1 - w) * 70 + w * (74 + i) for i in range(13)]
    # Archives sort object keys; mathematical evaluation must survive canonical serialization.
    panel = json.loads(canonical(panel))
    result = score_panel(panel, observations, config, parent)
    assert len(result["candidates"]) == len(result["comparisons"]) == 12
    assert [(r["hybrid"], r["reference"]) for r in result["comparisons"]] == [
        (h["id"], ref) for h in HYBRIDS for ref in REFERENCES
    ]
    assert {r["evaluation"]["development_cases"] for r in result["candidates"]} == {24}
    assert result["independent_score_max_error"] < 1e-10
    assert result["bootstrap"]["status"] == "unsupported_missing_calendar_days"
    assert panel["labels_accessed"] is False and panel["calibration_applied"] is False


def test_missing_case_drops_from_every_candidate_but_original_scores_are_not_reused():
    cases, physical, predictions, observations, config, parent = fixture()
    omitted = next(case["case_id"] for case in cases if case["split"] == "development")
    next(row for row in physical if row["case_id"] == omitted).update(
        available=False, point_f=None, reason="missing_tmp"
    )
    panel = prediction_panel(cases, physical, predictions, config, parent)
    assert not panel["full_panel_complete"]
    assert panel["classification"] == "common_available_subset_only"
    assert panel["missing"] == [{"case_id": omitted, "reason": "missing_tmp"}]
    assert all(omitted not in {r["case_id"] for r in rows} for rows in panel["predictions"].values())
    scored = score_panel(panel, observations, config, parent)
    assert {c["evaluation"]["development_cases"] for c in scored["candidates"]} == {23}


def test_development_targets_cannot_change_calibration_and_missing_support_reads_no_labels():
    cases, physical, predictions, observations, config, parent = fixture()
    panel = prediction_panel(cases, physical, predictions, config, parent)
    original = score_panel(panel, observations, config, parent)
    altered = copy.deepcopy(observations)
    for case in cases:
        if case["split"] == "development":
            altered[case["station_id"], case["target_ms"]]["temperature_f"] = 99.0
    other = score_panel(panel, altered, config, parent)
    assert [r["evaluation"]["calibration_offsets_f"] for r in original["candidates"]] == [
        r["evaluation"]["calibration_offsets_f"] for r in other["candidates"]
    ]
    assert (
        original["candidates"][-1]["evaluation"]["metrics"]
        != other["candidates"][-1]["evaluation"]["metrics"]
    )
    cal_ids = {c["case_id"] for c in cases if c["split"] == "calibration"}
    for row in physical:
        if row["case_id"] in cal_ids:
            row.update(available=False, point_f=None, reason="absent_object")
    limited = prediction_panel(cases, physical, predictions, config, parent)
    assert score_panel(limited, NoLabels(), config, parent)["status"] == "insufficient_common_calibration"


@pytest.mark.parametrize(
    "field", ["station_id", "target_ms", "decision_ms", "split", "run_ms", "forecast_hour", "key"]
)
def test_physical_binding_rejected_before_hybrid_arithmetic(field):
    cases, physical, _predictions, _, config, parent = fixture()
    old = physical[0][field]
    physical[0][field] = old + 1 if isinstance(old, int) else "bad"
    # No prediction payload is supplied: binding must fail before accessing forecast arrays.
    with pytest.raises(ValueError, match="binding differs from original"):
        prediction_panel(cases, physical, {}, config, parent)


@pytest.mark.parametrize(
    "mutation", ["duplicate", "nonfinite", "lost_quantile", "wrong_candidate", "wrong_weight"]
)
def test_candidate_and_hyperparameter_corruption_fails_before_labels(mutation):
    cases, physical, predictions, _, config, parent = fixture()
    if mutation == "duplicate":
        predictions[MODELS[0]][-1] = copy.deepcopy(predictions[MODELS[0]][0])
    elif mutation == "nonfinite":
        predictions[MODELS[0]][0]["quantiles_f"][0] = float("nan")
    elif mutation == "lost_quantile":
        predictions[MODELS[0]][0]["quantiles_f"].pop()
    elif mutation == "wrong_candidate":
        predictions.pop(MODELS[-1])
    else:
        config["hybrids"][0]["neural_weight"] = 0.2
    with pytest.raises(ValueError):
        prediction_panel(cases, physical, predictions, config, parent)


def test_shared_twelve_comparison_bootstrap_has_one_calendar_and_degenerate_intervals():
    days = [(date(2026, 7, 20) + timedelta(days=i)).isoformat() for i in range(28)]
    x = np.arange(28)[:, None] * np.arange(12)[None, :] / 100
    result = shared_intervals(x, days, BOOTSTRAP)
    assert result == shared_intervals(x, days, BOOTSTRAP)
    assert result["active"] == [False] + [True] * 11
    assert result["intervals"][0] is None
    np.testing.assert_allclose(
        np.asarray(result["standard_errors"])[1:], np.arange(1, 12) * result["standard_errors"][1], rtol=1e-12
    )
    rng = np.random.default_rng(6202901)
    starts = rng.integers(0, 28, size=(10000, 4))
    indices = ((starts[:, :, None] + np.arange(7)) % 28).reshape(-1, 28)
    samples = x[indices].mean(axis=1)
    errors = samples.std(axis=0, ddof=1)
    statistic = np.max(np.abs(samples[:, 1:] - x.mean(axis=0)[1:]) / errors[1:], axis=1)
    assert result["critical_value"] == float(np.quantile(statistic, 0.95, method="higher"))
    with pytest.raises(ValueError, match="matrix"):
        shared_intervals(x[:, :11], days, BOOTSTRAP)
    with pytest.raises(ValueError, match="fixed twelve"):
        shared_intervals(x, days, {**BOOTSTRAP, "seed": 1})
    assert shared_intervals(x, days[:-1], BOOTSTRAP)["status"] == "unsupported_missing_calendar_days"


def test_exact_attempt_census_precedes_any_physical_body_read(tmp_path):
    archive = Archive(tmp_path)
    try:
        acquisition = {"manifest": {"objects": [{"key": "a"}, {"key": "b"}]}}
        settings = {
            "acquisition_registration_id": 1,
            "acquisition_object_kind": "e025v2_object",
            "expected_objects": 2,
        }
        archive.append("fixture_protocol", "x", utcnow(), {}, b"{}")
        aid = archive.append("e025v2_object", "1:a", utcnow(), {}, b"not even parseable JSON")
        with pytest.raises(ValueError, match="Acquisition incomplete"):
            attempt_census(archive, acquisition, settings)
        bid = archive.append("e025v2_object", "1:b", utcnow(), {}, b"still not JSON")
        # Deliberately remove the blobs: the census must only inspect record metadata.
        for rid in [aid, bid]:
            sha = archive.db.execute("SELECT body_sha256 FROM records WHERE id=?", (rid,)).fetchone()[0]
            (archive.root / "blobs" / sha).unlink()
        assert [r["id"] for r in attempt_census(archive, acquisition, settings)] == [aid, bid]
        archive.append("e025v2_object", "1:b", utcnow(), {}, b"new duplicate attempt")
        with pytest.raises(ValueError, match="duplicated"):
            attempt_census(archive, acquisition, settings)
    finally:
        archive.close()


def test_prediction_artifact_keeps_exact_raw_arrays_before_later_labels(tmp_path):
    cases, physical, predictions, _, config, parent = fixture()
    panel = prediction_panel(cases, physical, predictions, config, parent)
    archive = Archive(tmp_path)
    try:
        reg = archive.append("fixture_protocol", "fixture", utcnow(), {}, b"{}")
        source = archive.append("fixture_binding", "fixture", utcnow(), {}, b"{}")
        rid = archive_panel(archive, reg, panel, [102, 101], source)
        row = archive.db.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
        retained = json.loads(gzip.decompress(archive.body(row)))
        assert retained["panel"] == panel
        assert retained["raw_source_ids"] == [101, 102]
        assert retained["labels_accessed"] is False and retained["weather_scores_computed"] == 0
        later = archive.append("fixture_score", "fixture", utcnow(), {}, b"{}")
        assert reg < source < rid < later
    finally:
        archive.close()


def test_registered_fixed_controls_cannot_expand_family_or_budget():
    config = json.loads(Path("config/e029_fixed_combinations.json").read_bytes())
    fixed_configuration(config)
    assert config["quantile_levels"] == LEVELS
    for key, value in [
        ("execution_budget_seconds", 601),
        ("attempts", 2),
        ("network_requests", 1),
        ("reference_ids", REFERENCES[:-1]),
        ("model_ids", MODELS[:-1]),
    ]:
        with pytest.raises(ValueError, match="fixed E029"):
            fixed_configuration({**config, key: value})
