"""Read-only order intents and capital diagnostics; no broker transport."""

from decimal import ROUND_FLOOR, Decimal
from uuid import NAMESPACE_URL, uuid5

from weatherpred.fees import FeeAccumulator
from weatherpred.market_making import valid_tick

D = Decimal


def order_intent(
    market,
    outcome,
    action,
    quantity,
    limit,
    *,
    key,
    time_in_force="immediate_or_cancel",
    post_only=False,
    expires_at=None,
    reduce_only=False,
):
    """Translate outcome-side economics into the current YES-book V2 schema.

    Returned data are reviewable payloads only. No HTTP/authentication exists
    here. A FOK instruction applies to one order, never an atomic two-leg pair.
    """
    q, price = D(str(quantity)), D(str(limit))
    if outcome not in ("yes", "no") or action not in ("buy", "sell"):
        raise ValueError("Invalid outcome/action")
    if not q.is_finite() or q <= 0 or q != q.quantize(D(".01")):
        raise ValueError("Quantity must use supported positive hundredth contracts")
    if not price.is_finite() or not 0 < price < 1:
        raise ValueError("Limit must be between zero and one")
    book_price = price if outcome == "yes" else 1 - price
    if not valid_tick(market, book_price):
        raise ValueError("Price is outside the market's YES-book grid")
    if time_in_force not in ("immediate_or_cancel", "fill_or_kill", "good_till_canceled"):
        raise ValueError("Unsupported order lifetime")
    if expires_at is not None and time_in_force != "good_till_canceled":
        raise ValueError("Expiring orders require good_till_canceled")
    if post_only and time_in_force != "good_till_canceled":
        raise ValueError("Passive intent must be a resting limit order")
    if not key:
        raise ValueError("A durable unique intent key is required")
    side = "bid" if (outcome == "yes") == (action == "buy") else "ask"
    result = {
        "ticker": market["ticker"],
        "client_order_id": str(uuid5(NAMESPACE_URL, "weatherpred:" + key)),
        "side": side,
        "count": f"{q:.2f}",
        "price": f"{book_price:.6f}",
        "time_in_force": time_in_force,
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": post_only,
        "cancel_order_on_pause": True,
        "reduce_only": reduce_only,
    }
    if expires_at is not None:
        result["expiration_time"] = int(expires_at)
    return result


def liquidation_value(book, side, quantity, schedule, retention="1", slippage="0"):
    """Displayed sell proceeds after fees; unquoted quantity is NOT liquidated."""
    remaining, fraction, slip = D(str(quantity)), D(str(retention)), D(str(slippage))
    if side not in ("yes", "no") or remaining <= 0 or not 0 < fraction <= 1 or not 0 <= slip < 1:
        raise ValueError("Invalid liquidation scenario")
    accumulator = FeeAccumulator(schedule)
    proceeds, filled = D(0), D(0)
    for bid, depth in book[side]:
        price = bid - slip
        if price <= 0:
            continue
        take = min(remaining, (depth * fraction).quantize(D(".01"), rounding=ROUND_FLOOR))
        if take <= 0:
            continue
        proceeds += accumulator.fill(price, take, sell=True)["balance_change"]
        remaining -= take
        filled += take
        if not remaining:
            break
    return {
        "filled_quantity": str(filled),
        "unfilled_quantity": str(remaining),
        "proceeds_after_fees": str(proceeds),
        "full_displayed_exit_available": remaining == 0,
    }


def capital_sensitivity(
    quantity, cost, bankroll="100", source_failure_probabilities=("0", ".01", ".03", ".05", ".10")
):
    """Scenario analysis, NOT estimated source-failure probabilities or returns."""
    q, c, b = D(str(quantity)), D(str(cost)), D(str(bankroll))
    if not 0 < c < q or c >= b:
        raise ValueError("Need a positive conditional surplus with less than full bankroll risk")
    win, loss = b + q - c, b - c
    rows = []
    for raw in source_failure_probabilities:
        probability = D(raw)
        if not 0 <= probability <= 1:
            raise ValueError("Invalid sensitivity probability")
        growth = (1 - probability) * (win / b).ln() + probability * (loss / b).ln()
        rows.append(
            {
                "assumed_source_failure_probability": str(probability),
                "expected_pnl": str((1 - probability) * q - c),
                "expected_log_growth": str(growth),
            }
        )
    return {
        "arithmetic_break_even_failure_probability": str(1 - c / q),
        "geometric_break_even_failure_probability": str((win / b).ln() / ((win / b).ln() - (loss / b).ln())),
        "rows": rows,
        "failure_probabilities_are_assumptions_not_estimates": True,
    }
