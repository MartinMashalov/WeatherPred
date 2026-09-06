from datetime import UTC, datetime
from decimal import Decimal

import pytest

from research.probes.market_expansion import count_period, threshold_signature
from research.probes.weekly_streak_bounds import possible_streaks, station_days


def test_streak_bounds_preserve_consecutive_day_structure():
    assert possible_streaks([False, False, True, True, True, True, None]) == [4, 5]
    assert possible_streaks([False, True, False, True, False, False, None]) == [1]
    assert possible_streaks([True, True, None, True, True, False, False]) == [2, 5]


def test_pending_future_or_sparse_days_cannot_establish_a_streak():
    observations = [
        {
            "localDate": "2026-09-05",
            "localHour": hour,
            "status": "settled",
            "reportTimeUTC": f"2026-09-05T{hour:02d}:00:00Z",
            "tempF": 95,
        }
        for hour in range(17)
    ]
    observations.append(
        {
            "localDate": "2026-09-05",
            "localHour": 17,
            "status": "pending",
            "reportTimeUTC": "2026-09-05T17:00:00Z",
            "tempF": 95,
        }
    )
    station = {"timezone": "UTC", "observations": observations, "dailyAverages": {}}
    received = datetime(2026, 9, 6, 12, tzinfo=UTC)
    rows = station_days(station, ["2026-09-05", "2026-09-06"], received, Decimal(90), Decimal(1))
    assert [r["qualifies"] for r in rows] == [None, None]
    observations[-1]["status"] = "settled"
    assert station_days(station, ["2026-09-05"], received, Decimal(90), Decimal(1))[0]["qualifies"] is True
    observations[-1]["reportTimeUTC"] = "2026-09-07T17:00:00Z"
    assert station_days(station, ["2026-09-05"], received, Decimal(90), Decimal(1))[0]["qualifies"] is None


def test_nested_thresholds_require_matching_terms_and_numeric_semantics():
    one = {
        "strike_type": "greater",
        "floor_strike": 3,
        "rules_primary": "If the total precipitation at CLIMIA in Sep 2026 is strictly greater than 3 inches, then the market resolves to Yes.",
        "rules_secondary": "Original source convention",
    }
    two = {**one, "floor_strike": 4, "rules_primary": one["rules_primary"].replace("than 3", "than 4")}
    assert threshold_signature(one) == threshold_signature(two)
    assert threshold_signature(one) != threshold_signature({**two, "rules_secondary": "Changed source"})
    with pytest.raises(ValueError, match="Ambiguous threshold"):
        threshold_signature({**two, "floor_strike": 5})


def test_hurricane_subset_cannot_survive_a_changed_count_definition():
    market = {
        "ticker": "KXHURCTOTMAJ-26DEC01-T3",
        "strike_type": "greater",
        "floor_strike": 3,
        "rules_primary": "If the NOAA's National Hurricane Center records more than 3 hurricanes of hurricane category 3 or above between January 1, 2026 and December 01, 2026, then the market resolves to Yes.",
    }
    assert count_period(market) == "January 1, 2026 and December 01, 2026"
    with pytest.raises(ValueError, match="Count definition changed"):
        count_period({**market, "rules_primary": market["rules_primary"].replace("category 3", "category 2")})
