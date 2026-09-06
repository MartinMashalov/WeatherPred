"""A frozen $200 selection study; missing annual data never becomes earned profit."""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import importlib.metadata
import json
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np

from weatherpred.archive import Archive, canonical
from weatherpred.bankroll_replay import replay
from weatherpred.bankroll_selection import simultaneous_growth_bounds
from weatherpred.timeutil import parse_time, utcnow
from weatherpred.trading_research import candidates

CONFIG = Path("config/e023_bankroll_replay.json")
SOURCES = [
    Path(__file__),
    CONFIG,
    Path("weatherpred/bankroll_replay.py"),
    Path("weatherpred/bankroll_selection.py"),
    Path("weatherpred/trading_research.py"),
    Path("config/e013_autoresearch.json"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
]


def ts(day):
    return int(datetime.combine(date.fromisoformat(day), datetime.min.time(), UTC).timestamp())


def days(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days)]


def digest(body):
    return hashlib.sha256(body).hexdigest()


def convert_decisions(parent, parent_record, scenario, *, decision_start=None, decision_end=None):
    """Preserve failed entry attempts and actual late receipts, without fake depth.

    The earlier study froze the side, limit, entry delay and exit policy. Terminal
    labels are supplied only to the account's release event, never its sizing.
    """
    orders = []
    omissions = Counter()
    for d in parent["decisions"]:
        if not "2025-01-01" <= d["day"] < "2025-10-01":
            raise ValueError("Protected or unregistered date in parent decision ledger")
        # Gate on timestamps before consulting side, entry, or terminal fields.
        if decision_start is not None and d["signal_ts"] < decision_start:
            continue
        if decision_end is not None and d["signal_ts"] >= decision_end:
            continue
        if d["status"] in ("no_signal", "outside_market_hours"):
            omissions[d["status"]] += 1
            continue
        status = d["status"]
        if status not in (
            "conditional_trade",
            "entry_limit_not_met",
            "missing_entry_quote",
            "missing_or_invalid_settlement_time",
        ):
            raise ValueError("Unreviewed parent decision status: " + status)
        limit = d.get("entry_limit", min(0.99, round(d["signal_ask"] + float(scenario["slippage"]), 8)))
        terminal = status == "conditional_trade"
        quoted_exit = terminal and d["exit_kind"] == "scheduled_quote"
        receipt_ts = parse_time(parent_record["available_at"]).timestamp()
        provenance = {
            "parent_decision_record_id": parent_record["id"],
            "parent_decision_sha256": digest(canonical(d).encode()),
            "available_ts": receipt_ts,
            "availability_verified": False,
            "receipt_meaning": "creation of the archived parent decision ledger, not a historical quote receipt",
            "availability_assumption": "candle endpoint available at its endpoint; unverified",
        }
        orders.append(
            {
                "trade_id": f"{d['event']}:{d['ticker']}:{d['side']}:{d['signal_ts']}",
                "event": d["event"],
                "cluster": "all_weather",
                "side": d["side"],
                "decision_ts": d["signal_ts"],
                "entry_ts": d["entry_ts"],
                "limit_price": str(limit),
                "entry_price": str(d["entry_price"]) if d.get("entry_price") is not None else None,
                "available_quantity": None,
                "exit_ts": d["exit_ts"] if terminal else None,
                "terminal_cash_per_contract": str(d["exit_price"]) if terminal else None,
                "terminal_kind": "sale" if quoted_exit else "settlement",
                "outcome_available_ts": d["exit_ts"] if terminal else None,
                "signal_provenance": dict(provenance, assumed_available_ts=d["signal_ts"]),
                "entry_price_provenance": dict(provenance, assumed_available_ts=d["entry_ts"]),
                "terminal_provenance": dict(provenance, assumed_available_ts=d.get("exit_ts")),
            }
        )
    return orders, dict(omissions)


def account_config(config, scenario, fraction, mode="hypothetical"):
    return {
        "initial_cash": config["initial_cash"],
        "risk_fraction": fraction,
        "max_event_fraction": config["max_event_fraction"],
        "max_cluster_fraction": config["max_cluster_fraction"],
        "max_total_fraction": config["max_total_fraction"],
        "drawdown_kill_fraction": config["drawdown_kill_fraction"],
        "mode": mode,
        "conditional_depth_cap": config["conditional_depth_cap"],
        "entry_coefficient": scenario["entry_coefficient"],
        "exit_coefficient": scenario["exit_coefficient"],
    }


def compact(account):
    if isinstance(account["accepted_trades"], int):
        return {k: v for k, v in account.items() if k != "daily"}
    return {
        **{
            k: v
            for k, v in account.items()
            if k not in ("orders", "daily", "accepted_trades", "closed_trades")
        },
        "accepted_trades": len(account["accepted_trades"]),
        "closed_trades": len(account["closed_trades"]),
        "released_event_days": sum(r["released_trades"] > 0 for r in account["daily"]),
        "contracts_entered": sum(r["quantity"] for r in account["accepted_trades"]),
        "maximum_order_quantity": max((r["quantity"] for r in account["accepted_trades"]), default=0),
    }


def run(args):
    archive = Archive()
    try:
        config = json.loads(CONFIG.read_text())
        parent_config = json.loads(Path(config["parent_policy_config"]).read_text())
        parent_registration = archive.db.execute(
            "SELECT * FROM records WHERE id=?", (config["parent_registration"],)
        ).fetchone()
        if parent_registration is None or parent_registration["kind"] != "experiment_protocol":
            raise ValueError("Missing parent registration")
        if archive.json(parent_registration)["config"] != parent_config:
            raise ValueError("Parent policy config differs from its original registration")
        for scenario in config["scenarios"]:
            original = next(s for s in parent_config["scenarios"] if s["name"] == scenario["name"])
            if original["entry_delay_hours"] != scenario["entry_delay_hours"] or Decimal(
                original["slippage"]
            ) != Decimal(scenario["slippage"]):
                raise ValueError("Parent entry timing or slippage changed")
        policies = candidates(parent_config)
        if len(policies) != config["policy_count"]:
            raise ValueError("Parent candidate family changed")
        hashes = {str(p): digest(p.read_bytes()) for p in SOURCES}
        if args.run_record_id:
            registration = archive.db.execute(
                "SELECT * FROM records WHERE id=?", (args.run_record_id,)
            ).fetchone()
            protocol = archive.json(registration)
            if registration["kind"] != "experiment_protocol" or protocol["source_hashes"] != hashes:
                raise ValueError("Registered source/config changed")
            if protocol["config"] != config:
                raise ValueError("Registered config changed")
        else:
            source_ids = {
                str(p): archive.append("research_source", str(p), utcnow(), {}, p.read_bytes())
                for p in SOURCES
            }
            parent_records = {}
            for policy in policies:
                for scenario in config["scenarios"]:
                    key = f"{config['parent_registration']}:{policy['id']}:{scenario['name']}"
                    rec = archive.latest("autoresearch_candidate", key)
                    if rec is None:
                        raise ValueError("Incomplete parent decision ledger")
                    parent_records[key] = {"id": rec["id"], "body_sha256": rec["body_sha256"]}
            protocol = {
                "config": config,
                "source_hashes": hashes,
                "source_record_ids": source_ids,
                "parent_records": parent_records,
                "policy_count": len(policies),
                "training_accounts": len(policies) * len(config["risk_fractions"]) * len(config["scenarios"]),
                "parent_registration_sha256": parent_registration["record_sha256"],
                "runtime_versions": {
                    name: importlib.metadata.version(name) for name in ("numpy", "weatherpred")
                },
                "holdout_accessed": False,
                "promotion_eligible": False,
            }
            identifier = archive.append(
                "experiment_protocol", config["experiment"], utcnow(), {}, canonical(protocol).encode()
            )
            registration = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
        print(
            json.dumps(
                {
                    "registration": registration["id"],
                    "training_accounts": protocol["training_accounts"],
                    "registered_before_scores": True,
                }
            ),
            flush=True,
        )
        if args.register_only:
            return
        accounts, orders_by_key = [], {}
        for policy in policies:
            for scenario in config["scenarios"]:
                parent_key = f"{config['parent_registration']}:{policy['id']}:{scenario['name']}"
                pinned = protocol["parent_records"][parent_key]
                rec = archive.db.execute("SELECT * FROM records WHERE id=?", (pinned["id"],)).fetchone()
                body = archive.body(rec)
                if digest(body) != pinned["body_sha256"]:
                    raise ValueError("Parent body hash changed")
                parent = json.loads(body)
                if parent["policy"] != policy or parent["scenario"] != scenario["name"]:
                    raise ValueError("Parent identity mismatch")
                training, omissions = convert_decisions(
                    parent,
                    rec,
                    scenario,
                    decision_start=ts(config["training_start"]),
                    decision_end=ts(config["training_decision_end_exclusive"]),
                )
                orders_by_key[(policy["id"], scenario["name"])] = dict(rec)
                for fraction in config["risk_fractions"]:
                    if Path(config["stop_file"]).exists():
                        print(json.dumps({"stopped": True, "completed": len(accounts)}), flush=True)
                        return
                    key = f"{registration['id']}:{policy['id']}:{scenario['name']}:{fraction}"
                    cached = archive.latest("e023_training_account_gzip", key)
                    if cached:
                        result = json.loads(gzip.decompress(archive.body(cached)))
                    else:
                        account = replay(
                            training,
                            account_config(config, scenario, fraction),
                            ts(config["training_start"]),
                            ts(config["selection_cutoff"]),
                        )
                        result = {
                            "policy": policy,
                            "scenario": scenario["name"],
                            "risk_fraction": fraction,
                            "parent_record_id": rec["id"],
                            "parent_status_omissions": omissions,
                            "account": account,
                        }
                        archive.append(
                            "e023_training_account_gzip",
                            key,
                            utcnow(),
                            {},
                            gzip.compress(canonical(result).encode(), mtime=0),
                        )
                    full = result["account"]
                    result["account"] = compact(full)
                    result["account"]["daily"] = [
                        {k: d[k] for k in ("date", "cost_basis_log_return", "released_trades")}
                        for d in full["daily"]
                    ]
                    accounts.append(result)
            if len(accounts) % 192 == 0:
                print(json.dumps({"training_accounts_completed": len(accounts)}), flush=True)
        finish(archive, registration, config, accounts, orders_by_key)
    finally:
        archive.close()


def finish(archive, registration, config, accounts, orders_by_key):
    expected_days = days(config["training_start"], config["selection_cutoff"])
    for row in accounts:
        if [d["date"] for d in row["account"]["daily"]] != expected_days:
            raise ValueError("Candidate training calendars differ")
    growth = np.asarray(
        [[d["cost_basis_log_return"] for d in r["account"]["daily"]] for r in accounts], dtype=float
    ).T
    if not np.all(np.isfinite(growth)):
        raise ValueError("Nonfinite growth cannot enter candidate selection")
    bounds = simultaneous_growth_bounds(
        growth,
        block_days=config["bootstrap_block_days"],
        resamples=config["bootstrap_resamples"],
        seed=config["bootstrap_seed"],
        alpha=config["familywise_alpha"],
    )
    stress = {(r["policy"]["id"], r["risk_fraction"]): r for r in accounts if r["scenario"] == "stress"}
    eligible, scored, rejected = [], [], Counter()
    for index, row in enumerate(accounts):
        account = row["account"]
        lower = bounds["lower_bounds"][index]
        statistics = {
            "mean_daily_log_growth": bounds["means"][index],
            "bootstrap_standard_error": bounds["standard_errors"][index],
            "simultaneous_lower_daily_log_growth": lower,
            "nondegenerate_bootstrap": bounds["eligible"][index],
        }
        card = {
            **{k: v for k, v in row.items() if k != "account"},
            "account": compact(account),
            "training_statistics": statistics,
        }
        scored.append(card)
        if row["scenario"] != "costed":
            continue
        stressed = stress[(row["policy"]["id"], row["risk_fraction"])]["account"]
        reasons = []
        if lower is None or lower <= 0 or not bounds["eligible"][index]:
            reasons.append("nonpositive_simultaneous_lower_bound")
        if sum(d["released_trades"] > 0 for d in account["daily"]) < config["minimum_selection_release_days"]:
            reasons.append("insufficient_release_days")
        if Decimal(str(stressed["cost_basis_equity"])) <= Decimal(config["initial_cash"]):
            reasons.append("nonpositive_stress_profit")
        if any(a["unresolved_holdings"] or a["pending_orders"] for a in (account, stressed)):
            reasons.append("unresolved_training_cash_flows")
        card["selection_rejections"] = reasons
        rejected.update(reasons)
        if not reasons:
            eligible.append((lower, row["policy"]["id"], Decimal(row["risk_fraction"]), row))
    eligible.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected = eligible[0][3] if eligible else None
    selection = {
        "action": "trade_frozen_policy" if selected else "cash",
        "policy": selected["policy"] if selected else None,
        "risk_fraction": selected["risk_fraction"] if selected else None,
        "eligible_policy_size_pairs": len(eligible),
        "rejection_counts": dict(rejected),
        "cutoff": config["selection_cutoff"],
        "uses_requested_period_outcomes": False,
        "prior_data_reused_in_earlier_research": True,
        "independent_profitability_proven": False,
    }
    # Record the choice before consulting the requested-period account result.
    selected_id = archive.append(
        "e023_frozen_selection", str(registration["id"]), utcnow(), {}, canonical(selection).encode()
    )
    panel_start, panel_end = config["requested_start"], config["available_panel_end_exclusive"]
    result_accounts = []
    for scenario in config["scenarios"]:
        if selected:
            parent_record = orders_by_key[(selected["policy"]["id"], scenario["name"])]
            orders, _ = convert_decisions(
                archive.json(parent_record),
                parent_record,
                scenario,
                decision_start=ts(panel_start),
                decision_end=ts(panel_end),
            )
        else:
            orders = []
        orders = [o for o in orders if ts(panel_start) <= o["decision_ts"] < ts(panel_end)]
        fraction = selected["risk_fraction"] if selected else config["risk_fractions"][0]
        for mode in ("hypothetical", "verified"):
            result = replay(
                orders, account_config(config, scenario, fraction, mode), ts(panel_start), ts(panel_end)
            )
            result_accounts.append({"scenario": scenario["name"], "mode": mode, "account": result})
    requested_days = days(config["requested_start"], config["requested_end_exclusive"])
    covered_days = days(panel_start, panel_end)
    missing_days = [day for day in requested_days if day not in covered_days]
    cash_baseline = replay(
        [],
        account_config(config, config["scenarios"][0], config["risk_fractions"][0], "verified"),
        ts(config["requested_start"]),
        ts(config["requested_end_exclusive"]),
    )
    if len(cash_baseline["daily"]) != len(requested_days):
        raise ValueError("Cash-only baseline has an incomplete calendar")
    report = {
        "experiment": config["experiment"],
        "protocol_record_id": registration["id"],
        "selection_record_id": selected_id,
        "initial_cash": float(Decimal(config["initial_cash"])),
        "requested_period": {
            "start": config["requested_start"],
            "end_exclusive": config["requested_end_exclusive"],
        },
        "requested_calendar_days": len(requested_days),
        "daily_candidate_panel_days": len(covered_days),
        "daily_candidate_panel_dates": covered_days,
        "missing_daily_candidate_panel_days": len(missing_days),
        "missing_daily_candidate_panel_dates": missing_days,
        "complete_annual_trading_replay": False,
        "annual_ending_trading_bankroll": None,
        "cash_only_annual_baseline": {
            "ending_cash": cash_baseline["cash"],
            "trades": 0,
            "calendar_days_replayed": len(cash_baseline["daily"]),
            "total_fees": cash_baseline["total_fees"],
            "maximum_drawdown": cash_baseline["max_cost_basis_drawdown"],
            "assumptions": "No interest, account costs or external cash flows",
        },
        "selection": selection,
        "training_accounts": len(accounts),
        "training_results": scored,
        "bootstrap": {
            k: v
            for k, v in bounds.items()
            if k not in ("means", "standard_errors", "lower_bounds", "eligible")
        },
        "available_period_accounts": result_accounts,
        "actual_fills": 0,
        "holdout_accessed": False,
        "profitability_proven": False,
        "limitations": [config[k] for k in ("fees", "execution", "calendar_coverage", "holdout", "reporting")]
        + [
            "The September2025 panel was already examined in earlier development research; this is not an untouched evaluation.",
            "The bootstrap is a within-batch selection diagnostic. It does not undo previous search or satisfy global final/forward promotion gates.",
            "Same-time ordering is a deterministic portfolio convention; absent historical exchange priority, it is an additional execution assumption.",
        ],
    }
    body = canonical(report).encode()
    report_id = archive.append(
        "experiment_report_gzip", "E023_bankroll", utcnow(), {}, gzip.compress(body, mtime=0)
    )
    Path("reports/E023_bankroll.json").write_text(json.dumps(report, indent=2))
    summary = {
        k: v
        for k, v in report.items()
        if k
        not in (
            "training_results",
            "available_period_accounts",
            "daily_candidate_panel_dates",
            "missing_daily_candidate_panel_dates",
        )
    }
    summary["available_period_accounts"] = [
        {**{k: v for k, v in a.items() if k != "account"}, "account": compact(a["account"])}
        for a in result_accounts
    ]
    summary["report_record_id"] = report_id
    Path("reports/E023_bankroll_summary.json").write_text(json.dumps(summary, indent=2))
    print(
        json.dumps(
            {
                "report_record_id": report_id,
                "training_accounts": len(accounts),
                "selection": selection,
                "available_period": [
                    {
                        "scenario": a["scenario"],
                        "mode": a["mode"],
                        "cash": a["account"]["cash"],
                        "cost_basis_equity": a["account"]["cost_basis_equity"],
                    }
                    for a in result_accounts
                ],
                "annual_ending_trading_bankroll": None,
            }
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int)
    parser.add_argument("--register-only", action="store_true")
    args = parser.parse_args()
    with Path("data/E023.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args)


if __name__ == "__main__":
    main()
