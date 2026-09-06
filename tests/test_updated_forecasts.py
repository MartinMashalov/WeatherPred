from datetime import UTC, datetime, timedelta

import pytest

from weatherpred.updated_forecasts import updated_features


def source(hour, stored_hour, record_id, temperature):
    base = datetime(2025, 1, 1, tzinfo=UTC)
    rows = [
        {
            "valid_at": (base + timedelta(hours=h)).isoformat(),
            "tmp": temperature,
            "txn": 80 if h == 24 else None,
            "xnd": 2 if h == 24 else None,
        }
        for h in range(6 if hour == 1 else (12 if hour == 7 else 18), 30, 3)
    ]
    return {
        "day": "2025-01-01",
        "record_id": record_id,
        "object": {"last_modified": (base + timedelta(hours=stored_hour)).isoformat()},
        "cards": {
            "KMIA": {
                "station": "KMIA",
                "runtime": (base + timedelta(hours=hour)).isoformat(),
                "version": "4.2",
                "rows": rows,
            }
        },
    }


def test_updated_grid_keeps_earlier_hours_and_rejects_unavailable_future_runs():
    sources = [source(1, 2, 1, 70), source(7, 8, 7, 75), source(13, 14, 13, 78)]
    early = updated_features("2025-01-01", -5, "KMIA", sources, datetime(2025, 1, 1, 5, tzinfo=UTC))
    assert early["grid_max"] == 70 and early["eligible_source_record_ids"] == [1]
    updated = updated_features("2025-01-01", -5, "KMIA", sources, datetime(2025, 1, 1, 17, tzinfo=UTC))
    assert updated["grid_max"] == 78 and len(updated["grid_source_lineage"]) == 8
    assert [r["source_record_id"] for r in updated["grid_source_lineage"].values()] == [
        1,
        1,
        7,
        7,
        13,
        13,
        13,
        13,
    ]
    late = [sources[0], sources[1], source(13, 18, 13, 99)]
    excluded = updated_features("2025-01-01", -5, "KMIA", late, datetime(2025, 1, 1, 17, tzinfo=UTC))
    assert excluded["grid_max"] == 75 and excluded["proxy_source_lineage"]["source_record_id"] == 7


def test_updated_sources_cannot_mix_stations_days_or_duplicate_cycles():
    early = source(1, 2, 1, 70)
    decision = datetime(2025, 1, 1, 17, tzinfo=UTC)
    with pytest.raises(ValueError, match="Duplicate"):
        updated_features("2025-01-01", -5, "KMIA", [early, early], decision)
    with pytest.raises(ValueError, match="day mismatch"):
        updated_features("2025-01-02", -5, "KMIA", [early], decision)
    early["cards"]["KMIA"]["station"] = "KORD"
    with pytest.raises(ValueError, match="station"):
        updated_features("2025-01-01", -5, "KMIA", [early], decision)
