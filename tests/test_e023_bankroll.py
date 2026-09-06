"""Synthetic E023 adapter/selection integration; no market archive is opened."""

import gzip
import json
from copy import deepcopy
from pathlib import Path

import pytest

from research.experiments.e023_bankroll import account_config, compact, convert_decisions, finish, ts
from weatherpred.bankroll_replay import replay

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config/e023_bankroll_replay.json"
PARENT_RECORD = {"id": 123, "available_at": "2026-09-06T12:00:00+00:00"}


@pytest.fixture
def config():
    return json.loads(CONFIG_PATH.read_text())


def decision(status="conditional_trade", **updates):
    result = {
        "day": "2025-01-02",
        "status": status,
        "event": "SYNTHETIC-EVENT",
        "ticker": "SYNTHETIC-CONTRACT",
        "side": "yes",
        "signal_ts": ts("2025-01-02") + 3600,
        "signal_ask": 0.49,
        "entry_ts": ts("2025-01-02") + 7200,
        "entry_limit": 0.50,
        "entry_price": 0.49,
        "exit_kind": "settlement",
        "exit_ts": ts("2025-01-03") + 3600,
        "exit_price": 1.0,
    }
    result.update(updates)
    return result


def convert(rows, config):
    return convert_decisions({"decisions": rows}, PARENT_RECORD, config["scenarios"][0])


def test_conversion_retains_every_failed_attempt_and_preserves_late_actual_receipts(config):
    failed = [
        decision("entry_limit_not_met", ticker="LIMIT", entry_price=0.51),
        decision("missing_entry_quote", ticker="QUOTE", entry_price=None),
        decision("missing_or_invalid_settlement_time", ticker="TERMINAL", exit_ts=None),
    ]
    orders, omissions = convert(
        failed
        + [
            {"day": "2025-01-02", "status": "no_signal"},
            {"day": "2025-01-02", "status": "outside_market_hours"},
        ],
        config,
    )
    assert len(orders) == 3
    assert omissions == {"no_signal": 1, "outside_market_hours": 1}
    assert [o["entry_price"] for o in orders] == ["0.51", None, "0.49"]
    assert all(o["exit_ts"] is None and o["terminal_cash_per_contract"] is None for o in orders)
    assert all(o["available_quantity"] is None for o in orders)
    for order in orders:
        provenance = order["signal_provenance"]
        assert provenance["availability_verified"] is False
        assert provenance["available_ts"] == ts("2026-09-06") + 12 * 3600
        assert provenance["assumed_available_ts"] == order["decision_ts"]
        assert provenance["available_ts"] > order["entry_ts"]
    # An unfamiliar failure status cannot silently disappear from the account.
    with pytest.raises(ValueError, match="Unreviewed parent decision status"):
        convert([decision("unexpected_failure")], config)
    with pytest.raises(ValueError, match="Protected or unregistered date"):
        convert([{"day": "2025-10-01", "status": "no_signal"}], config)


def test_training_window_is_checked_before_reading_future_order_or_outcome_fields(config):
    future_stub = {"day": "2025-09-10", "signal_ts": ts("2025-09-10")}
    orders, _ = convert_decisions(
        {"decisions": [future_stub]},
        PARENT_RECORD,
        config["scenarios"][0],
        decision_start=ts("2025-01-01"),
        decision_end=ts("2025-09-01"),
    )
    assert orders == []
    with pytest.raises(ValueError, match="Protected or unregistered date"):
        convert_decisions(
            {"decisions": [{"day": "2025-10-01", "signal_ts": ts("2025-10-01")}]},
            PARENT_RECORD,
            config["scenarios"][0],
            decision_start=ts("2025-01-01"),
            decision_end=ts("2025-09-01"),
        )


def test_future_terminal_labels_do_not_change_intent_reservation_or_entry(config):
    settings = account_config(config, config["scenarios"][0], "0.05")
    variants = [
        decision(exit_price=1.0),
        decision(exit_price=0.0),
        decision("missing_or_invalid_settlement_time", exit_ts=None, exit_price=None),
    ]
    before_release, after_release = [], []
    for row in variants:
        orders, _ = convert([row], config)
        before_release.append(replay(orders, settings, ts("2025-01-02"), ts("2025-01-03")))
        after_release.append(replay(orders, settings, ts("2025-01-02"), ts("2025-01-04")))
    sizing_keys = ("intended_quantity", "limit_price", "reserved_cash", "sizing_cost_basis_equity")
    sizing = [{key: result["orders"][0][key] for key in sizing_keys} for result in before_release]
    assert sizing[0] == sizing[1] == sizing[2]
    assert sizing[0]["intended_quantity"] == 19
    assert sizing[0]["reserved_cash"] == 9.84
    for result in before_release:
        assert len(result["accepted_trades"]) == 1
        assert result["accepted_trades"][0]["quantity"] == 19
        assert result["accepted_trades"][0]["entry_cost"] == 9.65
        assert result["cash"] == 190.35
        assert result["closed_trades"] == []
        assert len(result["unresolved_holdings"]) == 1
    assert after_release[0]["cash"] == 209.35
    assert after_release[1]["cash"] == 190.35
    assert after_release[2]["cash"] == 190.35
    assert len(after_release[0]["closed_trades"]) == len(after_release[1]["closed_trades"]) == 1
    assert after_release[2]["closed_trades"] == []
    assert after_release[2]["terminal_notes"][0]["reason"] == "missing_terminal_endpoint"


def test_account_mapping_runs_kernel_and_failed_entries_restore_reserved_cash(config):
    scenario = config["scenarios"][0]
    settings = account_config(config, scenario, "0.05")
    assert settings["conditional_depth_cap"] == 100
    assert settings["entry_coefficient"] == settings["exit_coefficient"] == "0.07"
    # Each failed order is decided after the previous attempt expires, so it
    # should recover the same full reservation, without a fee or cash loss.
    rows = [
        decision("entry_limit_not_met", ticker="LIMIT", entry_price=0.51),
        decision(
            "missing_entry_quote",
            ticker="QUOTE",
            signal_ts=ts("2025-01-02") + 10800,
            entry_ts=ts("2025-01-02") + 14400,
            entry_price=None,
        ),
    ]
    orders, _ = convert(rows, config)
    result = replay(orders, settings, ts("2025-01-02"), ts("2025-01-04"))
    assert result["no_trade_reasons"] == {"entry_limit_not_met": 1, "missing_entry_quote": 1}
    assert [r["status"] for r in result["orders"]] == ["reserved", "not_filled", "reserved", "not_filled"]
    assert [r["reserved_cash"] for r in result["orders"] if r["stage"] == "decision"] == [9.84, 9.84]
    assert result["cash"] == 200.0
    assert result["reserved_cash"] == result["total_fees"] == 0.0
    assert result["accepted_trades"] == result["unresolved_holdings"] == result["pending_orders"] == []
    valid, _ = convert([decision()], config)
    verified = replay(
        valid, account_config(config, scenario, "0.05", "verified"), ts("2025-01-02"), ts("2025-01-04")
    )
    assert verified["no_trade_reasons"] == {"signal_unverified_availability": 1}
    assert verified["accepted_trades"] == []
    assert verified["cash"] == 200.0


class RecordingSink:
    """Only the output store is replaced; replay and bootstrap execute normally."""

    def __init__(self):
        self.records = []

    def append(self, kind, key, when, metadata, body):
        self.records.append({"kind": kind, "key": key, "body": body})
        return len(self.records)


def test_zero_growth_selects_cash_without_imputing_an_annual_trading_result(config, tmp_path, monkeypatch):
    settings = deepcopy(config)
    settings["bootstrap_resamples"] = 31  # Synthetic test budget; registered config is never changed.
    accounts = []
    for scenario in settings["scenarios"]:
        account = replay(
            [],
            account_config(settings, scenario, settings["risk_fractions"][0]),
            ts(settings["training_start"]),
            ts(settings["selection_cutoff"]),
        )
        # Match the runner's memory representation, including its integer counts.
        reduced = compact(account)
        reduced["daily"] = [
            {key: row[key] for key in ("date", "cost_basis_log_return", "released_trades")}
            for row in account["daily"]
        ]
        accounts.append(
            {
                "policy": {"id": "synthetic-zero-growth"},
                "risk_fraction": settings["risk_fractions"][0],
                "scenario": scenario["name"],
                "account": reduced,
            }
        )
    monkeypatch.chdir(tmp_path)
    (tmp_path / "reports").mkdir()
    sink = RecordingSink()
    finish(sink, {"id": 42}, settings, accounts, {})
    assert [r["kind"] for r in sink.records] == ["e023_frozen_selection", "experiment_report_gzip"]
    selection = json.loads(sink.records[0]["body"])
    report = json.loads(gzip.decompress(sink.records[1]["body"]))
    assert selection == report["selection"]
    assert selection["action"] == "cash"
    assert selection["eligible_policy_size_pairs"] == 0
    assert selection["policy"] is selection["risk_fraction"] is None
    assert selection["uses_requested_period_outcomes"] is False
    assert selection["rejection_counts"] == {
        "nonpositive_simultaneous_lower_bound": 1,
        "insufficient_release_days": 1,
        "nonpositive_stress_profit": 1,
    }
    assert report["bootstrap"]["active_candidates"] == 0
    assert report["requested_calendar_days"] == 365
    assert report["daily_candidate_panel_days"] == 25
    assert report["missing_daily_candidate_panel_days"] == 340
    assert report["complete_annual_trading_replay"] is False
    assert report["annual_ending_trading_bankroll"] is None
    assert report["cash_only_annual_baseline"]["ending_cash"] == 200.0
    assert report["cash_only_annual_baseline"]["trades"] == 0
    assert report["actual_fills"] == 0
    assert report["holdout_accessed"] is report["profitability_proven"] is False
    assert len(report["available_period_accounts"]) == 4
    for row in report["available_period_accounts"]:
        assert row["account"]["cash"] == row["account"]["cost_basis_equity"] == 200.0
        assert row["account"]["accepted_trades"] == []
    assert json.loads((tmp_path / "reports/E023_bankroll.json").read_text()) == report
    summary = json.loads((tmp_path / "reports/E023_bankroll_summary.json").read_text())
    assert summary["annual_ending_trading_bankroll"] is None
    assert all(row["account"]["accepted_trades"] == 0 for row in summary["available_period_accounts"])
