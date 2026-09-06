"""Reproduce the initial original-text / parsed-archive NBM source comparison."""

import hashlib
import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

from weatherpred.archive import Archive, canonical
from weatherpred.nbm import station_cards
from weatherpred.timeutil import iso, parse_time, utcnow


def main():
    archive = Archive()
    source = archive.db.execute("SELECT * FROM records WHERE id=12809").fetchone()
    body = archive.body(source)
    headers = json.loads(source["metadata"])["headers"]
    protocol = json.loads(Path("config/e003_nbm_acquisition.json").read_text())
    cards = station_cards(body, set(protocol["stations"].values()))
    parsed = archive.json(archive.db.execute("SELECT * FROM records WHERE id=12531").fetchone())
    original = cards["KNYC"]["rows"]
    selected = [r for r in parsed if r["runtime"] == "2025-01-01T01:00:00.000"]
    errors, checks = [], 0
    for raw, other in zip(original, selected, strict=True):
        for key in ("tmp", "tsd", "txn", "xnd"):
            checks += 1
            if raw[key] != other[key]:
                errors.append(
                    {"valid_at": raw["valid_at"], "field": key, "raw": raw[key], "parsed": other[key]}
                )
        # IEM documents these naive forecast timestamps as UTC.
        other_time = datetime.fromisoformat(other["ftime"]).replace(tzinfo=UTC)
        checks += 1
        if parse_time(raw["valid_at"]) != other_time:
            errors.append({"field": "valid_at", "raw": raw["valid_at"], "parsed": other["ftime"]})
    report = {
        "generated_at": iso(utcnow()),
        "full_source_record_id": source["id"],
        "parsed_source_record_id": 12531,
        "full_object_bytes": len(body),
        "full_object_md5_matches_etag": hashlib.md5(body).hexdigest() == headers["etag"].strip('"'),
        "object_last_modified": iso(parsedate_to_datetime(headers["last-modified"])),
        "runtime": cards["KNYC"]["runtime"],
        "storage_delay_after_initialization_seconds": (
            parsedate_to_datetime(headers["last-modified"]) - parse_time(cards["KNYC"]["runtime"])
        ).total_seconds(),
        "exact_stations": sorted(cards),
        "forecast_rows": sum(len(card["rows"]) for card in cards.values()),
        "knyc_field_and_time_checks": checks,
        "mismatches": errors,
        "historical_public_access_independently_verified": False,
        "scores_computed": False,
        "raw_station_search_probes": {
            "json_returned_no_results": [12535, 12538],
            "http_200_error_text": [12737, 12740],
        },
        "limitations": [
            "One product/date source check; not a forecast skill test",
            "Storage time is not a proof of first public dissemination",
            "Do not use 18-hour TXN as a midnight-to-midnight settlement label",
        ],
    }
    archive.append("experiment_report", "E003_source_audit", utcnow(), {}, canonical(report).encode())
    Path("reports/E003_source_audit.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    archive.close()


if __name__ == "__main__":
    main()
