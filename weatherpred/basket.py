"""E001: an exploratory census of displayed basket costs, not a fill backtest."""

import json
import logging
from collections import Counter
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from weatherpred.archive import canonical
from weatherpred.books import parse_book, purchase_cost
from weatherpred.contracts import payout_bounds
from weatherpred.fees import FeeSchedule
from weatherpred.timeutil import iso, parse_time, utcnow

LOG = logging.getLogger(__name__)
D = Decimal


def resolve_current_fee(series, changes, at):
    fee_type, multiplier = series.get("fee_type"), series.get("fee_multiplier")
    for change in sorted(changes, key=lambda x: parse_time(x["scheduled_ts"])):
        if parse_time(change["scheduled_ts"]) > at:
            continue
        # A null override clears the event override and restores the current series.
        fee_type = change.get("fee_type_override") or series.get("fee_type")
        m = change.get("fee_multiplier_override")
        multiplier = series.get("fee_multiplier") if m is None else m
    if fee_type is None or multiplier is None:
        raise ValueError("Unknown fee schedule")
    schedule = FeeSchedule(fee_type, D(str(multiplier)))
    schedule.model_fee("0.5", 1)  # fail closed on unsupported models
    return schedule


def scan_event(client, event_ticker):
    result = {
        "event_ticker": event_ticker,
        "observed_at": iso(utcnow()),
        "experiment": "E001",
        "is_fill": False,
        "trade_authorized_by_evidence": False,
    }
    event_data, event_record = client.json("/events/" + event_ticker, kind="event", key=event_ticker)
    event, markets = event_data["event"], event_data["markets"]
    result["event_record_id"] = event_record
    result["markets"] = len(markets)
    if not event.get("mutually_exclusive"):
        return dict(result, status="rejected", reason="event_not_mutually_exclusive")
    now = utcnow()
    if not markets or any(m["status"] != "active" or parse_time(m["close_time"]) <= now for m in markets):
        return dict(result, status="rejected", reason="closed_or_missing_member")
    result["payout_continuous"] = payout_bounds(markets)
    result["payout_integer_assumption"] = payout_bounds(markets, integer_domain=True)
    series_data, series_record = client.json(
        "/series/" + event["series_ticker"], kind="series_current", key=event["series_ticker"]
    )
    changes, fee_records = [], []
    for page, record_id in client.pages(
        "/events/fee_changes",
        "event_fee_changes",
        {"event_ticker": event_ticker, "limit": 1000},
        kind="event_fee_changes",
        key=event_ticker,
    ):
        changes.extend(page)
        fee_records.append(record_id)
    schedule = resolve_current_fee(series_data["series"], changes, utcnow())
    result.update(
        series_record_id=series_record,
        fee_record_ids=fee_records,
        fee_type=schedule.fee_type,
        fee_multiplier=str(schedule.multiplier),
    )
    tickers = {m["ticker"] for m in markets}
    started = utcnow()
    data, book_record = client.json(
        "/markets/orderbooks", [("tickers", t) for t in sorted(tickers)], kind="book_batch", key=event_ticker
    )
    received = utcnow()
    result.update(
        book_record_id=book_record,
        book_request_started_at=iso(started),
        book_received_at=iso(received),
        elapsed_seconds=(received - started).total_seconds(),
    )
    raw = client.archive.db.execute("SELECT * FROM records WHERE id=?", (book_record,)).fetchone()
    headers = json.loads(raw["metadata"])["headers"]
    date = parsedate_to_datetime(headers["date"]) if "date" in headers else None
    age = max(float(headers.get("age", 0)), (received - date).total_seconds() if date else float("inf"))
    result["http_age_seconds"] = age if age != float("inf") else None
    if (received - started).total_seconds() > 5 or age > 5:
        return dict(result, status="rejected", reason="snapshot_too_slow_or_cached")
    rows = data.get("orderbooks", [])
    if {b["ticker"] for b in rows} != tickers or len(rows) != len(tickers):
        raise ValueError("Batch book response membership mismatch")
    if any(parse_time(m["close_time"]) <= received for m in markets):
        return dict(result, status="rejected", reason="closed_during_snapshot")
    books = {b["ticker"]: parse_book(b) for b in rows}
    result["legs"] = []
    for m in markets:
        b = books[m["ticker"]]
        y = b["yes"][0][0] if b["yes"] else None
        a = 1 - b["no"][0][0] if b["no"] else None
        result["legs"].append(
            {
                "ticker": m["ticker"],
                "bid": str(y) if y is not None else None,
                "ask": str(a) if a is not None else None,
                "spread": str(a - y) if y is not None and a is not None else None,
                "volume": m.get("volume_fp"),
                "open_interest": m.get("open_interest_fp"),
            }
        )
    scenarios = []
    for name, retention, slip in (
        ("optimistic", "1", "0"),
        ("realistic", "0.5", "0.01"),
        ("pessimistic", "0.25", "0.02"),
    ):
        for side in ("yes", "no"):
            costs = [purchase_cost(books[t], side, 1, schedule, retention, slip) for t in sorted(tickers)]
            row = {
                "scenario": name,
                "side": side,
                "full_displayed_size_available": all(c is not None for c in costs),
            }
            if row["full_displayed_size_available"]:
                cost = sum(c.total for c in costs)
                row.update(
                    cost_usd=str(cost),
                    fees_usd=str(sum(c.fees for c in costs)),
                    conditional_pnl_continuous_usd=str(D(result["payout_continuous"][side + "_min"]) - cost),
                    conditional_pnl_integer_usd=str(
                        D(result["payout_integer_assumption"][side + "_min"]) - cost
                    ),
                )
            scenarios.append(row)
    result.update(
        status="observed",
        scenarios=scenarios,
        limitations=[
            "displayed depth is not a fill or latency-survival measurement",
            "multi-leg execution is non-atomic",
            "integer precision and source conflicts unresolved",
            "normal settlement only; discretionary fair-price contingency excluded",
        ],
    )
    return result


def scan_baskets(client, output="reports"):
    census = json.loads(Path(output, "markets_open.json").read_text())
    selected = sorted(
        {
            m["event_ticker"]
            for m in census["markets"]
            if m["discovery_family"] in ("temperature_max", "temperature_min")
        }
    )
    results = []
    for index, event in enumerate(selected):
        try:
            result = scan_event(client, event)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            result = {"event_ticker": event, "status": "error", "reason": str(exc), "is_fill": False}
        client.archive.append(
            "basket_diagnostic", event, utcnow(), {"experiment": "E001"}, canonical(result).encode()
        )
        results.append(result)
        if index % 10 == 0 or index == len(selected) - 1:
            LOG.info(
                "E001 %s/%s events; states %s",
                index + 1,
                len(selected),
                dict(Counter(r["status"] for r in results)),
            )
    available = [s for r in results for s in r.get("scenarios", []) if s["full_displayed_size_available"]]
    summary = {
        "generated_at": iso(utcnow()),
        "events": len(selected),
        "states": dict(Counter(r["status"] for r in results)),
        "fully_quoted_scenarios": len(available),
        "positive_conditional_integer_scenarios": sum(
            D(s["conditional_pnl_integer_usd"]) > 0 for s in available
        ),
        "positive_conditional_continuous_scenarios": sum(
            D(s["conditional_pnl_continuous_usd"]) > 0 for s in available
        ),
        "best_conditional_integer_pnl_usd": str(
            max((D(s["conditional_pnl_integer_usd"]) for s in available), default=D(0))
        ),
        "filled_orders": 0,
        "profitability_proven": False,
    }
    report = {"summary": summary, "results": results}
    client.archive.append("experiment_report", "E001", utcnow(), {}, canonical(report).encode())
    Path(output, "E001_baskets.json").write_text(json.dumps(report, indent=2))
    return summary
