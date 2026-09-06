"""Preliminary daily high constraints, retaining source and correction uncertainty."""

import re
from datetime import date

from scipy.stats import beta

from weatherpred.contracts import nws_standard_day
from weatherpred.nws_climate import parse_climate_report
from weatherpred.timeutil import parse_time


def partial_high(text, filename, product, start, end, offset):
    row = parse_climate_report(text, filename, product, start, end, offset)
    if row["status"] != "partial_day_report":
        return None
    day_start, day_end = nws_standard_day(date.fromisoformat(row["day"]), offset)
    if not day_start <= parse_time(row["issued_at"]) < day_end:
        return None
    section = re.search(r"TEMPERATURE \(F\)(.*?)(?:PRECIPITATION|DEGREE DAYS)", text, re.DOTALL)
    maximum = re.search(r"(?m)^\s+MAXIMUM\s+(-?\d+|MM)(?=\s|R)", section[1]) if section else None
    if maximum is None or maximum[1] == "MM":
        raise ValueError("Partial report has no numerical daily maximum")
    return {**row, "maximum_so_far_f": int(maximum[1])}


def conservative_bound_probability(daily_failures):
    if len(daily_failures) < 100:
        return None
    failures, n = sum(daily_failures), len(daily_failures)
    if failures == n:
        return 0.0
    return float(1 - beta.ppf(0.95, failures + 1, n - failures))


def below_observed_bound(market, bound):
    if market["strike_type"] == "between":
        return market["cap_strike"] is not None and float(market["cap_strike"]) < bound
    if market["strike_type"] == "less":
        return market["cap_strike"] is not None and float(market["cap_strike"]) <= bound
    if market["strike_type"] == "greater":
        return False
    raise ValueError("Unknown settlement predicate")
