"""Metadata-only inventory for a $200 rolling-year research replay.

The local audit never opens archive blobs. Optional public acquisition is confined
to non-nested event metadata and the historical cutoff; it acquires no prices,
settlement outcomes, weather targets or order books and computes no returns.
"""

import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.http import PublicClient
from weatherpred.timeutil import iso, utcnow

START = date(2025, 9, 6)
END = date(2026, 9, 5)
NEW_START = date(2026, 1, 1)
SERIES = (
    "KXHIGHAUS",
    "KXHIGHCHI",
    "KXHIGHDEN",
    "KXHIGHLAX",
    "KXHIGHMIA",
    "KXHIGHNY",
    "KXHIGHPHIL",
)
KEY = "bankroll-year-event-metadata-20260101-20260905-v1"


def ticker_day(ticker):
    match = re.fullmatch(r"[A-Z0-9]+-(\d{2}[A-Z]{3}\d{2})(?:\d{2})?(?:-.+)?", ticker)
    return datetime.strptime(match[1], "%y%b%d").replace(tzinfo=UTC).date() if match else None


def safe_new_event(ticker):
    day = ticker_day(ticker)
    return ticker.split("-", 1)[0] in SERIES and day is not None and NEW_START <= day <= END


def dates(start, end):
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def inspect_local(root=Path("data"), reports=Path("reports")):
    db = sqlite3.connect(f"file:{root / 'archive.sqlite'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    # Selecting record columns does not open any response body, including sealed quarters.
    rows = db.execute("SELECT id,kind,key,available_at,metadata,body_sha256 FROM records").fetchall()
    db.close()
    daily = [row for row in rows if row["kind"] == "e002_candles" and START <= ticker_day(row["key"]) <= END]
    daily_events = sorted({r["key"].rsplit("-", 1)[0] for r in daily})
    daily_dates = sorted({ticker_day(r["key"]).isoformat() for r in daily})
    kinds = dict(Counter(r["kind"] for r in rows))
    hourly = [r for r in rows if r["kind"] == "e004_candles"]
    hourly_end_times = []
    for row in hourly:
        query = parse_qs(urlparse(json.loads(row["metadata"])["url"]).query)
        hourly_end_times.append(datetime.fromtimestamp(int(query["end_ts"][0]), UTC))
    books = []
    for row in rows:
        metadata = json.loads(row["metadata"])
        if "/orderbook" in metadata.get("url", ""):
            books.append(row)
    in_year_books = [r for r in books if START.isoformat() <= r["available_at"][:10] <= END.isoformat()]
    coverage = json.loads((reports / "E002_coverage.json").read_text())
    acquisition = json.loads((reports / "E002_acquisition.json").read_text())
    candle_counts = {r["record_id"]: r["candles"] for r in acquisition["rows"]}
    quote_coverage = json.loads((reports / "E004_baselines.json").read_text())["quote_coverage"]
    byte_sizes = [(root / "blobs" / r["body_sha256"]).stat().st_size for r in daily]
    sealed = {
        s["ticker"]: s["period_counts"].get("sealed_holdout_events", 0)
        for s in coverage["series"]
        if s["period_counts"].get("sealed_holdout_events", 0)
    }
    probes = [r for r in rows if r["kind"] == "candle_probe"]
    per_series = Counter(r["key"].split("-", 1)[0] for r in daily)
    return {
        "generated_at": iso(utcnow()),
        "record_cutoff_id": max(r["id"] for r in rows),
        "requested_starting_bankroll": 200,
        "requested_calendar": {"start": str(START), "end_inclusive": str(END), "days": 365},
        "archive_response_bodies_opened": 0,
        "sealed_outcomes_or_prices_opened": False,
        "simulation_or_policy_selection_performed": False,
        "daily_2025_development_overlap": {
            "source_kind": "e002_candles",
            "contracts": len(daily),
            "events": len(daily_events),
            "event_dates": daily_dates,
            "calendar_days": len(daily_dates),
            "contracts_per_series": dict(sorted(per_series.items())),
            "source_record_ids": sorted(r["id"] for r in daily),
            "candles_reported_by_acquisition": sum(candle_counts[r["id"]] for r in daily),
            "contracts_with_zero_reported_candles": sum(candle_counts[r["id"]] == 0 for r in daily),
            "uncompressed_blob_bytes": sum(byte_sizes),
            "median_uncompressed_blob_bytes": sorted(byte_sizes)[len(byte_sizes) // 2],
            "period_interval_minutes": 60,
            "request_window": "23 hours from earliest signal minus one hour to latest signal; not whole market life",
            "execution_evidence": "Top-of-book candle endpoints; no historical depth or queue history",
        },
        "hourly_miami_2026_development_overlap": {
            "source_kind": "e004_candles",
            "event_requests": len(hourly),
            "first_requested_end_utc": iso(min(hourly_end_times)),
            "last_requested_end_utc": iso(max(hourly_end_times)),
            "end_utc_days": sorted({t.date().isoformat() for t in hourly_end_times}),
            "source_record_ids": sorted(r["id"] for r in hourly),
            "period_interval_minutes": 1,
            "request_window_minutes": 60,
            "quote_coverage": quote_coverage,
        },
        "small_candle_canaries": {
            "raw_records": len(probes),
            "unique_contracts": len({r["key"] for r in probes}),
            "tickers": sorted({r["key"] for r in probes}),
            "source_record_ids": sorted(r["id"] for r in probes),
            "complete_universe": False,
        },
        "depth_receipts": {
            "public_orderbook_records": len(books),
            "first_receipt": min(r["available_at"] for r in books),
            "last_receipt": max(r["available_at"] for r in books),
            "receipts_inside_requested_year": len(in_year_books),
            "complete_year_days_with_executable_replay_evidence": 0,
            "limitation": "Candles and limited trade canaries cannot establish historic depth or maker queue fills",
        },
        "sealed_quarter_presence_from_existing_aggregate_only": {
            "start": "2025-10-01",
            "end_inclusive": "2025-12-31",
            "calendar_days": 92,
            "aggregate_source": "reports/E002_coverage.json",
            "events_by_series": sealed,
            "events": sum(sealed.values()),
            "price_or_outcome_access_authorized": False,
        },
        "missing_2026_daily_census_and_candles": {
            "start": str(NEW_START),
            "end_inclusive": str(END),
            "calendar_days": len(dates(NEW_START, END)),
            "series": list(SERIES),
            "potential_events_if_all_series_daily": len(SERIES) * len(dates(NEW_START, END)),
            "small_canaries_do_not_close_gap": True,
        },
        "weather_input_record_counts": {
            k: kinds.get(k, 0)
            for k in ("e003_nbm_day", "e010_nbm_cycle", "index_history", "station_history_weekly")
        },
    }


def acquire_event_metadata(root=Path("data"), reports=Path("reports")):
    archive = Archive(root)
    client = PublicClient(archive, interval=1)
    prior = archive.latest("bankroll_event_metadata_report", KEY)
    if prior:
        result = archive.json(prior)
        result["report_record_id"] = prior["id"]
        client.close()
        archive.close()
        return result
    plan = {
        "key": KEY,
        "created_at": iso(utcnow()),
        "purpose": "Count public event metadata before choosing any price acquisition or evaluation",
        "series": list(SERIES),
        "selected_event_dates": [str(NEW_START), str(END)],
        "endpoint": "/events",
        "with_nested_markets": False,
        "limit": 200,
        "maximum_pages_per_series": 5,
        "minimum_request_interval_seconds": 1,
        "maximum_transient_retries": 2,
        "no_price_or_outcome_endpoints": True,
        "date_guard_before_future_market_requests": "Require safe_new_event(event_ticker), never use series-wide historical market bodies",
    }
    protocol_id = archive.append(
        "bankroll_event_metadata_protocol", KEY, utcnow(), {}, canonical(plan).encode()
    )
    result = {"protocol_record_id": protocol_id, "series": [], "source_record_ids": [], "errors": []}
    try:
        cutoff, identifier = client.json("/historical/cutoff", kind="bankroll_historical_cutoff", key=KEY)
        result["current_cutoff"] = cutoff
        result["source_record_ids"].append(identifier)
        for series in SERIES:
            query = {
                "series_ticker": series,
                "with_nested_markets": "false",
                "min_close_ts": int(datetime.combine(NEW_START, datetime.min.time(), UTC).timestamp()),
                "limit": 200,
            }
            selected, source_ids, cursors = {}, [], set()
            total_returned = 0
            for page in range(5):
                data, identifier = client.json(
                    "/events", query, kind="bankroll_event_metadata", key=f"{series}:{page}"
                )
                source_ids.append(identifier)
                result["source_record_ids"].append(identifier)
                if "markets" in data or any("markets" in e for e in data["events"]):
                    raise ValueError("Unexpected nested market response; stop metadata-only census")
                total_returned += len(data["events"])
                for event in data["events"]:
                    ticker = event["event_ticker"]
                    if safe_new_event(ticker):
                        if ticker in selected:
                            raise ValueError("Repeated event across pages")
                        selected[ticker] = {
                            "event_ticker": ticker,
                            "day": str(ticker_day(ticker)),
                            "source_record_id": identifier,
                        }
                cursor = data.get("cursor")
                if not cursor:
                    break
                if cursor in cursors:
                    raise ValueError("Repeated pagination cursor")
                cursors.add(cursor)
                query["cursor"] = cursor
            else:
                raise ValueError("Metadata page cap reached; census incomplete")
            present = {r["day"] for r in selected.values()}
            result["series"].append(
                {
                    "series_ticker": series,
                    "returned_metadata_events": total_returned,
                    "selected_events": len(selected),
                    "missing_requested_dates": sorted(set(dates(NEW_START, END)) - present),
                    "source_record_ids": source_ids,
                    "events": sorted(selected.values(), key=lambda x: x["day"]),
                }
            )
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        result["errors"].append({"type": type(exc).__name__, "message": str(exc)})
    finally:
        result["finished_at"] = iso(utcnow())
        result["complete"] = len(result["series"]) == len(SERIES) and not result["errors"]
        result["selected_events"] = sum(s["selected_events"] for s in result["series"])
        result["prices_or_outcomes_acquired"] = False
        report_id = archive.append(
            "bankroll_event_metadata_report", KEY, utcnow(), {}, canonical(result).encode()
        )
        result["report_record_id"] = report_id
        (reports / "bankroll_event_metadata.json").write_text(json.dumps(result, indent=2) + "\n")
        client.close()
        archive.close()
    return result


def probe_candle_routes():
    """Fixed, safe-date route probes; report schema/counts, never score prices."""
    archive = Archive()
    client = PublicClient(archive, interval=1)
    key = KEY + ":route-canaries-v1"
    prior = archive.latest("bankroll_route_report", key)
    if prior:
        result = archive.json(prior)
        result["report_record_id"] = prior["id"]
        client.close()
        archive.close()
        return result
    plan = {
        "created_at": iso(utcnow()),
        "purpose": "Test archived event-candle versus single-market route and recent batch route before bulk acquisition",
        "events": ["KXHIGHNY-26JAN01", "KXHIGHNY-26SEP01"],
        "period_interval_minutes": 60,
        "request_window": "Fixed event UTC date at midnight through two days later; no 2025 prices",
        "selected_single_contract": "Lexicographically first returned contract, independent of prices/outcome",
        "no_scoring_or_strategy_selection": True,
        "minimum_request_interval_seconds": 1,
        "maximum_transient_retries": 2,
    }
    protocol_id = archive.append("bankroll_route_protocol", key, utcnow(), {}, canonical(plan).encode())
    result = {"protocol_record_id": protocol_id, "requests": [], "failures": []}

    def fetch(path, query, request_key):
        try:
            data, identifier = client.json(path, query, kind="bankroll_route_canary", key=request_key)
            result["requests"].append(
                {"request_key": request_key, "source_record_id": identifier, "top_level_fields": sorted(data)}
            )
            return data, identifier
        except (httpx.HTTPError, ValueError) as exc:
            result["failures"].append({"request_key": request_key, "error": str(exc)})
            return None, None

    try:
        for event in plan["events"]:
            if not safe_new_event(event):
                raise ValueError("Canary event outside safe acquisition scope")
            day = ticker_day(event)
            query = {
                "start_ts": int(datetime.combine(day, datetime.min.time(), UTC).timestamp()),
                "end_ts": int(
                    datetime.combine(day + timedelta(days=2), datetime.min.time(), UTC).timestamp()
                ),
                "period_interval": 60,
            }
            old = day < date(2026, 7, 8)
            path = "/historical/markets" if old else "/markets"
            metadata, _metadata_id = fetch(path, {"event_ticker": event, "limit": 1000}, event + ":metadata")
            if metadata is None:
                continue
            markets = metadata["markets"]
            if metadata.get("cursor") or any(m["event_ticker"] != event for m in markets):
                raise ValueError("Incomplete or cross-event market metadata")
            tickers = sorted(m["ticker"] for m in markets)
            result["requests"][-1].update({"market_count": len(markets), "tickers": tickers})
            if not tickers:
                continue
            if old:
                data, _identifier = fetch(
                    f"/series/KXHIGHNY/events/{event}/candlesticks", query, event + ":event-candles"
                )
                if data is not None:
                    result["requests"][-1].update(
                        {
                            "markets": len(data.get("market_tickers", [])),
                            "candles": sum(len(a) for a in data.get("market_candlesticks", [])),
                            "adjusted_end_ts": data.get("adjusted_end_ts"),
                        }
                    )
                data, _identifier = fetch(
                    f"/historical/markets/{tickers[0]}/candlesticks",
                    query,
                    event + ":single-historical-candles",
                )
                if data is not None:
                    result["requests"][-1].update({"candles": len(data.get("candlesticks", []))})
            else:
                data, _identifier = fetch(
                    "/markets/candlesticks",
                    dict(query, market_tickers=",".join(tickers), include_latest_before_start="false"),
                    event + ":batch-candles",
                )
                if data is not None:
                    result["requests"][-1].update(
                        {
                            "markets": len(data.get("markets", [])),
                            "candles": sum(len(m.get("candlesticks", [])) for m in data.get("markets", [])),
                        }
                    )
    finally:
        result["finished_at"] = iso(utcnow())
        result["strategy_scores_computed"] = 0
        result["sealed_quarter_accessed"] = False
        report_id = archive.append("bankroll_route_report", key, utcnow(), {}, canonical(result).encode())
        result["report_record_id"] = report_id
        Path("reports/bankroll_route_canaries.json").write_text(json.dumps(result, indent=2) + "\n")
        client.close()
        archive.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acquire-event-metadata", action="store_true")
    parser.add_argument("--probe-candle-routes", action="store_true")
    args = parser.parse_args()
    result = inspect_local()
    Path("reports/bankroll_data_audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("daily_2025_development_overlap", "hourly_miami_2026_development_overlap")
            },
            indent=2,
        )
    )
    print(
        "Daily overlap:",
        len(result["daily_2025_development_overlap"]["event_dates"]),
        "days;",
        result["daily_2025_development_overlap"]["contracts"],
        "contracts",
    )
    if args.acquire_event_metadata:
        metadata = acquire_event_metadata()
        print(
            "Metadata census:",
            metadata["selected_events"],
            "events; complete:",
            metadata["complete"],
            "report:",
            metadata["report_record_id"],
        )
        if not metadata["complete"]:
            raise SystemExit(1)
    if args.probe_candle_routes:
        print(json.dumps(probe_candle_routes(), indent=2))


if __name__ == "__main__":
    main()
