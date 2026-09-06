"""Date/bucket boundaries protect the sealed quarter and requested year."""

import pytest

from research.probes.bankroll_acquisition import FIRST_TS, LAST_TS, event_day, quote_window, validate_candles


@pytest.mark.parametrize(
    "event", ["KXHIGHNY-25DEC31", "KXHIGHNY-26SEP06", "KXTEMPMIAH-26JAN01", "KXHIGHNY-26JAN0100"]
)
def test_reject_event_outside_manifest_scope(event):
    with pytest.raises(ValueError):
        event_day(event)


def test_first_event_excludes_bucket_ending_at_year_start():
    window = quote_window(
        {
            "event_ticker": "KXHIGHNY-26JAN01",
            "open_time": "2025-12-31T15:00:00Z",
            "close_time": "2026-01-02T04:59:00Z",
        }
    )
    assert window["start_ts"] == FIRST_TS + 1
    assert window["excluded_preyear_seconds"] == 9 * 3600
    with pytest.raises(ValueError, match="escaped"):
        validate_candles([{"end_period_ts": FIRST_TS}], window)
    assert validate_candles([{"end_period_ts": FIRST_TS + 3600}], window) == 1


def test_last_event_keeps_final_year_bucket_and_excludes_future():
    window = quote_window(
        {
            "event_ticker": "KXHIGHNY-26SEP05",
            "open_time": "2026-09-04T14:00:00Z",
            "close_time": "2026-09-06T05:00:00Z",
        }
    )
    assert window["end_ts"] == LAST_TS
    assert window["excluded_postyear_seconds"] == 5 * 3600
    assert validate_candles([{"end_period_ts": LAST_TS}], window) == 1
    with pytest.raises(ValueError, match="escaped"):
        validate_candles([{"end_period_ts": LAST_TS + 3600}], window)


def test_duplicate_candle_endpoints_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        validate_candles(
            [{"end_period_ts": FIRST_TS + 3600}] * 2, {"start_ts": FIRST_TS + 1, "end_ts": LAST_TS}
        )
