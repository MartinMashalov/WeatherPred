"""Comparison semantics, with explicit continuous versus integer domains.

Current daily rules have unresolved source/precision differences. An integer
partition is a diagnostic assumption, not permission to treat it as exhaustive.
"""

import itertools
import math
import re
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal


def historical_predicate(market):
    """Recover absent structured strikes only from explicit numeric primary rules.

    Some historical winning contracts lack strike metadata. Excluding those
    selectively would bias the sample. Ambiguous prose is rejected, never guessed
    from the observed winner or from ticker/subtitle conventions.
    """
    required = {
        "greater": ("floor_strike",),
        "less": ("cap_strike",),
        "between": ("floor_strike", "cap_strike"),
    }
    kind = market.get("strike_type")
    if kind in required and all(market.get(k) is not None for k in required[kind]):
        return dict(market, predicate_provenance="structured_market_fields")
    text = market.get("rules_primary", "")
    number = r"(-?\d+(?:\.\d+)?)"
    between = re.search(r"\bis between " + number + r"\s*-\s*" + number + r"°", text)
    greater = re.search(r"\bis (?:greater than|above) " + number + r"°", text)
    less = re.search(r"\bis (?:less than|below) " + number + r"°", text)
    if sum(x is not None for x in (between, greater, less)) != 1:
        raise ValueError("Missing structured strike and no unambiguous numeric primary rule")
    if between:
        fields = {"strike_type": "between", "floor_strike": between[1], "cap_strike": between[2]}
    elif greater:
        fields = {"strike_type": "greater", "floor_strike": greater[1], "cap_strike": None}
    else:
        fields = {"strike_type": "less", "floor_strike": None, "cap_strike": less[1]}
    for key, value in fields.items():
        if (
            market.get(key) is not None
            and str(market[key]) != str(value)
            and (key == "strike_type" or value is None or Decimal(str(market[key])) != Decimal(str(value)))
        ):
            raise ValueError("Partially specified historical strike conflicts with primary rules")
    return dict(market, **fields, predicate_provenance="explicit_numeric_primary_rule")


def yes_at(market, value):
    kind = market["strike_type"]
    v = Decimal(str(value))
    lower = market.get("floor_strike")
    upper = market.get("cap_strike")
    if kind == "between" and lower is not None and upper is not None:
        if Decimal(str(lower)) > Decimal(str(upper)):
            raise ValueError("Inverted bracket")
        return Decimal(str(lower)) <= v <= Decimal(str(upper))
    if kind == "greater" and lower is not None:
        return v > Decimal(str(lower))
    if kind == "less" and upper is not None:
        return v < Decimal(str(upper))
    raise ValueError(f"Unsupported comparison: {kind}")


def payout_bounds(markets, integer_domain=False):
    if not markets or len({m["ticker"] for m in markets}) != len(markets):
        raise ValueError("Missing or duplicated event members")
    if len({m["event_ticker"] for m in markets}) != 1:
        raise ValueError("Different events cannot form a single-outcome partition")
    points = sorted(
        {Decimal(str(m[k])) for m in markets for k in ("floor_strike", "cap_strike") if m.get(k) is not None}
    )
    if not points or any(not p.is_finite() for p in points):
        raise ValueError("Invalid boundaries")
    if integer_domain:
        # All comparison changes occur at integers adjacent to a boundary.
        probes = {
            Decimal(i)
            for p in points
            for i in (math.floor(p) - 1, math.floor(p), math.ceil(p), math.ceil(p) + 1)
        }
    else:
        probes = set(points) | {points[0] - 1, points[-1] + 1}
        probes.update((a + b) / 2 for a, b in itertools.pairwise(points))
    payouts = [sum(yes_at(m, p) for m in markets) for p in probes]
    return {
        "yes_min": min(payouts),
        "yes_max": max(payouts),
        "no_min": len(markets) - max(payouts),
        "no_max": len(markets) - min(payouts),
        "exhaustive_exclusive": min(payouts) == max(payouts) == 1,
    }


def nws_standard_day(day, standard_utc_offset_hours):
    """Only for NWS contracts verified to use local STANDARD time, not TWC."""
    zone = timezone(timedelta(hours=standard_utc_offset_hours))
    start = datetime.combine(day, time(), tzinfo=zone)
    return start, start + timedelta(days=1)
