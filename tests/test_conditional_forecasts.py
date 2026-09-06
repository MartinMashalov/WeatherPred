from copy import deepcopy
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import numpy as np
import pytest

from research.experiments.e018_conditional_hourly import paired_prediction
from weatherpred.archive import Archive, canonical
from weatherpred.conditional_forecasts import (
    DAY,
    MINUTE,
    design,
    fit_model,
    location,
    probability,
    select_and_fit,
)
from weatherpred.timeutil import iso


def example(day, hour, horizon=15):
    target = int(datetime(2026, 8, 20, tzinfo=UTC).timestamp() * 1000) + day * DAY + hour * 60 * MINUTE
    decision = target - horizon * MINUTE
    f = {
        "last_point_ms": decision - 5 * MINUTE,
        "first_point_ms": decision - 35 * MINUTE,
        "points": 31,
        "persistence": 80.0,
        "trend": 80 + 0.4 * np.sin(hour * np.pi / 12 + 0.3),
    }
    row = {
        "settlement_ms": target,
        "decision_ms": decision,
        "label_point_ms": target,
        "horizon_minutes": horizon,
        "features": f,
    }
    x = design(row, 1)
    row["observed"] = 80.2 + 0.65 * x[0] + 1.3 * x[1] - 0.4 * x[2] + 0.06 * np.sin(day * 2 + hour)
    return row


@pytest.mark.parametrize("kind", ["gaussian", "student_t5"])
def test_conditional_model_predicts_later_diurnal_cycle_and_coherent_thresholds(kind):
    rows = [example(d, h) for d in range(8) for h in range(24)]
    cutoff = example(8, 0)["settlement_ms"]
    candidate = {"harmonics": 1, "ridge": 1.0, "distribution": kind}
    model = fit_model(rows, 15, candidate, cutoff)
    future = [example(9, h) for h in range(24)]
    assert np.mean([(location(model, r) - r["observed"]) ** 2 for r in future]) < 0.02
    assert model["latest_label_deadline_ms"] < cutoff
    row = future[12]
    p = [probability(model, row, k) for k in (50, 78, 79, 80, 81, 82, 110)]
    assert all(a >= b for a, b in pairwise(p))
    assert p[0] > 0.999 and p[-1] < 0.001
    assert model["distribution_scale"] >= 0.05


def test_future_labels_and_future_features_are_rejected():
    rows = [example(d, h) for d in range(8) for h in range(24)]
    cutoff = example(8, 0)["settlement_ms"]
    candidate = {"harmonics": 1, "ridge": 1.0, "distribution": "gaussian"}
    with pytest.raises(ValueError, match="label unavailable"):
        fit_model([*rows, example(8, 1, horizon=30)], 15, candidate, cutoff)
    model = fit_model(rows, 15, candidate, cutoff)
    with pytest.raises(ValueError, match="fitted after"):
        location(model, rows[-1])
    future = example(9, 12)
    changed = deepcopy(future)
    changed["features"]["last_point_ms"] = changed["decision_ms"] + 1
    with pytest.raises(ValueError, match="Future, stale"):
        location(model, changed)
    changed = deepcopy(future)
    changed["observed"] = 200
    assert location(model, changed) == location(model, future)
    assert probability(model, changed, 80) == probability(model, future, 80)


def test_later_august_outcomes_cannot_change_earlier_fold_predictions():
    rows = [example(d, h, horizon) for d in range(12) for h in range(24) for horizon in (5, 15, 30)]
    start = example(0, 0)["settlement_ms"]
    config = {
        "training_start_ms": start,
        "training_end_ms": start + 12 * DAY,
        "fold_start_day_offsets": [4, 6, 8, 10],
        "fold_days": 2,
        "horizons_minutes": [5, 15, 30],
        "candidates": [{"id": "fixed", "harmonics": 1, "ridge": 10, "distribution": "gaussian"}],
    }
    original = select_and_fit(rows, config)
    changed = deepcopy(rows)
    for row in changed:
        if row["settlement_ms"] >= start + 10 * DAY:
            row["observed"] += 10
    altered = select_and_fit(changed, config)
    earlier = lambda result: [r for r in result["predictions"] if r["settlement_ms"] < start + 10 * DAY]
    assert earlier(original) == earlier(altered)
    assert original["candidate_scores"][0]["days"] == 8
    assert original["models"]["15"]["distribution_scale"] != altered["models"]["15"]["distribution_scale"]
    with pytest.raises(ValueError, match="outside registered August"):
        select_and_fit([*rows, example(12, 1)], config)


def test_forward_adapter_uses_original_receipts_and_rejects_late_model(tmp_path):
    archive = Archive(tmp_path)
    try:
        scheduled = datetime(2026, 9, 6, 16, 30, tzinfo=UTC)
        target = scheduled + timedelta(minutes=30)
        baseline_names = [
            "persistence_gaussian",
            "trend_gaussian",
            "persistence_empirical",
            "trend_empirical",
        ]
        baseline = {
            name + ":30": {
                "base": name.split("_")[0],
                "kind": name.split("_")[1],
                "residuals": [-1.0, 0, 1.0],
            }
            for name in baseline_names
        }
        append = lambda kind, key, at, body: archive.append(kind, key, at, {}, canonical(body).encode())
        parent_model = append("model_artifact", "original", scheduled - timedelta(hours=2), {})
        baseline_id = append(
            "model_artifact", "baseline", scheduled - timedelta(hours=2), {"models": baseline}
        )
        cfg = {
            "experiment": "E018-test",
            "parent_original_model_record_id": parent_model,
            "maximum_publication_lateness_seconds": 20,
            "models": ["conditional_selected", *["fresh_5m_" + n for n in baseline_names]],
            "limits": "test",
        }
        slot = {"decision_at": iso(scheduled), "settlement_at": iso(target), "horizon_minutes": 30}
        run_id = append("experiment_protocol", "test", scheduled - timedelta(hours=1), {"slots": [slot]})
        training = [example(d, h, 30) for d in range(8) for h in range(24)]
        candidate = {"id": "fixed", "harmonics": 1, "ridge": 10, "distribution": "gaussian"}
        model = fit_model(training, 30, candidate, example(8, 0)["settlement_ms"])
        artifact = {
            "run_record_id": run_id,
            "config": cfg,
            "models": {"30": model},
            "chosen_candidate": candidate,
            "baseline_model_record_id": baseline_id,
        }
        model_id = append("model_artifact", "conditional", scheduled - timedelta(minutes=10), artifact)
        ms = int(scheduled.timestamp() * 1000)
        points = [{"t": ms + i * MINUTE, "v": 80 + i / 100, "status": "normal"} for i in range(-60, 1)]
        source = append("shadow_index", "miami", scheduled + timedelta(seconds=1), {"timeseries": points})
        parent = {
            "protocol": "E004-forward-v1",
            "model_record_id": parent_model,
            "event": "test",
            "scheduled_at": iso(scheduled),
            "snapshot_at": iso(scheduled + timedelta(seconds=1)),
            "settlement_at": iso(target),
            "published_at": iso(scheduled + timedelta(seconds=2)),
            "horizon_minutes": 30,
            "evidence_record_ids": [source],
            "markets": [{"ticker": "test-T80", "floor_strike": 79.99}],
        }
        parent_id = append("shadow_forecast", "test", scheduled + timedelta(seconds=2), parent)
        read = lambda i: archive.db.execute("SELECT * FROM records WHERE id=?", (i,)).fetchone()
        prediction = paired_prediction(
            archive, read(parent_id), read(model_id), scheduled + timedelta(seconds=10)
        )
        assert prediction["features"]["persistence"] == 79.95
        assert len(prediction["markets"][0]["probabilities"]) == 5
        assert prediction["real_money_recommended_size"] == 0
        with pytest.raises(ValueError, match="timing or identity"):
            paired_prediction(archive, read(parent_id), read(model_id), scheduled + timedelta(seconds=21))
        with pytest.raises(ValueError, match="not available"):
            paired_prediction(archive, read(parent_id), read(model_id), scheduled + timedelta(seconds=1))
        late_id = append("model_artifact", "late", scheduled + timedelta(seconds=1), artifact)
        with pytest.raises(ValueError, match="timing or identity"):
            paired_prediction(archive, read(parent_id), read(late_id), scheduled + timedelta(seconds=10))
    finally:
        archive.close()
