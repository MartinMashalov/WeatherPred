import copy
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from weatherpred.research_campaign import CampaignLedger, TrialRejected


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def setup_plan(tmp_path, candidates=2, *, requires_fit=True):
    files = {}
    for name in ("source.py", "config.json", "dataset.json.gz"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        files[name] = path
    plan = {
        "schema_version": 1,
        "campaign_id": "synthetic-statistical-search",
        "phase": "development",
        "availability_mode": "conditional_historical",
        "candidates": [{"id": f"candidate-{i}", "requires_fit": requires_fit} for i in range(candidates)],
        "pins": {
            "sources": {str(files["source.py"]): fingerprint(files["source.py"])},
            "config": {"path": str(files["config.json"]), "sha256": fingerprint(files["config.json"])},
            "dataset": {
                "path": str(files["dataset.json.gz"]),
                "sha256": fingerprint(files["dataset.json.gz"]),
            },
        },
        "max_trials": candidates,
        "max_trial_seconds": 30,
        "max_total_seconds": 60,
        "fit_cutoff": "2026-03-01T00:00:00Z",
        "fit_cutoffs": ["2026-03-01T00:00:00Z", "2026-04-01T00:00:00Z"],
        "evaluation_start": "2026-03-01T00:00:00Z",
        "evaluation_end": "2026-05-01T00:00:00Z",
    }
    return plan, files


def prediction(plan, identifier="candidate-0"):
    fitted = next(c["requires_fit"] for c in plan["candidates"] if c["id"] == identifier)
    folds, rows = [], []
    for index, cutoff in enumerate(plan["fit_cutoffs"]):
        when = datetime.fromisoformat(cutoff)
        available = (when - timedelta(days=1)).isoformat()
        # Both real receipts intentionally occur after historical simulated decisions.
        received = "2026-06-01T00:00:00Z"
        folds.append(
            {
                "fit_cutoff": cutoff,
                "model_sha256": str(index + 1) * 64,
                "training_labels_sha256": "a" * 64 if fitted else None,
                "latest_training_label_available_at": available if fitted else None,
                "latest_training_label_received_at": received if fitted else None,
            }
        )
        rows.append(
            {
                "opportunity_id": str(index),
                "decision_at": (when + timedelta(hours=1)).isoformat(),
                "fold_fit_cutoff": cutoff,
                "feature_available_at": cutoff,
                "feature_received_at": received,
                "probability": 0.5,
                "abstain": False,
            }
        )
    return {
        "candidate_id": identifier,
        "provenance": {
            "availability_mode": plan["availability_mode"],
            "model_sha256": "b" * 64,
            "feature_view_sha256": "c" * 64,
            "folds": folds,
        },
        "predictions": rows,
    }


def save(tmp_path, value):
    path = tmp_path / f"{value['candidate_id']}-predictions.json"
    path.write_text(json.dumps(value))
    return path


def result(plan, commitment):
    return {
        "prediction_sha256": commitment["payload"]["prediction_sha256"],
        "label_artifact_sha256": "d" * 64,
        "evaluation_end": plan["evaluation_end"],
        "metrics": {"costed": {"net_log_growth": 0.0}, "stress": {"net_log_growth": -0.01}},
    }


def test_restart_preserves_multifold_predictions_receipts_and_all_failures(tmp_path):
    plan, _ = setup_plan(tmp_path)
    ledger = CampaignLedger(tmp_path / "campaign.jsonl")
    ledger.register(plan)
    ledger.start_trial("candidate-0")
    value = prediction(plan)
    commit = ledger.commit_predictions("candidate-0", save(tmp_path, value))
    reopened = CampaignLedger(ledger.path)
    reopened.finish_trial("candidate-0", result(plan, commit))
    reopened.start_trial("candidate-1")
    reopened.fail_trial("candidate-1", "model process exited 1; log artifact abc")
    summary = reopened.summary()
    assert summary["attempted_trials"] == 2 and summary["results"] == summary["failures"] == 1
    assert summary["running"] == [] and not summary["promotion_eligible"]
    retained = json.loads(Path(commit["payload"]["artifact_path"]).read_bytes())
    assert retained["provenance"]["folds"][0]["latest_training_label_received_at"] == "2026-06-01T00:00:00Z"
    assert not summary["historical_availability_verified"] and not summary["os_sandbox"]
    assert reopened.verify()["records_verified"] == 6
    with pytest.raises(ValueError, match="already attempted"):
        reopened.start_trial("candidate-1")


def test_source_dataset_and_prediction_tampering_are_detected(tmp_path):
    plan, files = setup_plan(tmp_path)
    ledger = CampaignLedger(tmp_path / "campaign.jsonl")
    ledger.register(plan)
    for key in ("source.py", "config.json", "dataset.json.gz"):
        original = files[key].read_bytes()
        files[key].write_bytes(b"altered")
        with pytest.raises(ValueError, match="bytes changed"):
            ledger.start_trial("candidate-0")
        files[key].write_bytes(original)
    ledger.start_trial("candidate-0")
    committed = ledger.commit_predictions("candidate-0", save(tmp_path, prediction(plan)))
    artifact = Path(committed["payload"]["artifact_path"])
    original = artifact.read_bytes()
    artifact.write_bytes(b"altered")
    with pytest.raises(ValueError, match="artifact changed"):
        ledger.verify()
    artifact.write_bytes(original)
    lines = ledger.path.read_text().splitlines()
    altered = json.loads(lines[1])
    altered["payload"]["deadline"] = "2099-01-01T00:00:00Z"
    lines[1] = json.dumps(altered)
    ledger.path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="hash chain"):
        ledger.verify()


def test_result_without_commit_is_retained_as_terminal_failure(tmp_path):
    plan, _ = setup_plan(tmp_path)
    ledger = CampaignLedger(tmp_path / "campaign.jsonl")
    ledger.register(plan)
    ledger.start_trial("candidate-0")
    with pytest.raises(TrialRejected, match="prior prediction"):
        ledger.finish_trial("candidate-0", {"metrics": {"profit": 100}})
    assert ledger.summary()["failures"] == 1
    with pytest.raises(ValueError, match="already attempted"):
        ledger.start_trial("candidate-0")


@pytest.mark.parametrize(
    "corruption", ["late_label", "missing_fold", "wrong_fold", "future_feature", "duplicate_row"]
)
def test_invalid_chronological_predictions_retain_failure(tmp_path, corruption):
    plan, _ = setup_plan(tmp_path)
    ledger = CampaignLedger(tmp_path / "campaign.jsonl")
    ledger.register(plan)
    ledger.start_trial("candidate-0")
    value = prediction(plan)
    if corruption == "late_label":
        value["provenance"]["folds"][1]["latest_training_label_available_at"] = plan["fit_cutoffs"][1]
    elif corruption == "missing_fold":
        value["provenance"]["folds"].pop()
    elif corruption == "wrong_fold":
        value["predictions"][1]["fold_fit_cutoff"] = plan["fit_cutoffs"][0]
    elif corruption == "future_feature":
        value["predictions"][0]["feature_available_at"] = plan["evaluation_end"]
    else:
        value["predictions"].append(copy.deepcopy(value["predictions"][0]))
    with pytest.raises(TrialRejected):
        ledger.commit_predictions("candidate-0", save(tmp_path, value))
    assert ledger.summary()["failures"] == 1 and ledger.summary()["committed_trials"] == 0


def test_verified_receipts_cannot_be_replaced_by_conditional_clocks(tmp_path):
    plan, _ = setup_plan(tmp_path)
    plan["availability_mode"] = "receipt_verified"
    ledger = CampaignLedger(tmp_path / "campaign.jsonl")
    ledger.register(plan)
    ledger.start_trial("candidate-0")
    with pytest.raises(TrialRejected, match="actual training label receipt"):
        ledger.commit_predictions("candidate-0", save(tmp_path, prediction(plan)))


def test_no_fit_baseline_and_duplicate_commit_or_result(tmp_path):
    plan, _ = setup_plan(tmp_path, requires_fit=False)
    ledger = CampaignLedger(tmp_path / "campaign.jsonl")
    ledger.register(plan)
    ledger.start_trial("candidate-0")
    path = save(tmp_path, prediction(plan))
    commit = ledger.commit_predictions("candidate-0", path)
    with pytest.raises(ValueError, match="uncommitted"):
        ledger.commit_predictions("candidate-0", path)
    ledger.finish_trial("candidate-0", result(plan, commit))
    with pytest.raises(ValueError, match="running"):
        ledger.finish_trial("candidate-0", result(plan, commit))


@pytest.mark.parametrize("corruption", ["nan", "promotion", "wrong_predictions"])
def test_invalid_results_are_terminal_and_never_ranked(tmp_path, corruption):
    plan, _ = setup_plan(tmp_path)
    ledger = CampaignLedger(tmp_path / "campaign.jsonl")
    ledger.register(plan)
    ledger.start_trial("candidate-0")
    commit = ledger.commit_predictions("candidate-0", save(tmp_path, prediction(plan)))
    value = result(plan, commit)
    if corruption == "nan":
        value["metrics"]["bad"] = float("nan")
    elif corruption == "promotion":
        value["profitability_proven"] = True
    else:
        value["prediction_sha256"] = "0" * 64
    with pytest.raises(TrialRejected):
        ledger.finish_trial("candidate-0", value)
    assert ledger.summary()["results"] == 0 and ledger.summary()["failures"] == 1


def test_concurrent_reservations_and_expired_trial_consume_fixed_budget(tmp_path):
    plan, _ = setup_plan(tmp_path, candidates=3)
    plan.update(max_trial_seconds=0.03, max_total_seconds=0.06)
    ledger = CampaignLedger(tmp_path / "campaign.jsonl")
    ledger.register(plan)
    ledger.start_trial("candidate-0")
    ledger.start_trial("candidate-1")
    with pytest.raises(ValueError, match="budget"):
        ledger.start_trial("candidate-2")
    time.sleep(0.04)
    with pytest.raises(TrialRejected, match="deadline"):
        ledger.commit_predictions("candidate-0", save(tmp_path, prediction(plan)))
    ledger.fail_trial("candidate-1", "second worker cancelled after deadline")
    assert ledger.summary()["remaining_seconds"] == 0
    with pytest.raises(ValueError, match="budget"):
        ledger.start_trial("candidate-2")


def test_duplicate_candidates_invalid_budgets_and_registration_overwrite(tmp_path):
    plan, _ = setup_plan(tmp_path)
    for change in (
        {"max_total_seconds": float("inf")},
        {"max_trial_seconds": -1},
        {"candidates": [plan["candidates"][0], plan["candidates"][0]]},
    ):
        with pytest.raises(ValueError):
            CampaignLedger(tmp_path / "bad.jsonl").register({**plan, **change})
    ledger = CampaignLedger(tmp_path / "campaign.jsonl")
    ledger.register(plan)
    with pytest.raises(ValueError, match="already registered"):
        ledger.register(plan)
    with pytest.raises(ValueError, match="Unregistered"):
        ledger.start_trial("secret-new-seed")


def test_prospective_commit_is_before_decision_and_retains_real_receipts(tmp_path):
    plan, _ = setup_plan(tmp_path, candidates=1, requires_fit=False)
    now = datetime.now(UTC)
    cutoff = (now - timedelta(hours=1)).isoformat()
    start, end = (now + timedelta(hours=1)).isoformat(), (now + timedelta(hours=2)).isoformat()
    plan.update(
        phase="prospective",
        availability_mode="receipt_verified",
        fit_cutoff=cutoff,
        fit_cutoffs=[cutoff],
        evaluation_start=start,
        evaluation_end=end,
    )
    value = prediction(plan)
    value["predictions"][0].update(decision_at=start, feature_received_at=now.isoformat())
    ledger = CampaignLedger(tmp_path / "campaign.jsonl")
    ledger.register(plan)
    ledger.start_trial("candidate-0")
    ledger.commit_predictions("candidate-0", save(tmp_path, value))
    assert ledger.summary()["phase"] == "prospective" and not ledger.summary()["promotion_eligible"]


def test_failure_can_be_retained_after_input_or_committed_artifact_changes(tmp_path):
    plan, files = setup_plan(tmp_path)
    ledger = CampaignLedger(tmp_path / "campaign.jsonl")
    ledger.register(plan)
    ledger.start_trial("candidate-0")
    commit = ledger.commit_predictions("candidate-0", save(tmp_path, prediction(plan)))
    files["dataset.json.gz"].write_bytes(b"changed source")
    Path(commit["payload"]["artifact_path"]).write_bytes(b"changed predictions")
    failed = ledger.fail_trial("candidate-0", "input integrity failed; original fingerprints retained")
    assert failed["kind"] == "failure" and not failed["payload"]["automatic_retry"]
    with pytest.raises(ValueError, match="bytes changed"):
        ledger.verify()
    assert json.loads(ledger.path.read_text().splitlines()[-1])["kind"] == "failure"
