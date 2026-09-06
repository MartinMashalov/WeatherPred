"""Replay stored E001 arithmetic from original bytes, without network access."""

import json
from decimal import Decimal
from pathlib import Path

from weatherpred.basket import resolve_current_fee
from weatherpred.books import parse_book, purchase_cost
from weatherpred.contracts import payout_bounds
from weatherpred.timeutil import parse_time


def replay_baskets(archive, report_path="reports/E001_baskets.json"):
    report = json.loads(Path(report_path).read_text())

    def record(record_id):
        row = archive.db.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise ValueError(f"Missing provenance record {record_id}")
        return archive.json(row)

    events, scenarios = 0, 0
    for event in report["results"]:
        if event["status"] != "observed":
            continue
        markets = record(event["event_record_id"])["markets"]
        for integer, key in ((False, "payout_continuous"), (True, "payout_integer_assumption")):
            if payout_bounds(markets, integer_domain=integer) != event[key]:
                raise ValueError("Stored payout bounds differ from raw contract predicates")
        series = record(event["series_record_id"])["series"]
        changes = [change for rec in event["fee_record_ids"] for change in record(rec)["event_fee_changes"]]
        schedule = resolve_current_fee(series, changes, parse_time(event["book_request_started_at"]))
        books = {b["ticker"]: parse_book(b) for b in record(event["book_record_id"])["orderbooks"]}
        if set(books) != {m["ticker"] for m in markets}:
            raise ValueError("Missing or extra book member")
        for scenario in event["scenarios"]:
            retention, slip = {
                "optimistic": ("1", "0"),
                "realistic": ("0.5", "0.01"),
                "pessimistic": ("0.25", "0.02"),
            }[scenario["scenario"]]
            costs = [
                purchase_cost(books[t], scenario["side"], 1, schedule, retention, slip) for t in sorted(books)
            ]
            available = all(c is not None for c in costs)
            if available != scenario["full_displayed_size_available"]:
                raise ValueError("Displayed-size availability differs on replay")
            if available:
                total, fees = sum(c.total for c in costs), sum(c.fees for c in costs)
                if total != Decimal(scenario["cost_usd"]) or fees != Decimal(scenario["fees_usd"]):
                    raise ValueError("Fee/depth arithmetic differs on replay")
                for domain, suffix in (
                    ("payout_continuous", "continuous"),
                    ("payout_integer_assumption", "integer"),
                ):
                    net = Decimal(event[domain][scenario["side"] + "_min"]) - total
                    if net != Decimal(scenario["conditional_pnl_" + suffix + "_usd"]):
                        raise ValueError("Conditional basket payoff differs on replay")
                scenarios += 1
        events += 1
    return {
        "events_replayed_from_raw": events,
        "fully_quoted_scenarios_reproduced": scenarios,
        "network_requests": 0,
        "profitability_proven": False,
    }
