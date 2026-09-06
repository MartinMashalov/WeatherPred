from copy import deepcopy

import pytest

from research.probes.station_history import parse_snapshot, station_clock, weeks


def snapshot(observations, week="2026-08-24"):
    from datetime import date, timedelta

    days = [(date.fromisoformat(week) + timedelta(days=i)).isoformat() for i in range(7)]
    return {
        "weekStart": week,
        "weekEnd": days[-1],
        "dates": days,
        "fetchedAt": "2026-09-06T17:00:00Z",
        "source": "live",
        "totalObservations": len(observations),
        "stations": [
            {
                "icaoId": "KMIA",
                "stationName": "Miami",
                "timezone": "America/New_York",
                "observations": observations,
            }
        ],
    }


def hour(utc="2026-08-24T04:00:00Z", local_day="2026-08-24", local_hour=0, temperature=80, status="settled"):
    return {
        "icaoId": "KMIA",
        "reportTimeUTC": utc,
        "reportTimeLocal": "display label",
        "localDate": local_day,
        "localHour": local_hour,
        "tempF": temperature,
        "tempC": 26.7,
        "status": status,
    }


def parse(data):
    return parse_snapshot(data, "2026-08-24", 123, "2026-09-06T17:01:00Z")


def test_preserves_actual_receipt_and_status_without_inventing_missing_hours():
    rows, report = parse(snapshot([hour(), hour("2026-08-24T06:00:00Z", local_hour=2, status="pending")]))
    assert len(rows) == 2
    assert [r["local_hour"] for r in rows] == [0, 2]
    assert rows[1]["status"] == "pending"
    assert all(r["received_at"] == "2026-09-06T17:01:00.000000+00:00" for r in rows)
    assert all(r["source_record_id"] == 123 and not r["historical_availability_verified"] for r in rows)
    assert report["status_counts"] == {"settled": 1, "pending": 1}


def test_exact_duplicates_collapse_but_conflicting_corrections_are_quarantined():
    original = hour()
    rows, report = parse(snapshot([original, deepcopy(original)]))
    assert len(rows) == 1
    assert rows[0]["source_row_indexes"] == [0, 1]
    assert report["exact_duplicates_collapsed"] == 1
    corrected = {**original, "tempF": 83}
    rows, report = parse(snapshot([original, corrected]))
    assert rows == []
    assert len(report["conflicting_station_hours"]) == 1
    assert {v["temperature_f"] for v in report["conflicting_station_hours"][0]["variants"]} == {80, 83}


@pytest.mark.parametrize(
    ("changed", "reason"),
    [
        ({"localHour": 1}, "local calendar differs"),
        ({"reportTimeUTC": "2026-08-24T04:00:00"}, "Naive timestamp"),
        ({"tempF": None}, "missing or nonfinite"),
        ({"status": "revised"}, "Unknown source status"),
        ({"icaoId": "KIAH"}, "different station"),
    ],
)
def test_ambiguous_time_identity_temperature_or_status_never_becomes_a_model_row(changed, reason):
    rows, report = parse(snapshot([{**hour(), **changed}]))
    assert rows == []
    assert reason in report["rejected_rows"][0]["reason"]


def test_utc_cutoff_excludes_late_local_august_30_hours():
    rows, report = parse(
        snapshot(
            [
                hour("2026-08-30T23:00:00Z", "2026-08-30", 19),
                hour("2026-08-31T00:00:00Z", "2026-08-30", 20),
            ]
        )
    )
    assert len(rows) == 1
    assert rows[0]["observed_at"] == "2026-08-30T23:00:00.000000+00:00"
    assert "August 30" in report["rejected_rows"][0]["reason"]


def test_station_clock_keeps_both_occurrences_of_a_repeated_dst_hour_distinct():
    first = station_clock("2026-11-01T05:00:00Z", "America/New_York")
    second = station_clock("2026-11-01T06:00:00Z", "America/New_York")
    assert first.hour == second.hour == 1
    assert (first.fold, second.fold) == (0, 1)
    assert first.timestamp() != second.timestamp()
    # The generic clock conversion is tested synthetically; November data is
    # never acquired or permitted by the model-row calendar parser.


def test_empty_source_is_not_synthesized_and_wrong_week_or_count_fails_closed():
    rows, report = parse(snapshot([]))
    assert rows == [] and report["raw_observations"] == 0
    assert report["stations"][0]["raw_observations"] == 0
    with pytest.raises(ValueError, match="calendar"):
        parse({**snapshot([]), "weekStart": "2026-08-17"})
    with pytest.raises(ValueError, match="count"):
        parse({**snapshot([hour()]), "totalObservations": 0})
    assert len(weeks()) == 17
    assert weeks()[0] == "2026-05-04" and weeks()[-1] == "2026-08-24"
