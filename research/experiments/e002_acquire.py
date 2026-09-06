"""Acquire development/validation hourly quote candles, never final holdout.

Resumable public GETs; successful raw replies are reused. STOP_E002 stops between
requests. A later run retries logged failures without concealing earlier errors.
"""

import json
import logging
from collections import Counter
from datetime import date
from pathlib import Path

import httpx

from research.experiments.e002_coverage import event_date, source_name
from weatherpred.archive import Archive, canonical
from weatherpred.contracts import historical_predicate, nws_standard_day, yes_at
from weatherpred.http import PublicClient
from weatherpred.timeutil import iso, parse_time, utcnow

LOG = logging.getLogger(__name__)


def development_market(market, protocol, windows):
    # This gate MUST precede access to settlement labels or values.
    day = event_date(market["event_ticker"])
    if not day or not protocol["development_start"] <= day < protocol["validation_end_exclusive"]:
        return None
    series = market["event_ticker"].split("-")[0]
    if series not in windows["series"]:
        raise ValueError("New covered series needs a registered source-window mapping: " + series)
    if source_name(market) != "NWS":
        raise ValueError("Unverified daily source in development: " + market["ticker"])
    if market["result"] not in ("yes", "no"):
        return {"ticker": market["ticker"], "exclusion": "nonbinary_or_unsettled_result"}
    market = historical_predicate(market)
    if market.get("expiration_value") and yes_at(market, market["expiration_value"]) != (
        market["result"] == "yes"
    ):
        raise ValueError("Settlement predicate mismatch: " + market["ticker"])
    _, end = nws_standard_day(date.fromisoformat(day), windows["series"][series]["standard_utc_offset_hours"])
    times = {str(h): int(end.timestamp()) - h * 3600 for h in protocol["decision_hours_before_period_end"]}
    return {
        "ticker": market["ticker"],
        "event": market["event_ticker"],
        "series": series,
        "day": day,
        "split": "train" if day < protocol["train_end_exclusive"] else "validation",
        "outcome": int(market["result"] == "yes"),
        "decision_ts": times,
        "source_period_end": iso(end),
        "open_ts": int(parse_time(market["open_time"]).timestamp()),
        "close_ts": int(parse_time(market["close_time"]).timestamp()),
        "rules_primary": market["rules_primary"],
        "rules_secondary": market["rules_secondary"],
        "expiration_value": market.get("expiration_value"),
        "strike_type": market["strike_type"],
        "floor_strike": market.get("floor_strike"),
        "cap_strike": market.get("cap_strike"),
        "predicate_provenance": market["predicate_provenance"],
    }


def load_development(archive, coverage, protocol, windows):
    markets = {}
    exclusions = Counter()
    for series in coverage["series"]:
        if not (
            series["period_counts"].get("train_events") or series["period_counts"].get("validation_events")
        ):
            continue
        for rec in series["record_ids"]:
            raw = archive.db.execute("SELECT * FROM records WHERE id=?", (rec,)).fetchone()
            for m in archive.json(raw)["markets"]:
                value = development_market(m, protocol, windows)
                if value is None:
                    continue
                if "exclusion" in value:
                    exclusions[value["exclusion"]] += 1
                    continue
                markets[value["ticker"]] = dict(value, source_record_id=rec)
    return sorted(markets.values(), key=lambda m: (m["day"], m["series"], m["ticker"])), dict(exclusions)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    archive = Archive()
    client = PublicClient(archive)
    protocol = json.loads(Path("config/e002_market_baseline.json").read_text())
    windows = json.loads(Path("config/e002_source_windows.json").read_text())
    coverage = json.loads(Path("reports/E002_coverage.json").read_text())
    report = {
        "started_at": iso(utcnow()),
        "rows": [],
        "errors": [],
        "stop_reason": "error",
        "holdout_accessed": False,
        "network_requests": 0,
    }
    archive.append("experiment_protocol", "E002-source-windows-v1", utcnow(), {}, canonical(windows).encode())
    try:
        markets, exclusions = load_development(archive, coverage, protocol, windows)
        report.update(development_markets=len(markets), metadata_exclusions=exclusions)
        # Preserve the complete date-filtered inputs before processing any quotes.
        body = "\n".join(canonical(m) for m in markets) + "\n"
        archive.append("experiment_dataset", "E002_development_markets", utcnow(), {}, body.encode())
        Path("reports/E002_development_markets.jsonl").write_text(body)
        LOG.info("Registered %s development/validation markets; final holdout inaccessible", len(markets))
        for i, m in enumerate(markets):
            if Path("data/STOP_E002").exists():
                report["stop_reason"] = "stop_file"
                break
            cached = archive.latest("e002_candles", m["ticker"])
            try:
                if cached is not None:
                    rec = cached["id"]
                    data = archive.json(cached)
                else:
                    data, rec = client.json(
                        "/historical/markets/" + m["ticker"] + "/candlesticks",
                        {
                            "start_ts": min(m["decision_ts"].values()) - 3600,
                            "end_ts": max(m["decision_ts"].values()),
                            "period_interval": 60,
                        },
                        kind="e002_candles",
                        key=m["ticker"],
                    )
                    report["network_requests"] += 1
                if data.get("ticker") != m["ticker"] or "candlesticks" not in data:
                    raise ValueError("Historical candle ticker/schema mismatch")
                report["rows"].append(
                    {"ticker": m["ticker"], "record_id": rec, "candles": len(data["candlesticks"])}
                )
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                report["errors"].append({"ticker": m["ticker"], "error": str(exc)})
            if i % 100 == 0 or i + 1 == len(markets):
                Path("reports/E002_acquisition_progress.json").write_text(json.dumps(report, indent=2))
                LOG.info(
                    "Candles %s/%s; errors=%s; raw quote scores still uncomputed",
                    i + 1,
                    len(markets),
                    len(report["errors"]),
                )
        else:
            report["stop_reason"] = "all_development_markets_attempted"
    finally:
        report["finished_at"] = iso(utcnow())
        archive.append("experiment_report", "E002_acquisition", utcnow(), {}, canonical(report).encode())
        Path("reports/E002_acquisition.json").write_text(json.dumps(report, indent=2))
        client.close()
        archive.close()
    print(json.dumps({k: v for k, v in report.items() if k not in ("rows", "errors")}, indent=2))


if __name__ == "__main__":
    main()
