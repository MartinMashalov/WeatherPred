"""Bounded public-data collection, singleton lock and explicit stop file.

Captures features/quotes before outcomes. Does not yet generate strategy
probabilities or shadow fills; those require a registered fitted model.
"""

import fcntl
import json
import logging
import os
import shutil
import time
from pathlib import Path

import httpx

from weatherpred.archive import canonical
from weatherpred.timeutil import iso, parse_time, utcnow

LOG = logging.getLogger(__name__)


def capture(client, cycles=120, interval=30, directory="reports"):
    root = client.archive.root
    if cycles < 1 or interval < 5:
        raise ValueError("Use a finite positive cycle count and at least 5 seconds between cycles")
    catalog = json.loads(Path(directory, "universe.json").read_text())["series"]
    selected = [
        s["ticker"]
        for s in catalog
        if s["frequency"] in ("daily", "hourly")
        and (s["discovery_family"].startswith("temperature_") or "Daily temperature" in (s.get("tags") or []))
    ]
    # The current six hourly series use frequency 'hourly'; union with observed
    # hourly category to avoid missing a template with nonstandard frequency.
    selected = sorted(
        set(selected)
        | {s["ticker"] for s in catalog if s["discovery_family"] == "temperature_hourly_or_index"}
    )
    markets = json.loads(Path(directory, "markets_open.json").read_text())["markets"]
    markets = [m for m in markets if m["series_ticker"] in selected]
    lock = (root / "collector.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("A collector already holds the operating-system lock") from None
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()
    stop_reason = "error"
    try:
        for cycle in range(cycles):
            if (root / "STOP_COLLECTOR").exists():
                stop_reason = "stop_file"
                break
            if shutil.disk_usage(root).free < 2 * 1024**3:
                stop_reason = "low_disk_space"
                break
            started = utcnow()
            evidence, errors = [], []
            # Refresh listings every 10 minutes; archive partial failures rather
            # than treating unobserved new contracts as an empty universe.
            if cycle == 0 or (cycle * interval) % 600 < interval:
                refreshed = []
                failed = set()
                for series in selected:
                    try:
                        for page, rec in client.pages(
                            "/markets",
                            "markets",
                            {"series_ticker": series, "status": "open", "limit": 1000},
                            kind="capture_market_page",
                            key=series,
                        ):
                            refreshed.extend(dict(m, series_ticker=series) for m in page)
                            evidence.append(rec)
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        failed.add(series)
                        errors.append({"source": series, "error": str(exc)})
                markets = refreshed + [m for m in markets if m["series_ticker"] in failed]
            try:
                _, rec = client.json(
                    "/live_data/weather/miami",
                    {"last_sec": 600, "detailed": "true"},
                    kind="index_capture",
                    key="miami",
                )
                evidence.append(rec)
            except (httpx.HTTPError, ValueError) as exc:
                errors.append({"source": "miami_index", "error": str(exc)})
            tickers = sorted({m["ticker"] for m in markets if parse_time(m["close_time"]) > utcnow()})
            for offset in range(0, len(tickers), 100):
                batch = tickers[offset : offset + 100]
                try:
                    data, rec = client.json(
                        "/markets/orderbooks",
                        [("tickers", t) for t in batch],
                        kind="capture_books",
                        key="temperature:" + str(offset // 100),
                    )
                    evidence.append(rec)
                    if {b["ticker"] for b in data["orderbooks"]} != set(batch):
                        raise ValueError("Book response ticker set mismatch")
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    errors.append({"source": "books:" + str(offset), "error": str(exc)})
            if cycle % max(1, int(300 / interval)) == 0:
                urls = [("miami_calibrations", "/live_data/weather/miami/calibrations")]
                urls += [
                    (station, "https://api.weather.gov/stations/" + station + "/observations/latest")
                    for station in ("KMIA", "KNYC", "KORD", "KLAX")
                ]
                for name, url in urls:
                    try:
                        _, rec = client.json(url, kind="weather_capture", key=name)
                        evidence.append(rec)
                    except (httpx.HTTPError, ValueError) as exc:
                        errors.append({"source": name, "error": str(exc)})
            result = {
                "cycle": cycle,
                "pid": os.getpid(),
                "started_at": iso(started),
                "finished_at": iso(utcnow()),
                "market_tickers": len(tickers),
                "record_ids": evidence,
                "errors": errors,
                "shadow_trades": 0,
            }
            client.archive.append("capture_cycle", "temperature", utcnow(), {}, canonical(result).encode())
            Path(directory, "capture_status.json").write_text(json.dumps(result, indent=2))
            LOG.info(
                "Capture cycle=%s markets=%s archived_responses=%s errors=%s",
                cycle,
                len(tickers),
                len(evidence),
                len(errors),
            )
            remaining = interval - (utcnow() - started).total_seconds()
            # Short waits make stop-file changes responsive; no stale lock PID
            # is used as evidence of a live collector.
            while remaining > 0 and cycle + 1 < cycles and not (root / "STOP_COLLECTOR").exists():
                pause = min(remaining, 1)
                time.sleep(pause)
                remaining -= pause
        else:
            stop_reason = "cycle_limit"
    finally:
        client.archive.append(
            "capture_stopped",
            "temperature",
            utcnow(),
            {},
            canonical({"pid": os.getpid(), "reason": stop_reason}).encode(),
        )
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
    return {"stop_reason": stop_reason}
