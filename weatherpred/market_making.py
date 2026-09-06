"""Paired passive quotes and inventory accounting for registered E015 research.

Reuses the frozen paper ledger. Two buys in the same binary contract are not an
atomic pair: each requires its own future queue/tape evidence. No live orders.
"""

from collections import defaultdict
from decimal import ROUND_FLOOR, Decimal

from weatherpred.fees import FeeAccumulator
from weatherpred.paper import exposure, reserved

D = Decimal
CENT = D("0.01")
PREDICATE_FIELDS = (
    "ticker",
    "event_ticker",
    "market_type",
    "notional_value_dollars",
    "strike_type",
    "floor_strike",
    "cap_strike",
    "functional_strike",
    "close_time",
    "rules_primary",
    "rules_secondary",
)


def predicate(market):
    return {key: market.get(key) for key in PREDICATE_FIELDS}


def valid_tick(market, price):
    ranges = market.get("price_ranges") or []
    for row in ranges:
        start, end, step = (D(row[k]) for k in ("start", "end", "step"))
        if step > 0 and start <= price <= end and (price - start) % step == 0:
            return True
    return False


def passive_quotes(book, market, policy, net_yes, schedule):
    """Return both own-side buy prices, or abstain. No midpoint fills implied."""
    if not book["yes"] or not book["no"]:
        return None
    y, n = book["yes"][0][0], book["no"][0][0]
    spread = 1 - y - n
    if not CENT * 2 <= spread <= D("0.20"):
        return None
    if policy not in ("join", "improve_one_cent", "inventory_skew"):
        raise ValueError("Unregistered maker policy")
    improvement = D(0) if policy == "join" else CENT
    # One cent per net contract, at most three cents. A transparent benchmark,
    # not a fitted Avellaneda-Stoikov diffusion/intensity model.
    skew = max(-CENT * 3, min(CENT * 3, CENT * D(net_yes))) if policy == "inventory_skew" else D(0)
    prices = {
        "yes": min(y + improvement - skew, 1 - n - CENT).quantize(CENT, rounding=ROUND_FLOOR),
        "no": min(n + improvement + skew, 1 - y - CENT).quantize(CENT, rounding=ROUND_FLOOR),
    }
    if any(not CENT <= p <= 1 - CENT or not valid_tick(market, p) for p in prices.values()):
        return None
    if not valid_tick(market, 1 - prices["no"]):
        return None
    costs = sum(-FeeAccumulator(schedule).fill(p, 1, maker=True)["balance_change"] for p in prices.values())
    # Require at least one cent for a hypothetical matched pair after maker fees.
    # This is NOT the expected return: one-sided inventory can lose far more.
    if 1 - costs < CENT:
        return None
    return prices


def net_inventory(state, account, ticker):
    return sum(
        (
            D(p["quantity"]) * (1 if p["side"] == "yes" else -1)
            for p in state["positions"].values()
            if p["account"] == account and p["ticker"] == ticker
        ),
        D(0),
    )


def inventory_report(state, account):
    """Terminal payout bounds, with paired capital still locked until settlement.

    Average cost allocation describes pair/unmatched components; their sum is
    total cost. Only the total cash + payout bound is a portfolio profit bound.
    """
    grouped = defaultdict(dict)
    for p in state["positions"].values():
        if p["account"] == account:
            grouped[p["ticker"]][p["side"]] = p
    rows, minimum, maximum = [], D(0), D(0)
    for ticker, sides in sorted(grouped.items()):
        q = {s: D(sides.get(s, {}).get("quantity", "0")) for s in ("yes", "no")}
        c = {s: D(sides.get(s, {}).get("cost", "0")) for s in ("yes", "no")}
        paired = min(q.values())
        allocated = sum((c[s] * paired / q[s] if q[s] else D(0)) for s in q)
        minimum += paired
        maximum += max(q.values())
        rows.append(
            {
                "ticker": ticker,
                "yes_quantity": str(q["yes"]),
                "no_quantity": str(q["no"]),
                "paired_quantity": str(paired),
                "net_yes_quantity": str(q["yes"] - q["no"]),
                "total_cost": str(sum(c.values())),
                "allocated_pair_cost": str(allocated),
                "pair_component_pnl": str(paired - allocated),
                "unmatched_cost_at_risk": str(sum(c.values()) - allocated),
            }
        )
    a = state["accounts"][account]
    cash, initial = D(a["cash"]), D(a["initial_cash"])
    return {
        "account": account,
        "cash": str(cash),
        "reserved": str(reserved(state, account)),
        "gross_capital_committed": str(exposure(state, account)),
        "fees": a["fees"],
        "realized_pnl": a["realized_pnl"],
        "positions": rows,
        "terminal_pnl_lower_bound_on_current_fills": str(cash + minimum - initial),
        "terminal_pnl_upper_bound_on_current_fills": str(cash + maximum - initial),
        "bounds_assume_normal_binary_settlement": True,
        "outstanding_orders_excluded_from_payout_bounds": True,
    }
