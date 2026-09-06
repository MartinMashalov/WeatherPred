from datetime import timedelta
from decimal import Decimal
from email.utils import format_datetime

import httpx
import pytest

from research.experiments.e009_paper import arrival_orders, fresh_books
from weatherpred.archive import Archive
from weatherpred.http import PublicClient
from weatherpred.paper import PaperLedger, reserved
from weatherpred.timeutil import iso, utcnow


def setup_arrival(tmp_path, *, style="taker", cached=False):
    archive = Archive(tmp_path / "archive")
    paper = PaperLedger(archive, "adapter")
    now = utcnow()
    submitted = now - timedelta(seconds=10)
    close = now + timedelta(minutes=20)
    paper.emit("account", {"account": "a", "initial_cash": "100"}, submitted)
    paper.emit(
        "order",
        {
            "id": "one",
            "account": "a",
            "event": "event",
            "ticker": "ticker",
            "side": "yes",
            "style": style,
            "quantity": "3",
            "limit": "0.60",
            "floor_strike": 80,
            "submitted_at": iso(submitted),
            "arrival_due_at": iso(submitted + timedelta(seconds=5)),
            "expires_at": iso(now + timedelta(seconds=55)),
            "close_at": iso(close),
            "fee_schedule": {"fee_type": "quadratic", "multiplier": "1", "balance_precision": "0.0001"},
            "scenario": {"depth_retained": "0.5", "slippage": "0"},
        },
        submitted,
    )

    def handle(request):
        path = request.url.path
        if path.endswith("/events/event"):
            value = {
                "markets": [
                    {
                        "ticker": "ticker",
                        "status": "active",
                        "close_time": iso(close),
                        "strike_type": "greater",
                        "floor_strike": 80,
                    }
                ]
            }
        elif path.endswith("/series/KXTEMPMIAH"):
            value = {"series": {"fee_type": "quadratic", "fee_multiplier": 1}}
        elif path.endswith("/events/fee_changes"):
            value = {"event_fee_changes": [], "cursor": ""}
        elif path.endswith("/markets/orderbooks"):
            value = {
                "orderbooks": [
                    {
                        "ticker": "ticker",
                        "orderbook_fp": {"yes_dollars": [["0.35", "50"]], "no_dollars": [["0.60", "3"]]},
                    }
                ]
            }
        else:
            raise AssertionError("Unexpected external endpoint")
        return httpx.Response(
            200, json=value, headers={"date": format_datetime(now), "age": "60" if cached else "0"}
        )

    client = PublicClient(archive, interval=0)
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handle))
    return archive, paper, client


def test_real_adapter_records_future_book_partial_fill_and_cancels_remainder(tmp_path):
    archive, paper, client = setup_arrival(tmp_path)
    try:
        arrival_orders(client, paper)
        assert len(paper.state["fills"]) == 1
        fill = paper.state["fills"][0]
        assert Decimal(fill["quantity"]) == Decimal("1.50")
        assert Decimal(fill["cost"]) == Decimal("0.6252")
        assert Decimal(paper.state["accounts"]["a"]["cash"]) == Decimal("99.3748")
        assert paper.state["orders"]["one"]["status"] == "cancelled"
        assert reserved(paper.state, "a") == 0
        assert (
            archive.db.execute("SELECT kind FROM records WHERE id=?", (fill["source_record_id"],)).fetchone()[
                0
            ]
            == "paper_books"
        )
        assert PaperLedger(archive, "adapter").state == paper.state
    finally:
        client.close()
        archive.close()


def test_cached_arrival_and_post_only_crossing_never_fill(tmp_path):
    for name, params in (("cached", {"cached": True}), ("maker", {"style": "maker"})):
        archive, paper, client = setup_arrival(tmp_path / name, **params)
        try:
            arrival_orders(client, paper)
            assert paper.state["fills"] == []
            assert paper.state["orders"]["one"]["status"] == "cancelled"
            assert Decimal(paper.state["accounts"]["a"]["cash"]) == 100
        finally:
            client.close()
            archive.close()


def test_fresh_book_rejects_cached_http_evidence(tmp_path):
    archive, _, client = setup_arrival(tmp_path, cached=True)
    try:
        with pytest.raises(ValueError, match="Stale"):
            fresh_books(client, {"ticker"})
    finally:
        client.close()
        archive.close()
