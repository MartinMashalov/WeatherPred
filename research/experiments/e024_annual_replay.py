"""Three registered walk-forward choices and one continuous conditional account.

Imports have no data access or scoring side effects. Actual execution requires a
complete, source-pinned dataset and a separately reviewed registration. The
sealed 2025 quarter is represented only by the policy's mandatory cash prefix.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import importlib.metadata
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path

import numpy as np

from research.probes.bankroll_dataset import ArchiveReader, build_dataset, expected_events
from weatherpred.archive import Archive, canonical
from weatherpred.bankroll_replay import replay
from weatherpred.bankroll_selection import simultaneous_growth_bounds
from weatherpred.scheduled_bankroll import replay_scheduled
from weatherpred.timeutil import parse_time, utcnow
from weatherpred.trading_research import candidates, select_trade

CONFIG = Path("config/e024_annual_replay.json")
DAY = 86400
FIRST = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp())
LAST = int(datetime(2026, 9, 6, tzinfo=UTC).timestamp())
SOURCES = [
    Path(__file__),
    CONFIG,
    Path("research/probes/bankroll_dataset.py"),
    Path("research/probes/bankroll_acquisition_amendment.py"),
    Path("research/probes/bankroll_acquisition.py"),
    Path("tests/test_bankroll_acquisition_amendment.py"),
    Path("tests/test_bankroll_amendment_integration.py"),
    Path("weatherpred/http.py"),
    Path("weatherpred/scheduled_bankroll.py"),
    Path("weatherpred/bankroll_replay.py"),
    Path("weatherpred/bankroll_selection.py"),
    Path("weatherpred/trading_research.py"),
    Path("weatherpred/fees.py"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/contracts.py"),
    Path("weatherpred/timeutil.py"),
    Path("config/e002_source_windows.json"),
    Path("config/e013_autoresearch.json"),
    Path("config/validation.json"),
    Path("research/experiments/e024_account_audit.py"),
    Path("research/experiments/e023_audit.py"),
]


def ts(day):
    return int(datetime.combine(date.fromisoformat(day), datetime.min.time(), UTC).timestamp())


def calendar(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days)]


def sha(body):
    return hashlib.sha256(body).hexdigest()


def config_check(config, policies):
    if len(policies) != config["policy_count"] or len({p["id"] for p in policies}) != len(policies):
        raise ValueError("Registered policy family changed")
    if config["selection_dates"] != ["2026-07-01", "2026-08-01", "2026-09-01"]:
        raise ValueError("Selection dates cannot be changed")
    if config["bootstrap_block_days"] != [1, 7, 14] or config["bootstrap_resamples"] != 10000:
        raise ValueError("Bootstrap protocol differs from the fixed continuation")
    if config["risk_fractions"] != ["0.005", "0.01", "0.025", "0.05"]:
        raise ValueError("Risk fraction family changed")
    if (
        len(policies) * len(config["risk_fractions"]) * len(config["scenarios"]) * 3
        != config["max_training_evaluations"]
    ):
        raise ValueError("Finite trial budget differs from the registered family")
    if (
        config["requested_start"] != "2025-09-06"
        or config["requested_end_exclusive"] != "2026-09-06"
        or config["training_start"] != "2026-01-01"
    ):
        raise ValueError("Registered account and training windows changed")


def dataset_gate(dataset, config):
    if not dataset.get("complete_event_census") or dataset.get("events") != config["expected_events"]:
        raise ValueError("Incomplete event census cannot enter a trading experiment")
    if (
        dataset.get("acquisition_protocol_record_id") != config["acquisition_protocol_id"]
        or dataset.get("event_manifest_record_id") != config["event_manifest_id"]
    ):
        raise ValueError("Dataset has a different acquisition lineage")
    if dataset.get("acquisition_amendment_protocol_ids", []) != config.get(
        "acquisition_amendment_protocol_ids", []
    ):
        raise ValueError("Dataset acquisition amendments differ from the registered family")
    events = set()
    tickers = set()
    for market in dataset["markets"]:
        metadata = market["metadata"]
        if not "2026-01-01" <= metadata["day"] < "2026-09-06":
            raise ValueError("Protected or unregistered event reached E024")
        if metadata["ticker"] in tickers:
            raise ValueError("Duplicate contract in the frozen dataset")
        tickers.add(metadata["ticker"])
        events.add(metadata["event"])
    if events != expected_events() or len(tickers) != dataset["contracts"]:
        raise ValueError("Normalized market census is incomplete")


def provenance(parts, assumed_at):
    if not parts:
        raise ValueError("An input requires raw source provenance")
    return {
        "available_ts": max(parse_time(part["available_at"]).timestamp() for part in parts),
        "availability_verified": False,
        "assumed_available_ts": assumed_at,
        "source_parts": parts,
        "availability_basis": "Actual late source receipt preserved; historical endpoint availability is assumed and unverified.",
    }


def exact_quote(market, when, end_exclusive):
    """Gate the requested timestamp before accessing either price or its receipt."""
    if not FIRST < when < min(end_exclusive, LAST) or when % 3600:
        return None, None
    quotes = market["quotes"]
    value = quotes.get(when) if when in quotes else quotes.get(str(when))
    if value is None or value.get("bid") is None or value.get("ask") is None:
        return None, None
    bid, ask = Decimal(value["bid"]), Decimal(value["ask"])
    if not bid.is_finite() or not ask.is_finite() or not 0 < bid < ask < 1:
        return None, None
    sources = market["quote_provenance"]
    source = sources[when] if when in sources else sources[str(when)]
    return {"bid": float(bid), "ask": float(ask)}, source


def generate_intents(markets, policy, scenario, decision_start, decision_end, evaluation_end):
    """Use only the passed decision window and as-of execution information.

    Signal-only fields feed the unchanged fixed-family selector. Future entry
    failures never remove the original intent; unavailable terminal values leave
    valid entries unresolved. Recreating an old intent at a later evaluation end
    can reveal its later fill/exit without changing its immutable decision hash.
    """
    if not FIRST <= decision_start < decision_end <= evaluation_end <= LAST:
        raise ValueError("Intent windows must remain inside unsealed 2026")
    grouped, omissions = defaultdict(list), Counter()
    for market in markets:
        metadata = market["metadata"]
        day = metadata["day"]
        if not "2026-01-01" <= day < "2026-09-06":
            raise ValueError("Protected event date reached the decision generator")
        # Conservative calendar gate needs no quote, source-window, or label value.
        if ts(day) >= decision_end or ts(day) + 2 * DAY <= decision_start:
            omissions["outside_calendar_window"] += 1
            continue
        grouped[metadata["event"]].append(market)
    orders, decisions = [], []
    slip = float(scenario["slippage"])
    for event, members in sorted(grouped.items()):
        periods = {m["metadata"]["source_period_end"] for m in members}
        context = {"event": event, "day": members[0]["metadata"]["day"], "policy_id": policy["id"]}
        if None in periods or len(periods) != 1:
            decisions.append({**context, "status": "unsupported_or_inconsistent_source_window"})
            continue
        decision = int(parse_time(next(iter(periods))).timestamp()) - policy["horizon_hours"] * 3600
        context["decision_ts"] = decision
        if not decision_start <= decision < decision_end:
            decisions.append({**context, "status": "outside_decision_window"})
            continue
        signal_rows, signal_sources = [], []
        by_ticker = {}
        for market in sorted(members, key=lambda m: m["metadata"]["ticker"]):
            metadata = market["metadata"]
            if not metadata["open_ts"] <= decision < metadata["close_ts"]:
                continue
            current, source = exact_quote(market, decision, decision + 1)
            if current is None:
                continue
            past, past_source = exact_quote(market, decision - policy["lookback_hours"] * 3600, decision + 1)
            signal_rows.append(
                {
                    "ticker": metadata["ticker"],
                    **current,
                    "past_bid": past["bid"] if past else None,
                    "past_ask": past["ask"] if past else None,
                }
            )
            signal_sources.extend([metadata["metadata_provenance"], source])
            if past_source:
                signal_sources.append(past_source)
            by_ticker[metadata["ticker"]] = market
        selection = select_trade(signal_rows, policy)
        if selection is None:
            decisions.append({**context, "status": "no_signal", "available_contracts": len(signal_rows)})
            continue
        chosen = by_ticker[selection["ticker"]]
        metadata, side = chosen["metadata"], selection["side"]
        entry = decision + scenario["entry_delay_hours"] * 3600
        if not metadata["open_ts"] <= decision < entry < metadata["close_ts"]:
            decisions.append({**context, **selection, "entry_ts": entry, "status": "outside_market_hours"})
            continue
        planned_exit = entry + policy["exit_hours"] * 3600 if policy["exit_hours"] is not None else None
        intent = {
            "trade_id": f"{policy['id']}:{event}:{selection['ticker']}:{side}:{decision}",
            "policy_id": policy["id"],
            "event": event,
            "cluster": "all_weather",
            "side": side,
            "decision_ts": decision,
            "entry_ts": entry,
            "limit_price": str(min(0.99, round(selection["signal_ask"] + slip, 8))),
            "planned_exit_ts": planned_exit,
            "signal_provenance": provenance(signal_sources, decision),
        }
        intent["decision_sha256"] = sha(canonical(intent).encode())
        intent.update(
            entry_price=None,
            available_quantity=None,
            entry_price_provenance=None,
            exit_ts=None,
            terminal_cash_per_contract=None,
            terminal_kind=None,
            outcome_available_ts=None,
            terminal_provenance=None,
        )
        quote, source = exact_quote(chosen, entry, evaluation_end)
        if quote is None:
            status = "entry_not_yet_observable" if entry >= evaluation_end else "missing_entry_quote"
        else:
            entry_price = round((quote["ask"] if side == "yes" else 1 - quote["bid"]) + slip, 8)
            intent.update(entry_price=str(entry_price), entry_price_provenance=provenance([source], entry))
            if not 0 < entry_price < 1 or entry_price > float(intent["limit_price"]):
                status = "entry_limit_not_met"
            else:
                status = "entry_with_unresolved_terminal"
                settled_at = metadata.get("settled_ts")
                # Read the terminal timestamp, but not its outcome, before its release gate.
                known_settlement = (
                    settled_at if settled_at is not None and entry < settled_at < evaluation_end else None
                )
                exit_quote = exit_source = None
                if (
                    planned_exit is not None
                    and planned_exit < metadata["close_ts"]
                    and (known_settlement is None or planned_exit < known_settlement)
                ):
                    exit_quote, exit_source = exact_quote(chosen, planned_exit, evaluation_end)
                if exit_quote:
                    price = round((exit_quote["bid"] if side == "yes" else 1 - exit_quote["ask"]) - slip, 8)
                    if price > 0:
                        intent.update(
                            exit_ts=planned_exit,
                            terminal_cash_per_contract=str(price),
                            terminal_kind="sale",
                            outcome_available_ts=planned_exit,
                            terminal_provenance=provenance([exit_source], planned_exit),
                        )
                        status = "conditional_quoted_exit"
                if intent["exit_ts"] is None and known_settlement is not None:
                    outcome = metadata.get("outcome")
                    if outcome not in (0, 1) or isinstance(outcome, bool):
                        raise ValueError("A released settlement requires an exact binary outcome")
                    intent.update(
                        exit_ts=known_settlement,
                        terminal_cash_per_contract=str(outcome if side == "yes" else 1 - outcome),
                        terminal_kind="settlement",
                        outcome_available_ts=known_settlement,
                        terminal_provenance=provenance([metadata["metadata_provenance"]], known_settlement),
                    )
                    status = "conditional_settlement"
        orders.append(intent)
        decisions.append(
            {
                **context,
                **selection,
                "trade_id": intent["trade_id"],
                "status": status,
                "decision_sha256": intent["decision_sha256"],
            }
        )
    return {
        "orders": orders,
        "decisions": decisions,
        "status_counts": dict(Counter(d["status"] for d in decisions)),
        "omissions": dict(omissions),
        "decision_window": [decision_start, decision_end],
        "evaluation_end_exclusive": evaluation_end,
        "actual_fills": 0,
    }


def account_config(config, scenario, fraction, mode="hypothetical", policies=None):
    result = {
        key: config[key]
        for key in (
            "initial_cash",
            "max_event_fraction",
            "max_cluster_fraction",
            "max_total_fraction",
            "drawdown_kill_fraction",
            "conditional_depth_cap",
        )
    }
    result.update(
        risk_fraction=fraction,
        mode=mode,
        entry_coefficient=scenario["entry_coefficient"],
        exit_coefficient=scenario["exit_coefficient"],
    )
    if policies is not None:
        result.update(
            allowed_policy_ids=[policy["id"] for policy in policies],
            allowed_risk_fractions=config["risk_fractions"],
        )
    return result


def assert_prefix(previous, current):
    cutoff = previous["end_ts"]
    if previous["start_ts"] != current["start_ts"] or cutoff >= current["end_ts"]:
        raise ValueError("Account prefix windows are inconsistent")
    if previous["daily"] != [day for day in current["daily"] if day["timestamp"] <= cutoff]:
        raise ValueError("A later reconstruction changed an earlier daily account state")
    if previous["orders"] != [order for order in current["orders"] if order["timestamp"] < cutoff]:
        raise ValueError("A later reconstruction changed an earlier order journal entry")


def select_from_bounds(accounts, bounds, config):
    expected = len(accounts)
    if set(bounds) != {str(length) for length in config["bootstrap_block_days"]} or any(
        b["n_candidates"] != expected for b in bounds.values()
    ):
        raise ValueError("Every bootstrap must retain the entire candidate/scenario family")
    mapping = {
        (row["policy"]["id"], row["risk_fraction"], row["scenario"]): (index, row)
        for index, row in enumerate(accounts)
    }
    if len(mapping) != len(accounts):
        raise ValueError("Duplicate training account identity")
    accepted, rejected, details = [], Counter(), []
    for (policy_id, fraction, scenario), (index, row) in sorted(mapping.items()):
        if scenario != "costed":
            continue
        stress_index, stressed = mapping[(policy_id, fraction, "stress")]
        reasons = []
        for current_index, current in ((index, row), (stress_index, stressed)):
            account = current["account"]
            if len(account["daily"]) < config["minimum_training_calendar_days"]:
                reasons.append("insufficient_training_calendar")
            if (
                sum(day["released_trades"] > 0 for day in account["daily"])
                < config["minimum_release_days_per_scenario"]
            ):
                reasons.append("insufficient_release_days")
            if account["pending_orders"] or account["unresolved_holdings"]:
                reasons.append("unresolved_training_cash_flows")
            if Decimal(str(account["cash"])) <= Decimal(config["initial_cash"]):
                reasons.append("nonpositive_cash_profit")
            for block, result in bounds.items():
                lower = result["lower_bounds"][current_index]
                if not result["eligible"][current_index] or lower is None or lower <= 0:
                    reasons.append(f"nonpositive_or_degenerate_bound_{block}d")
        reasons = sorted(set(reasons))
        details.append({"policy_id": policy_id, "risk_fraction": fraction, "rejections": reasons})
        rejected.update(reasons)
        if not reasons:
            accepted.append((bounds["7"]["lower_bounds"][index], policy_id, Decimal(fraction), row))
    accepted.sort(key=lambda item: (-item[0], item[1], item[2]))
    selected = accepted[0][3] if accepted else None
    return {
        "action": "trade_frozen_policy" if selected else "cash",
        "policy": selected["policy"] if selected else None,
        "risk_fraction": selected["risk_fraction"] if selected else None,
        "eligible_policy_size_pairs": len(accepted),
        "rejection_counts": dict(rejected),
        "pair_rejections": details,
        "future_period_outcomes_used": False,
        "profitability_proven": False,
    }


class RequestedStop(Exception):
    """A clean stop between immutable units may resume under the same deadline."""


def check_budget(config, deadline):
    if Path(config["stop_file"]).exists():
        raise RequestedStop("Registered stop file is present")
    if time.time() >= deadline:
        raise TimeoutError("The original 90-minute execution deadline expired")
    if shutil.disk_usage(".").free < config["minimum_free_disk_bytes"]:
        raise RuntimeError("Free disk fell below the registered minimum")


def read_json_record(archive, record, compressed=False):
    fields = [
        record[key] for key in ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")
    ]
    if sha(canonical(fields).encode()) != record["record_sha256"]:
        raise ValueError("Archived artifact record hash changed")
    previous = archive.db.execute(
        "SELECT record_sha256 FROM records WHERE id<? ORDER BY id DESC LIMIT 1", (record["id"],)
    ).fetchone()
    if record["previous_sha256"] != (previous[0] if previous else "0" * 64):
        raise ValueError("Archived artifact predecessor link changed")
    body = archive.body(record)
    if sha(body) != record["body_sha256"]:
        raise ValueError("Archived artifact body hash changed")
    return json.loads(gzip.decompress(body) if compressed else body)


def unit(archive, registration, kind, key, operation, config, deadline):
    """Resume completed units by hash; any incomplete prior attempt fails closed."""
    check_budget(config, deadline)
    name = f"{registration}:{key}"
    previous = archive.latest(kind, name)
    if previous:
        return previous, read_json_record(archive, previous, compressed=True)
    attempt_key = f"{kind}:{name}"
    if archive.latest("e024_unit_started", attempt_key):
        raise RuntimeError(f"Incomplete/failed unit cannot silently retry: {attempt_key}")
    archive.append(
        "e024_unit_started", attempt_key, utcnow(), {}, canonical({"kind": kind, "key": name}).encode()
    )
    try:
        value = operation()
        identifier = archive.append(
            kind, name, utcnow(), {}, gzip.compress(canonical(value).encode(), mtime=0)
        )
    except BaseException as error:
        archive.append(
            "e024_unit_failed",
            attempt_key,
            utcnow(),
            {},
            canonical({"error": f"{type(error).__name__}: {error}", "automatic_retry": False}).encode(),
        )
        raise
    return archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone(), value


def source_hashes():
    return {str(path): sha(path.read_bytes()) for path in SOURCES}


def prepare_dataset(archive, config, cutoff_id, cutoff_at):
    hashes = source_hashes()
    declaration = {
        "config": config,
        "source_hashes": hashes,
        "source_cutoff_id": cutoff_id,
        "source_cutoff_at": cutoff_at,
        "strategy_scores_authorized": False,
    }
    identifier = archive.append(
        "e024_dataset_preparation_protocol",
        config["experiment"],
        utcnow(),
        {},
        canonical(declaration).encode(),
    )
    reader = ArchiveReader(archive.root, cutoff_id, cutoff_at)
    try:
        dataset = build_dataset(
            reader,
            json.loads(Path(config["source_windows_path"]).read_bytes()),
            config.get("acquisition_amendment_protocol_ids", []),
        )
    finally:
        reader.close()
    dataset_gate(dataset, config)
    if hashes != source_hashes():
        raise ValueError("Dataset preparation sources changed while reading inputs")
    dataset["preparation_protocol_id"] = identifier
    dataset["preparation_source_hashes"] = hashes
    return archive.append(
        "e024_dataset_gzip",
        str(identifier),
        utcnow(),
        {},
        gzip.compress(canonical(dataset).encode(), mtime=0),
    )


def register(archive, dataset_id):
    config = json.loads(CONFIG.read_bytes())
    policies = candidates(json.loads(Path(config["parent_policy_config"]).read_bytes()))
    config_check(config, policies)
    record = archive.db.execute("SELECT * FROM records WHERE id=?", (dataset_id,)).fetchone()
    if record is None or record["kind"] != "e024_dataset_gzip":
        raise ValueError("A completed separately prepared dataset record is required")
    dataset = read_json_record(archive, record, True)
    dataset_gate(dataset, config)
    hashes = source_hashes()
    if dataset["preparation_source_hashes"] != hashes:
        raise ValueError(
            "Source files changed after dataset preparation; review a new preparation before registration"
        )
    sources = {
        str(path): archive.append("research_source", str(path), utcnow(), {}, path.read_bytes())
        for path in SOURCES
    }
    protocol = {
        "config": config,
        "policies": policies,
        "source_hashes": hashes,
        "source_record_ids": sources,
        "dataset_record_id": dataset_id,
        "dataset_body_sha256": record["body_sha256"],
        "dataset_source_records": dataset["source_records"],
        "source_cutoff_id": dataset["source_cutoff_id"],
        "source_cutoff_at": dataset["source_cutoff_at"],
        "registered_before_strategy_scores": True,
        "python_executable": sys.executable,
        "working_directory": str(Path.cwd()),
        "runtime_versions": {name: importlib.metadata.version(name) for name in ("numpy", "weatherpred")},
        "training_accounts_per_look": len(policies)
        * len(config["risk_fractions"])
        * len(config["scenarios"]),
        "maximum_training_accounts": config["max_training_evaluations"],
        "promotion_eligible": False,
    }
    return archive.append(
        "experiment_protocol", config["experiment"], utcnow(), {}, canonical(protocol).encode()
    )


def load_registration(archive, identifier, load_dataset=True):
    record = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
    if record is None or record["kind"] != "experiment_protocol":
        raise ValueError("An E024 registration is required")
    protocol = read_json_record(archive, record)
    if protocol.get("config", {}).get("experiment") != "E024-restricted-annual-walk-forward-v1":
        raise ValueError("Wrong experiment registration")
    if protocol["source_hashes"] != source_hashes() or protocol["config"] != json.loads(CONFIG.read_bytes()):
        raise ValueError("Registered source/config bytes changed")
    if (
        protocol["python_executable"] != sys.executable
        or protocol["working_directory"] != str(Path.cwd())
        or protocol["runtime_versions"]
        != {name: importlib.metadata.version(name) for name in ("numpy", "weatherpred")}
    ):
        raise ValueError("Registered execution environment changed")
    config_check(protocol["config"], protocol["policies"])
    if not load_dataset:
        return protocol, None
    dataset_record = archive.db.execute(
        "SELECT * FROM records WHERE id=?", (protocol["dataset_record_id"],)
    ).fetchone()
    if dataset_record is None or dataset_record["kind"] != "e024_dataset_gzip":
        raise ValueError("Registered normalized dataset is missing")
    if dataset_record["body_sha256"] != protocol["dataset_body_sha256"]:
        raise ValueError("Registered dataset reference changed")
    dataset = read_json_record(archive, dataset_record, True)
    dataset_gate(dataset, protocol["config"])
    if dataset["source_records"] != protocol["dataset_source_records"]:
        raise ValueError("Registered transitive source list changed")
    # Verify all referenced bytes/links within the safe acquisition scope; never
    # traverse arbitrary archive bodies or the protected quarter.
    reader = ArchiveReader(archive.root, protocol["source_cutoff_id"], protocol["source_cutoff_at"])
    try:
        for expected in protocol["dataset_source_records"]:
            row = reader.record(expected["id"], expected["kind"])
            if any(row[key] != value for key, value in expected.items()):
                raise ValueError("Pinned raw source metadata changed")
            if sha((reader.root / "blobs" / row["body_sha256"]).read_bytes()) != expected["body_sha256"]:
                raise ValueError("Pinned raw source bytes changed")
    finally:
        reader.close()
    return protocol, dataset


def training_summary(result):
    account = result["account"]
    return {
        **{key: value for key, value in result.items() if key != "account"},
        "account": {
            "cash": account["cash"],
            "pending_orders": account["pending_orders"],
            "unresolved_holdings": account["unresolved_holdings"],
            "daily": [
                {key: row[key] for key in ("date", "cost_basis_log_return", "released_trades")}
                for row in account["daily"]
            ],
        },
    }


def prefix_account(markets, choices, policies, scenario, config, prefix_end, mode, previous=None):
    from research.experiments.e024_account_audit import audit_account

    schedule = [{"effective_ts": ts(config["requested_start"]), "policy_id": None, "risk_fraction": None}]
    orders, intent_records = [], []
    for index, choice in enumerate(choices):
        start = ts(choice["effective_date"])
        end = min(
            prefix_end,
            ts(config["selection_dates"][index + 1]) if index + 1 < len(config["selection_dates"]) else LAST,
        )
        schedule.append(
            {
                "effective_ts": start,
                "policy_id": choice["policy"]["id"] if choice["policy"] else None,
                "risk_fraction": choice["risk_fraction"],
            }
        )
        if choice["policy"]:
            intent = generate_intents(markets, choice["policy"], scenario, start, end, prefix_end)
            orders.extend(intent["orders"])
            intent_records.append({"effective_date": choice["effective_date"], **intent})
    settings = account_config(config, scenario, config["risk_fractions"][0], mode, policies)
    account = replay_scheduled(orders, settings, schedule, ts(config["requested_start"]), prefix_end)
    if previous is not None:
        assert_prefix(previous["account"], account)
        old = {trade["trade_id"]: trade["decision_sha256"] for trade in previous["orders"]}
        current = {trade["trade_id"]: trade["decision_sha256"] for trade in orders}
        if any(current.get(identifier) != value for identifier, value in old.items()):
            raise ValueError("An old selected intent changed its immutable decision")
    audit = audit_account(
        account, orders, settings, schedule, start_ts=ts(config["requested_start"]), end_ts=prefix_end
    )
    return {
        "scenario": scenario["name"],
        "mode": mode,
        "account": account,
        "orders": orders,
        "intent_records": intent_records,
        "schedule": schedule,
        "config": settings,
        "independent_account_audit": audit,
        "previous_prefix_reproduced": previous is not None,
        "start_ts": ts(config["requested_start"]),
        "end_ts": prefix_end,
    }


def training_account(intents, intent_record, config, scenario, fraction, policy, cutoff_day):
    cutoff = ts(cutoff_day)
    account = replay(intents["orders"], account_config(config, scenario, fraction), FIRST, cutoff)
    if [row["date"] for row in account["daily"]] != calendar(config["training_start"], cutoff_day):
        raise ValueError("Training account omitted a registered calendar day")
    return {
        "policy": policy,
        "scenario": scenario["name"],
        "risk_fraction": fraction,
        "intent_record_id": intent_record["id"],
        "intent_body_sha256": intent_record["body_sha256"],
        "start_ts": FIRST,
        "end_ts": cutoff,
        "account": account,
    }


def frozen_choice(accounts, bounds, config, cutoff_day, training_records, bootstrap_records):
    cutoff = ts(cutoff_day)
    return {
        **select_from_bounds(accounts, bounds, config),
        "effective_date": cutoff_day,
        "decision_end_exclusive": cutoff - config["decision_buffer_days"] * DAY,
        "cash_flow_cutoff_exclusive": cutoff,
        "training_account_record_ids": training_records[-len(accounts) :],
        "bootstrap_record_ids": bootstrap_records[-3:],
    }


def assert_sources(protocol):
    if source_hashes() != protocol["source_hashes"]:
        raise ValueError("Registered source bytes changed during execution")


def execute(archive, identifier, deadline):
    protocol, dataset = load_registration(archive, identifier)
    config, policies = protocol["config"], protocol["policies"]
    choices, selections, prior_prefix = [], [], {}
    training_records, bootstrap_records, prefix_records = [], [], []
    for look, cutoff_day in enumerate(config["selection_dates"]):
        assert_sources(protocol)
        cutoff = ts(cutoff_day)
        decision_end = cutoff - config["decision_buffer_days"] * DAY
        accounts = []
        for policy in policies:
            for scenario in config["scenarios"]:
                common = f"{cutoff_day}:{policy['id']}:{scenario['name']}"
                intent_record, intents = unit(
                    archive,
                    identifier,
                    "e024_intents_gzip",
                    common,
                    partial(
                        generate_intents, dataset["markets"], policy, scenario, FIRST, decision_end, cutoff
                    ),
                    config,
                    deadline,
                )
                for fraction in config["risk_fractions"]:
                    key = common + ":" + fraction

                    operation = partial(
                        training_account,
                        intents,
                        intent_record,
                        config,
                        scenario,
                        fraction,
                        policy,
                        cutoff_day,
                    )
                    record, result = unit(
                        archive, identifier, "e024_training_account_gzip", key, operation, config, deadline
                    )
                    training_records.append(record["id"])
                    accounts.append(training_summary(result))
                if len(accounts) % 192 == 0:
                    print(
                        json.dumps({"look": cutoff_day, "training_accounts_completed": len(accounts)}),
                        flush=True,
                    )
        if len(accounts) != protocol["training_accounts_per_look"]:
            raise ValueError("Incomplete candidate family at selection")
        growth = np.asarray(
            [[day["cost_basis_log_return"] for day in row["account"]["daily"]] for row in accounts],
            dtype=float,
        ).T
        if not np.isfinite(growth).all():
            raise ValueError("Nonfinite growth cannot enter selection")
        bounds = {}
        for block in config["bootstrap_block_days"]:
            record, value = unit(
                archive,
                identifier,
                "e024_bootstrap_gzip",
                f"{cutoff_day}:{block}",
                partial(
                    simultaneous_growth_bounds,
                    growth,
                    block_days=block,
                    resamples=config["bootstrap_resamples"],
                    seed=config["bootstrap_seeds"][look],
                    alpha=config["familywise_alpha"] / config["alpha_divisor_looks"],
                ),
                config,
                deadline,
            )
            bounds[str(block)] = value
            bootstrap_records.append(record["id"])
        assert_sources(protocol)
        record, selection = unit(
            archive,
            identifier,
            "e024_frozen_selection_gzip",
            cutoff_day,
            partial(frozen_choice, accounts, bounds, config, cutoff_day, training_records, bootstrap_records),
            config,
            deadline,
        )
        choices.append(selection)
        selections.append(record["id"])
        # The choice is archived above before the next month's portfolio prices
        # or labels are consulted. Reconstruct all selected earlier intents at
        # the later cutoff, preserving every previous daily/journal prefix.
        end = ts(config["selection_dates"][look + 1]) if look + 1 < len(config["selection_dates"]) else LAST
        for scenario in config["scenarios"]:
            for mode in ("hypothetical", "verified"):
                key = (scenario["name"], mode)
                old = prior_prefix.get(key)
                record, result = unit(
                    archive,
                    identifier,
                    "e024_portfolio_prefix_gzip",
                    f"{cutoff_day}:{scenario['name']}:{mode}",
                    partial(
                        prefix_account,
                        dataset["markets"],
                        choices,
                        policies,
                        scenario,
                        config,
                        end,
                        mode,
                        old,
                    ),
                    config,
                    deadline,
                )
                prior_prefix[key] = result
                prefix_records.append(record["id"])
        assert_sources(protocol)
        print(
            json.dumps(
                {
                    "selection": cutoff_day,
                    "action": selection["action"],
                    "eligible_pairs": selection["eligible_policy_size_pairs"],
                }
            ),
            flush=True,
        )
    if (
        len(training_records) != config["max_training_evaluations"]
        or len(bootstrap_records) != config["max_bootstrap_evaluations"]
    ):
        raise ValueError("Completed trial counts differ from the finite registration")
    report = {
        "experiment": config["experiment"],
        "protocol_record_id": identifier,
        "dataset_record_id": protocol["dataset_record_id"],
        "initial_cash": 200.0,
        "requested_period": [config["requested_start"], config["requested_end_exclusive"]],
        "mandatory_cash_days": config["mandatory_cash_days"],
        "maximum_active_calendar_days": config["maximum_active_calendar_days"],
        "training_accounts": len(training_records),
        "bootstrap_evaluations": len(bootstrap_records),
        "training_account_record_ids": training_records,
        "bootstrap_record_ids": bootstrap_records,
        "selection_record_ids": selections,
        "prefix_record_ids": prefix_records,
        "selections": choices,
        "ending_accounts": [
            {
                "scenario": scenario,
                "mode": mode,
                "account": value["account"],
                "independent_account_audit": value["independent_account_audit"],
            }
            for (scenario, mode), value in prior_prefix.items()
        ],
        "all_training_accounts_independently_audited": False,
        "training_audit_status": "A separate full-ledger audit is required; full accounts and linked immutable intents are retained.",
        "annual_classification": config["annual_classification"],
        "full_year_market_opportunity_audit_complete": False,
        "liquidation_equity_verified": False,
        "actual_fills": 0,
        "holdout_accessed": False,
        "historical_availability_verified": False,
        "profitability_proven": False,
        "source_hashes": protocol["source_hashes"],
        "limitations": [config[key] for key in ("fees", "execution", "holdout")],
    }
    _, report = unit(archive, identifier, "e024_report_gzip", "final", lambda: report, config, deadline)
    output = report_directory(archive, identifier) / "report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists():
        output.write_text(json.dumps(report, indent=2, allow_nan=False))
    print(
        json.dumps({"completed": True, "training_accounts": len(training_records), "report": str(output)}),
        flush=True,
    )


def report_directory(archive, identifier):
    root = Path("reports") if archive.root.resolve() == Path("data").resolve() else archive.root / "reports"
    return root / f"E024-run-{identifier}"


def interrupted_by_signal(number, _frame):
    raise InterruptedError(f"Execution supervisor received signal {number}")


def supervised_execute(args):
    archive = Archive(args.archive_root)
    previous_sigterm = signal.signal(signal.SIGTERM, interrupted_by_signal)
    try:
        protocol, _ = load_registration(archive, args.run_record_id, False)
        key = str(args.run_record_id)
        terminal = archive.latest("e024_execution_terminal", key)
        if terminal:
            print(canonical(read_json_record(archive, terminal)), flush=True)
            return
        prior = archive.latest("e024_execution_budget", key)
        if prior:
            budget = read_json_record(archive, prior)
        else:
            budget = {
                "started_unix": time.time(),
                "deadline_unix": time.time() + protocol["config"]["execution_budget_seconds"],
            }
            archive.append("e024_execution_budget", key, utcnow(), {}, canonical(budget).encode())
        try:
            check_budget(protocol["config"], budget["deadline_unix"])
        except TimeoutError:
            result = {
                "status": "timeout",
                "reason": "original_deadline_expired_before_resume",
                "automatic_retry": False,
            }
            archive.append("e024_execution_terminal", key, utcnow(), {}, canonical(result).encode())
            raise
        directory = report_directory(archive, args.run_record_id)
        directory.mkdir(parents=True, exist_ok=True)
        attempt = len(list(directory.glob("attempt-*.stdout.log")))
        command = [
            sys.executable,
            "-m",
            "research.experiments.e024_annual_replay",
            "--worker",
            "--run-record-id",
            key,
            "--archive-root",
            str(Path(args.archive_root).resolve()),
        ]
        process = None
        result = {"attempt": attempt, "deadline_unix": budget["deadline_unix"], "automatic_retry": False}
        try:
            with (
                (directory / f"attempt-{attempt}.stdout.log").open("xb") as stdout,
                (directory / f"attempt-{attempt}.stderr.log").open("xb") as stderr,
            ):
                process = subprocess.Popen(
                    command,
                    shell=False,
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    cwd=protocol["working_directory"],
                    env=dict(os.environ, OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2", MKL_NUM_THREADS="2"),
                )
                code = process.wait(timeout=max(0.001, budget["deadline_unix"] - time.time()))
                result.update(
                    exit_code=code, status="completed" if code == 0 else "stopped" if code == 20 else "failed"
                )
        except subprocess.TimeoutExpired:
            result.update(status="timeout", reason="original_execution_deadline")
        except BaseException as error:
            result.update(status="failed", reason=f"{type(error).__name__}: {error}")
            raise
        finally:
            if process is not None:
                for number in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(process.pid, number)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=0.2 if number == signal.SIGTERM else 3)
                    except subprocess.TimeoutExpired:
                        pass
            result["ended_unix"] = time.time()
            result["logs"] = {}
            for stream in ("stdout", "stderr"):
                path = directory / f"attempt-{attempt}.{stream}.log"
                if path.exists():
                    result["logs"][stream] = {"path": str(path), "sha256": sha(path.read_bytes())}
            archive.append("e024_execution_attempt", key, utcnow(), {}, canonical(result).encode())
            if result.get("status") in ("completed", "failed", "timeout"):
                archive.append("e024_execution_terminal", key, utcnow(), {}, canonical(result).encode())
        print(canonical(result), flush=True)
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        archive.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", default="data")
    parser.add_argument("--prepare-dataset", action="store_true")
    parser.add_argument("--source-cutoff-id", type=int)
    parser.add_argument("--source-cutoff-at")
    parser.add_argument("--register-only", action="store_true")
    parser.add_argument("--dataset-record-id", type=int)
    parser.add_argument("--run-record-id", type=int)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if args.worker:
        archive = Archive(args.archive_root)
        try:
            budget = archive.latest("e024_execution_budget", str(args.run_record_id))
            if budget is None:
                raise ValueError("Worker requires the original registered execution budget")
            with (archive.root / "E024.worker.lock").open("a+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                execute(archive, args.run_record_id, read_json_record(archive, budget)["deadline_unix"])
        except RequestedStop:
            raise SystemExit(20) from None
        finally:
            archive.close()
        return
    Path(args.archive_root).mkdir(parents=True, exist_ok=True)
    with (Path(args.archive_root) / "E024.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.prepare_dataset or args.register_only:
            archive = Archive(args.archive_root)
            try:
                if args.prepare_dataset:
                    if not args.source_cutoff_id or not args.source_cutoff_at:
                        raise ValueError(
                            "Dataset preparation requires an explicit frozen archive ID/time cutoff"
                        )
                    identifier = prepare_dataset(
                        archive, json.loads(CONFIG.read_bytes()), args.source_cutoff_id, args.source_cutoff_at
                    )
                    print(
                        json.dumps({"dataset_record_id": identifier, "strategy_scores_computed": 0}),
                        flush=True,
                    )
                else:
                    identifier = register(archive, args.dataset_record_id)
                    print(
                        json.dumps(
                            {"registration_record_id": identifier, "registered_before_strategy_scores": True}
                        ),
                        flush=True,
                    )
            finally:
                archive.close()
        elif args.run_record_id:
            supervised_execute(args)
        else:
            raise ValueError("Choose preparation, registration, or an existing reviewed run")


if __name__ == "__main__":
    main()
