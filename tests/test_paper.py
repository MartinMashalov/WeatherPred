from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from weatherpred.archive import Archive
from weatherpred.paper import PaperLedger, maker_trade, reserved, taker_slices

T = datetime(2026, 9, 6, 13, 30, tzinfo=UTC)


def order(**changes):
    return {
        "id": "order1",
        "account": "model:scenario",
        "event": "hour",
        "ticker": "T80",
        "side": "yes",
        "style": "taker",
        "quantity": "3",
        "limit": "0.60",
        "submitted_at": T.isoformat(),
        "arrival_due_at": (T + timedelta(seconds=5)).isoformat(),
        "expires_at": (T + timedelta(seconds=65)).isoformat(),
        "close_at": (T + timedelta(minutes=30)).isoformat(),
        "floor_strike": 80,
        "fee_schedule": {"fee_type": "quadratic", "multiplier": "1", "balance_precision": "0.0001"},
        **changes,
    }


def ledger(tmp_path):
    archive = Archive(tmp_path / "archive")
    result = PaperLedger(archive, "fixture")
    result.emit("account", {"account": "model:scenario", "initial_cash": "100"}, T)
    return archive, result


def test_depth_partial_fill_limit_and_slippage_once():
    book = {"yes": [(D("0.35"), D(100))], "no": [(D("0.60"), D(3)), (D("0.50"), D(2))]}
    assert taker_slices(book, "yes", 3, ".50", ".5", ".01") == [{"price": "0.41", "quantity": "1.50"}]
    assert taker_slices(book, "yes", 3, ".52", ".5", ".01") == [
        {"price": "0.41", "quantity": "1.50"},
        {"price": "0.51", "quantity": "1.00"},
    ]
    assert taker_slices(book, "no", 3, ".64", "1", "0") == []


def test_partial_cash_fees_cancel_settle_and_replay(tmp_path):
    archive, paper = ledger(tmp_path)
    try:
        paper.emit("order", order(), T)
        assert reserved(paper.state, "model:scenario") == D("1.8825")
        arrival = T + timedelta(seconds=6)
        paper.emit("arrival", {"order_id": "order1", "queue_ahead": "0", "book_record_id": 10}, arrival)
        for index, quantity, price in ((0, "2", "0.40"), (1, "0.50", "0.45")):
            paper.emit(
                "fill",
                {
                    "order_id": "order1",
                    "quantity": quantity,
                    "price": price,
                    "fill_id": str(index),
                    "evidence_at": arrival.isoformat(),
                    "source_record_id": 10,
                },
                arrival,
            )
        account = paper.state["accounts"]["model:scenario"]
        assert D(account["cash"]) == D("98.9327")
        assert D(account["fees"]) == D("0.0423")
        assert paper.state["orders"]["order1"]["remaining"] == "0.50"
        before = paper.state
        with pytest.raises(ValueError, match="already applied"):
            paper.emit(
                "fill",
                {
                    "order_id": "order1",
                    "quantity": "0.1",
                    "price": "0.4",
                    "fill_id": "0",
                    "evidence_at": arrival.isoformat(),
                },
                arrival,
            )
        assert paper.state is before
        paper.emit("cancel", {"order_id": "order1", "reason": "IOC remainder"}, arrival)
        assert reserved(paper.state, "model:scenario") == 0
        paper.emit(
            "settlement",
            {"event": "hour", "labels": {"T80": 1}, "source_record_id": 20},
            T + timedelta(hours=2),
        )
        assert D(paper.state["accounts"]["model:scenario"]["cash"]) == D("101.4327")
        assert D(paper.state["accounts"]["model:scenario"]["realized_pnl"]) == D("1.4327")
        assert paper.state["positions"] == {}
        assert PaperLedger(archive, "fixture").state == paper.state
        with pytest.raises(ValueError, match="already settled"):
            paper.emit(
                "settlement",
                {"event": "hour", "labels": {"T80": 1}, "source_record_id": 20},
                T + timedelta(hours=2),
            )
    finally:
        archive.close()


def test_latency_limits_reservation_and_drawdown_fail_closed(tmp_path):
    archive, paper = ledger(tmp_path)
    try:
        with pytest.raises(ValueError, match="exposure"):
            paper.emit("order", order(quantity="20"), T)
        paper.emit("order", order(), T)
        with pytest.raises(ValueError, match="arrival"):
            paper.emit(
                "arrival",
                {"order_id": "order1", "queue_ahead": "0", "book_record_id": 10},
                T + timedelta(seconds=4),
            )
        arrival = T + timedelta(seconds=6)
        paper.emit("arrival", {"order_id": "order1", "queue_ahead": "0", "book_record_id": 10}, arrival)
        fill = {
            "order_id": "order1",
            "quantity": "1",
            "price": "0.7",
            "fill_id": "first",
            "evidence_at": arrival.isoformat(),
        }
        with pytest.raises(ValueError, match="limit"):
            paper.emit("fill", fill, arrival)
        with pytest.raises(ValueError, match="expiration"):
            paper.emit("fill", dict(fill, price="0.5"), T + timedelta(seconds=66))
        paper.emit("mark", {"account": "model:scenario", "equity": "80"}, arrival)
        with pytest.raises(ValueError, match="kill switch"):
            paper.emit("order", order(id="new"), T)
    finally:
        archive.close()


def test_maker_queue_requires_direction_time_and_strict_trade_through():
    resting = {
        **order(style="maker", limit="0.4"),
        "arrived_at": (T + timedelta(seconds=6)).isoformat(),
        "queue_ahead": "10",
        "remaining": "3",
        "seen_trades": [],
    }
    trade = {
        "trade_id": "print1",
        "ticker": "T80",
        "count_fp": "14",
        "yes_price_dollars": "0.39",
        "no_price_dollars": "0.61",
        "taker_outcome_side": "no",
        "taker_book_side": "ask",
        "is_block_trade": False,
        "created_time": (T + timedelta(seconds=10)).isoformat(),
    }
    received = T + timedelta(seconds=11)
    update = maker_trade(resting, trade, received)
    assert update["queue_ahead"] == "0" and update["quantity"] == "1.00"
    assert (
        maker_trade(resting, dict(trade, yes_price_dollars="0.40", no_price_dollars="0.60"), received) is None
    )
    assert (
        maker_trade(resting, dict(trade, taker_outcome_side="yes", taker_book_side="bid"), received) is None
    )
    assert maker_trade(resting, dict(trade, is_block_trade=True), received) is None
    assert maker_trade(resting, dict(trade, created_time=T.isoformat()), received) is None
    assert maker_trade(dict(resting, seen_trades=["print1"]), trade, received) is None
    assert maker_trade(resting, trade, T + timedelta(seconds=66)) is None
    with pytest.raises(ValueError, match="direction"):
        maker_trade(resting, dict(trade, taker_book_side="bid"), received)
