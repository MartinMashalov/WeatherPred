"""Frozen, resumable, public GET-only 2026 daily-market acquisition.

All eligible events/contracts are acquired without reading any strategy score.
No forecasts, decisions, returns, balances or hypothetical fills are computed.
"""

import argparse
import fcntl
import hashlib
import json
import math
import os
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.http import PublicClient
from weatherpred.timeutil import iso, parse_time, utcnow

KEY = "bankroll-daily-2026-acquisition-v1"
FIRST_TS = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
LAST_TS = int(datetime(2026, 9, 6, tzinfo=UTC).timestamp())
MANIFEST_RECORD_ID = 92494
SERIES = {"KXHIGHAUS", "KXHIGHCHI", "KXHIGHDEN", "KXHIGHLAX", "KXHIGHMIA", "KXHIGHNY", "KXHIGHPHIL"}
STOP = Path("data/STOP_BANKROLL_ACQUISITION")
PROGRESS = Path("reports/bankroll_acquisition_progress.json")
PINNED = [
    Path(__file__),
    Path("weatherpred/http.py"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
]


class StopAcquisition(Exception):
    pass


def source_hashes():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in PINNED}


def event_day(event):
    series, encoded = event.split("-", 1)
    if series not in SERIES or len(encoded) != 7:
        raise ValueError("Event is outside the seven-series daily universe")
    day = datetime.strptime(encoded, "%y%b%d").replace(tzinfo=UTC).date()
    if not date(2026, 1, 1) <= day <= date(2026, 9, 5):
        raise ValueError("Event is outside the frozen 2026 acquisition dates")
    return day


def quote_window(market):
    """Exclude any bucket ending exactly at the lower UTC acquisition bound."""
    event_day(market["event_ticker"])
    opened = parse_time(market["open_time"]).timestamp()
    closed = parse_time(market["close_time"]).timestamp()
    if opened >= closed:
        raise ValueError("Invalid market lifetime")
    start = math.floor(max(opened, FIRST_TS)) + 1
    end = math.floor(min(closed, LAST_TS))
    return {
        "start_ts": start,
        "end_ts": end,
        "period_interval": 60,
        "excluded_preyear_seconds": max(0, FIRST_TS - opened),
        "excluded_postyear_seconds": max(0, closed - LAST_TS),
        "start_bucket_excluded": True,
    }


def validate_candles(candles, query):
    ends = [c["end_period_ts"] for c in candles]
    if len(set(ends)) != len(ends):
        raise ValueError("Duplicate candlestick endpoints")
    if any(not isinstance(t, int) or t < query["start_ts"] or t > query["end_ts"] for t in ends):
        raise ValueError("Candlestick escaped the frozen UTC interval")
    return len(ends)


def register(archive):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (MANIFEST_RECORD_ID,)).fetchone()
    if row is None or row["kind"] != "bankroll_event_metadata_report":
        raise ValueError("Pinned event metadata manifest is missing")
    manifest = archive.json(row)
    if not manifest["complete"] or manifest["prices_or_outcomes_acquired"]:
        raise ValueError("Expected complete metadata-only manifest")
    events = []
    for series in manifest["series"]:
        if series["series_ticker"] not in SERIES:
            raise ValueError("Unexpected series in manifest")
        for event in series["events"]:
            if str(event_day(event["event_ticker"])) != event["day"]:
                raise ValueError("Event day differs from its ticker")
            events.append(event)
    events.sort(key=lambda r: (r["day"], r["event_ticker"]))
    if len(events) != 1736 or len({r["event_ticker"] for r in events}) != 1736:
        raise ValueError("Event census differs from frozen 1736-event scope")
    config = {
        "key": KEY,
        "manifest_record_id": MANIFEST_RECORD_ID,
        "manifest_body_sha256": row["body_sha256"],
        "events_sha256": hashlib.sha256(canonical(events).encode()).hexdigest(),
        "source_hashes": source_hashes(),
        "public_get_only": True,
        "minimum_request_interval_seconds": 0.5,
        "maximum_transient_retries": 2,
        "maximum_consecutive_request_failures": 10,
        "stop_file": str(STOP),
        "events": 1736,
        "series": sorted(SERIES),
        "event_date_start": "2026-01-01",
        "event_date_end_inclusive": "2026-09-05",
        "price_window_utc": [
            iso(datetime.fromtimestamp(FIRST_TS, UTC)),
            iso(datetime.fromtimestamp(LAST_TS, UTC)),
        ],
        "candle_period_minutes": 60,
        "boundary_rule": "start_ts=floor(max(open_time,Jan1UTC))+1; end_ts=floor(min(close_time,Sep6UTC)); first included endpoint must be after lower bound",
        "market_route": "Exact manifest event on historical tier if eventday<cutoff; live otherwise. If empty, try opposite tier. Boundary days within2days ofcutoff queryboth and unionbyexactticker; conflict quarantines event.",
        "candle_route": "Actual settlement_ts before pinned cutoff uses historical single-contract; allothercontracts use current batch groupedbyidenticalwindow,maximum100contracts/10000potentialrows.",
        "all_contracts": True,
        "no_empty_quote_imputation": True,
        "all_errors_retained": True,
        "postyear_settlement_timestamps_retained": True,
        "sealed_Q4_2025_event_access": False,
        "new_evaluation_scoring_authorized": False,
        "cutoff": manifest["current_cutoff"],
        "canary_records_not_reused": [92870, 92871, 92872, 92873, 92874],
    }
    prior = archive.latest("bankroll_acquisition_protocol", KEY)
    if prior:
        protocol = archive.json(prior)
        if protocol["config"] != config:
            raise ValueError("Resume manifest/source/config differs from frozen acquisition")
        return prior["id"], config, events
    identifier = archive.append(
        "bankroll_acquisition_protocol",
        KEY,
        utcnow(),
        {},
        canonical({"registered_at": iso(utcnow()), "config": config}).encode(),
    )
    return identifier, config, events


def run(maximum_new_events=None):
    with Path("data/bankroll_acquisition.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        archive = Archive()
        client = PublicClient(archive, interval=0.5)
        protocol_id, config, events = register(archive)
        cutoff = parse_time(config["cutoff"]["market_settled_ts"])
        counters = Counter()
        failed_streak = 0
        report = {
            "protocol_record_id": protocol_id,
            "pid": os.getpid(),
            "started_at": iso(utcnow()),
            "events_total": len(events),
            "events_complete": 0,
            "events_failed": 0,
            "new_events_this_run": 0,
            "market_contracts": 0,
            "candles": 0,
            "failures": [],
            "source_record_cutoff_at_start": archive.db.execute("SELECT max(id) FROM records").fetchone()[0],
            "strategy_scores_computed": 0,
        }

        def progress(status, event=None):
            report.update(
                status=status, current_event=event, updated_at=iso(utcnow()), request_counts=dict(counters)
            )
            temp = PROGRESS.with_suffix(".json.tmp")
            temp.write_text(json.dumps(report, indent=2) + "\n")
            temp.replace(PROGRESS)

        def fetch(path, query):
            nonlocal failed_streak
            if STOP.exists():
                raise StopAcquisition("Stop file exists")
            request_key = KEY + ":" + hashlib.sha256(canonical([path, query]).encode()).hexdigest()
            prior = archive.latest("bankroll_acquisition_http", request_key)
            if prior:
                counters["reused_successful_http_records"] += 1
                return archive.json(prior), prior["id"]
            try:
                counters["new_get_operations"] += 1
                result = client.json(path, query, kind="bankroll_acquisition_http", key=request_key)
                failed_streak = 0
                return result
            except httpx.HTTPError as exc:
                failed_streak += 1
                failed = archive.latest("http_error", request_key)
                report["failures"].append(
                    {
                        "path": path,
                        "query": query,
                        "source_record_id": failed["id"] if failed else None,
                        "error": str(exc),
                    }
                )
                if (
                    isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403)
                ) or failed_streak >= 10:
                    raise StopAcquisition("Permission denial or systematic request failure") from exc
                raise

        def metadata(event):
            day = event_day(event)
            old = datetime.combine(day, datetime.min.time(), UTC) < cutoff
            first = "/historical/markets" if old else "/markets"
            paths = [first]
            if abs((day - cutoff.date()).days) <= 2:
                paths.append("/markets" if old else "/historical/markets")
            merged, sources = {}, []
            for path in paths:
                data, identifier = fetch(path, {"event_ticker": event, "limit": 1000})
                sources.append(identifier)
                if data.get("cursor"):
                    raise ValueError("Unanticipated market pagination; event incomplete")
                for market in data["markets"]:
                    if market["event_ticker"] != event or not market["ticker"].startswith(event + "-"):
                        raise ValueError("Market metadata crossed the frozen event boundary")
                    ticker = market["ticker"]
                    if ticker in merged and merged[ticker] != market:
                        raise ValueError("Cross-tier conflicting market metadata")
                    merged[ticker] = market
                if not merged and len(paths) == 1:
                    paths.append("/markets" if old else "/historical/markets")
            if not merged:
                raise ValueError("No markets returned for manifest event")
            return sorted(merged.values(), key=lambda m: m["ticker"]), sources

        try:
            progress("running")
            print(
                json.dumps(
                    {
                        "status": "registered",
                        "protocol_record_id": protocol_id,
                        "pid": os.getpid(),
                        "events": len(events),
                        "source_hashes": config["source_hashes"],
                    }
                ),
                flush=True,
            )
            for item in events:
                event = item["event_ticker"]
                if STOP.exists():
                    raise StopAcquisition("Stop file exists")
                if source_hashes() != config["source_hashes"]:
                    raise StopAcquisition("Pinned acquisition source changed")
                prior = archive.latest("bankroll_acquisition_event", event)
                if prior:
                    previous = archive.json(prior)
                    if previous["protocol_record_id"] != protocol_id:
                        raise ValueError("Existing event belongs to a different protocol")
                    if previous["complete"]:
                        report["events_complete"] += 1
                        report["market_contracts"] += len(previous["markets"])
                        report["candles"] += sum(m["candles"] for m in previous["markets"])
                        counters["reused_event_checkpoints"] += 1
                        continue
                if maximum_new_events is not None and report["new_events_this_run"] >= maximum_new_events:
                    raise StopAcquisition("Bounded invocation event limit reached")
                result = {
                    "protocol_record_id": protocol_id,
                    "event": event,
                    "day": item["day"],
                    "metadata_record_ids": [],
                    "markets": [],
                    "failures": [],
                    "complete": False,
                }
                try:
                    markets, result["metadata_record_ids"] = metadata(event)
                    batches = {}
                    for market in markets:
                        window = quote_window(market)
                        query = {k: window[k] for k in ("start_ts", "end_ts", "period_interval")}
                        record = {
                            "ticker": market["ticker"],
                            "window": window,
                            "status": market["status"],
                            "settlement_ts": market.get("settlement_ts"),
                            "candles": 0,
                        }
                        if query["start_ts"] > query["end_ts"]:
                            record.update(status="empty_window", candle_record_ids=[])
                            result["markets"].append(record)
                            continue
                        settled = parse_time(market["settlement_ts"]) if market.get("settlement_ts") else None
                        if settled is not None and settled < cutoff:
                            data, identifier = fetch(
                                f"/historical/markets/{market['ticker']}/candlesticks", query
                            )
                            if data.get("ticker") != market["ticker"]:
                                raise ValueError("Historical candle response ticker differs")
                            record.update(
                                candles=validate_candles(data["candlesticks"], query),
                                candle_record_ids=[identifier],
                                candle_route="historical",
                            )
                            result["markets"].append(record)
                        else:
                            batches.setdefault((query["start_ts"], query["end_ts"]), []).append(record)
                    for (start, end), records in sorted(batches.items()):
                        hours = (end - start) // 3600 + 2
                        maximum_batch = min(100, 10000 // hours)
                        if maximum_batch < 1:
                            raise ValueError("Market lifetime exceeds bounded recent batch window")
                        for i in range(0, len(records), maximum_batch):
                            subset = records[i : i + maximum_batch]
                            query = {
                                "start_ts": start,
                                "end_ts": end,
                                "period_interval": 60,
                                "market_tickers": ",".join(r["ticker"] for r in subset),
                                "include_latest_before_start": "false",
                            }
                            data, identifier = fetch("/markets/candlesticks", query)
                            returned = {m["market_ticker"]: m for m in data["markets"]}
                            if len(returned) != len(data["markets"]) or set(returned) != {
                                r["ticker"] for r in subset
                            }:
                                raise ValueError("Recent batch omitted, duplicated or added a contract")
                            for record in subset:
                                record.update(
                                    candles=validate_candles(
                                        returned[record["ticker"]]["candlesticks"], query
                                    ),
                                    candle_record_ids=[identifier],
                                    candle_route="recent_batch",
                                )
                                result["markets"].append(record)
                    result["complete"] = len(result["markets"]) == len(markets)
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    result["failures"].append({"type": type(exc).__name__, "message": str(exc)})
                    report["events_failed"] += 1
                finally:
                    result["finished_at"] = iso(utcnow())
                    identifier = archive.append(
                        "bankroll_acquisition_event", event, utcnow(), {}, canonical(result).encode()
                    )
                    report["last_event_record_id"] = identifier
                report["new_events_this_run"] += 1
                if result["complete"]:
                    report["events_complete"] += 1
                    report["market_contracts"] += len(result["markets"])
                    report["candles"] += sum(m["candles"] for m in result["markets"])
                progress("running", event)
                if report["new_events_this_run"] % 25 == 0:
                    print(
                        json.dumps(
                            {
                                k: report[k]
                                for k in (
                                    "events_complete",
                                    "events_failed",
                                    "market_contracts",
                                    "candles",
                                    "current_event",
                                    "updated_at",
                                )
                            }
                        ),
                        flush=True,
                    )
            progress("complete" if report["events_complete"] == len(events) else "finished_with_gaps")
        except StopAcquisition as exc:
            report["stop_reason"] = str(exc)
            progress("stopped")
        finally:
            report["finished_at"] = iso(utcnow())
            identifier = archive.append(
                "bankroll_acquisition_run", KEY, utcnow(), {}, canonical(report).encode()
            )
            report["run_record_id"] = identifier
            progress(report["status"])
            print(json.dumps(report), flush=True)
            client.close()
            archive.close()
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maximum-new-events", type=int)
    args = parser.parse_args()
    run(args.maximum_new_events)
