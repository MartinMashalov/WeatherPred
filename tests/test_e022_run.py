"""Chronology, day-weighted metrics and actual bounded child processes."""

import json
import sys
import time
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from research.experiments import e022_run as runner
from research.probes.station_transformer import HOUR, canonical, digest, timestamp_ms
from weatherpred.archive import Archive
from weatherpred.station_forecasts import describe_case

ROOT = Path(__file__).resolve().parents[1]
LEVELS = [0.05, 0.1, 0.5, 0.9, 0.95]


@pytest.fixture
def protocol():
    return json.loads((ROOT / "config/e022_station_forecasts.json").read_bytes())


def case(target, horizon, split):
    return {
        "case_id": f"KAAA:{target}:{horizon}",
        "station_id": "KAAA",
        "station_timezone": "UTC",
        "target_ms": target,
        "decision_ms": target - horizon * HOUR,
        "horizon_hours": horizon,
        "split": split,
        "eligible": True,
    }


def score_fixture():
    cases, observations = [], {}
    start = timestamp_ms("2026-07-06T00:00:00Z")
    for day in range(10):
        for hour in (6, 12, 18):
            target = start + (day * 24 + hour) * HOUR
            observations[("KAAA", target)] = {"temperature_f": 12.0, "status": "settled"}
            cases.extend(case(target, h, "calibration") for h in (1, 3, 6))
    start = timestamp_ms("2026-07-20T00:00:00Z")
    for day, hours, observed in ((0, [12], 12.0), (1, [6, 12, 18], 16.0)):
        for hour in hours:
            target = start + (day * 24 + hour) * HOUR
            observations[("KAAA", target)] = {"temperature_f": observed, "status": "settled"}
            cases.extend(case(target, h, "development") for h in (1, 3, 6))
    predictions = [
        {"case_id": c["case_id"], "point_f": 10.0, "quantiles_f": [10.0] * len(LEVELS)} for c in cases
    ]
    return {"schema_version": 1, "cases": cases}, observations, predictions


def test_calibration_uses_only_prior_labels_and_metrics_weight_days_equally(protocol):
    manifest, observations, predictions = score_fixture()
    original_predictions = deepcopy(predictions)
    result, scores = runner.score_predictions(predictions, manifest, observations, protocol, LEVELS)
    assert result["development_cases"] == 12 and result["development_utc_days"] == 2
    assert result["metrics"]["mae_f"] == 4.0  # Case-weighted mean would incorrectly be 5.
    assert result["metrics"]["rmse_f"] == pytest.approx(np.sqrt(20))
    assert result["metrics"]["pinball_loss_f"] == 1.0
    assert result["metrics"]["coverage_80"] == result["metrics"]["coverage_90"] == 0.5
    assert result["metrics"]["width_80_f"] == result["metrics"]["width_90_f"] == 0.0
    assert result["calibration_offsets_f"] == {str(h): [2.0] * len(LEVELS) for h in (1, 3, 6)}
    future = deepcopy(observations)
    for c in manifest["cases"]:
        if c["split"] == "development":
            future[(c["station_id"], c["target_ms"])]["temperature_f"] = -9999.0
    changed, _ = runner.score_predictions(predictions, manifest, future, protocol, LEVELS)
    assert changed["calibration_offsets_f"] == result["calibration_offsets_f"]
    assert predictions == original_predictions
    comparison = runner.paired_differences(scores, scores)
    assert comparison["candidate_minus_persistence_mae_f"] == 0
    assert comparison["case_pairs"] == 12 and comparison["utc_day_groups"] == 2


def test_missing_candidates_nonfinite_predictions_and_boundary_leakage_fail_closed(protocol):
    manifest, observations, predictions = score_fixture()
    with pytest.raises(ValueError, match="entire fixed common panel"):
        runner.score_predictions(predictions[:-1], manifest, observations, protocol, LEVELS)
    bad = deepcopy(predictions)
    bad[0]["point_f"] = float("nan")
    with pytest.raises(ValueError, match="Nonfinite"):
        runner.score_predictions(bad, manifest, observations, protocol, LEVELS)
    for date, split in (("2026-07-06T00:00:00Z", "calibration"), ("2026-07-20T00:00:00Z", "development")):
        with pytest.raises(ValueError, match="boundary"):
            runner.validate_cases({"cases": [case(timestamp_ms(date), 1, split)]}, protocol)
    with pytest.raises(ValueError, match="identical case IDs"):
        runner.paired_differences([{"case_id": "one", "day": "a", "absolute_error_f": 1}], [])


def test_all_six_baselines_fit_only_earlier_labels_and_ignore_later_values(protocol):
    start, end = timestamp_ms("2026-06-01T00:00:00Z"), timestamp_ms("2026-07-22T00:00:00Z")
    history = {t: 60.0 + 0.01 * ((t - start) // HOUR) for t in range(start, end, HOUR)}
    observations = {
        ("KAAA", t): {"temperature_f": value, "status": "settled"} for t, value in history.items()
    }
    ridge = []
    for day in range(10):
        target = timestamp_ms("2026-06-10T12:00:00Z") + day * 24 * HOUR
        for horizon in (1, 3, 6):
            item = case(target, horizon, "training")
            ridge.append(
                {
                    **item,
                    **describe_case(history, item, "UTC"),
                    "observed_f": history[target] + 0.7,
                    "target_assumed_available_ms": target + 15 * 60_000,
                }
            )
    example = case(timestamp_ms("2026-07-20T12:00:00Z"), 1, "development")
    changed = deepcopy(observations)
    for (_, when), point in changed.items():
        if when >= example["decision_ms"]:
            point["temperature_f"] = -9999
    for candidate in protocol["candidates"][:6]:
        original, models = runner.baseline_predictions(
            candidate, [example], ridge, observations, protocol, LEVELS
        )
        altered, _ = runner.baseline_predictions(candidate, [example], ridge, changed, protocol, LEVELS)
        assert original == altered
        assert np.isfinite(original[0]["point_f"])
        if "penalty" in candidate:
            assert set(models) == {"1", "3", "6"}
            assert all(
                m["last_training_target_ms"] < timestamp_ms(protocol["fit_cutoff"]) for m in models.values()
            )
    unavailable = deepcopy(ridge)
    unavailable[0]["target_assumed_available_ms"] = timestamp_ms(protocol["fit_cutoff"]) + 1
    with pytest.raises(ValueError, match="unavailable"):
        runner.baseline_predictions(
            protocol["candidates"][3], [example], unavailable, observations, protocol, LEVELS
        )


def test_supervised_child_success_timeout_and_incomplete_attempt_never_retry(tmp_path):
    success = tmp_path / "success"
    command = [
        sys.executable,
        "-c",
        "import sys; print('child evidence', flush=True); print('stderr evidence', file=sys.stderr, flush=True)",
    ]
    first = runner.supervised_once(command, success, 2)
    assert first["status"] == "completed"
    assert (success / "stdout.log").read_text() == "child evidence\n"
    assert (success / "stderr.log").read_text() == "stderr evidence\n"
    assert (
        runner.supervised_once([sys.executable, "-c", "raise RuntimeError('must not execute')"], success, 2)
        == first
    )
    timeout = tmp_path / "timeout"
    marker = tmp_path / "escaped_descendant.txt"
    script = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',\"import time,pathlib; time.sleep(0.5); pathlib.Path({str(marker)!r}).write_text('escaped')\"]); "
        "print('started parent and child',flush=True); time.sleep(10)"
    )
    result = runner.supervised_once([sys.executable, "-c", script], timeout, 0.15)
    assert result["status"] == "timeout" and result["reason"] == "hard_wall_deadline"
    assert "started parent and child" in (timeout / "stdout.log").read_text()
    assert runner.supervised_once(command, timeout, 2) == result
    time.sleep(0.6)
    assert not marker.exists(), "A descendant survived the supervised timeout"
    interrupted = tmp_path / "interrupted"
    runner.write_new(interrupted / "attempt_started.json", {"started": True})
    result = runner.supervised_once(command, interrupted, 2)
    assert result["reason"] == "previous_attempt_incomplete_no_retry"
    assert not (interrupted / "stdout.log").exists()


def test_registration_pins_bytes_and_rejects_source_changes_before_execution(protocol, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Use a genuine temporary Archive and tiny hashed input/checkpoint artifacts.
    # Only filesystem locations/data are fixtures; no registration function is mocked.
    for relative in ("research/probes/station_transformer.py", "research/experiments/e022_run.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic source bytes\n")
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    (checkpoint / "config.json").write_bytes(canonical({"chronos_config": {"quantiles": LEVELS}}))
    (checkpoint / "model.safetensors").write_bytes(b"synthetic checkpoint, never loaded")
    protocol["station_transformer"]["checkpoint_directory"] = str(checkpoint)
    protocol["station_transformer"]["checkpoint_files"] = {
        name: runner.sha(checkpoint / name) for name in ("config.json", "model.safetensors")
    }
    for kind in ("observations", "registry"):
        path = Path(protocol[kind + "_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
        protocol[kind + "_sha256"] = runner.sha(path)
    config_path, manifest_path, training_path = (
        tmp_path / "config.json",
        tmp_path / "manifest.json",
        tmp_path / "training.json",
    )
    start = timestamp_ms("2026-06-01T00:00:00Z")
    series = [
        {
            "station_id": "KAAA",
            "timestamps_ms": [start + i * HOUR for i in range(48)],
            "values_f": [70.0] * 48,
            "source_record_ids": [1] * 48,
        }
    ]
    config_path.write_bytes(canonical(protocol))
    manifest_path.write_bytes(canonical({"protocol_canonical_sha256": digest(protocol), "cases": []}))
    training_path.write_bytes(canonical({"series": series, "training_series_sha256": digest(series)}))
    monkeypatch.setattr(runner, "CONFIG", config_path)
    monkeypatch.setattr(runner, "MANIFEST", manifest_path)
    monkeypatch.setattr(runner, "TRAINING", training_path)
    monkeypatch.setattr(
        runner,
        "SOURCES",
        [
            config_path,
            manifest_path,
            training_path,
            Path(protocol["observations_path"]),
            Path(protocol["registry_path"]),
            Path("research/probes/station_transformer.py"),
            Path("research/experiments/e022_run.py"),
        ],
    )
    archive = Archive(tmp_path / "archive")
    try:
        identifier = runner.register(archive)
        registered, evidence = runner.verify_registration(archive, identifier)
        assert evidence["record_id"] == identifier
        assert registered["registered_before_model_execution"] is True
        assert registered["execution"]["chronos_fit_attempts"] == 1
        assert (
            archive.db.execute("SELECT COUNT(*) FROM records WHERE kind='e022_candidate_result'").fetchone()[
                0
            ]
            == 0
        )
        Path("research/experiments/e022_run.py").write_text("changed source bytes\n")
        with pytest.raises(ValueError, match="source or artifact changed"):
            runner.verify_registration(archive, identifier)
    finally:
        archive.close()
