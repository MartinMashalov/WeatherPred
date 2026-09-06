from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from research.experiments.e009_execution_diagnostics import liquidation, select_book
from weatherpred.fees import FeeSchedule


def test_sale_quote_includes_exit_fees_and_reports_unfillable_quantity():
    result = liquidation(
        {"yes": [(D("0.40"), D(2)), (D("0.30"), D(1))]},
        "yes",
        D(4),
        FeeSchedule("quadratic", D(1)),
    )
    assert result == {
        "quantity_quoted_for_sale": "3",
        "quantity_without_bid_depth": "1",
        "gross_bid_proceeds": "1.10",
        "net_bid_proceeds": "1.0517",
        "exit_fees": "0.048300",
    }


def test_maker_post_fill_quote_cannot_reuse_earlier_arrival_or_late_observation():
    fill = datetime(2026, 9, 6, 13, 45, 50, tzinfo=UTC)
    order = {"style": "maker", "ticker": "M", "arrival_book_record_id": 1, "close_at": "2026-09-06T14:00:00Z"}
    old = {
        "id": 1,
        "started": fill - timedelta(seconds=44),
        "received": fill - timedelta(seconds=43),
        "books": {"M": {}},
    }
    late = {
        "id": 2,
        "started": fill + timedelta(seconds=15),
        "received": fill + timedelta(seconds=16),
        "books": {"M": {}},
    }
    assert select_book([old, late], order, fill, 0) is None
    fresh = {
        "id": 3,
        "started": fill + timedelta(seconds=1),
        "received": fill + timedelta(seconds=2),
        "books": {"M": {}},
    }
    assert select_book([old, fresh, late], order, fill, 0) == fresh
    assert select_book([old, fresh, late], {**order, "style": "taker"}, fill, 0) == old
