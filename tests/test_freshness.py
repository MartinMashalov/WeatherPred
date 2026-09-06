import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from weatherpred.archive import Archive, canonical
from weatherpred.freshness import paired_forecast, train_fresh_model
from weatherpred.timeutil import iso


def test_fresh_fit_cannot_use_outside_training_values_or_future_conflicts():
    protocol = json.loads(Path("config/e006_fresh_index.json").read_text())
    start = datetime(2026, 8, 20, tzinfo=UTC)
    end = start + timedelta(hours=3)
    protocol["training_end_exclusive"] = iso(end)
    ms = int(start.timestamp() * 1000)
    points = [{"t": ms + i * 60_000, "v": 70 + i / 100, "status": "normal"} for i in range(-60, 180)]
    models, rows, exclusions = train_fresh_model(points, protocol)
    assert len(rows) == 9
    assert exclusions == {}
    assert all(fit["n"] == 3 for fit in models.values())
    future = [{"t": int(end.timestamp() * 1000), "v": v, "status": "normal"} for v in (0, 999)]
    assert train_fresh_model(points + future, protocol) == (models, rows, exclusions)
    assert all(r["features"]["last_point_ms"] <= r["decision_ms"] - 300_000 for r in rows)
    assert all(r["settlement_ms"] + 300_000 < int(end.timestamp() * 1000) for r in rows)


def fixture(archive, *, model_late=False):
    scheduled = datetime(2026, 9, 6, 12, 30, tzinfo=UTC)
    settlement = scheduled + timedelta(minutes=30)
    model_at = scheduled + timedelta(seconds=1) if model_late else scheduled - timedelta(hours=1)
    parent_model = archive.append("model_artifact", "parent", scheduled - timedelta(hours=1), {}, b"{}")
    protocol = json.loads(Path("config/e006_fresh_index.json").read_text())
    protocol["parent_model_record_id"] = parent_model
    slot = {"decision_at": iso(scheduled), "settlement_at": iso(settlement), "horizon_minutes": 30}
    fit = {
        name + ":30": {"base": name.split("_")[0], "kind": name.split("_")[1], "residuals": [-1, 0, 1]}
        for name in protocol["models"]
    }
    model = archive.append(
        "model_artifact",
        "fresh",
        model_at,
        {},
        canonical({"protocol": protocol, "slots": [slot], "models": fit}).encode(),
    )
    ms = int(scheduled.timestamp() * 1000)
    points = [{"t": ms + i * 60_000, "v": 80 + i / 100, "status": "normal"} for i in range(-60, 1)]
    points.append({"t": ms - 4 * 60_000, "v": 999, "status": "incomplete"})
    source = archive.append(
        "shadow_index",
        "test",
        scheduled + timedelta(seconds=1),
        {},
        canonical({"timeseries": points}).encode(),
    )
    published = scheduled + timedelta(seconds=2)
    parent = archive.append(
        "shadow_forecast",
        "test",
        published,
        {},
        canonical(
            {
                "protocol": "E004-forward-v1",
                "model_record_id": parent_model,
                "event": "test",
                "scheduled_at": iso(scheduled),
                "settlement_at": iso(settlement),
                "published_at": iso(published),
                "snapshot_at": iso(scheduled + timedelta(seconds=1)),
                "horizon_minutes": 30,
                "features": {"persistence": 79.9},
                "evidence_record_ids": [source],
                "markets": [
                    {
                        "ticker": "test-T80",
                        "floor_strike": 79.99,
                        "probabilities": {name: 0.4 for name in protocol["models"]},
                    }
                ],
            }
        ).encode(),
    )
    read = lambda rec: archive.db.execute("SELECT * FROM records WHERE id=?", (rec,)).fetchone()
    return read(parent), read(model), scheduled


def test_fresh_comparison_uses_original_snapshot_not_consumer_time(tmp_path):
    archive = Archive(tmp_path)
    parent, model, scheduled = fixture(archive)
    result = paired_forecast(archive, parent, model, scheduled + timedelta(seconds=10))
    assert result["features"]["last_point_ms"] == int((scheduled - timedelta(minutes=5)).timestamp() * 1000)
    assert result["features"]["persistence"] == 79.95
    assert len(result["markets"][0]["probabilities"]) == 8
    assert result["markets"][0]["probabilities"]["original_10m_persistence_empirical"] == 0.4
    assert result["real_money_recommended_size"] == result["fills"] == 0
    assert parent["id"] in result["evidence_record_ids"]
    with pytest.raises(ValueError, match="missed its window"):
        paired_forecast(archive, parent, model, scheduled + timedelta(seconds=21))
    with pytest.raises(ValueError, match="not available"):
        paired_forecast(archive, parent, model, scheduled + timedelta(seconds=1))
    archive.append("shadow_invalidated", str(parent["id"]), scheduled + timedelta(seconds=3), {}, b"invalid")
    with pytest.raises(ValueError, match="invalidated"):
        paired_forecast(archive, parent, model, scheduled + timedelta(seconds=10))
    archive.close()


def test_fresh_model_must_be_frozen_before_registered_slot(tmp_path):
    archive = Archive(tmp_path)
    parent, model, scheduled = fixture(archive, model_late=True)
    with pytest.raises(ValueError, match="not frozen"):
        paired_forecast(archive, parent, model, scheduled + timedelta(seconds=10))
    archive.close()
