"""Explicit schema adapter for Kalshi's historical fixed-dollar close fields."""

import re
from decimal import Decimal


def normalize_historical_candles(candles):
    result = []
    for candle in candles:
        normalized = dict(candle)
        for side in ("yes_bid", "yes_ask"):
            distribution = dict(candle.get(side, {}))
            value = distribution.get("close")
            if value is not None:
                # The historical endpoint documents fixed dollar strings without
                # the live endpoint's _dollars suffix. Never guess cents/units.
                if not isinstance(value, str) or re.fullmatch(r"(?:0\.\d{4}|1\.0000)", value) is None:
                    raise ValueError("Historical close is not a documented fixed-dollar string")
                if distribution.get("close_dollars") is not None and Decimal(
                    distribution["close_dollars"]
                ) != Decimal(value):
                    raise ValueError("Historical and live close aliases disagree")
                distribution["close_dollars"] = value
            normalized[side] = distribution
        result.append(normalized)
    return result
