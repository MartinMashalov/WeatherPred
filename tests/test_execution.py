from datetime import date
from decimal import Decimal as D

import pytest

from weatherpred.books import parse_book, purchase_cost
from weatherpred.contracts import nws_standard_day, payout_bounds, yes_at
from weatherpred.fees import FeeAccumulator, FeeSchedule


def brackets():
    return [
        {"ticker": "low", "event_ticker": "day", "strike_type": "less", "cap_strike": 73},
        {
            "ticker": "middle",
            "event_ticker": "day",
            "strike_type": "between",
            "floor_strike": 73,
            "cap_strike": 74,
        },
        {"ticker": "high", "event_ticker": "day", "strike_type": "greater", "floor_strike": 74},
    ]


def test_current_direct_fee_and_non_direct_fee():
    direct = FeeAccumulator(FeeSchedule("quadratic", D(1)))
    non_direct = FeeAccumulator(FeeSchedule("quadratic", D(1), D("0.01")))
    assert direct.fill("0.5", 1)["net_fee"] == D("0.0175")
    assert non_direct.fill("0.5", 1)["net_fee"] == D("0.02")
    assert direct.fill("0.5", 1, maker=True)["net_fee"] == 0
    maker = FeeAccumulator(FeeSchedule("quadratic_with_maker_fees", D(1)))
    assert maker.fill("0.5", 1, maker=True)["net_fee"] == D("0.0044")


def test_rounding_accumulates_across_partial_fills():
    schedule = FeeSchedule("quadratic", D(1), D("0.01"))
    acc = FeeAccumulator(schedule)
    partial = sum(acc.fill("0.5", 1)["net_fee"] for _ in range(100))
    assert partial == D("1.75")
    assert partial == FeeAccumulator(schedule).fill("0.5", 100)["net_fee"]


def test_fractional_fill_aligns_balance_and_never_negative_fee():
    acc = FeeAccumulator(FeeSchedule("quadratic", D(1)))
    balance = D(100)
    for quantity in ("0.07", "0.31", "1.29", "9.31"):
        f = acc.fill("0.37", quantity)
        balance += f["balance_change"]
        assert balance == balance.quantize(D("0.0001"))
        assert f["net_fee"] >= 0


def test_taker_uses_asks_and_depth_not_midpoints():
    book = parse_book(
        {"orderbook_fp": {"yes_dollars": [["0.20", "50"]], "no_dollars": [["0.60", "1"], ["0.50", "2"]]}}
    )
    q = purchase_cost(book, "yes", 2, FeeSchedule("quadratic", D(1)))
    assert q.principal == D("0.90")
    assert q.worst_price == D("0.50")
    assert purchase_cost(book, "yes", 4, FeeSchedule("quadratic", D(1))) is None
    assert purchase_cost(book, "yes", 2, FeeSchedule("quadratic", D(1)), depth_retained="0.5") is None


def test_empty_or_malformed_book_is_never_a_free_fill():
    with pytest.raises(ValueError):
        parse_book({})
    with pytest.raises(ValueError):
        parse_book({"orderbook_fp": {"yes_dollars": [["0.6", "1"]], "no_dollars": [["0.5", "1"]]}})
    book = parse_book({"orderbook_fp": {"yes_dollars": [], "no_dollars": []}})
    assert purchase_cost(book, "yes", 1, FeeSchedule("quadratic", D(1))) is None


def test_partition_boundaries_and_missing_bracket():
    ms = brackets()
    assert payout_bounds(ms)["exhaustive_exclusive"]
    assert yes_at(ms[0], 73) is False
    assert yes_at(ms[1], 73) is True
    assert payout_bounds([ms[0], ms[2]])["yes_min"] == 0
    assert payout_bounds([ms[0], ms[2]])["no_min"] == 1


def test_integer_partition_can_have_continuous_gaps():
    ms = brackets()
    ms[2]["floor_strike"] = 75
    ms.insert(
        2,
        {
            "ticker": "exact",
            "event_ticker": "day",
            "strike_type": "between",
            "floor_strike": 75,
            "cap_strike": 75,
        },
    )
    assert payout_bounds(ms, integer_domain=True)["exhaustive_exclusive"]
    assert not payout_bounds(ms)["exhaustive_exclusive"]


def test_nws_day_stays_24h_over_dst():
    for day in (date(2026, 3, 8), date(2026, 11, 1), date(2026, 9, 6)):
        start, end = nws_standard_day(day, -5)
        assert (end - start).total_seconds() == 86400
        assert start.utcoffset().total_seconds() == -18000
