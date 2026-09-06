"""Collect fresh future books for already recorded paper fills, public GET only."""

import fcntl
import json
import time
from datetime import timedelta
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.http import PublicClient
from weatherpred.timeutil import iso, parse_time, utcnow


def main():
    archive = Archive()
    client = PublicClient(archive, interval=0.25)
    try:
        with (archive.root / "paper_observer.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            started = utcnow()
            config = {
                "experiment": "E009-future-price-observer-v2",
                "paper_run_record_id": 21756,
                "started_at": iso(started),
                "stop_at": "2026-09-06T18:30:00Z",
                "horizons_seconds": [30, 60, 300],
                "maximum_delay_seconds": 15,
                "maximum_book_requests": 400,
                "scope": "Only paper fills recorded after this registration. No orders, strategy changes or retroactive observations. New observer book namespace; entry decisions remain frozen.",
            }
            source = archive.append(
                "research_source", str(Path(__file__)), started, {}, Path(__file__).read_bytes()
            )
            protocol = archive.append(
                "experiment_protocol",
                config["experiment"],
                started,
                {},
                canonical({**config, "source_record_id": source}).encode(),
            )
            print(json.dumps({"observer_protocol_record_id": protocol, **config}), flush=True)
            cursor, pending, requests = 0, {}, 0
            while utcnow() < parse_time(config["stop_at"]) and requests < config["maximum_book_requests"]:
                if (archive.root / "STOP_PAPER_OBSERVER").exists():
                    break
                for r in archive.db.execute(
                    "SELECT * FROM records WHERE kind='paper_event' AND key='21756' AND id>? ORDER BY id",
                    (cursor,),
                ).fetchall():
                    cursor = r["id"]
                    event = archive.json(r)
                    if event["type"] != "fill" or parse_time(event["at"]) < started:
                        continue
                    data = event["data"]
                    # Recorded fill data identify order; submission gives ticker/close.
                    order_id = data["order_id"]
                    for horizon in config["horizons_seconds"]:
                        due = parse_time(event["at"]) + timedelta(seconds=horizon)
                        pending[(order_id, horizon)] = {
                            "order_id": order_id,
                            "horizon": horizon,
                            "due": due,
                            "fill_record_id": r["id"],
                        }
                now = utcnow()
                ready = [v for v in pending.values() if v["due"] <= now]
                if ready:
                    order_ids = {v["order_id"] for v in ready}
                    orders = {}
                    for r in archive.db.execute(
                        "SELECT * FROM records WHERE kind='paper_event' AND key='21756' ORDER BY id"
                    ):
                        event = archive.json(r)
                        if event["type"] == "order" and event["data"]["id"] in order_ids:
                            orders[event["data"]["id"]] = event["data"]
                    eligible, expired = [], []
                    for item in ready:
                        order = orders[item["order_id"]]
                        if now > item["due"] + timedelta(seconds=15) or now >= parse_time(order["close_at"]):
                            expired.append(item)
                        else:
                            eligible.append(item)
                    result = {
                        "protocol_record_id": protocol,
                        "at": iso(now),
                        "requested": [],
                        "missed": [{**r, "due": iso(r["due"])} for r in expired],
                    }
                    if eligible:
                        tickers = sorted({orders[r["order_id"]]["ticker"] for r in eligible})
                        try:
                            _, identifier = client.json(
                                "/markets/orderbooks",
                                [("tickers", t) for t in tickers],
                                kind="paper_diagnostic_books",
                                key=str(protocol),
                            )
                            result["source_record_id"] = identifier
                            result["requested"] = [{**r, "due": iso(r["due"])} for r in eligible]
                        except (httpx.HTTPError, ValueError, KeyError) as exc:
                            result["error"] = f"{type(exc).__name__}: {exc}"
                        requests += 1
                    archive.append(
                        "paper_observer_observation", str(protocol), utcnow(), {}, canonical(result).encode()
                    )
                    for item in ready:
                        pending.pop((item["order_id"], item["horizon"]), None)
                    print(
                        json.dumps(
                            {
                                "at": iso(utcnow()),
                                "book_requests": requests,
                                "orders_observed": len(eligible),
                                "missed": len(expired),
                                "error": result.get("error"),
                            }
                        ),
                        flush=True,
                    )
                time.sleep(0.5)
            archive.append(
                "experiment_report",
                "E009_observer_stopped",
                utcnow(),
                {},
                canonical(
                    {
                        "protocol_record_id": protocol,
                        "book_requests": requests,
                        "remaining_observations": len(pending),
                    }
                ).encode(),
            )
    finally:
        client.close()
        archive.close()


if __name__ == "__main__":
    main()
