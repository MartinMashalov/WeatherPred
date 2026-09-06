"""Acquire updated NBM cycles without changing the pinned E003 early-cycle dataset."""

import fcntl
import json
import logging
import shutil
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import httpx

from research.experiments.e003_acquire_nbm import BUCKET, NS, check_response
from weatherpred.archive import Archive, canonical
from weatherpred.http import PublicClient
from weatherpred.nbm import station_cards
from weatherpred.timeutil import iso, utcnow

LOG = logging.getLogger(__name__)


def acquire(client, day, hour, stations, hints, range_radius_bytes):
    key = f"blend.{day:%Y%m%d}/{hour:02}/text/blend_nbstx.t{hour:02}z"
    identity = f"{day.isoformat()}:{hour:02}"
    response, listing_id = client.get(
        BUCKET, {"list-type": "2", "prefix": key, "max-keys": 10}, kind="e010_nbm_listing", key=identity
    )
    xml = ET.fromstring(response.content)
    matches = [node for node in xml.findall("s:Contents", NS) if node.findtext("s:Key", namespaces=NS) == key]
    if xml.findtext("s:IsTruncated", namespaces=NS) != "false" or len(matches) != 1:
        raise ValueError("Missing, duplicate or truncated updated-object listing")
    node = matches[0]
    obj = {
        "key": key,
        "last_modified": node.findtext("s:LastModified", namespaces=NS),
        "etag": node.findtext("s:ETag", namespaces=NS),
        "size": int(node.findtext("s:Size", namespaces=NS)),
        "listing_record_id": listing_id,
    }
    cards, sources, recovery = {}, [listing_id], None
    try:
        for station in sorted(stations):
            start = max(0, hints[station] - range_radius_bytes)
            end = min(obj["size"] - 1, hints[station] + range_radius_bytes - 1)
            response, rec = client.get(
                BUCKET + key,
                kind="e010_nbm_range",
                key=identity + ":" + station,
                byte_range=(start, end),
                expected_etag=obj["etag"],
            )
            sources.append(rec)
            check_response(response, obj)
            found = station_cards(response.content, {station})
            if set(found) != {station}:
                raise ValueError("Updated station offset moved: " + station)
            card = found[station]
            card["relative_byte_offset"] += start
            cards[station] = card
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        recovery = str(exc)
        response, rec = client.get(
            BUCKET + key, kind="e010_nbm_full", key=identity, expected_etag=obj["etag"]
        )
        sources.append(rec)
        check_response(response, obj)
        cards = station_cards(response.content, stations)
    if set(cards) != stations or any(
        card["runtime"] != f"{day.isoformat()}T{hour:02}:00:00+00:00" for card in cards.values()
    ):
        raise ValueError("Updated guidance has incorrect station or runtime")
    result = {
        "day": day.isoformat(),
        "cycle_utc_hour": hour,
        "object": obj,
        "cards": cards,
        "raw_source_record_ids": sources,
        "full_object_recovery_reason": recovery,
        "acquired_at": iso(utcnow()),
        "public_dissemination_verified": False,
    }
    rec = client.archive.append("e010_nbm_cycle", identity, utcnow(), {}, canonical(result).encode())
    return result, rec


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    archive = Archive()
    client = PublicClient(archive, interval=0.25)
    try:
        with (archive.root / "e010_nbm.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another updated-NBM collector holds the lock") from None
            config = json.loads(Path("config/e010_nbm_updates.json").read_text())
            source_paths = [
                Path(__file__),
                Path("config/e010_nbm_updates.json"),
                Path("research/experiments/e003_acquire_nbm.py"),
                Path("weatherpred/nbm.py"),
                Path("weatherpred/http.py"),
            ]
            source_ids = {
                str(p): archive.append("research_source", str(p), utcnow(), {}, p.read_bytes())
                for p in source_paths
            }
            protocol_id = archive.append(
                "experiment_protocol",
                config["experiment"],
                utcnow(),
                {},
                canonical({"config": config, "source_record_ids": source_ids}).encode(),
            )
            report = {
                "started_at": iso(utcnow()),
                "protocol_record_id": protocol_id,
                "cycles": [],
                "errors": [],
                "stop_reason": "error",
                "scores_computed": False,
                "holdout_accessed": False,
            }
            stations = set(config["stations"].values())
            seed = archive.db.execute(
                "SELECT * FROM records WHERE id=?", (config["seed_offset_record_id"],)
            ).fetchone()
            original = {
                s: card["relative_byte_offset"]
                for s, card in station_cards(archive.body(seed), stations).items()
            }
            hints = {hour: dict(original) for hour in config["cycles_utc"]}
            recoveries = 0
            start, end = (
                date.fromisoformat(config["start_day"]),
                date.fromisoformat(config["end_day_exclusive"]),
            )
            try:
                for i in range((end - start).days):
                    day = start + timedelta(days=i)
                    for hour in config["cycles_utc"]:
                        if (
                            (archive.root / "STOP_E010_NBM").exists()
                            or shutil.disk_usage(archive.root).free < 20 * 1024**3
                            or recoveries >= config["full_recovery_cap"]
                        ):
                            report["stop_reason"] = "stop_file_disk_or_recovery_cap"
                            return
                        identity = f"{day.isoformat()}:{hour:02}"
                        try:
                            cached = archive.latest("e010_nbm_cycle", identity)
                            if cached is None:
                                result, rec = acquire(
                                    client, day, hour, stations, hints[hour], config["range_radius_bytes"]
                                )
                            else:
                                result, rec = archive.json(cached), cached["id"]
                            hints[hour].update(
                                {s: card["relative_byte_offset"] for s, card in result["cards"].items()}
                            )
                            recoveries += int(result["full_object_recovery_reason"] is not None)
                            report["cycles"].append(
                                {
                                    "day": day.isoformat(),
                                    "cycle_utc_hour": hour,
                                    "record_id": rec,
                                    "object_last_modified": result["object"]["last_modified"],
                                    "stations": len(result["cards"]),
                                    "full_recovery": result["full_object_recovery_reason"] is not None,
                                }
                            )
                        except (httpx.HTTPError, ValueError, KeyError, ET.ParseError) as exc:
                            report["errors"].append(
                                {"day": day.isoformat(), "cycle_utc_hour": hour, "error": str(exc)}
                            )
                            LOG.warning("Updated cycle unavailable: %s %s", identity, exc)
                    if i % 5 == 0:
                        Path("reports/E010_acquisition_progress.json").write_text(
                            json.dumps(report, indent=2)
                        )
                        LOG.info(
                            "Updated NBM through%s; cycles=%s errors=%s",
                            day,
                            len(report["cycles"]),
                            len(report["errors"]),
                        )
                report["stop_reason"] = "all_development_cycles_attempted"
            finally:
                report["finished_at"] = iso(utcnow())
                archive.append(
                    "experiment_report", "E010_acquisition", utcnow(), {}, canonical(report).encode()
                )
                Path("reports/E010_acquisition.json").write_text(json.dumps(report, indent=2))
            print(json.dumps({k: v for k, v in report.items() if k != "cycles"}, indent=2))
    finally:
        client.close()
        archive.close()


if __name__ == "__main__":
    main()
