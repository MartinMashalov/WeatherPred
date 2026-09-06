from decimal import Decimal

import pytest

from weatherpred.quote_screen import choose_conditional_purchase

FREE = {"slippage_usd": "0", "fee_multiplier": "0", "balance_precision": "0.0001"}


def test_midpoint_forecast_cannot_earn_spread_by_crossing_either_side():
    assert choose_conditional_purchase([{"ticker": "A", "bid": 0.2, "ask": 0.4}], [0.3], FREE) is None


def test_yes_and_no_purchase_sides_use_actual_opposite_quotes():
    quote = {"ticker": "A", "bid": 0.2, "ask": 0.4}
    yes = choose_conditional_purchase([quote], [0.7], FREE)
    no = choose_conditional_purchase([quote], [0.1], FREE)
    assert yes["side"] == "yes" and Decimal(yes["assumed_cost"]) == Decimal("0.4")
    assert no["side"] == "no" and Decimal(no["assumed_cost"]) == Decimal("0.8")
    assert yes["actual_fills"] == no["actual_fills"] == 0


def test_costs_can_remove_an_apparent_edge_and_ties_do_not_use_outcomes():
    quotes = [
        {"ticker": "B", "bid": 0.45, "ask": 0.5},
        {"ticker": "A", "bid": 0.45, "ask": 0.5},
    ]
    assert choose_conditional_purchase(quotes, [0.53, 0.53], FREE)["ticker"] == "A"
    costly = {"slippage_usd": "0.02", "fee_multiplier": "1", "balance_precision": "0.01"}
    assert choose_conditional_purchase(quotes, [0.53, 0.53], costly) is None
    with pytest.raises(ValueError):
        choose_conditional_purchase([quotes[0]], [float("nan")], FREE)
