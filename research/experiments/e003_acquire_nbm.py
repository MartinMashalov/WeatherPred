"""Acquire original NBM cards with object timestamp evidence and exact station IDs."""

import fcntl
import json
import logging
import shutil
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.http import PublicClient
from weatherpred.nbm import station_cards
from weatherpred.timeutil import iso, parse_time, utcnow

LOG = logging.getLogger(__name__)
BUCKET = "https://noaa-nbm-grib2-pds.s3.amazonaws.com/"
NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}


def listing(client, day, hour):
    key = f"blend.{day:%Y%m%d}/{hour:02}/text/blend_nbstx.t{hour:02}z"
    response, rec = client.get(
        BUCKET,
        {"list-type": "2", "prefix": key, "max-keys": 10},
        kind="e003_nbm_listing",
        key=day.isoformat(),
    )
    root = ET.fromstring(response.content)
    if root.findtext("s:IsTruncated", namespaces=NS) != "false":
        raise ValueError("Unexpected truncated exact-object listing")
    matches = [
        node for node in root.findall("s:Contents", NS) if node.findtext("s:Key", namespaces=NS) == key
    ]
    if len(matches) != 1:
        raise ValueError("Original NBS object missing or ambiguous")
    node = matches[0]
    return {
        "key": key,
        "last_modified": node.findtext("s:LastModified", namespaces=NS),
        "etag": node.findtext("s:ETag", namespaces=NS),
        "size": int(node.findtext("s:Size", namespaces=NS)),
        "listing_record_id": rec,
    }


def check_response(response, obj):
    timestamp = parsedate_to_datetime(response.headers["last-modified"])
    if timestamp != parse_time(obj["last_modified"]) or response.headers["etag"] != obj["etag"]:
        raise ValueError("Object changed between listing and response")


def acquire_day(client, day, protocol, hints, seed):
    archive = client.archive
    obj = listing(client, day, protocol["cycle_utc_hour"])
    stations = set(protocol["stations"].values())
    cards, sources, failed_hints = {}, [], []
    if seed is not None:
        metadata = json.loads(seed["metadata"])
        if metadata["headers"].get("etag") == obj["etag"]:
            cards = station_cards(archive.body(seed), stations)
            sources = [seed["id"]]
    if not cards:
        try:
            for station in sorted(stations):
                offset = hints[station]
                low, high = max(0, offset - 4096), min(obj["size"] - 1, offset + 8191)
                response, rec = client.get(
                    BUCKET + obj["key"],
                    kind="e003_nbm_range",
                    key=day.isoformat() + ":" + station,
                    byte_range=(low, high),
                    expected_etag=obj["etag"],
                )
                check_response(response, obj)
                found = station_cards(response.content, {station})
                if set(found) != {station}:
                    raise ValueError("Station offset moved outside the requested range: " + station)
                card = found[station]
                card["relative_byte_offset"] += low
                cards[station] = card
                sources.append(rec)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            failed_hints.append(str(exc))
            response, rec = client.get(
                BUCKET + obj["key"], kind="e003_nbm_full", key=day.isoformat(), expected_etag=obj["etag"]
            )
            check_response(response, obj)
            cards = station_cards(response.content, stations)
            sources.append(rec)
    if set(cards) != stations:
        raise ValueError("Missing exact station cards after complete-object recovery")
    expected_runtime = f"{day.isoformat()}T{protocol['cycle_utc_hour']:02}:00:00+00:00"
    if any(card["runtime"] != expected_runtime for card in cards.values()):
        raise ValueError("Card runtime differs from requested object date/cycle")
    result = {
        "day": day.isoformat(),
        "object": obj,
        "cards": cards,
        "raw_source_record_ids": sources,
        "failed_range_hints": failed_hints,
        "acquired_at": iso(utcnow()),
        "historical_public_access_independently_verified": False,
    }
    rec = archive.append("e003_nbm_day", day.isoformat(), utcnow(), {}, canonical(result).encode())
    return result, rec


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    archive = Archive()
    client = PublicClient(archive, interval=0.5)
    protocol = json.loads(Path("config/e003_nbm_acquisition.json").read_text())
    report = {
        "started_at": iso(utcnow()),
        "days": [],
        "errors": [],
        "stop_reason": "error",
        "scores_computed": False,
        "holdout_accessed": False,
    }
    with (archive.root / "e003_nbm.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another NBM acquisition holds the lock") from None
        source_ids = []
        for path in (
            "config/e003_nbm_acquisition.json",
            "research/experiments/e003_acquire_nbm.py",
            "weatherpred/nbm.py",
            "weatherpred/http.py",
        ):
            source_ids.append(archive.append("research_source", path, utcnow(), {}, Path(path).read_bytes()))
        report["protocol_record_id"] = archive.append(
            "experiment_protocol",
            protocol["experiment"],
            utcnow(),
            {},
            canonical({"protocol": protocol, "source_record_ids": source_ids}).encode(),
        )
        seed = archive.db.execute(
            "SELECT * FROM records WHERE id=?", (protocol["seed_full_object_record_id"],)
        ).fetchone()
        hints = {
            station: card["relative_byte_offset"]
            for station, card in station_cards(archive.body(seed), set(protocol["stations"].values())).items()
        }
        day, end = (date.fromisoformat(protocol[k]) for k in ("start_day", "end_day_exclusive"))
        try:
            while day < end:
                if (archive.root / "STOP_E003_NBM").exists():
                    report["stop_reason"] = "stop_file"
                    break
                if shutil.disk_usage(archive.root).free < 20 * 1024**3:
                    report["stop_reason"] = "low_disk"
                    break
                try:
                    cached = archive.latest("e003_nbm_day", day.isoformat())
                    if cached is None:
                        result, rec = acquire_day(client, day, protocol, hints, seed)
                    else:
                        result, rec = archive.json(cached), cached["id"]
                    hints.update(
                        {station: card["relative_byte_offset"] for station, card in result["cards"].items()}
                    )
                    report["days"].append(
                        {
                            "day": day.isoformat(),
                            "record_id": rec,
                            "stations": len(result["cards"]),
                            "object_last_modified": result["object"]["last_modified"],
                            "versions": sorted({card["version"] for card in result["cards"].values()}),
                            "full_object_recovery": bool(result["failed_range_hints"]),
                        }
                    )
                except (httpx.HTTPError, ValueError, KeyError, ET.ParseError) as exc:
                    report["errors"].append({"day": day.isoformat(), "error": str(exc)})
                    LOG.warning("Missing NBM day %s: %s", day, exc)
                if (day - date.fromisoformat(protocol["start_day"])).days % 5 == 0:
                    Path("reports/E003_NBM_acquisition_progress.json").write_text(
                        json.dumps(report, indent=2)
                    )
                    LOG.info(
                        "NBM through %s; days=%s errors=%s; no scores",
                        day,
                        len(report["days"]),
                        len(report["errors"]),
                    )
                day += timedelta(days=1)
            else:
                report["stop_reason"] = "all_development_days_attempted"
        finally:
            report["finished_at"] = iso(utcnow())
            archive.append(
                "experiment_report", "E003_NBM_acquisition", utcnow(), {}, canonical(report).encode()
            )
            Path("reports/E003_NBM_acquisition.json").write_text(json.dumps(report, indent=2))
            client.close()
            archive.close()
    print(json.dumps({k: v for k, v in report.items() if k != "days"}, indent=2))


if __name__ == "__main__":
    main()
