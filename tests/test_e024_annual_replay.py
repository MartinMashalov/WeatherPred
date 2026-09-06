"""E024 chronology, continuous cash and immutable execution on synthetic inputs."""

import argparse
import importlib.metadata
import json
import sys
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest

from research.experiments.e024_annual_replay import (
    CONFIG,
    DAY,
    FIRST,
    LAST,
    RequestedStop,
    account_config,
    assert_prefix,
    calendar,
    config_check,
    exact_quote,
    generate_intents,
    prefix_account,
    read_json_record,
    report_directory,
    select_from_bounds,
    source_hashes,
    supervised_execute,
    training_account,
    ts,
    unit,
)
from weatherpred.archive import Archive, canonical
from weatherpred.bankroll_replay import replay
from weatherpred.timeutil import utcnow
from weatherpred.trading_research import candidates


def settings():
    return json.loads(CONFIG.read_bytes())


def policy(identifier="A", exit_hours=3):
    return {
        "id": identifier,
        "family": "favorite_yes",
        "threshold": 0.6,
        "horizon_hours": 6,
        "lookback_hours": 0,
        "maximum_spread": 0.1,
        "exit_hours": exit_hours,
    }


def receipt(identifier=1):
    return {
        "source_record_id": identifier,
        "available_at": "2026-09-06T18:00:00.123456+00:00",
        "body_sha256": "a" * 64,
        "historical_availability_verified": False,
    }


def market(identifier, decision, *, settled_at=None, outcome=None):
    source_end = decision + 6 * 3600
    endpoints = {
        decision: {"bid": ".69", "ask": ".71"},
        decision + 3600: {"bid": ".69", "ask": ".71"},
        decision + 4 * 3600: {"bid": ".81", "ask": ".83"},
    }
    return {
        "metadata": {
            "day": datetime.fromtimestamp(decision, UTC).date().isoformat(),
            "ticker": identifier,
            "event": "EVENT-" + identifier,
            "source_period_end": datetime.fromtimestamp(source_end, UTC).isoformat(),
            "open_ts": decision - DAY,
            "close_ts": source_end + DAY,
            "settled_ts": settled_at,
            "outcome": outcome,
            "metadata_provenance": receipt(),
        },
        "quotes": endpoints,
        "quote_provenance": {when: receipt() for when in endpoints},
    }


class Guarded(dict):
    """Raise if code even consults a field beyond its declared information gate."""

    def __init__(self, value, forbidden):
        super().__init__(value)
        self.forbidden = set(forbidden)

    def __getitem__(self, key):
        if key in self.forbidden:
            raise AssertionError(f"Future field accessed: {key}")
        return super().__getitem__(key)

    def __contains__(self, key):
        if key in self.forbidden:
            raise AssertionError(f"Future field consulted: {key}")
        return super().__contains__(key)

    def get(self, key, default=None):
        if key in self.forbidden:
            raise AssertionError(f"Future field accessed: {key}")
        return super().get(key, default)


def test_timestamp_gates_precede_future_contract_fields_prices_and_labels():
    config = settings()
    scenario = config["scenarios"][0]
    cutoff = ts("2026-07-01")
    future_stub = {"metadata": {"day": "2026-08-01"}}
    assert generate_intents([future_stub], policy(), scenario, FIRST, cutoff, cutoff)["orders"] == []
    with pytest.raises(ValueError, match="Protected event"):
        generate_intents([{"metadata": {"day": "2025-12-01"}}], policy(), scenario, FIRST, cutoff, cutoff)
    decision = cutoff - 3 * 3600
    row = market("unreleased", decision, settled_at=cutoff, outcome=1)
    row["metadata"] = Guarded(row["metadata"], ["outcome"])
    row["quotes"] = Guarded(row["quotes"], [cutoff, cutoff + 3600, str(cutoff), str(cutoff + 3600)])
    generated = generate_intents([row], policy(), scenario, FIRST, cutoff, cutoff)
    assert len(generated["orders"]) == 1
    assert generated["orders"][0]["entry_price"] == "0.72"
    assert generated["orders"][0]["exit_ts"] is None
    assert generated["status_counts"] == {"entry_with_unresolved_terminal": 1}
    assert exact_quote({}, cutoff, cutoff) == (None, None)
    assert exact_quote({}, FIRST, LAST) == (None, None)
    assert exact_quote({}, LAST, LAST + DAY) == (None, None)


def test_failed_entries_are_retained_and_future_outcomes_cannot_change_sizing():
    config = settings()
    scenario = config["scenarios"][0]
    decision = ts("2026-06-01") + 3600
    absent, too_high, valid = [market(name, decision) for name in ("absent", "too-high", "valid")]
    del absent["quotes"][decision + 3600]
    too_high["quotes"][decision + 3600] = {"bid": ".79", "ask": ".81"}
    too_high["metadata"] = Guarded(too_high["metadata"], ["outcome", "settled_ts"])
    valid["metadata"].update(settled_ts=decision + DAY, outcome=0)
    rows = generate_intents(
        [absent, too_high, valid],
        policy(exit_hours=None),
        scenario,
        FIRST,
        decision + 2 * DAY,
        decision + 2 * DAY,
    )
    assert len(rows["orders"]) == 3
    assert rows["status_counts"] == {
        "missing_entry_quote": 1,
        "entry_limit_not_met": 1,
        "conditional_settlement": 1,
    }
    losing = replay(rows["orders"], account_config(config, scenario, ".05"), FIRST, decision + 2 * DAY)
    valid["metadata"]["outcome"] = 1
    alternate = generate_intents(
        [absent, too_high, valid],
        policy(exit_hours=None),
        scenario,
        FIRST,
        decision + 2 * DAY,
        decision + 2 * DAY,
    )
    winning = replay(alternate["orders"], account_config(config, scenario, ".05"), FIRST, decision + 2 * DAY)
    assert [row["decision_sha256"] for row in rows["orders"]] == [
        row["decision_sha256"] for row in alternate["orders"]
    ]
    assert losing["accepted_trades"] == winning["accepted_trades"]
    assert losing["cash"] < 200 < winning["cash"]
    assert losing["no_trade_reasons"] == {"missing_entry_quote": 1, "entry_limit_not_met": 1}
    provenance = rows["orders"][-1]["signal_provenance"]
    assert provenance["available_ts"] > LAST
    assert provenance["assumed_available_ts"] == decision
    assert provenance["availability_verified"] is False


def test_longer_prefix_reveals_boundary_fill_and_old_exit_during_cash_without_reset():
    config = settings()
    scenario = config["scenarios"][0]
    boundary = ts("2026-08-01")
    rows = [market("held", boundary - 3 * 3600), market("pending", boundary - 3600)]
    selected = {"effective_date": "2026-07-01", "policy": policy(), "risk_fraction": ".05"}
    first = prefix_account(rows, [selected], [policy()], scenario, config, boundary, "hypothetical")
    assert len(first["account"]["unresolved_holdings"]) == 1
    assert len(first["account"]["pending_orders"]) == 1
    assert first["orders"][1]["entry_price"] is None
    cash = {"effective_date": "2026-08-01", "policy": None, "risk_fraction": None}
    second = prefix_account(
        rows, [selected, cash], [policy()], scenario, config, ts("2026-09-01"), "hypothetical", first
    )
    assert second["previous_prefix_reproduced"] is True
    assert second["independent_account_audit"]["accounting_reproduced"] is True
    assert second["account"]["pending_orders"] == second["account"]["unresolved_holdings"] == []
    assert [trade["quantity"] for trade in second["account"]["accepted_trades"]] == [13, 13]
    assert [trade["cash_release_ts"] for trade in second["account"]["closed_trades"]] == [
        boundary + 3600,
        boundary + 3 * 3600,
    ]
    assert second["account"]["cash"] == pytest.approx(201.40)
    assert len(first["account"]["daily"]) == 329
    assert first["account"]["daily"][:298] == second["account"]["daily"][:298]
    assert all(day["cash"] == 200 for day in first["account"]["daily"][:298])
    verified = prefix_account(
        rows, [selected, cash], [policy()], scenario, config, ts("2026-09-01"), "verified"
    )
    assert verified["account"]["cash"] == 200
    assert verified["account"]["accepted_trades"] == []
    assert verified["account"]["no_trade_reasons"] == {"signal_unverified_availability": 2}
    corrupted = deepcopy(second["account"])
    corrupted["daily"][0]["cash"] = 201
    with pytest.raises(ValueError, match="earlier daily"):
        assert_prefix(first["account"], corrupted)


def test_cash_schedule_has_actual_365_days_and_training_calendar_is_complete():
    config = settings()
    scenario = config["scenarios"][0]
    choices = [
        {"effective_date": day, "policy": None, "risk_fraction": None} for day in config["selection_dates"]
    ]
    result = prefix_account([], choices, [policy()], scenario, config, LAST, "hypothetical")
    assert result["account"]["cash"] == 200
    assert len(result["account"]["daily"]) == 365
    assert result["independent_account_audit"]["daily_rows"] == 365
    assert result["account"]["closed_trades"] == []
    training = training_account(
        {"orders": []}, {"id": 7, "body_sha256": "a" * 64}, config, scenario, ".005", policy(), "2026-07-01"
    )
    assert [day["date"] for day in training["account"]["daily"]] == calendar("2026-01-01", "2026-07-01")
    assert len(training["account"]["daily"]) == 181
    assert training["intent_record_id"] == 7
    assert training["account"]["cash"] == 200
    full_policies = candidates(json.loads(Path(config["parent_policy_config"]).read_bytes()))
    config_check(config, full_policies)
    assert len(full_policies) * 4 * 2 * 3 == 13824
    config["selection_dates"][0] = "2026-06-01"
    with pytest.raises(ValueError, match="Selection dates"):
        config_check(config, full_policies)


def summaries():
    return [
        {
            "policy": policy(name),
            "risk_fraction": fraction,
            "scenario": scenario,
            "account": {
                "cash": 210,
                "pending_orders": [],
                "unresolved_holdings": [],
                "daily": [{"released_trades": int(i < 60)} for i in range(181)],
            },
        }
        for name, fraction in (("A", ".05"), ("A", ".005"), ("B", ".01"))
        for scenario in ("costed", "stress")
    ]


def bounds(count):
    return {
        str(length): {"n_candidates": count, "eligible": [True] * count, "lower_bounds": [0.001] * count}
        for length in (1, 7, 14)
    }


def test_selection_requires_both_cost_cases_every_block_and_released_cash():
    rows = summaries()
    result = select_from_bounds(rows, bounds(6), settings())
    assert result["policy"]["id"] == "A" and result["risk_fraction"] == ".005"
    assert result["eligible_policy_size_pairs"] == 3
    lower = bounds(6)
    lower["14"]["lower_bounds"][3] = -0.001  # Smaller A fails on stress/sensitivity.
    rows[0]["account"]["pending_orders"] = [{"trade_id": "unreleased"}]
    selected = select_from_bounds(rows, lower, settings())
    assert selected["policy"]["id"] == "B"
    assert selected["eligible_policy_size_pairs"] == 1
    lower["1"]["eligible"][4] = False
    lower["1"]["lower_bounds"][4] = None
    cash = select_from_bounds(rows, lower, settings())
    assert cash["action"] == "cash" and cash["policy"] is None
    assert cash["eligible_policy_size_pairs"] == 0
    assert cash["rejection_counts"] == {
        "unresolved_training_cash_flows": 1,
        "nonpositive_or_degenerate_bound_14d": 1,
        "nonpositive_or_degenerate_bound_1d": 1,
    }
    del lower["14"]
    with pytest.raises(ValueError, match="entire candidate"):
        select_from_bounds(rows, lower, settings())


def test_units_reuse_exact_completed_artifacts_and_never_retry_failure(tmp_path):
    config = {"stop_file": str(tmp_path / "STOP"), "minimum_free_disk_bytes": 0}
    archive = Archive(tmp_path / "archive")
    deadline = time.time() + 30
    calls = []

    def complete():
        calls.append("success")
        return {"value": 1}

    def fail():
        calls.append("failed")
        raise ValueError("synthetic failure")

    try:
        first, result = unit(archive, 10, "result", "success", complete, config, deadline)
        again, duplicate = unit(archive, 10, "result", "success", complete, config, deadline)
        assert first["id"] == again["id"] and result == duplicate == {"value": 1}
        assert calls == ["success"]
        with pytest.raises(ValueError, match="synthetic failure"):
            unit(archive, 10, "result", "failure", fail, config, deadline)
        with pytest.raises(RuntimeError, match="cannot silently retry"):
            unit(archive, 10, "result", "failure", fail, config, deadline)
        assert calls == ["success", "failed"]
        retained = archive.latest("e024_unit_failed", "result:10:failure")
        assert read_json_record(archive, retained)["automatic_retry"] is False
        Path(config["stop_file"]).touch()
        with pytest.raises(RequestedStop):
            unit(archive, 10, "result", "not-started", complete, config, deadline)
        assert archive.latest("e024_unit_started", "result:10:not-started") is None
        Path(config["stop_file"]).unlink()
        with pytest.raises(TimeoutError):
            unit(archive, 10, "result", "expired", complete, config, time.time() - 1)
        (archive.root / "blobs" / first["body_sha256"]).write_bytes(b"corrupted")
        with pytest.raises(ValueError, match="body hash"):
            unit(archive, 10, "result", "success", complete, config, deadline)
    finally:
        archive.close()


def test_real_worker_failure_is_retained_without_retry_or_market_data(tmp_path):
    """Launch the real CLI against an isolated archive lacking any dataset."""
    config = settings()
    archive = Archive(tmp_path / "archive")
    protocol = {
        "config": config,
        "policies": candidates(json.loads(Path(config["parent_policy_config"]).read_bytes())),
        "source_hashes": source_hashes(),
        "python_executable": sys.executable,
        "working_directory": str(Path.cwd()),
        "runtime_versions": {name: importlib.metadata.version(name) for name in ("numpy", "weatherpred")},
        "dataset_record_id": 999999999,
        "dataset_body_sha256": "a" * 64,
    }
    identifier = archive.append(
        "experiment_protocol", config["experiment"], utcnow(), {}, canonical(protocol).encode()
    )
    args = argparse.Namespace(archive_root=str(archive.root), run_record_id=identifier)
    output = report_directory(archive, identifier)
    try:
        supervised_execute(args)
        terminal = archive.latest("e024_execution_terminal", str(identifier))
        result = read_json_record(archive, terminal)
        assert result["status"] == "failed" and result["exit_code"] != 0
        assert result["automatic_retry"] is False
        assert (
            "Registered normalized dataset is missing" in Path(result["logs"]["stderr"]["path"]).read_text()
        )
        assert (
            archive.db.execute("SELECT COUNT(*) FROM records WHERE kind='e024_execution_attempt'").fetchone()[
                0
            ]
            == 1
        )
        supervised_execute(args)
        assert (
            archive.db.execute("SELECT COUNT(*) FROM records WHERE kind='e024_execution_attempt'").fetchone()[
                0
            ]
            == 1
        )
        assert (
            archive.db.execute("SELECT COUNT(*) FROM records WHERE kind='e024_unit_started'").fetchone()[0]
            == 0
        )
    finally:
        archive.close()
        for path in output.glob("attempt-*.log"):
            path.unlink()
        output.rmdir()
