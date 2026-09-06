"""Bounded retrospective TWC station-hour acquisition, with explicit provenance.

No historical publication time is inferred from weather timestamps. This module
only acquires and parses data; it does not fit models, choose cases or score them.
"""

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.http import PublicClient
from weatherpred.timeutil import iso, parse_time, utcnow

KEY = "TWC-station-history-20260504-20260824-v1"
ENDPOINT = "https://weather.com/kalshi/api/metar"
FIRST = date(2026, 5, 4)
LAST = date(2026, 8, 24)
LAST_MODEL_DATE = date(2026, 8, 30)


def weeks():
    return [(FIRST + timedelta(weeks=i)).isoformat() for i in range((LAST - FIRST).days // 7 + 1)]


def station_clock(observed_at, timezone):
    """UTC identity is preserved even when a station's local hour repeats."""
    return parse_time(observed_at).astimezone(ZoneInfo(timezone))


def acquisition_plan(archive):
    existing = archive.latest("station_history_protocol", KEY)
    if existing:
        return existing["id"], archive.json(existing)
    plan = {
        "created_at": iso(utcnow()),
        "purpose": "Expand retrospective station training inputs; no fitting or outcome scoring.",
        "requested_mondays": weeks(),
        "availability_probes_first": [FIRST.isoformat(), LAST.isoformat()],
        "endpoint": ENDPOINT,
        "query_fixed_fields": {"primary": "true"},
        "minimum_request_interval_seconds": 1,
        "maximum_retries_per_request": 2,
        "stop_rule": "Stop after both endpoint-week probes are unavailable or invalid; stop immediately on authentication/permission denial. Retain each failure and all missing weeks.",
        "parsed_model_row_cutoff": "Both local_date and UTC observed_at must be on/before 2026-08-30.",
        "duplicate_rule": "Exact repeated station+UTC rows collapse with provenance; conflicting variants are quarantined, never chosen by their temperature or response order.",
        "availability_claim": "Current retrospective versions only; acquisition receipt is not historical publication time.",
        "model_fitting": False,
        "sealed_2025_data_accessed": False,
        "source_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    identifier = archive.append("station_history_protocol", KEY, utcnow(), {}, canonical(plan).encode())
    return identifier, plan


def parse_snapshot(data, requested_week, source_record_id, received_at):
    """Return unique valid retrospective rows plus detailed rejected-row evidence."""
    monday = date.fromisoformat(requested_week)
    dates = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    if monday.weekday() != 0 or requested_week not in weeks():
        raise ValueError("Snapshot is outside the predeclared 17 Mondays")
    if (
        data.get("weekStart") != requested_week
        or data.get("weekEnd") != dates[-1]
        or data.get("dates") != dates
    ):
        raise ValueError("Returned calendar does not match the requested week")
    if not isinstance(data.get("stations"), list) or not isinstance(data.get("source"), str):
        raise TypeError("Missing station array or provider source label")
    fetched = parse_time(data["fetchedAt"])
    received = parse_time(received_at)
    stations = data["stations"]
    if len({s["icaoId"] for s in stations}) != len(stations):
        raise ValueError("Duplicate station objects")
    if sum(len(s["observations"]) for s in stations) != data.get("totalObservations"):
        raise ValueError("Provider observation count does not match raw station arrays")
    grouped, rejected, station_summaries = defaultdict(list), [], []
    for station_index, station in enumerate(stations):
        station_id = station["icaoId"]
        if not isinstance(station_id, str) or not station_id:
            raise ValueError("Missing station identity")
        try:
            ZoneInfo(station["timezone"])
        except (KeyError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Invalid station timezone") from exc
        station_summaries.append(
            {
                "station_id": station_id,
                "station_timezone": station["timezone"],
                "raw_observations": len(station["observations"]),
            }
        )
        for row_index, observation in enumerate(station["observations"]):
            provenance = {
                "station_id": station_id,
                "source_station_index": station_index,
                "source_row_index": row_index,
                "source_record_id": source_record_id,
            }
            try:
                if observation.get("icaoId") != station_id:
                    raise ValueError("Observation belongs to a different station")
                observed = parse_time(observation["reportTimeUTC"])
                local = station_clock(observed, station["timezone"])
                if observed > received:
                    raise ValueError("Observation time is after our receipt")
                if local.date().isoformat() not in dates:
                    raise ValueError("Observation is outside the requested local week")
                if (
                    observation["localDate"] != local.date().isoformat()
                    or observation["localHour"] != local.hour
                ):
                    raise ValueError("Provider local calendar differs from station timezone conversion")
                if local.date() > LAST_MODEL_DATE or observed.date() > LAST_MODEL_DATE:
                    raise ValueError("Excluded by August 30 local-and-UTC model cutoff")
                if observed.minute or observed.second or observed.microsecond:
                    raise ValueError("Observation is not aligned to an exact UTC hour")
                value = observation["tempF"]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError("Fahrenheit temperature is missing or nonfinite")
                if observation["status"] not in ("settled", "pending"):
                    raise ValueError("Unknown source status")
                row = {
                    **provenance,
                    "station_name": station.get("stationName"),
                    "station_timezone": station["timezone"],
                    "observed_at": iso(observed),
                    "local_date": local.date().isoformat(),
                    "local_hour": local.hour,
                    "local_fold": local.fold,
                    "temperature_f": value,
                    "temperature_c_reported": observation.get("tempC"),
                    "status": observation["status"],
                    "source_report_time_utc": observation["reportTimeUTC"],
                    "source_report_time_local": observation.get("reportTimeLocal"),
                    "received_at": iso(received),
                    "provider_fetched_at": iso(fetched),
                    "provider_source": data["source"],
                    "requested_week_start": requested_week,
                    "historical_availability_verified": False,
                }
                grouped[(station_id, iso(observed))].append(row)
            except (ValueError, KeyError, TypeError) as exc:
                rejected.append({**provenance, "reason": str(exc)})
    rows, conflicts, duplicates = [], [], 0
    for key, values in sorted(grouped.items()):
        signatures = {
            canonical({k: v for k, v in r.items() if k not in ("source_row_index", "source_station_index")})
            for r in values
        }
        if len(signatures) != 1:
            conflicts.append(
                {
                    "station_id": key[0],
                    "observed_at": key[1],
                    "source_record_id": source_record_id,
                    "variants": [
                        {
                            "source_row_index": r["source_row_index"],
                            "temperature_f": r["temperature_f"],
                            "status": r["status"],
                        }
                        for r in values
                    ],
                }
            )
            continue
        row = dict(values[0], source_row_indexes=sorted(r["source_row_index"] for r in values))
        duplicates += len(values) - 1
        rows.append(row)
    report = {
        "source_record_id": source_record_id,
        "requested_week_start": requested_week,
        "raw_observations": data["totalObservations"],
        "station_count": len(stations),
        "parsed_rows": len(rows),
        "exact_duplicates_collapsed": duplicates,
        "conflicting_station_hours": conflicts,
        "rejected_rows": rejected,
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "stations": station_summaries,
        "historical_availability_verified": False,
    }
    return rows, report


def acquire(archive, probe_only=False):
    identifier, plan = acquisition_plan(archive)
    order = plan["availability_probes_first"] + [
        w for w in plan["requested_mondays"] if w not in plan["availability_probes_first"]
    ]
    if probe_only:
        order = order[:2]
    client = PublicClient(archive, interval=1)
    attempts, probe_usable, stop_reason = [], [], None
    try:
        for week in order:
            key = KEY + ":" + week
            cached = archive.latest("station_history_weekly", key)
            before = archive.db.execute("SELECT COALESCE(MAX(id),0) FROM records").fetchone()[0]
            attempt = {"requested_week_start": week, "reused_archived_response": cached is not None}
            try:
                if cached:
                    data, source_id = archive.json(cached), cached["id"]
                else:
                    data, source_id = client.json(
                        ENDPOINT,
                        {"primary": "true", "weekStart": week},
                        kind="station_history_weekly",
                        key=key,
                    )
                record = archive.db.execute("SELECT * FROM records WHERE id=?", (source_id,)).fetchone()
                _, coverage = parse_snapshot(data, week, source_id, record["available_at"])
                attempt.update(
                    source_record_id=source_id,
                    raw_observations=coverage["raw_observations"],
                    station_count=coverage["station_count"],
                    parsed_rows=coverage["parsed_rows"],
                    usable=coverage["parsed_rows"] > 0,
                )
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                attempt.update(usable=False, error=str(exc))
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (401, 403):
                    stop_reason = "Source denied unauthenticated access; no further requests"
            attempt["request_record_ids"] = [
                r["id"]
                for r in archive.db.execute(
                    "SELECT id FROM records WHERE key=? AND id>? ORDER BY id", (key, before)
                )
            ]
            attempts.append(attempt)
            print(json.dumps(attempt), flush=True)
            if len(attempts) <= 2:
                probe_usable.append(attempt["usable"])
            if len(attempts) == 2 and not any(probe_usable):
                stop_reason = "Both boundary-week probes unavailable or invalid; remaining requests stopped"
            if stop_reason:
                break
    finally:
        client.close()
    result = {
        "generated_at": iso(utcnow()),
        "protocol_record_id": identifier,
        "attempts": attempts,
        "stop_reason": stop_reason,
        "requested_weeks": plan["requested_mondays"],
        "unattempted_weeks": [
            w for w in plan["requested_mondays"] if w not in {a["requested_week_start"] for a in attempts}
        ],
        "historical_availability_verified": False,
        "model_fitting_performed": False,
    }
    rec = archive.append("station_history_acquisition", KEY, utcnow(), {}, canonical(result).encode())
    Path("reports/station_history_acquisition.json").write_text(
        json.dumps({"record_id": rec, **result}, indent=2)
    )
    return rec, result


def export(archive):
    protocol = archive.latest("station_history_protocol", KEY)
    if protocol is None:
        raise ValueError("Acquisition protocol missing")
    all_rows, week_reports, missing = [], [], []
    for week in weeks():
        record = archive.latest("station_history_weekly", KEY + ":" + week)
        if record is None:
            missing.append(week)
            continue
        body = archive.body(record)
        if hashlib.sha256(body).hexdigest() != record["body_sha256"]:
            raise ValueError("Archived source bytes changed")
        try:
            rows, report = parse_snapshot(json.loads(body), week, record["id"], record["available_at"])
            all_rows.extend(rows)
            week_reports.append(report)
        except (ValueError, KeyError, TypeError) as exc:
            week_reports.append(
                {"requested_week_start": week, "source_record_id": record["id"], "error": str(exc)}
            )
    if len({(r["station_id"], r["observed_at"]) for r in all_rows}) != len(all_rows):
        raise ValueError("Overlapping station-hours across distinct requested weeks")
    all_rows.sort(key=lambda r: (r["station_id"], r["observed_at"]))
    parsed_bytes = "".join(canonical(r) + "\n" for r in all_rows).encode()
    row_path = Path("reports/station_history_rows.jsonl")
    temporary = row_path.with_suffix(".jsonl.tmp")
    temporary.write_bytes(parsed_bytes)
    temporary.replace(row_path)
    station_coverage = []
    station_timezones = {}
    for week in week_reports:
        for station in week.get("stations", []):
            previous_zone = station_timezones.setdefault(station["station_id"], station["station_timezone"])
            if previous_zone != station["station_timezone"]:
                raise ValueError("Station timezone changed between source snapshots")
    for station in sorted(station_timezones):
        rows = [r for r in all_rows if r["station_id"] == station]
        zone = ZoneInfo(station_timezones[station])
        expected = set()
        for week in weeks():
            start = datetime.combine(date.fromisoformat(week), time(), zone).astimezone(UTC)
            end = (datetime.combine(date.fromisoformat(week) + timedelta(days=7), time(), zone)).astimezone(
                UTC
            )
            observed = start
            while observed < end:
                if observed.date() <= LAST_MODEL_DATE and observed.astimezone(zone).date() <= LAST_MODEL_DATE:
                    expected.add(iso(observed))
                observed += timedelta(hours=1)
        observed = {r["observed_at"] for r in rows}
        station_coverage.append(
            {
                "station_id": station,
                "timezone": station_timezones[station],
                "rows": len(rows),
                "expected_hours_in_requested_window": len(expected),
                "missing_hours": len(expected - observed),
                "missing_hours_between_first_and_last_observation": (
                    int(
                        (
                            parse_time(rows[-1]["observed_at"]) - parse_time(rows[0]["observed_at"])
                        ).total_seconds()
                        // 3600
                    )
                    + 1
                    - len(observed)
                    if rows
                    else None
                ),
                "missing_hour_timestamps": sorted(expected - observed),
                "first_observed_at": rows[0]["observed_at"] if rows else None,
                "last_observed_at": rows[-1]["observed_at"] if rows else None,
                "status_counts": dict(Counter(r["status"] for r in rows)),
            }
        )
    result = {
        "generated_at": iso(utcnow()),
        "protocol_record_id": protocol["id"],
        "weeks": week_reports,
        "missing_weeks": missing,
        "empty_source_weeks": [
            w["requested_week_start"] for w in week_reports if w.get("raw_observations") == 0
        ],
        "rows": len(all_rows),
        "stations": len(station_coverage),
        "stations_with_rows": sum(s["rows"] > 0 for s in station_coverage),
        "station_coverage": station_coverage,
        "parsed_rows_sha256": hashlib.sha256(parsed_bytes).hexdigest(),
        "historical_availability_verified": False,
        "model_fitting_performed": False,
        "parser_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "limits": "Current source versions retrieved now. Missing hours remain missing, pending rows remain labeled pending, conflicting rows are quarantined. No interval is declared untouched or scored. No model rows after August30 UTC or station local date.",
    }
    rec = archive.append("station_history_export", KEY, utcnow(), {}, canonical(result).encode())
    Path("reports/station_history_coverage.json").write_text(
        json.dumps({"record_id": rec, **result}, indent=2)
    )
    return {
        "record_id": rec,
        "rows": len(all_rows),
        "stations": len(station_coverage),
        "stations_with_rows": result["stations_with_rows"],
        "missing_weeks": missing,
        "parsed_rows_sha256": result["parsed_rows_sha256"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--export-only", action="store_true")
    args = parser.parse_args()
    arc = Archive()
    try:
        if not args.export_only:
            acquire(arc, args.probe_only)
        print(json.dumps(export(arc), indent=2))
    finally:
        arc.close()
