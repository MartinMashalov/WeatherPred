from decimal import Decimal as D

import pytest

from weatherpred.execution_finance import capital_sensitivity, liquidation_value, order_intent
from weatherpred.fees import FeeSchedule

MARKET = {"ticker": "RAIN", "price_ranges": [{"start": "0", "end": "1", "step": ".01"}]}


def test_outcome_side_orders_map_to_yes_book_without_changing_cash_economics():
    for outcome, action, side, price in (
        ("yes", "buy", "bid", ".81"),
        ("no", "buy", "ask", ".19"),
        ("yes", "sell", "ask", ".81"),
        ("no", "sell", "bid", ".19"),
    ):
        payload = order_intent(
            MARKET, outcome, action, "2.44", ".81", key=outcome + action, reduce_only=action == "sell"
        )
        assert payload["side"] == side
        assert D(payload["price"]) == D(price)
        assert payload["count"] == "2.44"
        assert "expiration_time" not in payload
    assert order_intent(MARKET, "no", "buy", 1, ".81", key="stable") == order_intent(
        MARKET, "no", "buy", 1, ".81", key="stable"
    )
    with pytest.raises(ValueError, match="grid"):
        order_intent(MARKET, "yes", "buy", 1, ".815", key="bad")
    with pytest.raises(ValueError, match="Expiring"):
        order_intent(MARKET, "yes", "buy", 1, ".81", key="bad", expires_at=100)


def test_early_sale_counts_exit_fees_and_reports_unfilled_size():
    book = {"yes": [(D(".50"), D(2))], "no": []}
    result = liquidation_value(book, "yes", 3, FeeSchedule("quadratic", D(1)))
    assert result == {
        "filled_quantity": "2.00",
        "unfilled_quantity": "1.00",
        "proceeds_after_fees": "0.9650",
        "full_displayed_exit_available": False,
    }
    stress = liquidation_value(book, "yes", 3, FeeSchedule("quadratic", D(1)), ".5", ".01")
    assert D(stress["filled_quantity"]) == 1
    assert D(stress["proceeds_after_fees"]) == D(".4725")


def test_positive_arithmetic_gain_can_still_have_negative_geometric_growth():
    result = capital_sensitivity(100, 90, source_failure_probabilities=(".08",))
    row = result["rows"][0]
    assert D(row["expected_pnl"]) == 2
    assert D(row["expected_log_growth"]) < 0
    assert D(result["geometric_break_even_failure_probability"]) < D(
        result["arithmetic_break_even_failure_probability"]
    )
