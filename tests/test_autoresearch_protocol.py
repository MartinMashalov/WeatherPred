import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

import pytest

from research.probes.autoresearch_protocol import (
    TrialLedger,
    check_information_boundaries,
    source_hashes,
    validate_plan,
)
from research.probes.autoresearch_run import run_trial

PROCESS_CANDIDATE = """
import json, os, pathlib, signal, subprocess, sys, time
root = pathlib.Path(os.environ["WEATHERPRED_TRIAL_DIR"])
config = json.loads(os.environ["WEATHERPRED_CANDIDATE_JSON"])
mode = config["parameters"].get("mode", "success")
print("candidate arguments:", repr(sys.argv[1:]), flush=True)
print("candidate stderr retained", file=sys.stderr, flush=True)
if mode == "crash":
    raise SystemExit(3)
if mode == "timeout":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child_code = "import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); p=pathlib.Path(%r); " % str(root / "heartbeat")
    child_code += "\\nwhile True:\\n p.write_text(str(time.time())); time.sleep(.02)"
    child = subprocess.Popen([sys.executable, "-c", child_code])
    (root / "grandchild.pid").write_text(str(child.pid))
    while True:
        time.sleep(.02)
if mode == "slow_candidate":
    time.sleep(.35)
(root / "predictions.json").write_text(json.dumps({"score": 1 if config["id"] == "baseline" else .8}))
(root / "result.json").write_text('{"metrics":{"loss": -999}}')
"""


PROCESS_EVALUATOR = """
import json, os, pathlib, sys, time
root = pathlib.Path(os.environ["WEATHERPRED_TRIAL_DIR"])
config = json.loads(os.environ["WEATHERPRED_CANDIDATE_JSON"])
mode = config["parameters"].get("mode", "success")
print("fixed evaluator ran", flush=True)
print("evaluator stderr retained", file=sys.stderr, flush=True)
if mode == "evaluator_crash":
    raise SystemExit(4)
if mode == "slow_candidate":
    time.sleep(.5)
if mode == "missing_result":
    raise SystemExit(0)
manifest = pathlib.Path(os.environ["WEATHERPRED_INPUT_MANIFEST"])
report = json.loads(manifest.read_text())
import hashlib
report["input_manifest_sha256"] = hashlib.sha256(manifest.read_bytes()).hexdigest()
report["metrics"]["loss"] = json.loads((root / "predictions.json").read_text())["score"]
(root / "result.json").write_text(json.dumps(report))
"""


@pytest.fixture
def setup(tmp_path):
    evaluator = tmp_path / "fixed_evaluator.py"
    evaluator.write_text("# Fixed evaluator fixture\n")
    candidate = tmp_path / "candidate.py"
    candidate.write_text("# Frozen candidate fixture\n")
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"training": "fixed synthetic data"}\n')
    plan = {
        "phase": "development",
        "training_start": "2024-01-01T00:00:00Z",
        "fit_cutoff": "2024-01-03T00:00:00Z",
        "evaluation_start": "2024-01-03T00:00:00Z",
        "evaluation_end": "2024-01-05T00:00:00Z",
        "sealed_periods": [{"start": "2024-01-05T00:00:00Z", "end": "2024-02-01T00:00:00Z"}],
        "max_trials": 3,
        "max_trial_seconds": 10,
        "max_total_seconds": 30,
        "minimum_evaluation_days": 2,
        "prior_comparisons": 1836,
        "input_manifest_path": str(manifest),
        "input_manifest_sha256": source_hashes([manifest])[str(manifest)],
        "evaluator_sources": source_hashes([evaluator]),
        "baseline_id": "baseline",
        "metric": {"name": "loss", "direction": "min", "minimum_improvement": 0.01},
        "candidates": [
            {"id": name, "parameters": {"ridge": ridge}, "seed": 42, "sources": source_hashes([candidate])}
            for name, ridge in [("baseline", 1), ("conditional", 10), ("failed", 100)]
        ],
    }

    def row(day):
        return {
            "event_id": f"station_A_{day}",
            "feature_observed_at": f"2024-01-{day:02}T10:59:00Z",
            "feature_received_at": f"2024-01-{day:02}T11:00:00Z",
            "decision_at": f"2024-01-{day:02}T11:01:00Z",
            "target_at": f"2024-01-{day:02}T12:00:00Z",
            "label_received_at": f"2024-01-{day:02}T12:10:00Z",
        }

    report = {
        "input_manifest_sha256": plan["input_manifest_sha256"],
        "training_rows": [row(1), row(2)],
        "evaluation_rows": [row(3), row(4)],
        "metrics": {"loss": 1.0},
    }
    return plan, report, TrialLedger(tmp_path / "trials.jsonl"), tmp_path / "report.json"


def score(ledger, candidate, report, path):
    ledger.start(candidate)
    path.write_text(json.dumps(report))
    return ledger.finish(candidate, report_path=path)["payload"]


def test_trial_registry_resumes_without_erasing_failure_or_creating_profit_claim(setup):
    plan, report, ledger, path = setup
    ledger.register(plan)
    with pytest.raises(ValueError, match="baseline"):
        ledger.start("conditional")
    assert score(ledger, "baseline", report, path)["status"] == "scored"
    report["metrics"]["loss"] = 0.8
    assert score(ledger, "conditional", report, path)["status"] == "scored"
    ledger.start("failed")
    failure = ledger.finish("failed", failure="Candidate process exited with code 1")["payload"]
    assert failure["status"] == "failed"
    resumed = TrialLedger(ledger.path)
    status = resumed.status()
    rank = resumed.ranking()
    assert status["attempted_trials"] == 3
    assert status["planned_comparisons"] == 3
    assert status["prior_comparisons"] == 1836
    assert rank["failed_trials"] == 1
    assert rank["best_research_candidate"] == "conditional"
    assert rank["keep_for_research"] is True
    assert rank["promotion_eligible"] is False
    with pytest.raises(ValueError, match="already attempted"):
        resumed.start("failed")
    with pytest.raises(ValueError, match="overwritten"):
        resumed.register(plan)


@pytest.mark.parametrize("source", ["evaluator", "candidate", "manifest"])
def test_all_frozen_sources_and_input_manifest_are_checked_before_a_trial(setup, source):
    plan, report, ledger, path = setup
    ledger.register(plan)
    assert score(ledger, "baseline", report, path)["status"] == "scored"
    paths = {
        "evaluator": next(iter(plan["evaluator_sources"])),
        "candidate": next(iter(plan["candidates"][0]["sources"])),
        "manifest": plan["input_manifest_path"],
    }
    Path(paths[source]).write_text("# Changed input invalidates the entire registered batch\n")
    with pytest.raises(ValueError, match="frozen"):
        TrialLedger(ledger.path).start("conditional")
    assert ledger.status()["attempted_trials"] == 1


def test_interruption_reserves_budget_and_cannot_be_silently_restarted(setup):
    plan, _, ledger, _ = setup
    ledger.register(plan)
    ledger.start("baseline")
    resumed = TrialLedger(ledger.path)
    assert resumed.status()["reserved_seconds"] == 10
    assert resumed.status()["running"] == ["baseline"]
    with pytest.raises(ValueError, match="still running"):
        resumed.start("conditional")
    resumed.finish("baseline", failure="OS confirmed the original PID exited")
    assert resumed.status()["attempted_trials"] == 1
    with pytest.raises(ValueError, match="baseline"):
        resumed.start("conditional")


def test_timeouts_and_exhausted_budget_are_retained(setup):
    plan, report, ledger, path = setup
    plan["max_trial_seconds"] = 1e-9
    ledger.register(plan)
    result = score(ledger, "baseline", report, path)
    assert result["status"] == "invalid"
    assert "wall-clock" in result["failure"]
    assert ledger.status()["attempted_trials"] == 1

    other = TrialLedger(ledger.path.with_name("small_budget.jsonl"))
    plan["max_trial_seconds"] = 10
    plan["max_total_seconds"] = 10
    other.register(plan)
    assert score(other, "baseline", report, path)["status"] == "scored"
    with pytest.raises(ValueError, match="resource budget"):
        other.start("conditional")


def test_receipt_leakage_future_labels_and_holdout_access_are_rejected(setup):
    plan, report, _, _ = setup
    assert check_information_boundaries(plan, report) == 2
    for part, field, value, message in [
        ("training_rows", "label_received_at", "2024-01-03T00:00:00Z", "unavailable"),
        ("evaluation_rows", "feature_received_at", "2024-01-03T11:02:00Z", "chronology"),
        ("evaluation_rows", "feature_observed_at", "2024-01-03T11:02:00Z", "chronology"),
        ("evaluation_rows", "target_at", "2024-01-05T12:00:00Z", "chronology"),
        ("evaluation_rows", "label_received_at", "2099-01-03T12:00:00Z", "chronology"),
    ]:
        changed = deepcopy(report)
        changed[part][0][field] = value
        with pytest.raises(ValueError, match=message):
            check_information_boundaries(plan, changed)
    changed = deepcopy(plan)
    changed["evaluation_end"] = "2024-01-06T00:00:00Z"
    with pytest.raises(ValueError, match="sealed"):
        validate_plan(changed, "2024-02-01T00:00:00Z")


def test_same_day_contracts_do_not_create_independent_days_and_bad_metric_cannot_win(setup):
    plan, report, ledger, path = setup
    ledger.register(plan)
    report["evaluation_rows"] = [
        {**report["evaluation_rows"][0], "event_id": f"same_day_contract_{i}"} for i in range(100)
    ]
    result = score(ledger, "baseline", report, path)
    assert result["status"] == "invalid"
    assert "distinct evaluation days" in result["failure"]
    assert ledger.ranking()["best_research_candidate"] is None


def test_candidate_renaming_chain_tampering_and_nonfinite_results_are_detected(setup):
    plan, report, ledger, path = setup
    duplicate = deepcopy(plan)
    duplicate["candidates"][1] = {**duplicate["candidates"][0], "id": "renamed"}
    with pytest.raises(ValueError, match="Renaming"):
        ledger.register(duplicate)
    ledger.register(plan)
    report["metrics"]["loss"] = float("nan")
    result = score(ledger, "baseline", report, path)
    assert result["status"] == "invalid"
    assert "finite" in result["failure"]
    text = ledger.path.read_text().replace('"prior_comparisons":1836', '"prior_comparisons":0')
    ledger.path.write_text(text)
    with pytest.raises(ValueError, match="hash chain"):
        ledger.status()


def test_prospective_predictions_require_registration_then_commit_before_decision(setup):
    plan, report, _, _ = setup
    plan["phase"] = "prospective"
    registration = "2024-01-02T23:00:00Z"
    plan["fit_cutoff"] = "2024-01-02T22:00:00Z"
    validate_plan(plan, registration)
    for row in report["evaluation_rows"]:
        row["prediction_committed_at"] = row["decision_at"]
    assert check_information_boundaries(plan, report, registered_at=registration) == 2
    report["evaluation_rows"][0]["prediction_committed_at"] = "2024-01-02T21:00:00Z"
    with pytest.raises(ValueError, match="committed"):
        check_information_boundaries(plan, report, registered_at=registration)
    report["evaluation_rows"][0]["prediction_committed_at"] = "2024-01-03T11:02:00Z"
    with pytest.raises(ValueError, match="committed"):
        check_information_boundaries(plan, report, registered_at=registration)
    with pytest.raises(ValueError, match="precede"):
        validate_plan(plan, "2024-01-03T00:01:00Z")


def test_changed_evaluation_panel_cannot_beat_baseline_by_selecting_easier_events(setup):
    plan, report, ledger, path = setup
    ledger.register(plan)
    assert score(ledger, "baseline", report, path)["status"] == "scored"
    report["evaluation_rows"][0]["event_id"] = "easier_replacement_event"
    report["metrics"]["loss"] = 0.01
    result = score(ledger, "conditional", report, path)
    assert result["status"] == "invalid"
    assert "panel differs" in result["failure"]
    assert result["report_sha256"]
    assert ledger.ranking()["best_research_candidate"] == "baseline"


@pytest.fixture
def process_setup(setup):
    plan, report, ledger, path = setup
    worker = Path(next(iter(plan["candidates"][0]["sources"])))
    evaluator = Path(next(iter(plan["evaluator_sources"])))
    worker.write_text(PROCESS_CANDIDATE)
    evaluator.write_text(PROCESS_EVALUATOR)
    manifest = Path(plan["input_manifest_path"])
    manifest.write_text(json.dumps(report))
    plan["input_manifest_sha256"] = source_hashes([manifest])[str(manifest)]
    runner = Path(__file__).resolve().parents[1] / "research/probes/autoresearch_run.py"
    plan["evaluator_sources"] = source_hashes([evaluator, runner])
    plan["execution"] = {"cwd": str(path.parent), "evaluator_command": [sys.executable, str(evaluator)]}
    for candidate in plan["candidates"]:
        candidate["sources"] = source_hashes([worker])
        candidate["command"] = [sys.executable, str(worker)]
    return plan, ledger


def test_real_subprocesses_use_fixed_evaluator_preserve_logs_and_do_not_invoke_a_shell(process_setup):
    plan, ledger = process_setup
    unexpected = ledger.path.parent / "shell_was_invoked"
    literal = f"$(touch {unexpected})"
    plan["candidates"][0]["command"].append(literal)
    ledger.register(plan)
    first = run_trial(ledger.path, "baseline")
    assert first["terminal"]["payload"]["status"] == "scored"
    assert first["terminal"]["payload"]["metric"] == 1
    assert not unexpected.exists()
    artifact = first["terminal"]["payload"]["artifacts"]
    assert literal in Path(artifact["candidate_stdout"]["path"]).read_text()
    assert "candidate stderr retained" in Path(artifact["candidate_stderr"]["path"]).read_text()
    assert "fixed evaluator ran" in Path(artifact["evaluator_stdout"]["path"]).read_text()
    assert "-999" in Path(artifact["candidate_untrusted_result"]["path"]).read_text()
    for record in artifact.values():
        assert source_hashes([record["path"]])[record["path"]] == record["sha256"]
    second = run_trial(ledger.path, "conditional")
    assert second["ranking"]["best_research_candidate"] == "conditional"
    assert second["ranking"]["promotion_eligible"] is False
    with pytest.raises(ValueError, match="already attempted"):
        run_trial(ledger.path, "conditional")
    assert ledger.status()["attempted_trials"] == 2


@pytest.mark.parametrize("mode,code", [("crash", 3), ("evaluator_crash", 4), ("missing_result", 0)])
def test_real_candidate_and_evaluator_failures_are_retained_without_retry(process_setup, mode, code):
    plan, ledger = process_setup
    plan["candidates"][0]["parameters"]["mode"] = mode
    ledger.register(plan)
    result = run_trial(ledger.path, "baseline")
    terminal = result["terminal"]["payload"]
    assert terminal["status"] in {"failed", "invalid"}
    assert result["execution"]["phases"][-1]["returncode"] == code
    assert terminal["metric"] is None
    assert Path(terminal["artifacts"]["candidate_stderr"]["path"]).read_text()
    assert ledger.status()["attempted_trials"] == 1
    assert ledger.status()["running"] == []
    with pytest.raises(ValueError, match="already attempted"):
        run_trial(ledger.path, "baseline")


def test_wall_deadline_kills_a_real_stubborn_grandchild_and_preserves_timeout(process_setup):
    plan, ledger = process_setup
    plan["max_trial_seconds"] = 0.7
    plan["candidates"][0]["parameters"]["mode"] = "timeout"
    ledger.register(plan)
    began = time.monotonic()
    result = run_trial(ledger.path, "baseline")
    assert time.monotonic() - began < 3
    assert result["terminal"]["payload"]["status"] == "invalid"
    assert result["execution"]["phases"][0]["timed_out"] is True
    assert result["execution"]["phases"][0]["returncode"] != 0
    root = Path(result["execution"]["artifact_directory"])
    child_pid = int((root / "grandchild.pid").read_text())
    heartbeat = (root / "heartbeat").read_text()
    time.sleep(0.15)
    assert (root / "heartbeat").read_text() == heartbeat
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(child_pid)], capture_output=True, text=True, check=False
    )
    assert not state.stdout.strip() or state.stdout.strip().startswith("Z")
    assert ledger.status()["running"] == []
    assert ledger.status()["attempted_trials"] == 1


def test_candidate_and_evaluator_share_one_budget_instead_of_each_receiving_a_new_one(process_setup):
    plan, ledger = process_setup
    plan["max_trial_seconds"] = 0.65
    plan["candidates"][0]["parameters"]["mode"] = "slow_candidate"
    ledger.register(plan)
    result = run_trial(ledger.path, "baseline")
    phases = result["execution"]["phases"]
    assert phases[0]["returncode"] == 0
    assert phases[1]["timed_out"] is True
    assert result["terminal"]["payload"]["status"] == "invalid"


def test_real_runner_termination_cleans_up_its_child_and_records_interruption(process_setup):
    plan, ledger = process_setup
    plan["candidates"][0]["parameters"]["mode"] = "timeout"
    ledger.register(plan)
    project = Path(__file__).resolve().parents[1]
    runner = subprocess.Popen(
        [sys.executable, "-m", "research.probes.autoresearch_run", str(ledger.path), "baseline"],
        cwd=project,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        until = time.monotonic() + 4
        root = ledger.path.with_suffix(".artifacts")
        while time.monotonic() < until:
            markers = list(root.glob("*/grandchild.pid")) if root.exists() else []
            if markers:
                break
            time.sleep(0.02)
        assert markers
        os.kill(runner.pid, 15)
        stdout, stderr = runner.communicate(timeout=4)
        assert runner.returncode == 1, stderr
        result = json.loads(stdout)
        assert "signal 15" in result["execution"]["failure"]
        assert ledger.status()["running"] == []
        assert ledger.status()["attempted_trials"] == 1
        pid = int(markers[0].read_text())
        state = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        )
        assert not state.stdout.strip() or state.stdout.strip().startswith("Z")
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait(timeout=2)


def test_runner_refuses_an_unpinned_actual_evaluator_before_starting_a_trial(process_setup):
    plan, ledger = process_setup
    other = ledger.path.parent / "unregistered_evaluator.py"
    other.write_text("raise SystemExit('This source was never registered')\n")
    plan["execution"]["evaluator_command"][1] = str(other)
    ledger.register(plan)
    with pytest.raises(ValueError, match="actual script entrypoint"):
        run_trial(ledger.path, "baseline")
    assert ledger.status()["attempted_trials"] == 0
