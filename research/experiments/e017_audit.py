"""Read-only receipt audit: distinguish new observations from metadata versions."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path

from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import iso, parse_time, utcnow


def main(run_id):
    archive = Archive()
    try:
        archive.db.execute("BEGIN")
        sources = {}

        def raw(identifier):
            if identifier not in sources:
                record = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
                if (
                    record is None
                    or hashlib.sha256(archive.body(record)).hexdigest() != record["body_sha256"]
                ):
                    raise ValueError("Missing or modified receipt source")
                sources[identifier] = (record, archive.json(record))
            return sources[identifier]

        registration, protocol = raw(run_id)
        if protocol["experiment"] != "E017-station-receipts-v1":
            raise ValueError("Wrong receipt protocol")
        for path, digest in protocol["code_sha256"].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ValueError("Registered collector changed")
        frames = list(
            archive.db.execute(
                "SELECT * FROM records WHERE kind='station_receipt_frame' AND key=? ORDER BY id",
                (str(run_id),),
            )
        )
        errors, versions, groups, bracketing = [], set(), defaultdict(list), 0
        for frame_row in frames:
            _, frame = raw(frame_row["id"])
            errors.extend(frame["errors"])
            if frame["errors"]:
                continue
            before, _ = raw(frame["before_book_record_id"])
            source, observations = raw(frame["metar_record_id"])
            after, _ = raw(frame["after_book_record_id"])
            if not (
                before["kind"] == after["kind"] == "station_reaction_books"
                and source["kind"] == "metar_receipts"
                and parse_time(before["available_at"])
                < parse_time(source["available_at"])
                < parse_time(after["available_at"])
                and source["available_at"] == json.loads(source["metadata"])["received_at"]
            ):
                raise ValueError("METAR and book receipts do not bracket the batch")
            bracketing += 1
            for identifier in frame["new_version_ids"]:
                if identifier in versions:
                    raise ValueError("First-seen record reused by multiple frames")
                versions.add(identifier)
                record, version = raw(identifier)
                report = version["report"]
                digest = hashlib.sha256(canonical(report).encode()).hexdigest()
                if (
                    record["key"] != str(run_id) + ":" + digest
                    or report not in observations
                    or version["source_record_id"] != source["id"]
                    or version["first_received_at"] != source["available_at"]
                    or record["available_at"] != source["available_at"]
                    or version["station"] not in protocol["stations"]
                    or parse_time(version["provider_receipt_at"]) > parse_time(version["first_received_at"])
                    or parse_time(version["observation_at"]) > parse_time(version["first_received_at"])
                ):
                    raise ValueError("Original METAR version or receipt differs from raw source")
                if version["provider_receipt_predates_registration"] != (
                    parse_time(version["provider_receipt_at"]) < parse_time(registration["available_at"])
                ):
                    raise ValueError("Old provider receipt was relabeled as new")
                groups[(version["station"], version["observation_at"])].append(
                    {"record_id": identifier, **version}
                )
        observations = []
        for (station, observation), rows in sorted(groups.items()):
            first = rows[0]
            if first["is_initial_backfill"] or first["provider_receipt_predates_registration"]:
                continue
            changed = Counter()
            for previous, current in pairwise(rows):
                for key in set(previous["report"]) | set(current["report"]):
                    if previous["report"].get(key) != current["report"].get(key):
                        changed[key] += 1
            observations.append(
                {
                    "station": station,
                    "observation_at": observation,
                    "first_received_at": first["first_received_at"],
                    "provider_receipt_at": first["provider_receipt_at"],
                    "provider_to_first_receipt_seconds": (
                        parse_time(first["first_received_at"]) - parse_time(first["provider_receipt_at"])
                    ).total_seconds(),
                    "temperature_c": first["report"].get("temp"),
                    "version_record_ids": [r["record_id"] for r in rows],
                    "distinct_raw_reports": len({r["report"]["rawOb"] for r in rows}),
                    "changed_provider_fields": dict(changed),
                }
            )
        result = {
            "generated_at": iso(utcnow()),
            "run_record_id": run_id,
            "last_frame_record_id": frames[-1]["id"] if frames else None,
            "frames": len(frames),
            "bracketed_receipt_frames_verified": bracketing,
            "first_seen_versions_verified": len(versions),
            "raw_records_checked": len(sources),
            "frame_errors": errors,
            "new_station_observation_groups": len(observations),
            "new_observations": observations,
            "network_requests": 0,
            "profitability_proven": False,
            "limits": "Provider receipt is not independently verified first public availability. Metadata-only versions are not independent weather news. Book receipts surrounding our fetch do not prove the market had not already reacted; one-minute sampling cannot establish a subminute speed edge. METAR is not substituted for official settlement.",
        }
        archive.db.commit()
        archive.append("experiment_report", "E017_receipt_audit", utcnow(), {}, canonical(result).encode())
        Path("reports/E017_audit.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        archive.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int, default=47846)
    main(parser.parse_args().run_record_id)
