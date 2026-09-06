"""The separate E024 run audit must reject convincing synthetic corruptions."""

import gzip
import json
import time
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from research.experiments.e024_annual_replay import (
    CONFIG,
    FIRST,
    generate_intents,
    prefix_account,
    training_account,
    ts,
    unit,
)
from research.experiments.e024_run_audit import (
    audit_bootstrap,
    audit_prefix,
    audit_training_family,
    checked_unit,
    independent_catalog,
    independent_choice,
    independent_settings,
    intent_window,
)
from weatherpred.archive import Archive, canonical
from weatherpred.bankroll_replay import replay
from weatherpred.bankroll_selection import simultaneous_growth_bounds
from weatherpred.timeutil import utcnow
from weatherpred.trading_research import candidates


def config():
    return json.loads(CONFIG.read_bytes())


def catalog():
    parent = json.loads(Path(config()["parent_policy_config"]).read_bytes())
    return independent_catalog(parent)


def append_unit(archive, run_id, kind, suffix, value):
    name = f"{run_id}:{suffix}"
    archive.append(
        "e024_unit_started", f"{kind}:{name}", utcnow(), {}, canonical({"kind": kind, "key": name}).encode()
    )
    return archive.append(kind, name, utcnow(), {}, gzip.compress(canonical(value).encode(), mtime=0))


def empty_family(archive, short_first=False):
    settings = config()
    settings["risk_fractions"] = ["0.005", "0.05"]
    policies = catalog()[:1]
    run_id = archive.append("experiment_protocol", "synthetic", utcnow(), {}, b"{}")
    ids = []
    cutoff_day = "2026-07-01"
    end = ts(cutoff_day)
    for scenario in settings["scenarios"]:
        suffix = f"{cutoff_day}:{policies[0]['id']}:{scenario['name']}"
        intents = generate_intents([], policies[0], scenario, FIRST, end - 5 * 86400, end)
        intent_id = append_unit(archive, run_id, "e024_intents_gzip", suffix, intents)
        intent_record = archive.db.execute("SELECT * FROM records WHERE id=?", (intent_id,)).fetchone()
        for fraction in settings["risk_fractions"]:
            account = training_account(
                intents, intent_record, settings, scenario, fraction, policies[0], cutoff_day
            )
            if short_first and not ids:
                account["account"] = replay(
                    [], independent_settings(settings, scenario, fraction), FIRST, end - 86400
                )
            ids.append(
                append_unit(archive, run_id, "e024_training_account_gzip", suffix + ":" + fraction, account)
            )
    choice = archive.append("synthetic_choice_start", "choice", utcnow(), {}, b"{}")
    return settings, policies, run_id, ids, choice


def test_independent_catalog_and_full_training_identity_calendar_and_hash_links(tmp_path):
    original = json.loads(Path(config()["parent_policy_config"]).read_bytes())
    assert independent_catalog(original) == candidates(original)
    assert len(independent_catalog(original)) == 576
    archive = Archive(tmp_path)
    try:
        settings, policies, run_id, ids, choice = empty_family(archive)
        summaries, growth, audits = audit_training_family(
            archive, run_id, settings, policies, "2026-07-01", ids, choice, time.time() + 30
        )
        assert growth.shape == (181, 4) and np.count_nonzero(growth) == 0
        assert len(audits) == 4 and all(row["audit"]["accounting_reproduced"] for row in audits)
        assert all(row["release_days"] == 0 and row["cash"] == 200 for row in summaries)
        with pytest.raises(ValueError, match="incomplete or duplicated"):
            audit_training_family(
                archive, run_id, settings, policies, "2026-07-01", ids[:-1], choice, time.time() + 30
            )
        with pytest.raises(ValueError, match="mismatched"):
            audit_training_family(
                archive,
                run_id,
                settings,
                policies,
                "2026-07-01",
                list(reversed(ids)),
                choice,
                time.time() + 30,
            )
        with pytest.raises(ValueError, match="pre-choice chronology"):
            audit_training_family(
                archive, run_id, settings, policies, "2026-07-01", ids, ids[0], time.time() + 30
            )
        row = archive.db.execute("SELECT * FROM records WHERE id=?", (ids[0],)).fetchone()
        (archive.root / "blobs" / row["body_sha256"]).write_bytes(b"changed")
        with pytest.raises(ValueError, match="body hash"):
            audit_training_family(
                archive, run_id, settings, policies, "2026-07-01", ids, choice, time.time() + 30
            )
    finally:
        archive.close()


def test_account_with_consistent_shorter_calendar_is_rejected_against_declared_look(tmp_path):
    archive = Archive(tmp_path)
    try:
        settings, policies, run_id, ids, choice = empty_family(archive, short_first=True)
        with pytest.raises(ValueError, match="omitted or reordered a calendar day"):
            audit_training_family(
                archive, run_id, settings, policies, "2026-07-01", ids, choice, time.time() + 30
            )
    finally:
        archive.close()


def test_bootstrap_reproduction_and_bounds_cannot_hide_degenerate_or_changed_columns():
    settings = config()
    settings["bootstrap_resamples"] = 31
    rng = np.random.default_rng(44)
    growth = np.column_stack((rng.normal(0.001, 0.01, 28), np.zeros(28), np.ones(28) * 0.001))
    retained = simultaneous_growth_bounds(
        growth,
        block_days=7,
        resamples=31,
        seed=settings["bootstrap_seeds"][0],
        alpha=settings["familywise_alpha"] / 3,
    )
    audit = audit_bootstrap(retained, growth, settings, 0, 7, replay_seed=True)
    assert audit["random_draws_replayed"] is True
    assert audit["candidate_vectors_checked"] == 3
    changed = deepcopy(retained)
    changed["means"][0] += 0.01
    with pytest.raises(ValueError, match="means do not match"):
        audit_bootstrap(changed, growth, settings, 0, 7)
    changed = deepcopy(retained)
    changed["lower_bounds"][0] += 0.01
    with pytest.raises(ValueError, match="lower bound arithmetic"):
        audit_bootstrap(changed, growth, settings, 0, 7)
    changed = deepcopy(retained)
    changed["eligible"][2] = True
    changed["lower_bounds"][2] = 0.001
    with pytest.raises(ValueError, match="nondegenerate family"):
        audit_bootstrap(changed, growth, settings, 0, 7)
    changed = deepcopy(retained)
    changed["seed"] += 1
    with pytest.raises(ValueError, match="registered controls"):
        audit_bootstrap(changed, growth, settings, 0, 7)
    assert audit_bootstrap(retained, growth, settings, 0, 7)["random_draws_replayed"] is False


def test_independent_choice_requires_all_scenarios_and_sensitivity_bounds():
    policies = catalog()[:2]
    rows = [
        {
            "policy": p,
            "scenario": scenario,
            "risk_fraction": risk,
            "cash": 210,
            "days": 181,
            "release_days": 61,
            "unresolved": False,
        }
        for p in policies
        for risk in ("0.005", "0.05")
        for scenario in ("costed", "stress")
    ]
    bounds = {str(block): {"eligible": [True] * 8, "lower_bounds": [0.001] * 8} for block in (1, 7, 14)}
    selected = independent_choice(rows, bounds, config())
    expected_policy = min(policy["id"] for policy in policies)
    assert selected["policy"]["id"] == expected_policy
    assert selected["risk_fraction"] == "0.005"
    for row in rows:
        if row["scenario"] == "stress":
            row["unresolved"] = True
    cash = independent_choice(rows, bounds, config())
    assert cash["action"] == "cash" and cash["eligible_policy_size_pairs"] == 0
    assert cash["rejection_counts"] == {"unresolved_training_cash_flows": 4}
    rows[1]["unresolved"] = False
    bounds["14"]["lower_bounds"][1] = -0.01
    assert independent_choice(rows, bounds, config())["action"] == "cash"


def test_prefix_schedules_prior_identity_and_retained_audit_are_checked_independently():
    settings, policies = config(), catalog()[:1]
    scenario = settings["scenarios"][0]
    choices = [
        {"effective_date": day, "policy": None, "risk_fraction": None} for day in settings["selection_dates"]
    ]
    first = prefix_account([], choices[:1], policies, scenario, settings, ts("2026-08-01"), "hypothetical")
    later = prefix_account(
        [], choices[:2], policies, scenario, settings, ts("2026-09-01"), "hypothetical", first
    )
    result = audit_prefix(
        later, settings, policies, scenario, "hypothetical", choices[:2], ts("2026-09-01"), first
    )
    assert result["ending_cash"] == 200 and result["daily_rows"] == 360
    corrupted = deepcopy(later)
    corrupted["schedule"][1]["risk_fraction"] = ".05"
    with pytest.raises(ValueError, match="chronological schedule"):
        audit_prefix(
            corrupted, settings, policies, scenario, "hypothetical", choices[:2], ts("2026-09-01"), first
        )
    corrupted = deepcopy(first)
    corrupted["account"]["daily"][0]["cash"] = 201
    with pytest.raises(ValueError, match="earlier daily states"):
        audit_prefix(
            later, settings, policies, scenario, "hypothetical", choices[:2], ts("2026-09-01"), corrupted
        )
    corrupted = deepcopy(later)
    corrupted["independent_account_audit"]["ending_cash"] = 1000
    with pytest.raises(ValueError, match="fresh independent reconstruction"):
        audit_prefix(
            corrupted, settings, policies, scenario, "hypothetical", choices[:2], ts("2026-09-01"), first
        )


def test_intent_missing_failed_attempt_or_revealed_future_price_is_rejected():
    p = catalog()[0]
    decision = ts("2026-06-01")
    trade = {
        "trade_id": "original",
        "policy_id": p["id"],
        "event": "synthetic",
        "cluster": "all_weather",
        "side": "yes",
        "decision_ts": decision,
        "entry_ts": decision + 3600,
        "limit_price": ".4",
        "planned_exit_ts": None,
        "signal_provenance": {"availability_verified": False},
    }
    import hashlib

    trade["decision_sha256"] = hashlib.sha256(canonical(trade).encode()).hexdigest()
    trade.update(entry_price=None, exit_ts=None)
    intents = {
        "decision_window": [FIRST, decision + 3600],
        "evaluation_end_exclusive": decision + 3600,
        "actual_fills": 0,
        "orders": [trade],
        "decisions": [{"trade_id": "original", "status": "entry_not_yet_observable"}],
    }
    intent_window(intents, p, FIRST, decision + 3600, decision + 3600)
    changed = deepcopy(intents)
    changed["decisions"] = []
    with pytest.raises(ValueError, match="attempts were omitted"):
        intent_window(changed, p, FIRST, decision + 3600, decision + 3600)
    changed = deepcopy(intents)
    changed["orders"][0]["entry_price"] = ".4"
    with pytest.raises(ValueError, match="beyond the evaluation cutoff"):
        intent_window(changed, p, FIRST, decision + 3600, decision + 3600)
    changed = deepcopy(intents)
    changed["orders"][0]["side"] = "no"
    with pytest.raises(ValueError, match="immutable decision hash"):
        intent_window(changed, p, FIRST, decision + 3600, decision + 3600)


def test_completed_unit_cannot_hide_a_failed_or_duplicate_attempt(tmp_path):
    archive = Archive(tmp_path)
    try:
        registration = archive.append("experiment_protocol", "synthetic", utcnow(), {}, b"{}")
        settings = {"stop_file": str(tmp_path / "STOP"), "minimum_free_disk_bytes": 0}
        record, _ = unit(
            archive,
            registration,
            "fixture_gzip",
            "one",
            lambda: {"retained": True},
            settings,
            time.time() + 30,
        )
        assert checked_unit(archive, registration, record["id"], "fixture_gzip", "one")[1] == {
            "retained": True
        }
        archive.append("e024_unit_failed", f"fixture_gzip:{registration}:one", utcnow(), {}, b"{}")
        with pytest.raises(ValueError, match="cannot follow a failed attempt"):
            checked_unit(archive, registration, record["id"], "fixture_gzip", "one")
    finally:
        archive.close()
