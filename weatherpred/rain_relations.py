"""Conditional rain-calendar identities and non-atomic two-leg execution plans.

The identity is conditional on the source continuing to treat Saturday as dry.
It is not a guarantee against source revisions or discretionary settlement.
"""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from weatherpred.books import purchase_cost
from weatherpred.timeutil import parse_time

D = Decimal


def calendar_rule(market):
    if (
        market.get("market_type") != "binary"
        or market.get("strike_type") != "greater"
        or D(str(market.get("floor_strike"))) != 0
        or D(market.get("notional_value_dollars", "0")) != 1
    ):
        raise ValueError("Unsupported rain payout predicate")
    secondary = market.get("rules_secondary", "")
    if (
        not all(
            text in secondary
            for text in (
                "Weather Company",
                "https://weather.com/kalshi",
                "missing daily precipitation values",
            )
        )
        or "counted as 0 inches" not in secondary
    ):
        raise ValueError("Rain source or missing-value convention is unrecognized")
    match = re.fullmatch(
        r"If the total precipitation at (CLI\w+) in (.+?) "
        r"(in |on any day within )(.+?) is strictly greater than 0 inches, "
        r"then the market resolves to Yes\.",
        market["rules_primary"],
    )
    if not match:
        raise ValueError("Unrecognized rain calendar rule")
    station, city, mode, dates = match.groups()

    def day(value):
        for fmt in ("%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=UTC).date()
            except ValueError:
                pass
        raise ValueError("Unrecognized rain date")

    parts = dates.split(" through ")
    if len(parts) != (1 if mode == "in " else 2):
        raise ValueError("Rain date count differs from predicate")
    start, end = day(parts[0]), day(parts[-1])
    if end < start:
        raise ValueError("Reversed rain dates")
    return {"station": station, "city": city, "start": start.isoformat(), "end": end.isoformat()}


def relation(saturday, sunday, weekend):
    a, b, w = map(calendar_rule, (saturday, sunday, weekend))
    start = date.fromisoformat(a["start"])
    if (
        a["station"] != b["station"]
        or a["station"] != w["station"]
        or a["city"] != b["city"]
        or a["city"] != w["city"]
        or a["start"] != a["end"]
        or b["start"] != b["end"]
        or start.weekday() != 5
        or (start + timedelta(days=1)).isoformat() != b["start"]
        or (w["start"], w["end"]) != (a["start"], b["start"])
        or len({m["rules_secondary"] for m in (saturday, sunday, weekend)}) != 1
    ):
        raise ValueError("Rain contracts differ in station, date, city or source conventions")
    return {**w, "saturday": saturday["ticker"], "daily": sunday["ticker"], "weekend": weekend["ticker"]}


def confirmed_dry(saturday, source_table, as_of):
    rule = calendar_rule(saturday)
    if (
        saturday.get("status") != "finalized"
        or saturday.get("result") != "no"
        or D(saturday.get("expiration_value") or "-1") != 0
        or not saturday.get("settlement_ts")
        or parse_time(saturday["settlement_ts"]) > as_of
        or source_table.get("date") != rule["start"]
    ):
        raise ValueError("Saturday is not a previously finalized zero-rain day")
    entries = [r for r in source_table["results"] if "CLI" + r["station"]["cliId"] == rule["station"]]
    if len(entries) != 1:
        raise ValueError("Ambiguous official rain station")
    entry = entries[0]
    data = entry.get("data") or {}
    # Deliberately require an explicit numeric zero. Missing/trace values may
    # settle as zero but are not accepted as this strategy's source confirmation.
    if (
        entry.get("status") != "official"
        or data.get("isOfficial") is not True
        or data.get("reportDate") != rule["start"]
        or data.get("precipitation") is None
        or isinstance(data.get("precipitation"), bool)
        or D(str(data["precipitation"])) != 0
    ):
        raise ValueError("Official source does not independently confirm numeric zero rain")
    return entry["station"]["icao"]


def plan_pair(
    books, schedules, scenario, *, budget="5", maximum_quantity=5, surplus="0.02", allowance="0.01"
):
    """Largest affordable whole matched size, testing both equivalent directions.

    `allowance` is a fixed source-risk stress deduction, not an estimated failure
    probability. Both legs still arrive independently and can fill unequally.
    """
    candidates = []
    for sides in (("yes", "no"), ("no", "yes")):
        for quantity in range(1, maximum_quantity + 1):
            costs = [
                purchase_cost(book, side, quantity, fee, scenario["depth_retained"], scenario["slippage"])
                for book, side, fee in zip(books, sides, schedules, strict=True)
            ]
            if any(c is None for c in costs):
                continue
            # Match PaperLedger's conservative maximum fee/rounding reservation.
            reserve = sum(
                (c.worst_price + D("0.0175") * fee.multiplier + D("0.01")) * quantity
                for c, fee in zip(costs, schedules, strict=True)
            )
            total = sum(c.total for c in costs)
            net = D(quantity) - total
            if reserve > D(budget) or net / quantity - D(allowance) < D(surplus):
                continue
            candidates.append(
                {
                    "sides": list(sides),
                    "quantity": str(quantity),
                    "limits": [str(c.worst_price) for c in costs],
                    "decision_cost": str(total),
                    "reservation": str(reserve),
                    "conditional_surplus": str(net),
                    "surplus_after_source_allowance": str(net - D(allowance) * quantity),
                }
            )
    return max(candidates, key=lambda c: (D(c["conditional_surplus"]), c["sides"]), default=None)


def payout_bounds(quantities, sides):
    """Unequal legs can lose under either shared binary outcome.

    Return shared-outcome payout bounds, plus the zero-payout source-break case.
    These do not credit cash; different contracts do not offset at the venue.
    """
    values = [
        sum(D(str(q)) * (y if side == "yes" else 1 - y) for q, side in zip(quantities, sides, strict=True))
        for y in (0, 1)
    ]
    return min(values), max(values), D(0)
