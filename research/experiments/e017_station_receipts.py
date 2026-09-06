"""Bounded original METAR receipts with adjacent public books. No trading signals."""

import argparse
import fcntl
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.http import PublicClient
from weatherpred.timeutil import iso, parse_time, utcnow


def main(run_id=None):
    archive = Archive()
    client = PublicClient(archive, interval=0.25)
    try:
        with (archive.root / "station_receipts.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if run_id is None:
                if archive.latest("experiment_protocol", "E017-station-receipts-v1"):
                    raise ValueError("Collector already registered; resume its record ID")
                parent = archive.json(archive.db.execute("SELECT * FROM records WHERE id=42731").fetchone())
                station_config = json.loads(Path("config/e003_nbm_acquisition.json").read_text())["stations"]
                protocol = {
                    "experiment": "E017-station-receipts-v1",
                    "started_at": iso(utcnow()),
                    "stop_at": "2026-09-06T18:30:00Z",
                    "interval_seconds": 60,
                    "maximum_cycles": 180,
                    "stations": sorted(set(station_config.values())),
                    "panel": parent["panel"],
                    "parent_panel_record_id": 42731,
                    "purpose": "Preserve exact eight-station original METAR versions, provider receiptTime, observation obsTime, reportTime and our first receipt. Chicago is KMDW; no KORD substitution. Collect books before and after each public METAR batch.",
                    "limitations": "Source acquisition only. METAR is not the official daily CLI or hourly Synoptic settlement. Provider receiptTime is not independently verified first public availability. reportTime can be an hour bucket later than receiptTime; never treat it as publication. Initial two-hour backfill is not newly published news. Sixty-second sampling cannot establish subminute reaction or guaranteed execution. No forecast changes, trades, historical holdout or account credentials.",
                }
                paths = [
                    Path(__file__),
                    Path("config/e003_nbm_acquisition.json"),
                    *[Path("weatherpred") / n for n in ("archive.py", "http.py", "timeutil.py")],
                ]
                protocol["code_sha256"], protocol["source_record_ids"] = {}, {}
                for path in paths:
                    body = path.read_bytes()
                    protocol["code_sha256"][str(path)] = hashlib.sha256(body).hexdigest()
                    protocol["source_record_ids"][str(path)] = archive.append(
                        "research_source", str(path), utcnow(), {}, body
                    )
                run_id = archive.append(
                    "experiment_protocol", protocol["experiment"], utcnow(), {}, canonical(protocol).encode()
                )
                Path("reports/E017_registration.json").write_text(
                    json.dumps({"run_record_id": run_id, **protocol}, indent=2)
                )
            else:
                protocol = archive.json(
                    archive.db.execute("SELECT * FROM records WHERE id=?", (run_id,)).fetchone()
                )
            for path, expected in protocol["code_sha256"].items():
                if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                    raise ValueError("Frozen collector source changed: " + path)
            if any(not isinstance(s, str) or len(s) != 4 for s in protocol["stations"]):
                raise ValueError("Expected explicit ICAO station strings")
            print(json.dumps({"run_record_id": run_id, "stations": protocol["stations"]}), flush=True)
            cycles = archive.db.execute(
                "SELECT COUNT(*) FROM records WHERE kind='station_receipt_frame' AND key=?", (str(run_id),)
            ).fetchone()[0]
            while cycles < protocol["maximum_cycles"] and utcnow() < parse_time(protocol["stop_at"]):
                if (archive.root / "STOP_STATION_RECEIPTS").exists():
                    break
                started = time.monotonic()
                frame = {
                    "cycle": cycles + 1,
                    "run_record_id": run_id,
                    "started_at": iso(utcnow()),
                    "errors": [],
                }
                tickers = [m["ticker"] for m in protocol["panel"] if parse_time(m["close_time"]) > utcnow()]
                for phase in ("before", "metar", "after"):
                    try:
                        if phase != "metar":
                            if tickers:
                                _, identifier = client.json(
                                    "/markets/orderbooks",
                                    [("tickers", t) for t in tickers],
                                    kind="station_reaction_books",
                                    key=str(run_id) + ":" + phase,
                                )
                                frame[phase + "_book_record_id"] = identifier
                            continue
                        observations, identifier = client.json(
                            "https://aviationweather.gov/api/data/metar",
                            {"ids": ",".join(protocol["stations"]), "format": "json", "hours": 2},
                            kind="metar_receipts",
                            key=str(run_id),
                        )
                        source = archive.db.execute(
                            "SELECT * FROM records WHERE id=?", (identifier,)
                        ).fetchone()
                        frame["metar_record_id"] = identifier
                        frame["report_count"] = len(observations)
                        frame["possible_response_limit"] = len(observations) >= 400
                        frame["missing_stations"] = sorted(
                            set(protocol["stations"]) - {m["icaoId"] for m in observations}
                        )
                        frame["new_version_ids"], frame["latest_observation_at"] = [], {}
                        for m in observations:
                            station = m["icaoId"]
                            if station not in protocol["stations"]:
                                raise ValueError("Unrequested station in METAR batch")
                            valid = datetime.fromtimestamp(m["obsTime"], UTC)
                            receipt = parse_time(m["receiptTime"])
                            if valid > parse_time(source["available_at"]) or receipt > parse_time(
                                source["available_at"]
                            ):
                                raise ValueError("Future observation/provider receipt requires review")
                            frame["latest_observation_at"][station] = max(
                                frame["latest_observation_at"].get(station, ""), iso(valid)
                            )
                            key = str(run_id) + ":" + hashlib.sha256(canonical(m).encode()).hexdigest()
                            if archive.latest("metar_version_first_seen", key) is None:
                                event = {
                                    "station": station,
                                    "source_record_id": identifier,
                                    "observation_at": iso(valid),
                                    "provider_receipt_at": iso(receipt),
                                    "first_received_at": source["available_at"],
                                    "report_time_raw": m.get("reportTime"),
                                    "report": m,
                                    "is_initial_backfill": cycles == 0
                                    or receipt < parse_time(protocol["started_at"]),
                                    "provider_receipt_predates_registration": receipt
                                    < parse_time(protocol["started_at"]),
                                }
                                ref = archive.append(
                                    "metar_version_first_seen",
                                    key,
                                    parse_time(source["available_at"]),
                                    {},
                                    canonical(event).encode(),
                                )
                                frame["new_version_ids"].append(ref)
                    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                        frame["errors"].append({"phase": phase, "error": str(exc)})
                frame["finished_at"] = iso(utcnow())
                archive.append("station_receipt_frame", str(run_id), utcnow(), {}, canonical(frame).encode())
                Path("reports/E017_station_receipts.json").write_text(json.dumps(frame, indent=2))
                print(json.dumps(frame), flush=True)
                cycles += 1
                # Short sleeps preserve responsiveness to the dedicated stop file.
                remaining = protocol["interval_seconds"] - (time.monotonic() - started)
                deadline = time.monotonic() + max(0, remaining)
                while time.monotonic() < deadline and utcnow() < parse_time(protocol["stop_at"]):
                    if (archive.root / "STOP_STATION_RECEIPTS").exists():
                        break
                    time.sleep(max(0, min(5, deadline - time.monotonic())))
            archive.append(
                "experiment_report",
                "E017_stopped",
                utcnow(),
                {},
                canonical({"run_record_id": run_id, "cycles": cycles}).encode(),
            )
    finally:
        client.close()
        archive.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int)
    main(parser.parse_args().run_record_id)
