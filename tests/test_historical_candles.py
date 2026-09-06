import pytest

from weatherpred.forecasts import candle_quote
from weatherpred.historical_candles import normalize_historical_candles


def test_historical_dollar_close_is_normalized_without_midpoint_fill_or_time_change():
    raw = [
        {
            "end_period_ts": 120,
            "yes_bid": {"close": "0.2400"},
            "yes_ask": {"close": "0.2800"},
            "price": {"close": "0.9900"},
        }
    ]
    normalized = normalize_historical_candles(raw)
    assert "close_dollars" not in raw[0]["yes_bid"]
    assert candle_quote(normalized, 120, max_age_seconds=0) == {
        "bid": 0.24,
        "ask": 0.28,
        "midpoint": 0.26,
        "end_period_ts": 120,
    }
    assert candle_quote(normalized, 119, max_age_seconds=0) is None
    assert candle_quote(normalized, 121, max_age_seconds=0) is None


def test_historical_adapter_rejects_cent_ambiguity_and_conflicting_aliases():
    for value in (24, "24", ".24", "NaN"):
        with pytest.raises(ValueError, match="fixed-dollar"):
            normalize_historical_candles([{"yes_bid": {"close": value}}])
    with pytest.raises(ValueError, match="disagree"):
        normalize_historical_candles([{"yes_bid": {"close": "0.2400", "close_dollars": "0.25"}}])
    assert (
        candle_quote(normalize_historical_candles([{"end_period_ts": 120, "yes_bid": {"close": None}}]), 120)
        is None
    )
