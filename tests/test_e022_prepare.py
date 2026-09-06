"""Real preparation helpers on synthetic temperatures; no neural execution."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from research.experiments.e022_prepare import calendar_cases, inspect_case, prepare, validate_protocol
from research.probes.station_transformer import (
    FIT_CUTOFF,
    HOUR,
    TRAIN_START,
    index_observations,
    prepare_manifest,
    timestamp_ms,
    validate_training_series,
)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config/e022_station_forecasts.json"


@pytest.fixture
def protocol():
    return json.loads(CONFIG_PATH.read_text())


def synthetic_rows():
    start, end = timestamp_ms("2026-06-01T00:00:00Z"), timestamp_ms("2026-08-17T00:00:00Z")
    missing = timestamp_ms("2026-06-10T15:00:00Z")
    result = []
    for hour in range(start, end, HOUR):
        if hour == missing:
            continue
        dt = datetime.fromtimestamp(hour / 1000, UTC)
        result.append(
            {
                "station_id": "KAAA",
                "station_timezone": "UTC",
                "observed_at": dt.isoformat(),
                "local_date": dt.date().isoformat(),
                "local_hour": dt.hour,
                "temperature_f": 60.0 + dt.hour / 10,
                "status": "settled",
                "source_record_id": 123,
                "received_at": "2026-09-06T17:43:38.154154+00:00",
                "historical_availability_verified": False,
            }
        )
    return result


def test_protocol_and_full_station_calendar_are_fixed_before_errors_exist(protocol):
    assert validate_protocol(protocol)
    cases = calendar_cases({"KAAA": "UTC", "EMPTY": "UTC"}, protocol)
    assert len(cases) == 2 * 98 * 4 * 3
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert {case["station_id"] for case in cases} == {"KAAA", "EMPTY"}
    assert min(case["target_ms"] for case in cases) == TRAIN_START
    assert max(case["target_ms"] for case in cases) == timestamp_ms("2026-08-16T18:00:00Z")
    altered = deepcopy(protocol)
    altered["fixed_fit"]["num_steps"] = 201
    with pytest.raises(ValueError, match="FIXED_FIT"):
        validate_protocol(altered)
    altered = deepcopy(protocol)
    altered["calibration_fit_available"] = "2026-07-19T00:00:00Z"
    with pytest.raises(ValueError, match="cutoffs disagree"):
        validate_protocol(altered)


def test_midnight_boundary_decisions_abstain_before_any_observation_or_label_lookup(protocol):
    class ForbiddenLookup(dict):
        def get(self, *args, **kwargs):
            raise AssertionError("A boundary case consulted observations before its time gate")

    cases = calendar_cases({"KAAA": "UTC"}, protocol)
    for target, split, reason in (
        ("2026-07-06T00:00:00Z", "calibration", "decision_precedes_model_fit_cutoff"),
        ("2026-07-20T00:00:00Z", "development", "decision_precedes_calibration_fit_cutoff"),
    ):
        matches = [case for case in cases if case["target_ms"] == timestamp_ms(target)]
        assert len(matches) == 3
        for case in matches:
            assert case["split"] == split
            inspected = inspect_case(case, ForbiddenLookup(), protocol)
            assert inspected["eligible"] is False
            assert inspected["exclusion_reasons"] == [reason]
            assert "target_source_record_ids" not in inspected


def test_preparation_retains_null_hours_empty_stations_and_training_labels_separately(protocol):
    registry = {"stations": [{"icao": "KAAA", "timezone": "UTC"}, {"icao": "EMPTY", "timezone": "UTC"}]}
    rows = synthetic_rows()
    # After the registered end even missing station/value fields must be ignored.
    rows.append({"observed_at": "2026-08-17T00:00:00Z"})
    manifest, training, coverage = prepare(protocol, registry, rows)
    assert len(manifest["calendar_cases"]) == 2 * 98 * 4 * 3
    assert coverage["station_directory_count"] == 2
    assert coverage["training_supported_stations"] == coverage["training_excluded_stations"] == 1
    empty = [case for case in manifest["calendar_cases"] if case["station_id"] == "EMPTY"]
    assert len(empty) == 98 * 4 * 3
    assert all(not case["eligible"] for case in empty)
    assert all("insufficient_station_training_support" in case["exclusion_reasons"] for case in empty)
    series = training["series"][0]
    assert series["timestamps_ms"] == list(range(TRAIN_START, FIT_CUTOFF, HOUR))
    assert len(series["timestamps_ms"]) == 56 * 24
    assert all(value is None for value in series["values_f"][: 21 * 24])
    gap = (timestamp_ms("2026-06-10T15:00:00Z") - TRAIN_START) // HOUR
    assert series["values_f"][gap] is None and series["missing_mask"][gap]
    assert series["source_record_ids"][gap] is None
    assert series["actual_received_at_original"][21 * 24] == ["2026-09-06T17:43:38.154154+00:00"]
    assert validate_training_series(training["series"]) == training["training_series_sha256"]
    assert all(value is None for value in training["excluded_series"][0]["values_f"])
    assert training["ridge_cases"]
    assert all(case["target_ms"] < FIT_CUTOFF for case in training["ridge_cases"])
    assert all(case["target_assumed_available_ms"] <= FIT_CUTOFF for case in training["ridge_cases"])
    assert all("observed_f" in case and "features" in case for case in training["ridge_cases"])
    assert "observed_f" not in json.dumps(manifest)
    assert coverage["model_fits"] == coverage["model_inferences"] == coverage["scores_computed"] == 0
    assert coverage["data_audit"]["excluded_rows"] == {"outside_registered_data_window": 1}
    observations, _ = index_observations(rows)
    prepared = prepare_manifest(manifest, observations, protocol)
    assert len(prepared) == len(manifest["cases"]) > 0
    assert all(case["eligible"] for case in prepared)
    assert all(case["decision_ms"] >= FIT_CUTOFF for case in prepared)
    assert all(case["forecast_steps_from_grid_end"] == case["horizon_hours"] + 1 for case in prepared)
    assert all(case["context_end_ms"] < case["decision_ms"] for case in prepared)
    assert {case["split"] for case in manifest["cases"]} == {"calibration", "development"}
    assert all(value["eligible"] for value in manifest["calibration_support"].values())
    # Training support uses whole UTC target days, not a count of hourly values.
    assert manifest["training_support"]["KAAA"]["target_days"] == 30
    assert coverage["splits"]["calibration"]["eligible_cases"] == 165
    assert coverage["splits"]["development"]["eligible_cases"] == 333
    assert coverage["adapter_prepared_cases_verified"] == 498
