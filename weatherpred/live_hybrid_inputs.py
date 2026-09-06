"""I/O-free KAUS source readiness; no fetching, model loading, scoring or orders.

The caller supplies original archived bytes and records. A separately registered
collector must create those records before their decision-time use.
"""

import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from weatherpred.archive import canonical
from weatherpred.nbh_v2 import HEADER, SEPARATOR
from weatherpred.timeutil import parse_time

HOUR = 3_600_000
STATION = "KAUS"
ZONE = "America/Chicago"
TWC_URL = "https://weather.com/kalshi/api/metar"
NBH_BASE = "https://noaa-nbm-grib2-pds.s3.amazonaws.com/"
TARGETS = [
    int(datetime(2026, 9, day, hour, tzinfo=UTC).timestamp()) * 1000
    for day in (8, 9)
    for hour in (0, 6, 12, 18)
]
MAPPING_OFFSETS = [-60, 0, 30, 60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 21600, 43200, 86400]


def sha(body):
    return hashlib.sha256(body).hexdigest()


def milliseconds(value):
    parsed = parse_time(value)
    if parsed.utcoffset() is None:
        raise ValueError("A source clock needs an explicit timezone")
    # Ceiling rather than truncation prevents a submillisecond late receipt
    # from being admitted at a decision boundary.
    epoch = parsed - datetime(1970, 1, 1, tzinfo=UTC)
    microseconds = (epoch.days * 86400 + epoch.seconds) * 1_000_000 + epoch.microseconds
    return (microseconds + 999) // 1000


def binding(target_ms):
    if type(target_ms) is not int or target_ms not in TARGETS:
        raise ValueError("Target is outside the eight fixed mapping hours")
    run = datetime.fromtimestamp((target_ms - 3 * HOUR) / 1000, UTC)
    return {
        "station_id": STATION,
        "target_ms": target_ms,
        "decision_ms": target_ms - HOUR,
        "run_ms": target_ms - 3 * HOUR,
        "context_end_ms": target_ms - 2 * HOUR,
        "horizon_hours": 1,
        "forecast_step": 2,
        "nbh_key": f"blend.{run:%Y%m%d}/{run:%H}/text/blend_nbhtx.t{run:%H}z",
    }


def planned_requests(config):
    """Union exact request times/URLs/parameters; each receipt may serve many purposes."""
    start, stop = milliseconds(config["start_utc"]), milliseconds(config["hard_stop_utc"])
    if (
        start != milliseconds("2026-09-07T20:00:00Z")
        or stop != milliseconds("2026-09-10T18:10:00Z")
        or config["target_utc"] != [datetime.fromtimestamp(t / 1000, UTC).isoformat() for t in TARGETS]
        or config["mapping_offsets_seconds"] != MAPPING_OFFSETS
        or config["context_refresh_seconds"] != 600
        or config["decision_refresh_seconds_before"] != 120
        or config["nbh_fetch_minutes_before_decision"] != [30, 10]
    ):
        raise ValueError("Fixed shared acquisition calendar changed")
    requests = {}

    def add(when, url, params, purpose, target=None):
        if not start <= when < stop:
            raise ValueError("A request is outside the finite collector window")
        identity = (when, url, canonical(params))
        row = requests.setdefault(
            identity, {"scheduled_ms": when, "url": url, "params": params, "purposes": [], "target_ms": []}
        )
        if purpose not in row["purposes"]:
            row["purposes"].append(purpose)
        if target is not None and target not in row["target_ms"]:
            row["target_ms"].append(target)

    current_params = {"primary": "true", "weekStart": "2026-09-07"}
    add(start, TWC_URL, {"primary": "true", "weekStart": "2026-08-31"}, "initial_older_context")
    for when in range(start, stop, 600_000):
        add(when, TWC_URL, current_params, "context_refresh")
    for target in TARGETS:
        case = binding(target)
        eastern = datetime.fromtimestamp(target / 1000, UTC).astimezone(ZoneInfo("America/New_York"))
        ticker = f"KXTEMPAUSH-{eastern:%y%b%d%H}".upper()
        event_url = "https://external-api.kalshi.com/trade-api/v2/events/" + ticker
        event_params = {"with_nested_markets": "true"}
        add(case["decision_ms"] - 120_000, TWC_URL, current_params, "decision_readiness", target)
        add(case["decision_ms"] + 30_000, event_url, event_params, "mapping_listing_census", target)
        for seconds in MAPPING_OFFSETS:
            when = target + seconds * 1000
            add(when, TWC_URL, current_params, "mapping_source", target)
            add(when, event_url, event_params, "mapping_event", target)
        for minutes in (30, 10):
            add(
                case["decision_ms"] - minutes * 60_000,
                NBH_BASE + case["nbh_key"],
                None,
                "original_nbh_readiness",
                target,
            )
    return [requests[key] for key in sorted(requests)]


def checked_receipt(body, record, registration_id, registered_at):
    """Validate body and hash-chained record metadata; chain anchoring is external."""
    fields = [
        record[k] for k in ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")
    ]
    if sha(body) != record["body_sha256"] or sha(canonical(fields).encode()) != record["record_sha256"]:
        raise ValueError("Original response bytes or metadata hash changed")
    metadata = json.loads(record["metadata"])
    received = milliseconds(metadata["actual_received_at"])
    if (
        record["kind"] != "kaus_source_http"
        or type(record["id"]) is not int
        or not registration_id < record["id"]
        or metadata["registration_id"] != registration_id
        or parse_time(record["available_at"]) != parse_time(metadata["actual_received_at"])
        or not parse_time(registered_at)
        <= parse_time(metadata["request_started_at"])
        <= parse_time(metadata["actual_received_at"])
        or metadata["complete"] is not True
        or metadata["status"] != 200
    ):
        raise ValueError("Source request/receipt registration or completion is invalid")
    return metadata, received


class JsonNumber(str):
    """Exact original JSON number spelling, including trailing zeroes/exponent."""


def lexical_tree(value):
    if isinstance(value, JsonNumber):
        return {"json_number_lexeme": str(value)}
    if isinstance(value, list):
        return [lexical_tree(item) for item in value]
    if isinstance(value, dict):
        return {key: lexical_tree(item) for key, item in value.items()}
    return value


def reject_constant(value):
    raise ValueError("Nonstandard/nonfinite JSON number: " + value)


def integer(value):
    if not isinstance(value, JsonNumber) or re.fullmatch(r"-?\d+", value) is None:
        raise ValueError("Expected an integer JSON field")
    return int(value)


def parse_twc_snapshot(body, record, *, week_start, registration_id, registered_at):
    """Preserve every KAUS row edition; no conversion from Celsius or METAR."""
    metadata, received = checked_receipt(body, record, registration_id, registered_at)
    if (
        metadata["url"] != TWC_URL
        or metadata["params"] != {"primary": "true", "weekStart": week_start}
        or len(body) > 4 * 1024**2
    ):
        raise ValueError("Wrong TWC source request or response size")
    if week_start not in ("2026-08-31", "2026-09-07"):
        raise ValueError("Unregistered context/mapping week")
    monday = date.fromisoformat(week_start)
    dates = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    data = json.loads(body, parse_int=JsonNumber, parse_float=JsonNumber, parse_constant=reject_constant)
    if (
        data["weekStart"] != dates[0]
        or data["weekEnd"] != dates[-1]
        or data["dates"] != dates
        or data["source"] != "live"
        or milliseconds(data["fetchedAt"]) > received
        or len({station["icaoId"] for station in data["stations"]}) != len(data["stations"])
        or sum(len(station["observations"]) for station in data["stations"])
        != integer(data["totalObservations"])
    ):
        raise ValueError("TWC calendar/source/fetched clock/census changed")
    stations = [(i, station) for i, station in enumerate(data["stations"]) if station["icaoId"] == STATION]
    snapshot = {
        "station_id": STATION,
        "week_start": week_start,
        "received_ms": received,
        "source_record_id": record["id"],
        "complete": len(stations) == 1,
    }
    if len(stations) != 1:
        return {"rows": [], "reason": "kaus_station_absent", "snapshot": snapshot}
    station_index, station = stations[0]
    if station["timezone"] != ZONE:
        raise ValueError("KAUS timezone changed")
    rows = []
    for row_index, raw in enumerate(station["observations"]):
        observed = milliseconds(raw["reportTimeUTC"])
        local = datetime.fromtimestamp(observed / 1000, UTC).astimezone(ZoneInfo(ZONE))
        if (
            raw["icaoId"] != STATION
            or observed % HOUR
            or observed > received
            or raw["localDate"] != local.date().isoformat()
            or integer(raw["localHour"]) != local.hour
            or local.date().isoformat() not in dates
            or raw["status"] not in ("pending", "settled")
        ):
            raise ValueError("TWC station/hour/local clock/status differs")
        value = raw["tempF"]
        if value is not None and (not isinstance(value, JsonNumber) or not Decimal(value).is_finite()):
            raise ValueError("TWC Fahrenheit value is not a finite original JSON number")
        rows.append(
            {
                "station_id": STATION,
                "station_timezone": ZONE,
                "observed_ms": observed,
                "local_date": local.date().isoformat(),
                "local_hour": local.hour,
                "local_fold": local.fold,
                "status": raw["status"],
                "temperature_f_lexeme": str(value) if value is not None else None,
                "revision_sha256": sha(canonical(lexical_tree(raw)).encode()),
                "source_record_id": record["id"],
                "source_body_sha256": record["body_sha256"],
                "source_record_sha256": record["record_sha256"],
                "source_station_index": station_index,
                "source_row_index": row_index,
                "received_ms": received,
                "actual_received_at": metadata["actual_received_at"],
                "provider_fetched_at": data["fetchedAt"],
                "provider_source": data["source"],
                "product": TWC_URL,
                "requested_week_start": week_start,
                "observation_predates_registration": observed < milliseconds(registered_at),
                "source_publication_time_known": False,
            }
        )
    return {"rows": rows, "reason": None, "snapshot": snapshot}


def context_asof(rows, snapshots, target_ms):
    """Latest received edition at D; later settlement/revisions never repair D."""
    case = binding(target_ms)
    decision, end = case["decision_ms"], case["context_end_ms"]
    start = end - 167 * HOUR
    grouped = defaultdict(list)
    for row in rows:
        # Gate time/identity before reading temperature/status/revision values.
        if row["station_id"] != STATION or row["product"] != TWC_URL:
            raise ValueError("Wrong station or product in TWC context")
        when, received = row["observed_ms"], row["received_ms"]
        if type(when) is not int or when % HOUR or type(received) is not int or received < when:
            raise ValueError("Invalid observation/receipt clock")
        if start <= when <= end and received <= decision and when + 900_000 <= decision:
            grouped[when].append(row)
    provenance, values, reasons = [], [], defaultdict(int)
    for when in range(start, end + HOUR, HOUR):
        variants = grouped.get(when, [])
        if not variants:
            values.append(None)
            provenance.append(None)
            reasons["no_received_hour"] += 1
            continue
        latest = max(row["received_ms"] for row in variants)
        newest = [row for row in variants if row["received_ms"] == latest]
        signatures = {row["revision_sha256"] for row in newest}
        if len(signatures) != 1:
            values.append(None)
            provenance.append(
                {"reason": "same_receipt_conflict", "source_ids": [r["source_record_id"] for r in newest]}
            )
            reasons["same_receipt_conflict"] += 1
            continue
        selected = min(newest, key=lambda row: (row["source_record_id"], row["source_row_index"]))
        same_revision = [row for row in variants if row["revision_sha256"] == selected["revision_sha256"]]
        evidence = {
            **selected,
            "first_local_receipt_ms": min(row["received_ms"] for row in same_revision),
            "all_asof_source_ids": sorted({row["source_record_id"] for row in same_revision}),
        }
        provenance.append(evidence)
        token = selected["temperature_f_lexeme"]
        if selected["status"] != "settled" or token is None:
            values.append(None)
            reasons["latest_received_not_settled_or_missing"] += 1
        else:
            number = Decimal(token)
            if not number.is_finite():
                raise ValueError("Nonfinite normalized context")
            values.append(str(number))
    finite = [start + i * HOUR for i, value in enumerate(values) if value is not None]
    current = [
        s
        for s in snapshots
        if s["received_ms"] <= decision
        and s["station_id"] == STATION
        and s["week_start"] == "2026-09-07"
        and s["complete"] is True
    ]
    latest_snapshot = max((s["received_ms"] for s in current), default=None)
    failures = []
    if len(finite) < 120:
        failures.append("fewer_than_120_finite_context_hours")
    if not finite or decision - max(finite) > 120 * 60_000:
        failures.append("last_finite_hour_older_than_120_minutes")
    if latest_snapshot is None or decision - latest_snapshot > 3 * 60_000:
        failures.append("no_complete_current_week_snapshot_within_3_minutes")
    return {
        **case,
        "eligible": not failures,
        "reasons": failures,
        "hour_exclusions": dict(reasons),
        "context_start_ms": start,
        "temperature_f_decimal_strings": values,
        "provenance": provenance,
        "finite_context_hours": len(finite),
        "latest_snapshot_received_ms": latest_snapshot,
        "availability_basis": "actual_local_receipts_and_asof_settled_status",
        "settlement_product_equivalence_verified": False,
    }


def nbh_input(body, record, *, target_ms, registration_id, registered_at):
    """Only exact KAUS leads2/3; receipt-gate before any temperature decoding."""
    case = binding(target_ms)
    metadata, received = checked_receipt(body, record, registration_id, registered_at)
    if metadata["url"] != NBH_BASE + case["nbh_key"] or metadata.get("params") not in (None, {}):
        raise ValueError("NBH object is not the fixed target's original cycle")
    if received > case["decision_ms"]:
        return {
            **case,
            "eligible": False,
            "reason": "nbh_received_after_decision",
            "source_record_id": record["id"],
            "actual_received_ms": received,
            "response_sha256": record["body_sha256"],
        }
    headers = metadata["headers"]
    modified = milliseconds(parsedate_to_datetime(headers["last-modified"]).isoformat())
    if (
        not case["run_ms"] <= modified <= received <= case["decision_ms"]
        or not headers.get("etag")
        or headers.get("content-encoding", "identity").lower() != "identity"
        or int(headers["content-length"]) != len(body)
        or len(body) > 40 * 1024**2
    ):
        raise ValueError("NBH original object edition/size/receipt is invalid")
    headers_found = list(HEADER.finditer(body))
    wanted = [(i, h) for i, h in enumerate(headers_found) if h[1] == STATION.encode()]
    if not wanted:
        return {
            **case,
            "eligible": False,
            "reason": "kaus_card_missing",
            "source_record_id": record["id"],
            "actual_received_ms": received,
            "response_sha256": record["body_sha256"],
        }
    if len(wanted) != 1:
        raise ValueError("Duplicate KAUS NBH cards")
    index, header = wanted[0]
    limit = headers_found[index + 1].start() if index + 1 < len(headers_found) else len(body)
    end = SEPARATOR.search(body, header.end(), limit)
    if end is None:
        raise ValueError("Incomplete KAUS NBH card")
    raw = body[header.start() : end.end()]
    # Shared frozen extraction is disclosed; only leads2/3 are interpreted.
    from research.probes.e031_extract_trajectories import cell_path

    result = cell_path(
        body,
        body_offset=0,
        card={
            "station_id": STATION,
            "run_ms": case["run_ms"],
            "version": "5.0",
            "byte_offset": header.start(),
            "raw_card_bytes": len(raw),
            "raw_card_sha256": sha(raw),
        },
        case={**case, "case_id": f"KAUS:{target_ms}:h1", "split": "prospective_mapping"},
        source={
            "response_id": record["id"],
            "response_sha256": record["body_sha256"],
            "response_record_sha256": record["record_sha256"],
            "etag": headers["etag"],
            "object_last_modified": headers["last-modified"],
            "object_last_modified_ms": modified,
            "conditional_eligible_at_ms": modified,
            "conditional_eligible_at": datetime.fromtimestamp(modified / 1000, UTC).isoformat(),
            "actual_received_at": metadata["actual_received_at"],
        },
    )
    return {
        **result,
        "eligible": result["available"],
        "actual_received_ms": received,
        "source_record_id": record["id"],
        "response_sha256": record["body_sha256"],
        "availability_basis": "original_edition_actually_received_by_decision",
        "settlement_product_equivalence_verified": False,
    }


def select_nbh_asof(versions, target_ms):
    case = binding(target_ms)
    candidates = []
    for version in versions:
        if (version["station_id"], version["target_ms"], version["run_ms"]) != (
            STATION,
            target_ms,
            case["run_ms"],
        ):
            raise ValueError("NBH version changed fixed station/target/cycle")
        if version["actual_received_ms"] <= case["decision_ms"]:
            candidates.append(version)
    if not candidates:
        return {**case, "eligible": False, "reason": "no_original_nbh_received_by_decision"}
    latest = max(version["actual_received_ms"] for version in candidates)
    selected = [version for version in candidates if version["actual_received_ms"] == latest]
    if len({version["response_sha256"] for version in selected}) != 1:
        raise ValueError("Conflicting NBH editions share the latest receipt timestamp")
    # Do not skip a missing latest edition to rescue an older complete forecast.
    return min(selected, key=lambda version: version["source_record_id"])
