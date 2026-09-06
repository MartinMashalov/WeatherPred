"""Acquire the preregistered Miami window, preserving all source flags.

This is a historical retrieval, not evidence of initial publication or fills.
Run: uv run python research/experiments/e004_acquire.py
"""

import json
from collections import Counter
from datetime import timedelta
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.http import PublicClient
from weatherpred.index import canonical_point
from weatherpred.timeutil import iso, parse_time, utcnow


def main():
    protocol = json.loads(Path("config/e004_index_baselines.json").read_text())
    archive = Archive()
    protocol_id = archive.append("experiment_protocol", "E004-v1", utcnow(), {}, canonical(protocol).encode())
    client = PublicClient(archive)
    start = parse_time(protocol["training_start"])
    end = parse_time(protocol["validation_end_exclusive"])
    summary = {"protocol_record_id": protocol_id, "days": [], "market_record_ids": [], "errors": []}
    # An extra preceding hour supports the first forecast without future fill.
    cursor = start - timedelta(hours=1)
    try:
        while cursor < end:
            stop = min(cursor + timedelta(days=1), end)
            try:
                data, rec = client.json(
                    "/live_data/weather/miami",
                    {
                        "from": int(cursor.timestamp() * 1000),
                        "to": int(stop.timestamp() * 1000) - 1,
                        "detailed": "true",
                    },
                    kind="index_history",
                    key="miami:" + iso(cursor),
                )
                if data["city"] != "miami" or data["units"] != "fahrenheit":
                    raise ValueError("Unexpected index city or units")
                points = data["timeseries"]
                if any(not cursor.timestamp() * 1000 <= p["t"] < stop.timestamp() * 1000 for p in points):
                    raise ValueError("History response exceeds requested bounds")
                result = {
                    "from": iso(cursor),
                    "to_exclusive": iso(stop),
                    "record_id": rec,
                    "points": len(points),
                    "canonical_candidates": sum(map(canonical_point, points)),
                    "statuses": dict(Counter(p.get("status") for p in points)),
                    "receipt_basis": dict(Counter(p.get("receipt_basis", "absent") for p in points)),
                    "config_version": data.get("config_version"),
                }
                summary["days"].append(result)
                print(json.dumps(result), flush=True)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                summary["errors"].append({"from": iso(cursor), "error": str(exc)})
            cursor = stop
        # Preserve every page even if the endpoint ignores optional time filters.
        for _, rec in client.pages(
            "/markets",
            "markets",
            {
                "series_ticker": "KXTEMPMIAH",
                "limit": 1000,
                "min_close_ts": int(start.timestamp()),
                "max_close_ts": int(end.timestamp()),
            },
            kind="index_history_markets",
            key="KXTEMPMIAH",
        ):
            summary["market_record_ids"].append(rec)
        _, rec = client.json(
            "/live_data/weather/miami/calibrations", kind="index_history_calibrations", key="miami"
        )
        summary["calibration_record_id"] = rec
    finally:
        client.close()
        summary["finished_at"] = iso(utcnow())
        summary["historical_availability_verified"] = False
        summary["profitability_proven"] = False
        archive.append("experiment_report", "E004_acquisition", utcnow(), {}, canonical(summary).encode())
        Path("reports/E004_acquisition.json").write_text(json.dumps(summary, indent=2))
        archive.close()
    print(
        json.dumps(
            {
                "days": len(summary["days"]),
                "market_pages": len(summary["market_record_ids"]),
                "errors": summary["errors"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
