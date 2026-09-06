"""Conditional unit-purchase cost screen; archived candles do not prove fills."""

import math
from decimal import Decimal

from weatherpred.fees import FeeAccumulator, FeeSchedule


def choose_conditional_purchase(quotes, probabilities, scenario):
    if len(quotes) != len(probabilities) or not quotes:
        raise ValueError("Quotes and probabilities do not align")
    if len({q["ticker"] for q in quotes}) != len(quotes):
        raise ValueError("Duplicate quote contract")
    candidates = []
    slippage = Decimal(scenario["slippage_usd"])
    if not slippage.is_finite() or slippage < 0:
        raise ValueError("Invalid slippage assumption")
    schedule = FeeSchedule(
        "quadratic", Decimal(scenario["fee_multiplier"]), Decimal(scenario["balance_precision"])
    )
    for quote, probability in zip(quotes, probabilities, strict=True):
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("Invalid forecast probability")
        bid, ask = Decimal(str(quote["bid"])), Decimal(str(quote["ask"]))
        if not 0 < bid < ask < 1:
            raise ValueError("Quote does not meet registered strict two-sided rule")
        for side, p, price in (("yes", probability, ask), ("no", 1 - probability, 1 - bid)):
            assumed_price = price + slippage
            if assumed_price >= 1:
                continue
            fee = FeeAccumulator(schedule).fill(assumed_price, 1)
            cost = -fee["balance_change"]
            edge = Decimal(str(p)) - cost
            candidates.append(
                {
                    "ticker": quote["ticker"],
                    "side": side,
                    "probability": p,
                    "raw_purchase_price": str(price),
                    "assumed_purchase_price": str(assumed_price),
                    "assumed_fee": str(fee["net_fee"]),
                    "assumed_cost": str(cost),
                    "expected_edge": float(edge),
                    "conditional_quantity": 1,
                    "actual_fills": 0,
                }
            )
    candidates.sort(key=lambda c: (-c["expected_edge"], c["ticker"], c["side"]))
    return candidates[0] if candidates and candidates[0]["expected_edge"] > 0 else None
