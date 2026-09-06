from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

from weatherpred.archive import Archive
from weatherpred.fees import FeeSchedule
from weatherpred.market_making import inventory_report, net_inventory, passive_quotes, predicate, valid_tick
from weatherpred.paper import PaperLedger, maker_trade

T = datetime(2026, 9, 6, 15, tzinfo=UTC)
MARKET = {"ticker": "M", "price_ranges": [{"start": "0", "end": "1", "step": "0.01"}]}
BOOK = {"yes": [(D("0.40"), D(10))], "no": [(D("0.50"), D(10))]}


def test_passive_inventory_skew_and_actual_fee_threshold():
    fees = FeeSchedule("quadratic_with_maker_fees", D(1))
    assert passive_quotes(BOOK, MARKET, "join", 0, fees) == {"yes": D(".40"), "no": D(".50")}
    assert passive_quotes(BOOK, MARKET, "improve_one_cent", 0, fees) == {"yes": D(".41"), "no": D(".51")}
    long = passive_quotes(BOOK, MARKET, "inventory_skew", 2, fees)
    short = passive_quotes(BOOK, MARKET, "inventory_skew", -2, fees)
    assert long == {"yes": D(".39"), "no": D(".53")}
    assert short == {"yes": D(".43"), "no": D(".49")}
    for prices in (long, short):
        assert prices["yes"] < 1 - BOOK["no"][0][0]
        assert prices["no"] < 1 - BOOK["yes"][0][0]
    narrow = {"yes": [(D(".48"), D(10))], "no": [(D(".50"), D(10))]}
    assert passive_quotes(narrow, MARKET, "improve_one_cent", 0, fees) is None
    assert passive_quotes(BOOK, MARKET, "join", 0, FeeSchedule("quadratic_with_maker_fees", D(20))) is None
    assert not valid_tick(MARKET, D(".405"))
    assert passive_quotes(BOOK, {**MARKET, "price_ranges": []}, "join", 0, fees) is None


def test_two_sided_fill_evidence_unmatched_loss_and_locked_cash(tmp_path):
    archive = Archive(tmp_path)
    ledger = PaperLedger(archive, "pair-fixture")
    ledger.emit("account", {"account": "A", "initial_cash": "100"}, T)
    for side, price, quantity in (("yes", ".40", "3"), ("no", ".50", "1")):
        ledger.emit(
            "order",
            {
                "id": side,
                "account": "A",
                "event": "E",
                "ticker": "M",
                "side": side,
                "style": "maker",
                "quantity": quantity,
                "limit": price,
                "floor_strike": 80,
                "submitted_at": T.isoformat(),
                "arrival_due_at": (T + timedelta(seconds=5)).isoformat(),
                "expires_at": (T + timedelta(seconds=65)).isoformat(),
                "close_at": (T + timedelta(minutes=30)).isoformat(),
                "fee_schedule": {
                    "fee_type": "quadratic_with_maker_fees",
                    "multiplier": "1",
                    "balance_precision": ".0001",
                },
            },
            T,
        )
        ledger.emit(
            "arrival", {"order_id": side, "queue_ahead": "2", "book_record_id": 1}, T + timedelta(seconds=6)
        )
    sell_yes = {
        "trade_id": "sell-yes",
        "ticker": "M",
        "count_fp": "14",
        "yes_price_dollars": ".39",
        "no_price_dollars": ".61",
        "taker_outcome_side": "no",
        "taker_book_side": "ask",
        "is_block_trade": False,
        "created_time": (T + timedelta(seconds=8)).isoformat(),
    }
    assert maker_trade(ledger.state["orders"]["no"], sell_yes, T + timedelta(seconds=9)) is None
    assert maker_trade(ledger.state["orders"]["yes"], sell_yes, T + timedelta(seconds=66)) is None
    touch = {**sell_yes, "yes_price_dollars": ".40", "no_price_dollars": ".60"}
    assert maker_trade(ledger.state["orders"]["yes"], touch, T + timedelta(seconds=9)) is None
    buy_yes = {
        **sell_yes,
        "trade_id": "buy-yes",
        "count_fp": "6",
        "yes_price_dollars": ".51",
        "no_price_dollars": ".49",
        "taker_outcome_side": "yes",
        "taker_book_side": "bid",
        "created_time": (T + timedelta(seconds=10)).isoformat(),
    }
    for side, trade in (("yes", sell_yes), ("no", buy_yes)):
        at = T + timedelta(seconds=12)
        order = ledger.state["orders"][side]
        advance = maker_trade(order, trade, at, ".25")
        assert advance["quantity"] == ("3" if side == "yes" else "1")
        ledger.emit(
            "queue",
            {"order_id": side, "trade_id": trade["trade_id"], "queue_ahead": advance["queue_ahead"]},
            at,
        )
        assert maker_trade(ledger.state["orders"][side], trade, at, ".25") is None
        ledger.emit(
            "fill",
            {
                "order_id": side,
                "quantity": advance["quantity"],
                "price": order["limit"],
                "fill_id": trade["trade_id"],
                "evidence_at": trade["created_time"],
                "source_record_id": 2,
            },
            at,
        )
    r = inventory_report(ledger.state, "A")
    assert net_inventory(ledger.state, "A", "M") == 2
    assert D(r["cash"]) == D("98.2830")
    assert D(r["fees"]) == D(".0170")
    assert D(r["positions"][0]["pair_component_pnl"]) == D(".0914")
    assert D(r["positions"][0]["unmatched_cost_at_risk"]) == D(".8084")
    assert D(r["terminal_pnl_lower_bound_on_current_fills"]) == D("-.7170")
    assert D(r["terminal_pnl_upper_bound_on_current_fills"]) == D("1.2830")
    # The positive paired component did not credit cash or realize profit.
    assert D(r["realized_pnl"]) == 0
    ledger.emit(
        "settlement", {"event": "E", "labels": {"M": 0}, "source_record_id": 3}, T + timedelta(hours=1)
    )
    final = inventory_report(ledger.state, "A")
    assert D(final["realized_pnl"]) == D("-.7170")
    assert D(final["cash"]) == D("99.2830")
    assert final["positions"] == []
    assert PaperLedger(archive, "pair-fixture").state == ledger.state
    archive.close()


def test_finalized_contract_identity_includes_rule_and_notional_changes():
    original = {
        "ticker": "M",
        "event_ticker": "E",
        "floor_strike": 80,
        "cap_strike": 82,
        "rules_primary": "station A",
        "market_type": "binary",
        "notional_value_dollars": "1.0000",
    }
    assert predicate(original) == predicate({**original, "status": "finalized", "result": "no"})
    for field, changed in (
        ("rules_primary", "station B"),
        ("cap_strike", 83),
        ("notional_value_dollars", "2.0000"),
    ):
        assert predicate(original) != predicate({**original, field: changed})
