from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from weatherpred.archive import Archive
from weatherpred.market_making import inventory_report
from weatherpred.netted_paper import NettedPaperLedger
from weatherpred.paper import PaperLedger

T = datetime(2026, 9, 6, 15, tzinfo=UTC)


def fill_pair(ledger, second_price=".50", yes_quantity="3"):
    ledger.emit("account", {"account": "A", "initial_cash": "100"}, T)
    for side, price, quantity in (("yes", ".40", yes_quantity), ("no", second_price, "1")):
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
            "arrival", {"order_id": side, "queue_ahead": "0", "book_record_id": 1}, T + timedelta(seconds=6)
        )
        ledger.emit(
            "fill",
            {
                "order_id": side,
                "quantity": quantity,
                "price": price,
                "fill_id": side,
                "evidence_at": (T + timedelta(seconds=8)).isoformat(),
                "source_record_id": 2,
            },
            T + timedelta(seconds=9),
        )


@pytest.mark.parametrize("label", [0, 1])
def test_immediate_offset_returns_cash_and_preserves_terminal_economics(tmp_path, label):
    archive = Archive(tmp_path)
    locked, netted = PaperLedger(archive, "locked"), NettedPaperLedger(archive, "netted")
    for ledger in (locked, netted):
        fill_pair(ledger)
    a = netted.state["accounts"]["A"]
    assert D(a["cash"]) == D("99.2830")
    assert D(a["realized_pnl"]) == D(".0914")
    assert len(netted.state["nettings"]) == 1
    assert netted.state["nettings"][0]["cash_returned"] == "1"
    assert list(netted.state["positions"]) == ["A:M:yes"]
    assert D(netted.state["positions"]["A:M:yes"]["quantity"]) == 2
    assert D(netted.state["positions"]["A:M:yes"]["cost"]) == D(".8084")
    before, after = inventory_report(locked.state, "A"), inventory_report(netted.state, "A")
    for key in ("terminal_pnl_lower_bound_on_current_fills", "terminal_pnl_upper_bound_on_current_fills"):
        assert D(before[key]) == D(after[key])
    assert D(after["terminal_pnl_lower_bound_on_current_fills"]) < 0
    assert NettedPaperLedger(archive, "netted").state == netted.state
    for ledger in (locked, netted):
        ledger.emit(
            "settlement",
            {"event": "E", "labels": {"M": label}, "source_record_id": 3},
            T + timedelta(hours=1),
        )
    for key in ("cash", "fees", "realized_pnl"):
        assert D(netted.state["accounts"]["A"][key]) == D(locked.state["accounts"]["A"][key])
    assert D(a["cash"]) == D("99.2830")  # previous immutable state is not mutated
    archive.close()


def test_offset_may_realize_a_loss_and_cannot_be_replayed_twice(tmp_path):
    archive = Archive(tmp_path)
    ledger = NettedPaperLedger(archive, "loss")
    fill_pair(ledger, second_price=".70", yes_quantity="1")
    assert ledger.state["positions"] == {}
    assert D(ledger.state["accounts"]["A"]["cash"]) == D("99.8921")
    assert D(ledger.state["accounts"]["A"]["realized_pnl"]) == D("-.1079")
    assert NettedPaperLedger(archive, "loss").state == ledger.state
    before = ledger.state
    with pytest.raises(ValueError, match="not fillable|already applied"):
        ledger.emit(
            "fill",
            {
                "order_id": "no",
                "quantity": "1",
                "price": ".70",
                "fill_id": "no",
                "evidence_at": (T + timedelta(seconds=8)).isoformat(),
                "source_record_id": 2,
            },
            T + timedelta(seconds=10),
        )
    assert ledger.state is before
    archive.close()
