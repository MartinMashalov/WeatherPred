"""Pure timestamp/receipt/manifest guards; no neural dependency or model execution."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from research.probes.station_transformer import (
    FIT_CUTOFF,
    FIXED_CONFIG,
    HOUR,
    index_observations,
    prepare_case,
    prepare_manifest,
    receipt_timestamp_ms,
    timestamp_ms,
    validate_training_series,
    verify_artifacts,
)


def fixture_rows():
    target = timestamp_ms("2026-07-08T00:00:00Z")
    decision = target - 6 * HOUR
    end = decision - HOUR
    rows = []
    for i in range(168):
        observed = end - (167 - i) * HOUR
        dt = datetime.fromtimestamp(observed / 1000, UTC)
        rows.append(
            {
                "station_id": "KNYC",
                "station_timezone": "UTC",
                "observed_at": dt.isoformat(),
                "local_date": dt.date().isoformat(),
                "local_hour": dt.hour,
                "local_fold": 0,
                "temperature_f": 30 + i / 2,
                "status": "settled",
                "source_record_id": i + 1,
                "source_row_index": i,
                "received_at": "2026-09-06T18:00:00Z",
                "historical_availability_verified": False,
            }
        )
    case = {
        "case_id": "one",
        "station_id": "KNYC",
        "decision_ms": decision,
        "target_ms": target,
        "horizon_hours": 6,
    }
    return rows, case


def test_microsecond_receipts_round_later_without_relaxing_observation_grid():
    text = "2026-09-06T17:43:38.154154+00:00"
    exact = timestamp_ms("2026-09-06T17:43:38.155000+00:00")
    assert receipt_timestamp_ms(text) == exact
    assert receipt_timestamp_ms("2026-09-06T17:43:38.154000+00:00") == exact - 1
    with pytest.raises(ValueError, match="Sub-millisecond"):
        timestamp_ms(text)
    rows, case = fixture_rows()
    rows[-1]["received_at"] = text
    indexed, _ = index_observations(rows)
    prepared = prepare_case(case, indexed, FIXED_CONFIG)
    assert prepared["history_source_provenance"][-1]["actual_received_ms"] == [exact]
    assert prepared["history_source_provenance"][-1]["actual_received_at_original"] == [text]


def test_target_step_and_baselines_share_lagged_context_without_future_values():
    rows, case = fixture_rows()
    indexed, audit = index_observations(rows)
    result = prepare_case(case, indexed, FIXED_CONFIG)
    assert result["eligible"]
    assert result["forecast_steps_from_grid_end"] == 7
    assert result["last_input_ms"] == case["decision_ms"] - HOUR
    assert result["persistence_f"] == rows[-1]["temperature_f"]
    seasonal = next(
        row["temperature_f"]
        for row in rows
        if timestamp_ms(row["observed_at"]) == case["target_ms"] - 24 * HOUR
    )
    assert result["seasonal_24h_f"] == seasonal
    assert result["historical_availability_verified"] is False
    assert audit["conflicting_hours"] == []
    assert all(
        point["assumed_eligible_ms"] <= case["decision_ms"] for point in result["history_source_provenance"]
    )
    assert all(
        point["actual_received_ms"][0] > case["decision_ms"] for point in result["history_source_provenance"]
    )
    future = deepcopy(rows[-1])
    future.update(
        observed_at="2026-07-08T00:00:00Z",
        local_date="2026-07-08",
        local_hour=0,
        temperature_f=9999,
        source_record_id=9999,
    )
    altered, _ = index_observations([*rows, future])
    assert prepare_case(case, altered, FIXED_CONFIG)["input_sha256"] == result["input_sha256"]


def test_missing_recent_hours_are_masked_or_rejected_without_interpolation():
    rows, case = fixture_rows()
    indexed, _ = index_observations(rows[:-1])
    result = prepare_case(case, indexed, FIXED_CONFIG)
    assert result["eligible"] and result["history_missing_mask"][-1]
    assert result["history_values_f"][-1] is None
    assert result["forecast_steps_from_grid_end"] == 7
    assert result["last_input_ms"] == case["decision_ms"] - 2 * HOUR
    older, _ = index_observations(rows[:-2])
    assert prepare_case(case, older, FIXED_CONFIG)["exclusion_reason"] == "last_input_too_old"
    insufficient, _ = index_observations(rows[-119:])
    assert prepare_case(case, insufficient, FIXED_CONFIG)["exclusion_reason"] == "insufficient_finite_history"
    rows[-1]["status"] = "pending"
    pending, _ = index_observations(rows)
    assert prepare_case(case, pending, FIXED_CONFIG)["history_values_f"][-1] is None


def test_conflicting_versions_and_time_inconsistency_cannot_enter_context():
    rows, case = fixture_rows()
    correction = {**rows[-1], "temperature_f": -100, "source_record_id": 999}
    indexed, audit = index_observations([*rows, correction])
    assert len(audit["conflicting_hours"]) == 1
    assert prepare_case(case, indexed, FIXED_CONFIG)["history_values_f"][-1] is None
    invalid_local = {**rows[0], "local_hour": (rows[0]["local_hour"] + 1) % 24}
    with pytest.raises(ValueError, match="Local station time"):
        index_observations([invalid_local])
    with pytest.raises(ValueError, match="receipt precedes"):
        index_observations([{**rows[0], "received_at": "2026-05-01T00:00:00Z"}])
    with pytest.raises(ValueError, match="Naive timestamps"):
        timestamp_ms("2026-07-01T00:00:00")


def test_manifest_registration_and_fit_availability_guards():
    rows, case = fixture_rows()
    indexed, _ = index_observations(rows)
    duplicate = {
        **case,
        "case_id": "two",
        "decision_ms": datetime.fromtimestamp(case["decision_ms"] / 1000, UTC).isoformat(),
    }
    with pytest.raises(ValueError, match="Duplicate"):
        prepare_manifest({"schema_version": 1, "cases": [case, duplicate]}, indexed, FIXED_CONFIG)
    wrong_horizon = {**case, "horizon_hours": 3}
    with pytest.raises(ValueError, match="horizon"):
        prepare_case(wrong_horizon, indexed, FIXED_CONFIG)
    early_target = FIT_CUTOFF
    early = {**case, "target_ms": early_target, "decision_ms": early_target - 6 * HOUR}
    assert (
        prepare_case(early, indexed, FIXED_CONFIG)["exclusion_reason"] == "decision_precedes_model_fit_cutoff"
    )
    with pytest.raises(ValueError, match="registered hashes"):
        verify_artifacts({}, b"protocol", b"manifest", b"observations", b"adapter")


def test_supervised_arrays_cannot_contain_post_cutoff_labels_or_compress_gaps():
    start = timestamp_ms("2026-06-01T00:00:00Z")
    item = {
        "station_id": "KNYC",
        "timestamps_ms": [start + i * HOUR for i in range(48)],
        "values_f": [70.0] * 48,
        "source_record_ids": list(range(1, 49)),
    }
    assert len(validate_training_series([item])) == 64
    future = deepcopy(item)
    future["timestamps_ms"][-1] = FIT_CUTOFF
    with pytest.raises(ValueError, match="cutoff"):
        validate_training_series([future])
    missing = deepcopy(item)
    missing["timestamps_ms"].pop(20)
    missing["values_f"].pop(20)
    missing["source_record_ids"].pop(20)
    with pytest.raises(ValueError, match="regular hourly grid"):
        validate_training_series([missing])
    absent = deepcopy(item)
    absent["values_f"][20] = None
    absent["source_record_ids"][20] = None
    assert len(validate_training_series([absent])) == 64
