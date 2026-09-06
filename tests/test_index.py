import pytest

from weatherpred.index import calibration_at, settlement_point


def test_backdated_calibration_cannot_leak_before_publication():
    old = {"effective_at_ms": 0, "published_at_ms": 0, "config_version": "old"}
    new = {"effective_at_ms": 100, "published_at_ms": 200, "config_version": "backdated"}
    assert calibration_at([old, new], 150, 150) == old
    assert calibration_at([old, new], 150, 200) == new
    assert calibration_at([old, new], 99, 500) == old


def test_index_settlement_excludes_backfill_and_pending_and_future():
    s = 10_000_000
    points = [
        {"t": s - 60_000, "v": 90.05, "status": "degraded"},
        {"t": s, "v": 89, "status": "normal", "receipt_basis": "synoptic_latency"},
        {"t": s, "status": "incomplete"},
        {"t": s + 60_000, "v": 88, "status": "normal"},
    ]
    assert settlement_point(points, s, s + 300_000) == points[0]
    with pytest.raises(ValueError, match="deadline"):
        settlement_point(points, s, s + 299_999)


def test_index_60_minute_fallback_limit_is_inclusive():
    point = {"t": 1000, "v": 90, "status": "normal"}
    assert settlement_point([point], 3_601_000, 4_000_000) == point
    assert settlement_point([point], 3_601_001, 4_000_000) is None
