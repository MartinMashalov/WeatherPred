import copy
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from weatherpred.archive import canonical
from weatherpred.live_hybrid_inputs import (
    HOUR,
    NBH_BASE,
    TARGETS,
    TWC_URL,
    binding,
    context_asof,
    milliseconds,
    nbh_input,
    parse_twc_snapshot,
    planned_requests,
    select_nbh_asof,
    sha,
)

REGISTERED = "2026-09-07T19:00:00Z"
TARGET = TARGETS[0]
DECISION = TARGET - HOUR


def iso(when):
    return datetime.fromtimestamp(when / 1000, UTC).isoformat()


def record(body, received, url, params=None, *, identifier=101, headers=None):
    metadata = {
        "url": url,
        "params": params,
        "actual_received_at": received,
        "request_started_at": iso(milliseconds(received) - 1000),
        "registration_id": 100,
        "complete": True,
        "status": 200,
        "headers": headers or {},
    }
    row = {
        "id": identifier,
        "kind": "kaus_source_http",
        "key": f"100:{identifier}",
        "available_at": received,
        "metadata": canonical(metadata),
        "body_sha256": sha(body),
        "previous_sha256": "0" * 64,
    }
    row["record_sha256"] = sha(
        canonical(
            [row[k] for k in ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")]
        ).encode()
    )
    return row


def twc_body(start, count, week, status="settled"):
    monday = datetime.fromisoformat(week)
    dates = [(monday + timedelta(days=i)).date().isoformat() for i in range(7)]
    observations = []
    for i in range(count):
        observed = start + i * HOUR
        local = datetime.fromtimestamp(observed / 1000, UTC).astimezone(ZoneInfo("America/Chicago"))
        observations.append(
            {
                "icaoId": "KAUS",
                "localDate": local.date().isoformat(),
                "localHour": local.hour,
                "reportTimeUTC": iso(observed),
                "reportTimeLocal": local.isoformat(),
                "tempF": 72.5,
                "tempC": 22.5,
                "status": status,
            }
        )
    data = {
        "weekStart": week,
        "weekEnd": dates[-1],
        "dates": dates,
        "source": "live",
        "fetchedAt": iso(DECISION - 180_000),
        "totalObservations": count,
        "stations": [{"icaoId": "KAUS", "timezone": "America/Chicago", "observations": observations}],
    }
    return json.dumps(data).replace('"tempF": 72.5', '"tempF": 72.500').encode()


def parsed(body, week, received=None, identifier=101):
    received = received or iso(DECISION - 120_000)
    return parse_twc_snapshot(
        body,
        record(body, received, TWC_URL, {"primary": "true", "weekStart": week}, identifier=identifier),
        week_start=week,
        registration_id=100,
        registered_at=REGISTERED,
    )


def context_fixture():
    start, boundary = TARGET - 169 * HOUR, milliseconds("2026-09-07T05:00:00Z")
    old = parsed(twc_body(start, (boundary - start) // HOUR, "2026-08-31"), "2026-08-31")
    new = parsed(
        twc_body(boundary, (TARGET - 2 * HOUR - boundary) // HOUR + 1, "2026-09-07"),
        "2026-09-07",
        identifier=102,
    )
    return old["rows"] + new["rows"], [old["snapshot"], new["snapshot"]]


def nbh_fixture(missing=False, received=None, identifier=110):
    case = binding(TARGET)
    run = datetime.fromtimestamp(case["run_ms"] / 1000, UTC)
    utc = " UTC " + "".join(f"{(run.hour + lead) % 24:3d}" for lead in range(1, 26)) + " "
    values = [f"{70 + i:3d}" for i in range(1, 26)]
    values[3] = "BAD"  # Lead4 is never authorized as an input for this h1 case.
    if missing:
        values[1] = "-99"
    body = (
        f" KAUS NBM V5.0 NBH GUIDANCE {run:%m/%d/%Y} {run:%H}00 UTC\n"
        + utc
        + "\n TMP "
        + "".join(values)
        + "\n\n"
    ).encode()
    headers = {
        "last-modified": format_datetime(run + timedelta(minutes=45), usegmt=True),
        "etag": '"edition"',
        "content-length": str(len(body)),
    }
    row = record(
        body,
        received or iso(DECISION - 600_000),
        NBH_BASE + case["nbh_key"],
        identifier=identifier,
        headers=headers,
    )
    return body, row


def test_original_json_lexeme_backfill_and_same_product_context():
    rows, snapshots = context_fixture()
    assert len(rows) == 168
    assert all(row["temperature_f_lexeme"] == "72.500" for row in rows)
    assert any(row["observation_predates_registration"] for row in rows)
    result = context_asof(rows, snapshots, TARGET)
    assert result["eligible"] and result["finite_context_hours"] == 168
    assert result["context_end_ms"] == DECISION - HOUR
    assert result["forecast_step"] == 2
    assert result["settlement_product_equivalence_verified"] is False


def test_future_received_settlement_and_revisions_cannot_repair_pending_hour():
    rows, snapshots = context_fixture()
    current = rows[-1]
    current.update(status="pending", revision_sha256="a" * 64)

    class NoFutureValue(dict):
        def __getitem__(self, key):
            if key in ("temperature_f_lexeme", "status", "revision_sha256"):
                raise AssertionError("Future revision was inspected")
            return super().__getitem__(key)

    rows.append(NoFutureValue({**current, "received_ms": DECISION + 1, "status": "settled"}))
    result = context_asof(rows, snapshots, TARGET)
    assert result["temperature_f_decimal_strings"][-1] is None
    assert result["finite_context_hours"] == 167 and result["eligible"]
    rows[-3].update(status="pending", revision_sha256="b" * 64)
    result = context_asof(rows, snapshots, TARGET)
    assert "last_finite_hour_older_than_120_minutes" in result["reasons"]


def test_latest_revision_first_receipt_and_same_receipt_conflict():
    rows, snapshots = context_fixture()
    repeat = {**rows[-1], "received_ms": DECISION - 60_000, "source_record_id": 105}
    result = context_asof(rows + [repeat], snapshots, TARGET)
    assert result["provenance"][-1]["first_local_receipt_ms"] == DECISION - 120_000
    assert result["provenance"][-1]["all_asof_source_ids"] == [102, 105]
    conflict = {**repeat, "revision_sha256": "a" * 64, "temperature_f_lexeme": "999"}
    result = context_asof(rows + [repeat, conflict], snapshots, TARGET)
    assert result["temperature_f_decimal_strings"][-1] is None
    assert result["hour_exclusions"] == {"same_receipt_conflict": 1}


def test_missing_context_stale_snapshot_and_late_initial_history_reject():
    rows, snapshots = context_fixture()
    assert not context_asof(rows[:119], snapshots, TARGET)["eligible"]
    assert not context_asof(rows, [{**s, "received_ms": DECISION - 180_001} for s in snapshots], TARGET)[
        "eligible"
    ]
    late = [{**row, "received_ms": DECISION + 1} for row in rows]
    result = context_asof(late, snapshots, TARGET)
    assert result["finite_context_hours"] == 0 and not result["eligible"]


@pytest.mark.parametrize("mutation", ["utc", "local_hour", "station", "source", "week", "value"])
def test_changed_twc_identity_clocks_product_or_numeric_type_reject(mutation):
    body = twc_body(DECISION - HOUR, 1, "2026-09-07")
    data = json.loads(body)
    row = data["stations"][0]["observations"][0]
    if mutation == "utc":
        row["reportTimeUTC"] = iso(DECISION - HOUR + 60_000)
    elif mutation == "local_hour":
        row["localHour"] += 1
    elif mutation == "station":
        row["icaoId"] = "KATT"
    elif mutation == "source":
        data["source"] = "METAR-equivalent"
    elif mutation == "week":
        data["weekEnd"] = "2026-09-14"
    else:
        row["tempF"] = "72.5"
    with pytest.raises(ValueError):
        parsed(json.dumps(data).encode(), "2026-09-07")


def test_missing_kaus_is_retained_and_not_a_fresh_successful_snapshot():
    data = json.loads(twc_body(DECISION - HOUR, 1, "2026-09-07"))
    data["stations"] = []
    data["totalObservations"] = 0
    result = parsed(json.dumps(data).encode(), "2026-09-07")
    assert result["reason"] == "kaus_station_absent" and result["rows"] == []
    assert result["snapshot"]["complete"] is False


def test_exact_nbh_grid_offsets_and_missing_latest_edition():
    body, row = nbh_fixture()
    result = nbh_input(body, row, target_ms=TARGET, registration_id=100, registered_at=REGISTERED)
    assert result["eligible"] and result["future_tmp_f"] == [72, 73, None, None, None, None, None]
    for cell in result["cells"]:
        assert body[cell["cell_offset"] : cell["cell_offset"] + 3].decode() == cell["raw_lexeme"]
    body2, row2 = nbh_fixture(True, iso(DECISION - 60_000), 111)
    missing = nbh_input(body2, row2, target_ms=TARGET, registration_id=100, registered_at=REGISTERED)
    assert not select_nbh_asof([result, missing], TARGET)["eligible"]


def test_late_nbh_receipt_gates_before_temperature_bytes_are_decoded():
    body = b"not a forecast card"
    row = record(body, iso(DECISION + 1), NBH_BASE + binding(TARGET)["nbh_key"])
    result = nbh_input(body, row, target_ms=TARGET, registration_id=100, registered_at=REGISTERED)
    assert result["reason"] == "nbh_received_after_decision"
    assert select_nbh_asof([result], TARGET)["reason"] == "no_original_nbh_received_by_decision"


@pytest.mark.parametrize("change", ["body", "metadata", "cycle", "lastmodified", "pre_registration"])
def test_original_response_lineage_and_nbh_cycle_gates(change):
    body, row = nbh_fixture()
    if change == "body":
        body += b"x"
    elif change == "metadata":
        row["metadata"] = row["metadata"].replace("complete", "incomplete")
    else:
        meta = json.loads(row["metadata"])
        if change == "cycle":
            meta["url"] = meta["url"].replace("t21z", "t20z")
        elif change == "lastmodified":
            meta["headers"]["last-modified"] = "Mon, 07 Sep 2026 23:00:00 GMT"
        else:
            meta["request_started_at"] = "2026-09-07T18:00:00Z"
        row["metadata"] = canonical(meta)
        row["record_sha256"] = sha(
            canonical(
                [
                    row[k]
                    for k in ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")
                ]
            ).encode()
        )
    with pytest.raises(ValueError):
        nbh_input(body, row, target_ms=TARGET, registration_id=100, registered_at=REGISTERED)


def test_receipts_keep_submillisecond_lateness_and_timezone_requirements():
    assert milliseconds("2026-09-07T23:00:00.000001Z") == DECISION + 1
    with pytest.raises(ValueError):
        milliseconds("2026-09-07T23:00:00")


def test_one_merged_schedule_covers_fixed_targets_and_separate_new_window():
    config = json.loads(Path("config/live_hybrid_readiness_design.json").read_bytes())
    requests = planned_requests(config)
    keys = [(row["scheduled_ms"], row["url"], canonical(row["params"])) for row in requests]
    assert len(keys) == len(set(keys))
    assert sum("original_nbh_readiness" in row["purposes"] for row in requests) == 16
    assert sum("decision_readiness" in row["purposes"] for row in requests) == 8
    assert any(len(row["purposes"]) > 1 for row in requests)
    assert max(row["scheduled_ms"] for row in requests) < milliseconds(config["hard_stop_utc"])
    assert min(row["scheduled_ms"] for row in requests) > milliseconds("2026-09-07T19:48:04.271244Z")
    altered = copy.deepcopy(config)
    altered["target_utc"][-1] = "2026-09-10T00:00:00+00:00"
    with pytest.raises(ValueError):
        planned_requests(altered)
