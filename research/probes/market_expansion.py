"""Bounded public census and conditional payout-relation screen; never submits orders.

The universe selection is frozen before refreshing quotes. Same-event threshold
relations require matching normalized primary rules and exact secondary rules.
Cross-series hurricane relations also require matching dates and source agencies.
This is a discovery snapshot, not simultaneous fills or a profitability test.
"""

import argparse
import itertools
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.basket import resolve_current_fee
from weatherpred.books import parse_book, purchase_cost
from weatherpred.http import PublicClient
from weatherpred.timeutil import iso, parse_time, utcnow

D = Decimal
COUNTS = {"KXHURCTOTMAJ": 0, "KXHURCTOT": 1, "KXTROPSTORM": 2}
COUNT_TERMS = {
    "KXHURCTOTMAJ": "hurricanes of hurricane category 3 or above",
    "KXHURCTOT": "hurricanes of hurricane category 1 or above",
    "KXTROPSTORM": "storms with maximum sustained windspeeds of 39 miles per hour or above",
}


def count_period(market):
    series = market["ticker"].split("-")[0]
    if series not in COUNT_TERMS or market.get("strike_type") != "greater":
        raise ValueError("Unsupported count family")
    pattern = (
        r"If the NOAA's National Hurricane Center records more than (\d+) "
        + re.escape(COUNT_TERMS[series])
        + r" between (.+?), then the market resolves to Yes\."
    )
    match = re.fullmatch(pattern, market["rules_primary"])
    if not match or D(match[1]) != D(str(market["floor_strike"])):
        raise ValueError("Count definition changed")
    return match[2]


def threshold_signature(market):
    """Reject unsupported wording, rather than infer semantics from ticker names."""
    kind = market.get("strike_type")
    threshold = market.get("floor_strike")
    if kind not in ("greater", "greater_or_equal") or threshold is None:
        raise ValueError("Not a supported lower threshold")
    patterns = (
        r"(?<=more than )\d+(?:\.\d+)?",
        r"(?<=strictly greater than )\d+(?:\.\d+)?",
        r"(?<=at least )\d+(?:\.\d+)?",
    )
    text = market["rules_primary"]
    hits = [(p, re.search(p, text)) for p in patterns]
    hits = [(p, m) for p, m in hits if m is not None and D(m[0]) == D(str(threshold))]
    if len(hits) != 1:
        raise ValueError("Ambiguous threshold wording")
    pattern, _ = hits[0]
    return kind, re.sub(pattern, "<threshold>", text, count=1), market["rules_secondary"]


def selected_series(universe, original_markets):
    originally_active = {m["ticker"].split("-")[0] for m in original_markets}
    selected = []
    for s in universe["series"]:
        ticker = s["ticker"]
        family = s["discovery_family"]
        # All snow series are queried, including those with no original open market.
        include = family == "snow" or (
            ticker in originally_active
            and (
                family == "temperature_min"
                or ticker.startswith("KXAVGTK")
                or (family == "precipitation" and ticker not in ("KXRAIN", "KXRAINWKND"))
                or ticker in COUNTS
            )
        )
        if include:
            selected.append(s)
    return sorted(selected, key=lambda s: s["ticker"])


def run(client, output):
    archive = client.archive
    universe = json.loads(Path("reports/universe.json").read_text())
    original = json.loads(Path("reports/markets_open.json").read_text())
    selection = selected_series(universe, original["markets"])
    if len(selection) > 100:
        raise ValueError("Bounded discovery requires a new scope decision above 100 series")
    result = {
        "started_at": iso(utcnow()),
        "universe_catalog_record_id": universe["catalog_record_id"],
        "original_open_census_at": original["generated_at"],
        "selected_series": [s["ticker"] for s in selection],
        "selection_rule": "Originally open minimum-temperature markets, weekly heat streaks, monthly rain and three annual Atlantic storm-count series, plus all catalog snow series.",
        "raw_source_ids": [],
        "errors": [],
        "series": [],
        "markets": [],
        "relations": [],
        "profitability_proven": False,
        "orders_submitted": 0,
    }
    markets, by_event, series_data = [], defaultdict(list), {}
    for index, s in enumerate(selection):
        ticker = s["ticker"]
        current = []
        try:
            for page, rec in client.pages(
                "/markets",
                "markets",
                {"series_ticker": ticker, "status": "open", "limit": 1000},
                kind="expansion_markets",
                key=ticker,
            ):
                current.extend(page)
                result["raw_source_ids"].append(rec)
            result["series"].append(
                {"ticker": ticker, "family": s["discovery_family"], "open_markets": len(current)}
            )
            if current:
                data, rec = client.json("/series/" + ticker, kind="expansion_series", key=ticker)
                result["raw_source_ids"].append(rec)
                series_data[ticker] = data["series"]
            for m in current:
                if m["status"] != "active" or parse_time(m["close_time"]) <= utcnow():
                    continue
                m["discovery_family"] = s["discovery_family"]
                markets.append(m)
                by_event[m["event_ticker"]].append(m)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            result["errors"].append({"stage": "series", "key": ticker, "error": str(exc)})
        if index % 10 == 0:
            print(json.dumps({"series_completed": index + 1, "of": len(selection)}), flush=True)
    fees, event_sources = {}, {}
    for event, members in sorted(by_event.items()):
        series_name = members[0]["ticker"].split("-")[0]
        try:
            data, rec = client.json("/events/" + event, kind="expansion_event", key=event)
            result["raw_source_ids"].append(rec)
            event_sources[event] = data["event"]["settlement_sources"]
            changes = []
            for page, rec in client.pages(
                "/events/fee_changes",
                "event_fee_changes",
                {"event_ticker": event, "limit": 1000},
                kind="expansion_fees",
                key=event,
            ):
                changes.extend(page)
                result["raw_source_ids"].append(rec)
            fees[event] = resolve_current_fee(series_data[series_name], changes, utcnow())
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            result["errors"].append({"stage": "event", "key": event, "error": str(exc)})
    books, book_refs = {}, {}
    tickers = sorted(m["ticker"] for m in markets)
    for start in range(0, len(tickers), 100):
        requested = tickers[start : start + 100]
        try:
            data, rec = client.json(
                "/markets/orderbooks",
                [("tickers", t) for t in requested],
                kind="expansion_books",
                key="market_expansion",
            )
            result["raw_source_ids"].append(rec)
            raw = archive.db.execute("SELECT * FROM records WHERE id=?", (rec,)).fetchone()
            meta = json.loads(raw["metadata"])
            received = parse_time(raw["available_at"])
            headers = meta["headers"]
            server = parsedate_to_datetime(headers["date"]) if headers.get("date") else None
            if (
                server is None
                or not -2 <= (received - server).total_seconds() <= 5
                or float(headers.get("age", 0)) > 5
                or (received - parse_time(meta["request_started_at"])).total_seconds() > 5
            ):
                raise ValueError("Book batch failed freshness checks")
            rows = data["orderbooks"]
            if {r["ticker"] for r in rows} != set(requested) or len(rows) != len(requested):
                raise ValueError("Book batch membership mismatch")
            for row in rows:
                try:
                    books[row["ticker"]] = parse_book(row)
                    book_refs[row["ticker"]] = rec
                except ValueError as exc:
                    result["errors"].append({"stage": "book", "key": row["ticker"], "error": str(exc)})
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            result["errors"].append({"stage": "books", "key": requested[0], "error": str(exc)})
    for m in markets:
        b = books.get(m["ticker"])
        row = dict(m)
        if b:
            bid = b["yes"][0] if b["yes"] else None
            no_bid = b["no"][0] if b["no"] else None
            row["book"] = {
                "record_id": book_refs[m["ticker"]],
                "yes_bid": str(bid[0]) if bid else None,
                "yes_ask": str(1 - no_bid[0]) if no_bid else None,
                "yes_bid_depth": str(bid[1]) if bid else "0",
                "yes_ask_depth": str(no_bid[1]) if no_bid else "0",
            }
        result["markets"].append(row)

    implications = []
    for event, members in by_event.items():
        for high, low in itertools.permutations(members, 2):
            try:
                if threshold_signature(high) == threshold_signature(low) and D(str(high["floor_strike"])) > D(
                    str(low["floor_strike"])
                ):
                    implications.append((high, low, "same_event_nested_threshold"))
            except ValueError:
                pass
    count_members = [m for m in markets if m["ticker"].split("-")[0] in COUNTS]
    for narrow, broad in itertools.permutations(count_members, 2):
        sn, sb = (m["ticker"].split("-")[0] for m in (narrow, broad))
        try:
            dates = [count_period(m) for m in (narrow, broad)]
        except ValueError:
            continue
        if (
            COUNTS[sn] < COUNTS[sb]
            and all(dates)
            and dates[0] == dates[1]
            and narrow["strike_type"] == broad["strike_type"] == "greater"
            and event_sources.get(narrow["event_ticker"]) == event_sources.get(broad["event_ticker"])
            and event_sources.get(narrow["event_ticker"])
            and D(str(narrow["floor_strike"])) >= D(str(broad["floor_strike"]))
        ):
            implications.append(
                (narrow, broad, "annual_atlantic_count_subset_conditional_on_shared_NHC_review")
            )
    for narrow, broad, mechanism in implications:
        # A implies B: NO(A) + YES(B) pays at least one dollar.
        a, b = narrow["ticker"], broad["ticker"]
        row = {
            "mechanism": mechanism,
            "no_ticker": a,
            "yes_ticker": b,
            "conditional_minimum_payout": "1",
            "scenarios": [],
        }
        if a in books and b in books and all(m["event_ticker"] in fees for m in (narrow, broad)):
            row["same_book_batch"] = book_refs[a] == book_refs[b]
            row["book_record_ids"] = [book_refs[a], book_refs[b]]
            for label, retained, slip in (
                ("displayed", "1", "0"),
                ("half_depth_1c", ".5", ".01"),
                ("quarter_depth_2c", ".25", ".02"),
            ):
                costs = [
                    purchase_cost(books[m["ticker"]], side, 1, fees[m["event_ticker"]], retained, slip)
                    for m, side in ((narrow, "no"), (broad, "yes"))
                ]
                scenario = {"name": label, "full_size_available": all(c is not None for c in costs)}
                if all(c is not None for c in costs):
                    cost = sum(c.total for c in costs)
                    scenario.update(
                        total_cost=str(cost),
                        fees=str(sum(c.fees for c in costs)),
                        conditional_profit_floor=str(1 - cost),
                    )
                row["scenarios"].append(scenario)
        result["relations"].append(row)
    result["finished_at"] = iso(utcnow())
    result["summary"] = {
        "queried_series": len(selection),
        "active_series": sum(s["open_markets"] > 0 for s in result["series"]),
        "active_markets": len(markets),
        "active_events": len(by_event),
        "valid_book_count": len(books),
        "relations": len(implications),
        "families": dict(Counter(m["discovery_family"] for m in markets)),
        "positive_displayed_relations": sum(
            any(
                s.get("name") == "displayed" and D(s.get("conditional_profit_floor", "-1")) > 0
                for s in r["scenarios"]
            )
            for r in result["relations"]
        ),
        "errors": len(result["errors"]),
    }
    result["limitations"] = [
        "A quoted conditional payout bound is not an executed arbitrage. Legs can fill separately and rules can invoke exceptional settlement.",
        "Cross-batch comparisons are asynchronous even when each individual book response is fresh.",
        "Cumulative venue volume is not recent traded volume and does not establish executable capacity.",
        "Threshold counts are correlated comparisons, not independent experiments or statistical evidence.",
        "No daily/monthly precipitation equality is assumed; binary daily rain does not reveal its amount.",
        "No held-out 2025 data, private account endpoint, submitted order or historical profit simulation is used.",
    ]
    identifier = archive.append(
        "discovery_report", "market_expansion", utcnow(), {}, canonical(result).encode()
    )
    Path(output).write_text(json.dumps({"record_id": identifier, **result}, indent=2))
    return {"record_id": identifier, **result["summary"], "output": output}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="reports/market_expansion.json")
    args = parser.parse_args()
    archive = Archive()
    client = PublicClient(archive, interval=0.6)
    try:
        print(json.dumps(run(client, args.output), indent=2))
    finally:
        client.close()
        archive.close()
