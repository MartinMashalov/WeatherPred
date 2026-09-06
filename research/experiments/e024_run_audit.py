"""Independently audit a registered E024 run, without reading sealed market data.

The default verifies every retained account, selection, source link and bootstrap
vector's arithmetic. Replaying bootstrap random draws is a separate option that
must be declared in this audit's own registration before reading result bodies.
Neither option establishes historical fills or contemporaneous availability.
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
import importlib.metadata
import itertools
import json
import math
import sys
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np

from research.experiments.e024_account_audit import audit_account
from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import utcnow

AUDIT_SOURCES = [
    Path(__file__),
    Path("research/experiments/e024_account_audit.py"),
    Path("research/experiments/e023_audit.py"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
    Path("weatherpred/bankroll_selection.py"),
]
FIELDS = ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")
LOOKS = ["2026-07-01", "2026-08-01", "2026-09-01"]
DAY = 86400


def digest(body):
    return hashlib.sha256(body).hexdigest()


def midnight(day):
    return int(datetime.combine(date.fromisoformat(day), datetime.min.time(), UTC).timestamp())


def dates(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days)]


def checked_record(archive, identifier, kind=None, key=None, *, compressed=False, raw=False):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
    if row is None or (kind is not None and row["kind"] != kind) or (key is not None and row["key"] != key):
        raise ValueError("Missing or mismatched explicitly referenced audit artifact")
    if digest(canonical([row[field] for field in FIELDS]).encode()) != row["record_sha256"]:
        raise ValueError("Audit artifact record hash mismatch")
    previous = archive.db.execute(
        "SELECT record_sha256 FROM records WHERE id<? ORDER BY id DESC LIMIT 1", (identifier,)
    ).fetchone()
    if row["previous_sha256"] != (previous[0] if previous else "0" * 64):
        raise ValueError("Audit artifact predecessor hash mismatch")
    body = archive.body(row)
    if digest(body) != row["body_sha256"]:
        raise ValueError("Audit artifact body hash mismatch")
    return row, body if raw else json.loads(gzip.decompress(body) if compressed else body)


def checked_unit(archive, run_id, identifier, kind, suffix):
    name = f"{run_id}:{suffix}"
    row, value = checked_record(archive, identifier, kind, name, compressed=True)
    starts = archive.db.execute(
        "SELECT id FROM records WHERE kind='e024_unit_started' AND key=? ORDER BY id", (f"{kind}:{name}",)
    ).fetchall()
    if len(starts) != 1 or not run_id < starts[0]["id"] < identifier:
        raise ValueError("Unit is missing its unique earlier registered attempt")
    _, attempt = checked_record(archive, starts[0]["id"], "e024_unit_started", f"{kind}:{name}")
    if attempt != {"kind": kind, "key": name}:
        raise ValueError("Unit attempt declares a different artifact")
    if archive.latest("e024_unit_failed", f"{kind}:{name}"):
        raise ValueError("A completed unit cannot follow a failed attempt")
    if (
        archive.db.execute("SELECT COUNT(*) FROM records WHERE kind=? AND key=?", (kind, name)).fetchone()[0]
        != 1
    ):
        raise ValueError("Duplicate completed artifact identity")
    return row, value, starts[0]["id"]


def independent_catalog(parent):
    result = []
    for family in parent["families"]:
        if family in ("momentum", "reversal"):
            variants = list(itertools.product(parent["lookback_hours"], parent["move_thresholds"]))
        else:
            thresholds = parent[
                "favorite_thresholds" if family.startswith("favorite") else "longshot_thresholds"
            ]
            variants = [(0, threshold) for threshold in thresholds]
        for lookback, threshold in variants:
            for horizon, spread, exit_hours in itertools.product(
                parent["signal_hours_before_end"], parent["maximum_spreads"], parent["exit_hours_after_entry"]
            ):
                policy = {
                    "family": family,
                    "lookback_hours": lookback,
                    "threshold": threshold,
                    "horizon_hours": horizon,
                    "maximum_spread": spread,
                    "exit_hours": exit_hours,
                }
                policy["id"] = digest(canonical(policy).encode())[:16]
                result.append(policy)
    return result


def validate_protocol(protocol):
    config, policies = protocol["config"], protocol["policies"]
    if config["experiment"] != "E024-restricted-annual-walk-forward-v1":
        raise ValueError("This auditor only accepts the registered E024 continuation")
    if config["selection_dates"] != LOOKS or config["risk_fractions"] != ["0.005", "0.01", "0.025", "0.05"]:
        raise ValueError("E024 fixed selection schedule or risk family changed")
    if [s["name"] for s in config["scenarios"]] != ["costed", "stress"]:
        raise ValueError("Both fixed cost scenarios are required in their declared order")
    if (
        config["requested_start"] != "2025-09-06"
        or config["requested_end_exclusive"] != "2026-09-06"
        or config["training_start"] != "2026-01-01"
    ):
        raise ValueError("E024 declared calendar changed")
    if (
        config["decision_buffer_days"] != 5
        or config["mandatory_cash_days"] != 298
        or config["maximum_active_calendar_days"] != 67
    ):
        raise ValueError("E024 buffer or cash restriction changed")
    if (
        config["bootstrap_block_days"] != [1, 7, 14]
        or config["bootstrap_resamples"] != 10000
        or config["alpha_divisor_looks"] != 3
        or config["familywise_alpha"] != 0.05
    ):
        raise ValueError("E024 uncertainty family changed")
    if (
        policies != independent_catalog(json.loads(Path(config["parent_policy_config"]).read_bytes()))
        or len(policies) != 576
    ):
        raise ValueError("The original 576-policy catalog changed")
    if (
        protocol["training_accounts_per_look"] != 4608
        or config["max_training_evaluations"] != 13824
        or config["max_bootstrap_evaluations"] != 9
    ):
        raise ValueError("E024 finite family count changed")


def independent_settings(config, scenario, fraction, mode="hypothetical", policies=None):
    output = {
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
    output.update(
        mode=mode,
        risk_fraction=fraction,
        entry_coefficient=scenario["entry_coefficient"],
        exit_coefficient=scenario["exit_coefficient"],
    )
    if policies is not None:
        output.update(
            allowed_policy_ids=[p["id"] for p in policies], allowed_risk_fractions=config["risk_fractions"]
        )
    return output


def intent_window(intents, policy, start, decision_end, evaluation_end):
    if (
        intents["decision_window"] != [start, decision_end]
        or intents["evaluation_end_exclusive"] != evaluation_end
    ):
        raise ValueError("Intent artifact has different decision/release bounds")
    if intents["actual_fills"] != 0:
        raise ValueError("Historical quotes cannot become verified fills")
    seen = set()
    for trade in intents["orders"]:
        if (
            trade["trade_id"] in seen
            or trade["policy_id"] != policy["id"]
            or not start <= trade["decision_ts"] < decision_end
        ):
            raise ValueError("Intent identity or decision window changed")
        seen.add(trade["trade_id"])
        fixed = {
            key: trade[key]
            for key in (
                "trade_id",
                "policy_id",
                "event",
                "cluster",
                "side",
                "decision_ts",
                "entry_ts",
                "limit_price",
                "planned_exit_ts",
                "signal_provenance",
            )
        }
        if trade["decision_sha256"] != digest(canonical(fixed).encode()):
            raise ValueError("Intent immutable decision hash changed")
        if trade["entry_ts"] >= evaluation_end and trade["entry_price"] is not None:
            raise ValueError("Entry price is revealed beyond the evaluation cutoff")
        if trade["exit_ts"] is not None and not trade["entry_ts"] < trade["exit_ts"] < evaluation_end:
            raise ValueError("Terminal cash flow lies beyond its eligible release window")
        if trade["signal_provenance"].get("availability_verified") is not False:
            raise ValueError("Late historical metadata cannot have verified availability")
    decisions = [decision["trade_id"] for decision in intents["decisions"] if "trade_id" in decision]
    if len(decisions) != len(set(decisions)) or set(decisions) != seen:
        raise ValueError("Failed or unresolved decision attempts were omitted")


def remaining(deadline):
    if time.time() >= deadline:
        raise TimeoutError("The registered audit execution budget expired")


def audit_training_family(archive, run_id, config, policies, cutoff_day, identifiers, choice_id, deadline):
    expected = [
        (policy, scenario, fraction)
        for policy in policies
        for scenario in config["scenarios"]
        for fraction in config["risk_fractions"]
    ]
    if len(identifiers) != len(expected) or len(set(identifiers)) != len(expected):
        raise ValueError("Training family is incomplete or duplicated")
    start, end = midnight(config["training_start"]), midnight(cutoff_day)
    decision_end = end - config["decision_buffer_days"] * DAY
    expected_days = dates(config["training_start"], cutoff_day)
    summaries, growth_columns, verified = [], [], []
    cached_identity, cached_id, cached_intents, cached_row = None, None, None, None
    for identifier, (policy, scenario, fraction) in zip(identifiers, expected, strict=True):
        remaining(deadline)
        suffix = f"{cutoff_day}:{policy['id']}:{scenario['name']}:{fraction}"
        row, value, _ = checked_unit(archive, run_id, identifier, "e024_training_account_gzip", suffix)
        if (
            not identifier < choice_id
            or value["policy"] != policy
            or value["scenario"] != scenario["name"]
            or value["risk_fraction"] != fraction
        ):
            raise ValueError("Training identity/order or pre-choice chronology differs")
        if value["start_ts"] != start or value["end_ts"] != end:
            raise ValueError("Training artifact changed its registered calendar")
        identity = (value["intent_record_id"], policy["id"], scenario["name"])
        if cached_identity != identity:
            cached_identity = identity
            cached_id = value["intent_record_id"]
            cached_row, cached_intents, _ = checked_unit(
                archive,
                run_id,
                cached_id,
                "e024_intents_gzip",
                f"{cutoff_day}:{policy['id']}:{scenario['name']}",
            )
            intent_window(cached_intents, policy, start, decision_end, end)
        if cached_id >= identifier or cached_row["body_sha256"] != value["intent_body_sha256"]:
            raise ValueError("Training account changed its earlier immutable intent link")
        account = value["account"]
        if [day["date"] for day in account["daily"]] != expected_days:
            raise ValueError("Training account omitted or reordered a calendar day")
        settings = independent_settings(config, scenario, fraction)
        audit = audit_account(account, cached_intents["orders"], settings, start_ts=start, end_ts=end)
        summaries.append(
            {
                "policy": policy,
                "risk_fraction": fraction,
                "scenario": scenario["name"],
                "cash": account["cash"],
                "days": len(account["daily"]),
                "release_days": sum(day["released_trades"] > 0 for day in account["daily"]),
                "unresolved": bool(account["pending_orders"] or account["unresolved_holdings"]),
            }
        )
        growth_columns.append([day["cost_basis_log_return"] for day in account["daily"]])
        verified.append(
            {
                "record_id": identifier,
                "body_sha256": row["body_sha256"],
                "intent_record_id": cached_id,
                "audit": audit,
            }
        )
    return summaries, np.asarray(growth_columns, dtype=float).T, verified


def audit_bootstrap(bounds, growth, config, look, block, *, replay_seed=False):
    days, count = growth.shape
    expected = {
        "n_days": days,
        "n_candidates": count,
        "block_days": block,
        "resamples": config["bootstrap_resamples"],
        "seed": config["bootstrap_seeds"][look],
        "alpha": config["familywise_alpha"] / config["alpha_divisor_looks"],
        "standard_error_floor": 1e-14,
        "quantile_method": "higher",
    }
    if any(bounds.get(key) != value for key, value in expected.items()):
        raise ValueError("Bootstrap family dimensions, order, or registered controls changed")
    for key in ("means", "standard_errors"):
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bounds[key]):
            raise ValueError("Bootstrap numeric vectors cannot contain booleans or nonnumbers")
    for key in ("means", "standard_errors", "lower_bounds", "eligible"):
        if len(bounds[key]) != count:
            raise ValueError("Bootstrap omitted a candidate vector element")
    means, errors = (
        np.asarray(bounds["means"], dtype=float),
        np.asarray(bounds["standard_errors"], dtype=float),
    )
    if (
        not np.isfinite(growth).all()
        or not np.isfinite(means).all()
        or not np.isfinite(errors).all()
        or (errors < 0).any()
    ):
        raise ValueError("Bootstrap vectors require finite means and nonnegative errors")
    if not np.allclose(means, growth.mean(axis=0), atol=1e-14, rtol=1e-12):
        raise ValueError("Bootstrap means do not match all retained daily account returns")
    varying = np.any(growth != growth[0], axis=0)
    eligible = (varying & (errors > 1e-14)).tolist()
    if any(type(value) is not bool for value in bounds["eligible"]) or bounds["eligible"] != eligible:
        raise ValueError("Bootstrap eligibility differs from the nondegenerate family")
    if (
        np.any(errors[~varying] != 0)
        or bounds["active_candidates"] != sum(eligible)
        or bounds["degenerate_candidates"] != count - sum(eligible)
    ):
        raise ValueError("Degenerate bootstrap accounting is inconsistent")
    critical = bounds["critical_value"]
    if any(eligible):
        if isinstance(critical, bool) or critical is None or not math.isfinite(critical) or critical < 0:
            raise ValueError("Bootstrap critical value is invalid")
    elif critical is not None:
        raise ValueError("A completely degenerate family cannot have a critical value")
    for index, active in enumerate(eligible):
        lower = bounds["lower_bounds"][index]
        if not active:
            if lower is not None:
                raise ValueError("A degenerate candidate cannot acquire a positive proof bound")
        elif (
            isinstance(lower, bool)
            or lower is None
            or not math.isfinite(lower)
            or not math.isclose(lower, means[index] - critical * errors[index], rel_tol=1e-12, abs_tol=1e-14)
        ):
            raise ValueError("Bootstrap lower bound arithmetic is inconsistent")
    if replay_seed:
        from weatherpred.bankroll_selection import simultaneous_growth_bounds

        replayed = simultaneous_growth_bounds(
            growth,
            block_days=block,
            resamples=expected["resamples"],
            seed=expected["seed"],
            alpha=expected["alpha"],
        )
        if replayed != bounds:
            raise ValueError("Shared fixed-seed bootstrap does not reproduce retained vectors")
    return {
        "candidate_vectors_checked": count,
        "day_count": days,
        "block_days": block,
        "mean_and_bound_arithmetic_reproduced": True,
        "random_draws_replayed": replay_seed,
        "uncertainty_estimates_independently_validated": False,
    }


def independent_choice(summaries, bounds, config):
    indexed = {
        (row["policy"]["id"], row["risk_fraction"], row["scenario"]): (i, row)
        for i, row in enumerate(summaries)
    }
    if len(indexed) != len(summaries):
        raise ValueError("Duplicate account identity in selection")
    accepted, rejected, details = [], {}, []
    identities = sorted({(row["policy"]["id"], row["risk_fraction"]) for row in summaries})
    for policy_id, fraction in identities:
        reasons = set()
        for scenario in ("costed", "stress"):
            index, row = indexed[(policy_id, fraction, scenario)]
            if row["days"] < config["minimum_training_calendar_days"]:
                reasons.add("insufficient_training_calendar")
            if row["release_days"] < config["minimum_release_days_per_scenario"]:
                reasons.add("insufficient_release_days")
            if row["unresolved"]:
                reasons.add("unresolved_training_cash_flows")
            if Decimal(str(row["cash"])) <= Decimal(config["initial_cash"]):
                reasons.add("nonpositive_cash_profit")
            for block in config["bootstrap_block_days"]:
                result = bounds[str(block)]
                lower = result["lower_bounds"][index]
                if not result["eligible"][index] or lower is None or lower <= 0:
                    reasons.add(f"nonpositive_or_degenerate_bound_{block}d")
        reasons = sorted(reasons)
        details.append({"policy_id": policy_id, "risk_fraction": fraction, "rejections": reasons})
        for reason in reasons:
            rejected[reason] = rejected.get(reason, 0) + 1
        if not reasons:
            index, row = indexed[(policy_id, fraction, "costed")]
            accepted.append((bounds["7"]["lower_bounds"][index], policy_id, Decimal(fraction), row))
    accepted.sort(key=lambda row: (-row[0], row[1], row[2]))
    chosen = accepted[0][3] if accepted else None
    return {
        "action": "trade_frozen_policy" if chosen else "cash",
        "policy": chosen["policy"] if chosen else None,
        "risk_fraction": chosen["risk_fraction"] if chosen else None,
        "eligible_policy_size_pairs": len(accepted),
        "rejection_counts": rejected,
        "pair_rejections": details,
        "future_period_outcomes_used": False,
        "profitability_proven": False,
    }


def audit_prefix(value, config, policies, scenario, mode, choices, end, previous=None):
    start = midnight(config["requested_start"])
    schedule = [{"effective_ts": start, "policy_id": None, "risk_fraction": None}]
    reconstructed_orders = []
    active = [choice for choice in choices if choice["policy"] is not None]
    if len(value["intent_records"]) != len(active):
        raise ValueError("Selected prefix omitted a month of original intents")
    for choice in choices:
        schedule.append(
            {
                "effective_ts": midnight(choice["effective_date"]),
                "policy_id": choice["policy"]["id"] if choice["policy"] else None,
                "risk_fraction": choice["risk_fraction"],
            }
        )
    for intent, choice in zip(value["intent_records"], active, strict=True):
        index = LOOKS.index(choice["effective_date"])
        month_end = midnight(
            LOOKS[index + 1] if index + 1 < len(LOOKS) else config["requested_end_exclusive"]
        )
        if intent["effective_date"] != choice["effective_date"]:
            raise ValueError("Selected intent month differs from its frozen choice")
        intent_window(intent, choice["policy"], midnight(choice["effective_date"]), min(end, month_end), end)
        reconstructed_orders.extend(intent["orders"])
    settings = independent_settings(config, scenario, config["risk_fractions"][0], mode, policies)
    if (
        value["orders"] != reconstructed_orders
        or value["schedule"] != schedule
        or value["config"] != settings
    ):
        raise ValueError("Selected account changed its inputs, settings, or chronological schedule")
    if (
        value["start_ts"] != start
        or value["end_ts"] != end
        or value["scenario"] != scenario["name"]
        or value["mode"] != mode
    ):
        raise ValueError("Selected prefix identity or requested bounds changed")
    result = audit_account(value["account"], value["orders"], settings, schedule, start_ts=start, end_ts=end)
    if value["independent_account_audit"] != result or value["previous_prefix_reproduced"] != (
        previous is not None
    ):
        raise ValueError("Retained prefix audit differs from fresh independent reconstruction")
    if previous is not None:
        cutoff = previous["end_ts"]
        if previous["account"]["daily"] != [
            row for row in value["account"]["daily"] if row["timestamp"] <= cutoff
        ]:
            raise ValueError("A later monthly prefix changed earlier daily states")
        if previous["account"]["orders"] != [
            row for row in value["account"]["orders"] if row["timestamp"] < cutoff
        ]:
            raise ValueError("A later monthly prefix changed the earlier order journal")
        hashes = {trade["trade_id"]: trade["decision_sha256"] for trade in value["orders"]}
        if any(hashes.get(trade["trade_id"]) != trade["decision_sha256"] for trade in previous["orders"]):
            raise ValueError("An earlier decision changed across policy re-selection")
    return result


def file_hashes(paths):
    return {str(path): digest(Path(path).read_bytes()) for path in paths}


def register_audit(archive, run_id, replay_bootstrap=False):
    run_record, run_protocol = checked_record(archive, run_id, "experiment_protocol")
    validate_protocol(run_protocol)  # Source/configuration only: no result body is opened.
    if file_hashes(run_protocol["source_hashes"]) != run_protocol["source_hashes"]:
        raise ValueError("Original registered E024 source files changed")
    final = archive.latest("e024_report_gzip", f"{run_id}:final")
    if final is None:
        raise ValueError("A completed E024 report is required before its audit registration")
    protocol = {
        "experiment": "E024-independent-full-account-audit-v1",
        "run_registration_id": run_id,
        "run_protocol_body_sha256": run_record["body_sha256"],
        "final_report_record_id": final["id"],
        "final_report_body_sha256": final["body_sha256"],
        "replay_bootstrap": replay_bootstrap,
        "source_hashes": file_hashes(AUDIT_SOURCES),
        "e024_source_hashes": run_protocol["source_hashes"],
        "registered_before_result_evaluation": True,
        "execution_budget_seconds": 5400,
        "python_executable": sys.executable,
        "working_directory": str(Path.cwd()),
        "numpy_version": importlib.metadata.version("numpy"),
        "raw_quote_signal_reconstruction": False,
        "historical_fills_verified": False,
    }
    protocol["source_record_ids"] = {
        str(path): archive.append("research_source", str(path), utcnow(), {}, path.read_bytes())
        for path in AUDIT_SOURCES
    }
    return archive.append(
        "experiment_protocol", protocol["experiment"], utcnow(), {}, canonical(protocol).encode()
    )


def check_files(protocol):
    for name in ("source_hashes", "e024_source_hashes"):
        if file_hashes(protocol[name]) != protocol[name]:
            raise ValueError("Audit or E024 source bytes changed after audit registration")
    if (
        protocol["python_executable"] != sys.executable
        or protocol["working_directory"] != str(Path.cwd())
        or protocol["numpy_version"] != importlib.metadata.version("numpy")
    ):
        raise ValueError("Audit execution environment changed")


def audit_source_records(archive, protocol, registration_id):
    if set(protocol["source_record_ids"]) != set(protocol["source_hashes"]):
        raise ValueError("Archived sources do not match the pinned source family")
    for path, identifier in protocol["source_record_ids"].items():
        row, _ = checked_record(archive, identifier, "research_source", path, raw=True)
        if identifier >= registration_id or row["body_sha256"] != protocol["source_hashes"][path]:
            raise ValueError("A source artifact changed or was archived after registration")


def evaluate(archive, audit_id, audit_protocol, deadline):
    run_id = audit_protocol["run_registration_id"]
    row, protocol = checked_record(archive, run_id, "experiment_protocol")
    if row["body_sha256"] != audit_protocol["run_protocol_body_sha256"]:
        raise ValueError("Pinned E024 registration changed")
    validate_protocol(protocol)
    audit_source_records(archive, protocol, run_id)
    audit_source_records(archive, audit_protocol, audit_id)
    dataset_row, _ = checked_record(archive, protocol["dataset_record_id"], "e024_dataset_gzip", raw=True)
    if dataset_row["id"] >= run_id or dataset_row["body_sha256"] != protocol["dataset_body_sha256"]:
        raise ValueError("Pinned normalized dataset changed after original registration")
    config, policies = protocol["config"], protocol["policies"]
    row, report, _ = checked_unit(
        archive, run_id, audit_protocol["final_report_record_id"], "e024_report_gzip", "final"
    )
    if row["body_sha256"] != audit_protocol["final_report_body_sha256"] or row["id"] >= audit_id:
        raise ValueError("Final E024 report differs from its pre-evaluation audit pin")
    if (
        report["protocol_record_id"] != run_id
        or report["source_hashes"] != protocol["source_hashes"]
        or report["dataset_record_id"] != protocol["dataset_record_id"]
    ):
        raise ValueError("Final report changed its experiment lineage")
    training_ids, bootstrap_ids, selection_ids, prefix_ids = [
        report[key]
        for key in (
            "training_account_record_ids",
            "bootstrap_record_ids",
            "selection_record_ids",
            "prefix_record_ids",
        )
    ]
    if (len(training_ids), len(bootstrap_ids), len(selection_ids), len(prefix_ids)) != (13824, 9, 3, 12):
        raise ValueError("Final report omitted part of the registered finite experiment")
    all_ids = training_ids + bootstrap_ids + selection_ids + prefix_ids
    if len(set(all_ids)) != len(all_ids) or any(
        not run_id < identifier < row["id"] for identifier in all_ids
    ):
        raise ValueError("Experiment artifacts are duplicated or outside report chronology")
    choices, prior, account_audits, bootstrap_audits, prefix_audits = [], {}, [], [], []
    for look, cutoff_day in enumerate(LOOKS):
        remaining(deadline)
        check_files(audit_protocol)
        choice_id = selection_ids[look]
        _, selection, choice_started = checked_unit(
            archive, run_id, choice_id, "e024_frozen_selection_gzip", cutoff_day
        )
        subset = training_ids[look * 4608 : (look + 1) * 4608]
        summaries, growth, audited = audit_training_family(
            archive, run_id, config, policies, cutoff_day, subset, choice_started, deadline
        )
        account_audits.extend(audited)
        bounds = {}
        for offset, block in enumerate(config["bootstrap_block_days"]):
            identifier = bootstrap_ids[look * 3 + offset]
            _, retained, started = checked_unit(
                archive, run_id, identifier, "e024_bootstrap_gzip", f"{cutoff_day}:{block}"
            )
            if not max(subset) < started < identifier < choice_started:
                raise ValueError("Bootstrap or choice began before its complete training family")
            audit = audit_bootstrap(
                retained, growth, config, look, block, replay_seed=audit_protocol["replay_bootstrap"]
            )
            bootstrap_audits.append({"record_id": identifier, **audit})
            bounds[str(block)] = retained
        expected = {
            **independent_choice(summaries, bounds, config),
            "effective_date": cutoff_day,
            "decision_end_exclusive": midnight(cutoff_day) - config["decision_buffer_days"] * DAY,
            "cash_flow_cutoff_exclusive": midnight(cutoff_day),
            "training_account_record_ids": subset,
            "bootstrap_record_ids": bootstrap_ids[look * 3 : (look + 1) * 3],
        }
        if selection != expected or report["selections"][look] != selection:
            raise ValueError("Frozen monthly choice does not reproduce independent acceptance gates")
        choices.append(selection)
        end = midnight(LOOKS[look + 1] if look + 1 < len(LOOKS) else config["requested_end_exclusive"])
        for scenario_index, scenario in enumerate(config["scenarios"]):
            for mode_index, mode in enumerate(("hypothetical", "verified")):
                identifier = prefix_ids[look * 4 + scenario_index * 2 + mode_index]
                _, value, started = checked_unit(
                    archive,
                    run_id,
                    identifier,
                    "e024_portfolio_prefix_gzip",
                    f"{cutoff_day}:{scenario['name']}:{mode}",
                )
                if not choice_id < started < identifier:
                    raise ValueError("Monthly portfolio evaluation preceded its archived choice")
                if look < 2 and not identifier < training_ids[(look + 1) * 4608]:
                    raise ValueError("Later training was evaluated before the previous month's prefix")
                identity = (scenario["name"], mode)
                audited = audit_prefix(
                    value, config, policies, scenario, mode, choices, end, prior.get(identity)
                )
                prior[identity] = value
                prefix_audits.append({"record_id": identifier, **audited})
        print(
            canonical({"audited_selection": cutoff_day, "training_accounts": len(account_audits)}), flush=True
        )
    endings = [
        {
            "scenario": scenario,
            "mode": mode,
            "account": value["account"],
            "independent_account_audit": value["independent_account_audit"],
        }
        for (scenario, mode), value in prior.items()
    ]
    if (
        report["ending_accounts"] != endings
        or report["training_accounts"] != 13824
        or report["bootstrap_evaluations"] != 9
    ):
        raise ValueError("Final account path or trial counts differ from retained prefixes")
    required = {
        "mandatory_cash_days": 298,
        "maximum_active_calendar_days": 67,
        "actual_fills": 0,
        "holdout_accessed": False,
        "historical_availability_verified": False,
        "profitability_proven": False,
        "full_year_market_opportunity_audit_complete": False,
        "liquidation_equity_verified": False,
        "annual_classification": config["annual_classification"],
        "initial_cash": 200.0,
        "requested_period": [config["requested_start"], config["requested_end_exclusive"]],
    }
    if any(report.get(key) != value for key, value in required.items()):
        raise ValueError("Final report overstates annual coverage or historical execution evidence")
    remaining(deadline)
    check_files(audit_protocol)
    return {
        "audit_registration_id": audit_id,
        "run_registration_id": run_id,
        "all_training_accounts_independently_audited": True,
        "training_accounts": len(account_audits),
        "bootstrap_vectors_audited": len(bootstrap_audits),
        "prefix_accounts_audited": len(prefix_audits),
        "monthly_choices_independently_reproduced": 3,
        "account_audits": account_audits,
        "bootstrap_audits": bootstrap_audits,
        "prefix_audits": prefix_audits,
        "bootstrap_random_draws_replayed": audit_protocol["replay_bootstrap"],
        "bootstrap_seed_replay_pending": not audit_protocol["replay_bootstrap"],
        "raw_quote_signal_reconstruction": False,
        "historical_fills_verified": False,
        "historical_availability_verified": False,
        "profitability_proven": False,
        "holdout_accessed": False,
    }


def audit_run(archive, audit_id):
    _, protocol = checked_record(
        archive, audit_id, "experiment_protocol", "E024-independent-full-account-audit-v1"
    )
    check_files(protocol)
    previous = archive.latest("e024_full_audit_gzip", str(audit_id))
    if previous:
        return checked_record(
            archive, previous["id"], "e024_full_audit_gzip", str(audit_id), compressed=True
        )[1]
    if archive.latest("e024_full_audit_started", str(audit_id)):
        raise RuntimeError("An incomplete audit cannot silently retry")
    deadline = time.time() + protocol["execution_budget_seconds"]
    archive.append(
        "e024_full_audit_started",
        str(audit_id),
        utcnow(),
        {},
        canonical({"deadline_unix": deadline}).encode(),
    )
    try:
        result = evaluate(archive, audit_id, protocol, deadline)
        archive.append(
            "e024_full_audit_gzip",
            str(audit_id),
            utcnow(),
            {},
            gzip.compress(canonical(result).encode(), mtime=0),
        )
    except BaseException as error:
        archive.append(
            "e024_full_audit_failed",
            str(audit_id),
            utcnow(),
            {},
            canonical({"error": f"{type(error).__name__}: {error}", "automatic_retry": False}).encode(),
        )
        raise
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", default="data")
    parser.add_argument("--e024-registration-id", type=int)
    parser.add_argument("--register-only", action="store_true")
    parser.add_argument("--replay-bootstrap", action="store_true")
    parser.add_argument("--audit-record-id", type=int)
    args = parser.parse_args()
    archive = Archive(args.archive_root)
    lock = (archive.root / "E024.audit.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.register_only:
            if args.e024_registration_id is None or args.audit_record_id is not None:
                raise ValueError("Specify the completed E024 registration for a new audit declaration")
            identifier = register_audit(archive, args.e024_registration_id, args.replay_bootstrap)
            print(canonical({"audit_registration_id": identifier, "result_bodies_evaluated": 0}), flush=True)
        elif args.audit_record_id is not None:
            if args.replay_bootstrap:
                raise ValueError("Bootstrap replay must be fixed in the earlier audit registration")
            result = audit_run(archive, args.audit_record_id)
            print(
                canonical({key: value for key, value in result.items() if not key.endswith("_audits")}),
                flush=True,
            )
        else:
            raise ValueError("Register an audit first, then execute its unchanged audit record")
    finally:
        lock.close()
        archive.close()


if __name__ == "__main__":
    main()
