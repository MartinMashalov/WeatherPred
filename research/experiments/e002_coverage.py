"""Discover historical daily temperature coverage without inspecting forecast scores.

Raw market bodies contain outcomes but this stage only reports dates/rules/coverage;
the final holdout is never passed to a scoring function.
"""

import json
import logging
import re
from collections import Counter
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.http import PublicClient
from weatherpred.timeutil import iso, utcnow
from weatherpred.universe import family

LOG = logging.getLogger(__name__)


def source_name(market):
    text = market.get("rules_primary", "").lower()
    if "weather company" in text:
        return "TWC"
    if "synoptic" in text or "kalshi weather index" in text:
        return "Miami_index"
    if "national weather service" in text or re.search(r"\bnws\b", text):
        return "NWS"
    return "unresolved"


def event_date(ticker):
    from datetime import datetime

    m = re.search(r"-(\d{2}[A-Z]{3}\d{2})(?:-|$)", ticker)
    # This is a calendar date encoded in a ticker, not a settlement timestamp.
    return datetime.strptime(m[1] + "+0000", "%y%b%d%z").date().isoformat() if m else None


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    archive = Archive()
    client = PublicClient(archive)
    protocol = json.loads(Path("config/e002_market_baseline.json").read_text())
    catalog = json.loads(Path("reports/universe.json").read_text())["series"]
    selected = [s for s in catalog if s["frequency"] == "daily" and family(s).startswith("temperature_")]
    report = {"protocol": protocol, "series": [], "started_at": iso(utcnow())}
    archive.append("experiment_protocol", "E002-v1", utcnow(), {}, canonical(protocol).encode())
    try:
        for n, series in enumerate(selected):
            records, dates, sources = [], {}, Counter()
            missing_dates, count, errors = 0, 0, []
            # Both tiers are required. No reliance on unverified pagination sort order.
            for path in ("/historical/markets", "/markets"):
                try:
                    for markets, rec in client.pages(
                        path,
                        "markets",
                        {"series_ticker": series["ticker"], "limit": 1000},
                        kind="history_census_page",
                        key=series["ticker"],
                    ):
                        records.append(rec)
                        for market in markets:
                            count += 1
                            d = event_date(market["ticker"])
                            if not d:
                                missing_dates += 1
                                continue
                            dates[market["event_ticker"]] = d
                            sources[source_name(market)] += 1
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    errors.append({"path": path, "error": str(exc)})
            period_counts = Counter()
            for date in dates.values():
                if protocol["development_start"] <= date < protocol["train_end_exclusive"]:
                    period_counts["train_events"] += 1
                elif protocol["train_end_exclusive"] <= date < protocol["validation_end_exclusive"]:
                    period_counts["validation_events"] += 1
                elif (
                    protocol["validation_end_exclusive"]
                    <= date
                    < protocol["sealed_final_holdout_end_exclusive"]
                ):
                    period_counts["sealed_holdout_events"] += 1
            row = {
                "ticker": series["ticker"],
                "title": series["title"],
                "family": family(series),
                "market_count": count,
                "event_count": len(dates),
                "missing_dates": missing_dates,
                "first_date": min(dates.values(), default=None),
                "last_date": max(dates.values(), default=None),
                "period_counts": dict(period_counts),
                "sources": dict(sources),
                "record_ids": records,
                "errors": errors,
            }
            archive.append("history_coverage", series["ticker"], utcnow(), {}, canonical(row).encode())
            report["series"].append(row)
            Path("reports/E002_coverage_progress.json").write_text(json.dumps(report, indent=2))
            if n % 10 == 0 or n == len(selected) - 1:
                LOG.info(
                    "Historical coverage %s/%s series, %s market rows, %s failed requests",
                    n + 1,
                    len(selected),
                    sum(r["market_count"] for r in report["series"]),
                    sum(len(r["errors"]) for r in report["series"]),
                )
        report["finished_at"] = iso(utcnow())
        report["summary"] = {
            "series": len(selected),
            "market_rows": sum(r["market_count"] for r in report["series"]),
            "series_with_2025_training": sum(
                r["period_counts"].get("train_events", 0) > 0 for r in report["series"]
            ),
            "failed_requests": sum(len(r["errors"]) for r in report["series"]),
            "scores_computed": 0,
        }
        archive.append("experiment_report", "E002-coverage", utcnow(), {}, canonical(report).encode())
        Path("reports/E002_coverage.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report["summary"], indent=2))
    finally:
        client.close()
        archive.close()


if __name__ == "__main__":
    main()
