"""Registered, bounded statistical research on one shared conditional broker.

Candidate functions receive numerical feature/label arrays only. The trusted
orchestrator owns the private dataset. This argument boundary is tested, but is
not an operating-system sandbox. Imports never read research data.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.metadata
import json
import math
import os
import signal
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from research.experiments.e024_account_audit import audit_account
from research.probes.e031_supervisor import supervise
from weatherpred.archive import Archive, canonical
from weatherpred.bankroll_replay import aggregate_cash
from weatherpred.bankroll_selection import simultaneous_growth_bounds
from weatherpred.scheduled_bankroll import replay_scheduled
from weatherpred.timeutil import utcnow

DEFAULT_CONFIG = Path("config/e032_statistical_lab.json")
SOURCE_FILES = [
    Path(__file__),
    Path("weatherpred/statistical_replay_data.py"),
    Path("weatherpred/statistical_candidates.py"),
    Path("weatherpred/research_campaign.py"),
    Path("tests/test_statistical_replay_data.py"),
    Path("tests/test_statistical_candidates.py"),
    Path("tests/test_research_campaign.py"),
    Path("tests/test_e032_statistical_lab.py"),
    Path("weatherpred/scheduled_bankroll.py"),
    Path("weatherpred/bankroll_replay.py"),
    Path("weatherpred/bankroll_selection.py"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
    Path("research/experiments/e024_account_audit.py"),
    Path("research/experiments/e023_audit.py"),
    Path("research/probes/e031_supervisor.py"),
    Path("pyproject.toml"),
    Path("uv.lock"),
]


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def stamp(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp())


def iso(value):
    return datetime.fromtimestamp(value, UTC).isoformat()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    return path


def source_hashes(config_path):
    return {str(p.resolve()): digest(p.read_bytes()) for p in [*SOURCE_FILES, Path(config_path)]}


def read_json_record(archive, record, compressed=False):
    fields = [
        record[k] for k in ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")
    ]
    if digest(fields) != record["record_sha256"]:
        raise ValueError("Archive record fingerprint changed")
    previous = archive.db.execute(
        "SELECT record_sha256 FROM records WHERE id<? ORDER BY id DESC LIMIT 1", (record["id"],)
    ).fetchone()
    if record["previous_sha256"] != (previous[0] if previous else "0" * 64):
        raise ValueError("Archive predecessor fingerprint changed")
    body = archive.body(record)
    if digest(body) != record["body_sha256"]:
        raise ValueError("Archive body fingerprint changed")
    return json.loads(gzip.decompress(body) if compressed else body)


def validate_config(config):
    if config["phase"] != "development" or config["availability_mode"] != "conditional_historical":
        raise ValueError("This adapter only supports explicitly conditional reused development")
    if config["promotion_eligible"] or config["historical_execution_verified"]:
        raise ValueError("A candle replay cannot authorize promotion or verified fills")
    ids = [c["id"] for c in config["candidates"]]
    if ids != ["cash", "midpoint", "logistic_offset", "ridge_net_return"] or config["max_trials"] != 4:
        raise ValueError("The first campaign has exactly four registered candidates")
    dates = config["fit_dates"]
    if dates != [f"2026-{m:02d}-01" for m in range(3, 9)]:
        raise ValueError("The six monthly refits are fixed before data/model execution")
    if config["training_start"] != "2026-01-01" or config["account_end"] != "2026-09-06":
        raise ValueError("Protected or unsupported dataset date")
    if config["decision_buffer_days"] != 5 or config["minimum_training_days"] != 45:
        raise ValueError("Training buffer and minimum cannot be changed inside this experiment")
    if config["decision_horizon_hours"] != 12 or config["limit_allowance"] != "0.01":
        raise ValueError("This adapter requires the fixed 12-hour horizon and one-cent limit allowance")
    if config["risk_fraction"] != "0.01" or config["minimum_predicted_edge"] != 0.03:
        raise ValueError("No adaptive size or entry-threshold search is permitted")
    if (
        config["maximum_spread"] != "0.08"
        or config["initial_cash"] != "200.00"
        or config["account_start"] != "2025-09-06"
    ):
        raise ValueError("The shared spread gate and requested annual account are fixed")
    if [s["name"] for s in config["scenarios"]] != ["costed", "stress"]:
        raise ValueError("Both cost scenarios are required")
    return config


def register(archive, config_path=DEFAULT_CONFIG):
    config_path = Path(config_path).resolve()
    config = validate_config(json.loads(config_path.read_bytes()))
    if archive.latest("experiment_protocol", config["experiment"]):
        raise ValueError("Campaign already registered; a rerun needs an explicit new version")
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (config["dataset_record_id"],)).fetchone()
    if (
        row is None
        or row["kind"] != "e024_dataset_gzip"
        or row["body_sha256"] != config["dataset_body_sha256"]
    ):
        raise ValueError("The released immutable development dataset does not match")
    hashes = source_hashes(config_path)
    copies = {p: archive.append("e032_source", p, utcnow(), {}, Path(p).read_bytes()) for p in hashes}
    protocol = {
        "config": config,
        "config_path": str(config_path),
        "source_hashes": hashes,
        "source_record_ids": copies,
        "dataset_record": {k: row[k] for k in ("id", "kind", "body_sha256", "record_sha256")},
        "runtime": {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "weatherpred")},
        "python_executable": sys.executable,
        "working_directory": str(Path.cwd()),
        "registered_before_this_batch_fits": True,
        "earlier_development_results_known": True,
        "prior_research": config["prior_research"],
        "promotion_eligible": False,
    }
    return archive.append(
        "experiment_protocol", config["experiment"], utcnow(), {}, canonical(protocol).encode()
    )


def load_protocol(archive, identifier):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
    if row is None or row["kind"] != "experiment_protocol":
        raise ValueError("Registered experiment required")
    protocol = read_json_record(archive, row)
    validate_config(protocol["config"])
    if protocol["source_hashes"] != source_hashes(protocol["config_path"]):
        raise ValueError("Registered source bytes changed; preserve the failed attempt")
    if protocol["python_executable"] != sys.executable or protocol["working_directory"] != str(Path.cwd()):
        raise ValueError("Registered interpreter or working directory changed")
    if protocol["runtime"] != {n: importlib.metadata.version(n) for n in protocol["runtime"]}:
        raise ValueError("Registered numerical environment changed")
    return protocol


def put(archive, kind, key, payload, compressed=True):
    raw = canonical(payload).encode()
    body = gzip.compress(raw, mtime=0) if compressed else raw
    identifier = archive.append(kind, str(key), utcnow(), {}, body)
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
    return {"record_id": identifier, "body_sha256": row["body_sha256"], "record_sha256": row["record_sha256"]}


def choose_decisions(predictions, candidate_id, config):
    """Choose once per event using forecasts and decision quotes, never fills."""
    grouped = defaultdict(list)
    for row in predictions:
        grouped[row["event"]].append(row)
    decisions, abstentions = [], []
    for event, members in sorted(grouped.items()):
        scored = []
        for row in members:
            if row.get("prediction") is None or not row.get("eligible"):
                continue
            for side_index, side in enumerate(("yes", "no")):
                ask = row["yes_ask"] if side == "yes" else row["no_ask"]
                limit = min(0.99, round(float(ask) + float(config["limit_allowance"]), 8))
                if row["kind"] == "probability":
                    probability = float(row["prediction"])
                    probability = probability if side == "yes" else 1 - probability
                    training_scenario = next(
                        s for s in config["scenarios"] if s["name"] == config["training_scenario"]
                    )
                    score = probability + float(
                        aggregate_cash(str(limit), 1, training_scenario["entry_coefficient"])["cash_change"]
                    )
                elif row["kind"] == "net_return":
                    score = float(row["prediction"][side_index])
                else:
                    continue
                if not math.isfinite(score):
                    raise ValueError("Nonfinite candidate score")
                scored.append((-score, row["ticker"], side, limit, row))
        if not scored or -min(scored, key=lambda x: x[:3])[0] <= config["minimum_predicted_edge"]:
            abstentions.append({"event": event, "reason": "cash_or_no_edge_or_missing_features"})
            continue
        minus_score, ticker, side, limit, row = min(scored, key=lambda x: x[:3])
        decision = {
            "opportunity": row["opportunity"],
            "trade_id": f"{candidate_id}:{row['opportunity_id']}:{side}",
            "opportunity_id": row["opportunity_id"],
            "policy_id": candidate_id,
            "event": event,
            "cluster": "all_weather",
            "ticker": ticker,
            "side": side,
            "decision_ts": row["decision_ts"],
            "limit_price": str(limit),
            "signal_provenance": row["signal_provenance"],
            "predicted_edge": -minus_score,
        }
        decision["decision_sha256"] = digest(decision)
        decisions.append(decision)
    return {"decisions": decisions, "abstentions": abstentions}


def account_config(config, candidate_id, scenario, mode):
    keys = (
        "initial_cash",
        "max_event_fraction",
        "max_cluster_fraction",
        "max_total_fraction",
        "drawdown_kill_fraction",
        "conditional_depth_cap",
    )
    return {
        **{k: config[k] for k in keys},
        "mode": mode,
        "entry_coefficient": scenario["entry_coefficient"],
        "exit_coefficient": scenario["exit_coefficient"],
        "allowed_policy_ids": [candidate_id],
        "allowed_risk_fractions": [config["risk_fraction"]],
    }


def replay_candidate(orders, config, candidate_id, scenario, mode="hypothetical"):
    start, end = stamp(config["account_start"]), stamp(config["account_end"])
    schedule = [
        {"effective_ts": start, "policy_id": None, "risk_fraction": None},
        {
            "effective_ts": stamp(config["fit_dates"][0]),
            "policy_id": None if candidate_id == "cash" else candidate_id,
            "risk_fraction": None if candidate_id == "cash" else config["risk_fraction"],
        },
    ]
    settings = account_config(config, candidate_id, scenario, mode)
    account = replay_scheduled(orders, settings, schedule, start, end)
    audit = audit_account(account, orders, settings, schedule, start_ts=start, end_ts=end)
    return {"account": account, "audit": audit, "schedule": schedule}


def summarize_account(result):
    account = result["account"]
    returns = account["closed_trades"]
    release_days = {datetime.fromtimestamp(t["cash_release_ts"], UTC).date().isoformat() for t in returns}
    gains = sum(max(0, t["pnl"]) for t in returns)
    losses = -sum(min(0, t["pnl"]) for t in returns)
    by_series = defaultdict(lambda: {"trades": 0, "pnl": 0.0})
    for trade in returns:
        item = by_series[trade["event"].split("-")[0]]
        item["trades"] += 1
        item["pnl"] += trade["pnl"]
    return {
        **{
            k: account[k]
            for k in (
                "cash",
                "realized_pnl",
                "total_fees",
                "cost_basis_equity",
                "zero_mark_equity",
                "max_cost_basis_drawdown",
                "max_zero_mark_drawdown",
                "drawdown_killed",
                "no_trade_reasons",
            )
        },
        "entered_trades": len(account["accepted_trades"]),
        "closed_trades": len(returns),
        "release_days": len(release_days),
        "win_fraction": sum(t["pnl"] > 0 for t in returns) / len(returns) if returns else None,
        "profit_factor": gains / losses if losses > 0 else None,
        "gross_gains": gains,
        "gross_losses": losses,
        "by_series": dict(by_series),
        "unresolved_holdings": len(account["unresolved_holdings"]),
        "pending_orders": len(account["pending_orders"]),
        "annual_log_cash_growth": math.log(account["cash"] / account["initial_cash"])
        if account["cash"] > 0
        else None,
        "accounting_audit": result["audit"],
        "liquidation_mark_available": False,
    }


def compare_accounts(accounts, config):
    """Shared day-block draws across every successful candidate/cost column."""
    keys = sorted(accounts)
    planned = [f"{c['id']}:{s['name']}" for c in config["candidates"] for s in config["scenarios"]]
    missing = sorted(set(planned) - set(keys))
    start_eval = stamp(config["fit_dates"][0])
    series = []
    timestamps = list(range(start_eval + 86400, stamp(config["account_end"]) + 1, 86400))
    for key in keys:
        daily = [r for r in accounts[key]["account"]["daily"] if r["timestamp"] > start_eval]
        if [r["timestamp"] for r in daily] != timestamps:
            raise ValueError(
                "Candidate accounts must share the exact complete contiguous evaluation calendar"
            )
        series.append([r["cost_basis_log_return"] for r in daily])
    if not keys:
        return {
            "columns": [],
            "planned_columns": planned,
            "missing_columns": missing,
            "bounds": {},
            "research_gate": {
                c["id"]: {
                    "further_research_candidate": False,
                    "reasons": ["incomplete_campaign"],
                    "promotion_eligible": False,
                }
                for c in config["candidates"]
            },
        }
    matrix = np.asarray(series, dtype=float).T
    bounds = {
        str(block): simultaneous_growth_bounds(
            matrix,
            block_days=block,
            resamples=config["bootstrap_resamples"],
            seed=config["bootstrap_seed"],
            alpha=config["familywise_alpha"] / len(config["bootstrap_block_days"]),
        )
        for block in config["bootstrap_block_days"]
    }
    gates = {}
    for candidate in config["candidates"]:
        reasons = ["incomplete_campaign"] if missing else []
        for scenario in config["scenarios"]:
            key = f"{candidate['id']}:{scenario['name']}"
            if key not in accounts:
                reasons.append(f"{scenario['name']}:failed_or_missing_trial")
                continue
            summary = summarize_account(accounts[key])
            for failed, reason in (
                (summary["cash"] <= 200, "nonpositive_cash_profit"),
                (
                    summary["release_days"] < config["minimum_research_release_days"],
                    "insufficient_release_days",
                ),
                (summary["unresolved_holdings"] or summary["pending_orders"], "unresolved_account"),
                (summary["drawdown_killed"], "drawdown_kill"),
            ):
                if failed:
                    reasons.append(f"{scenario['name']}:{reason}")
            for block, bound in bounds.items():
                value = bound["lower_bounds"][keys.index(key)]
                if value is None or value <= 0:
                    reasons.append(f"{scenario['name']}:nonpositive_or_degenerate_{block}d_bound")
        gates[candidate["id"]] = {
            "further_research_candidate": not reasons,
            "reasons": reasons,
            "promotion_eligible": False,
        }
    return {
        "columns": keys,
        "planned_columns": planned,
        "missing_columns": missing,
        "bounds": bounds,
        "research_gate": gates,
        "interpretation": "Descriptive reused-development bounds. No correction removes prior adaptive reuse; no untouched or forward validation. Cost-basis growth is not executable liquidation growth.",
    }


def eligible(opportunity, config):
    return opportunity["feature_complete"] and opportunity["feature_vector"][1] <= float(
        config["maximum_spread"]
    )


def latest_receipt(value):
    """Maximum original receipt in the training-label provenance, never assumed."""
    if isinstance(value, dict):
        own = [float(value["available_ts"])] if "available_ts" in value else []
        return max([*own, *(latest_receipt(v) for v in value.values())], default=0)
    if isinstance(value, list):
        return max((latest_receipt(v) for v in value), default=0)
    return 0


def fit_predict_candidate(archive, identifier, candidate, panel, private_lookup, config):
    """Assemble cutoff-specific inputs; model APIs receive arrays, not records."""
    from weatherpred.statistical_candidates import (
        FEATURE_NAMES,
        event_balanced_weights,
        fit_candidate,
        predict_candidate,
    )
    from weatherpred.statistical_replay_data import label_rows_before

    rows, folds, models = [], [], []
    cutoffs = [stamp(d) for d in config["fit_dates"]]
    end = stamp(config["account_end"])
    scenario = next(s for s in config["scenarios"] if s["name"] == config["training_scenario"])
    for index, cutoff in enumerate(cutoffs):
        next_cutoff = cutoffs[index + 1] if index + 1 < len(cutoffs) else end
        training = [
            p
            for p in panel
            if stamp(config["training_start"])
            <= p["decision_ts"]
            < cutoff - config["decision_buffer_days"] * 86400
            and eligible(p, config)
        ]
        evaluation = [p for p in panel if cutoff <= p["decision_ts"] < next_cutoff]
        usable = [p for p in evaluation if eligible(p, config)]
        label_hash = latest_available = actual_received = None
        training_record = None
        if candidate["requires_fit"]:
            labels = label_rows_before(training, private_lookup, cutoff, scenario)
            if candidate["id"] == "logistic_offset":
                y = np.asarray([r["probability_label"] for r in labels], dtype=float)
                mask = np.asarray([r["probability_known"] for r in labels], dtype=bool)
                times = np.asarray([r["probability_release_ts"] or 0 for r in labels], dtype=float)
            else:
                y = np.asarray([r["net_return_labels"] for r in labels], dtype=float)
                mask = np.asarray([r["net_return_known"] for r in labels], dtype=bool)
                times = np.asarray(
                    [[v or 0 for v in r["net_return_release_ts"]] for r in labels], dtype=float
                )
            heads = mask[:, None] if mask.ndim == 1 else mask
            known_days = [
                len({r["day"] for r, known in zip(training, head, strict=True) if known}) for head in heads.T
            ]
            if min(known_days) < config["minimum_training_days"]:
                raise ValueError(
                    f"{candidate['id']}:{iso(cutoff)} released training days by head: {known_days}"
                )
            weight = event_balanced_weights(
                [p["day"] for p in training], [p["event"] for p in training], mask
            )
            label_hash = digest(labels)
            latest_available = iso(float(times[mask].max()))
            actual_received = iso(latest_receipt(labels))
            training_record = put(
                archive,
                "e032_training_labels_gzip",
                f"{identifier}:{candidate['id']}:{cutoff}",
                {
                    "fit_cutoff": iso(cutoff),
                    "labels": labels,
                    "opportunity_ids": [p["opportunity_id"] for p in training],
                },
            )
            artifact = fit_candidate(
                candidate["id"],
                [p["feature_vector"] for p in training],
                y,
                weights=weight,
                known_mask=mask,
                label_known_ts=times,
                fit_cutoff_ts=cutoff,
            )
        else:
            artifact = fit_candidate(candidate["id"], np.empty((0, len(FEATURE_NAMES))))
        models.append(artifact)
        prediction = predict_candidate(
            artifact,
            np.asarray([p["feature_vector"] for p in usable], dtype=float).reshape(-1, len(FEATURE_NAMES)),
        )
        predictions = {
            p["opportunity_id"]: value.tolist() if isinstance(value, np.ndarray) else float(value)
            for p, value in zip(usable, prediction["values"], strict=True)
        }
        model_record = put(archive, "e032_model_gzip", f"{identifier}:{candidate['id']}:{cutoff}", artifact)
        fold = {
            "fit_cutoff": iso(cutoff),
            "model_sha256": digest(artifact),
            "training_labels_sha256": label_hash,
            "latest_training_label_available_at": latest_available,
            "latest_training_label_received_at": actual_received,
            "training_rows": len(training) if candidate["requires_fit"] else 0,
            "evaluation_rows": len(evaluation),
            "feature_eligible_evaluation_rows": len(usable),
            "model_record": model_record,
            "training_label_record": training_record,
        }
        folds.append(fold)
        first_row = len(rows)
        for opportunity in evaluation:
            rows.append(
                {
                    "opportunity": opportunity,
                    **{
                        k: opportunity[k]
                        for k in (
                            "opportunity_id",
                            "event",
                            "day",
                            "ticker",
                            "series",
                            "decision_ts",
                            "yes_ask",
                            "no_ask",
                            "signal_provenance",
                        )
                    },
                    "decision_at": iso(opportunity["decision_ts"]),
                    "fold_fit_cutoff": iso(cutoff),
                    "feature_available_at": iso(opportunity["signal_provenance"]["assumed_available_ts"]),
                    "feature_received_at": iso(opportunity["signal_provenance"]["available_ts"]),
                    "eligible": eligible(opportunity, config),
                    "kind": prediction["kind"],
                    "prediction": predictions.get(opportunity["opportunity_id"]),
                }
            )
        fold_rows = rows[first_row:]
        fold["prediction_record"] = put(
            archive,
            "e032_fold_predictions_gzip",
            f"{identifier}:{candidate['id']}:{cutoff}",
            {
                "candidate_id": candidate["id"],
                "fit_cutoff": iso(cutoff),
                "model_sha256": digest(artifact),
                "predictions": fold_rows,
                **choose_decisions(fold_rows, candidate["id"], config),
            },
        )
    selected = choose_decisions(rows, candidate["id"], config)
    return {
        "candidate_id": candidate["id"],
        "predictions": rows,
        **selected,
        "provenance": {
            "availability_mode": "conditional_historical",
            "folds": folds,
            "model_sha256": digest(models),
            "feature_view_sha256": digest(panel),
        },
        "prediction_kind": models[0]["prediction_kind"],
        "actual_fills": 0,
        "promotion_eligible": False,
    }


def forecast_metrics(predictions, labels):
    """Same released event/day panel, without counting contract sides as days."""
    by_id = {r["opportunity_id"]: r for r in labels}
    daily = defaultdict(lambda: defaultdict(list))
    return_daily = defaultdict(lambda: defaultdict(list))
    return_values = []
    known_targets = unknown_targets = 0
    for row in predictions:
        if row["prediction"] is None:
            continue
        label = by_id[row["opportunity_id"]]
        if row["kind"] == "net_return":
            paired = [
                (float(p), y)
                for p, y, known in zip(
                    row["prediction"], label["net_return_labels"], label["net_return_known"], strict=True
                )
                if known
            ]
            known_targets += len(paired)
            unknown_targets += 2 - len(paired)
            return_values.extend(float(p) for p in row["prediction"])
            if paired:
                return_daily[row["day"]][row["event"]].append(
                    sum((p - y) ** 2 for p, y in paired) / len(paired)
                )
            continue
        if row["kind"] != "probability":
            continue
        if not label["probability_known"]:
            continue
        p = float(np.clip(row["prediction"], 1e-12, 1 - 1e-12))
        y = label["probability_label"]
        daily[row["day"]][row["event"]].append(((p - y) ** 2, -(y * math.log(p) + (1 - y) * math.log1p(-p))))
    values = [np.mean([np.mean(v, axis=0) for v in events.values()], axis=0) for events in daily.values()]
    means = np.mean(values, axis=0) if values else [None, None]
    return_means = [float(np.mean([np.mean(v) for v in events.values()])) for events in return_daily.values()]
    return {
        "brier": None if means[0] is None else float(means[0]),
        "log_loss": None if means[1] is None else float(means[1]),
        "days": len(daily),
        "net_return_mse": float(np.mean(return_means)) if return_means else None,
        "net_return_days": len(return_means),
        "known_return_targets": known_targets,
        "unknown_return_targets": unknown_targets,
        "return_prediction_range": [min(return_values), max(return_values)] if return_values else None,
        "return_predictions_outside_unit_payoff_range": sum(p < -1 or p > 1 for p in return_values),
        "return_prediction_note": "Raw ridge estimates are not calibrated profit guarantees; no post-score clipping. Unknown targets are excluded from error metrics and retained in coverage.",
        "weighting": "equal days, equal events within day, equal eligible contracts within event",
        "probability_scope": "Individual binary marginals; no claim that fitted probabilities sum to one across a ladder",
    }


def execute(archive, identifier, output_root=Path("reports")):
    from weatherpred.research_campaign import CampaignLedger, TrialRejected
    from weatherpred.statistical_replay_data import (
        build_opportunities,
        label_rows_before,
        make_private_lookup,
        resolve_intents,
    )

    protocol = load_protocol(archive, identifier)
    config = protocol["config"]
    if archive.latest("e032_started", str(identifier)):
        raise ValueError("This registered attempt already started; no automatic retry")
    put(archive, "e032_started", identifier, {"started_at": utcnow().isoformat()}, False)
    directory = Path(output_root) / f"E032-run-{identifier}"
    directory.mkdir(parents=True, exist_ok=False)
    dataset_row = archive.db.execute(
        "SELECT * FROM records WHERE id=?", (config["dataset_record_id"],)
    ).fetchone()
    if dataset_row["body_sha256"] != config["dataset_body_sha256"]:
        raise ValueError("Dataset pin changed")
    ledger = CampaignLedger(directory / "campaign.jsonl")
    plan = {
        "schema_version": 1,
        "campaign_id": f"{config['experiment']}:{identifier}",
        "phase": config["phase"],
        "availability_mode": config["availability_mode"],
        "candidates": config["candidates"],
        **{k: config[k] for k in ("max_trials", "max_trial_seconds", "max_total_seconds")},
        "fit_cutoff": iso(stamp(config["fit_dates"][0])),
        "fit_cutoffs": [iso(stamp(d)) for d in config["fit_dates"]],
        "evaluation_start": iso(stamp(config["fit_dates"][0])),
        "evaluation_end": iso(stamp(config["account_end"])),
        "pins": {
            "sources": protocol["source_hashes"],
            "config": {
                "path": protocol["config_path"],
                "sha256": digest(Path(protocol["config_path"]).read_bytes()),
            },
            "dataset": {
                "path": str((archive.root / "blobs" / dataset_row["body_sha256"]).resolve()),
                "sha256": dataset_row["body_sha256"],
            },
        },
    }
    ledger.register(plan)
    dataset = read_json_record(archive, dataset_row, True)
    built = build_opportunities(dataset)
    panel = built["opportunities"]
    private = make_private_lookup(dataset)
    feature_record = put(archive, "e032_features_gzip", identifier, built)
    all_accounts, trials = {}, []
    prior_alarm = signal.getsignal(signal.SIGALRM)

    def timeout(_number, _frame):
        raise TimeoutError("Registered per-candidate wall-time budget exhausted")

    for candidate in config["candidates"]:
        name = candidate["id"]
        try:
            ledger.start_trial(name)
        except ValueError as error:
            condition = {"status": "not_started", "candidate_id": name, "reason": str(error)}
            put(archive, "e032_trial_not_started", f"{identifier}:{name}", condition, False)
            trials.append(condition)
            continue
        start = time.monotonic()
        terminal_committed = False
        signal.signal(signal.SIGALRM, timeout)
        signal.alarm(config["max_trial_seconds"])
        try:
            predictions = fit_predict_candidate(archive, identifier, candidate, panel, private, config)
            path = write_json(directory / f"{name}_predictions.json", predictions)
            ledger.commit_predictions(name, path.resolve())
            prediction_record = put(archive, "e032_predictions_gzip", f"{identifier}:{name}", predictions)
            # Verify the actual archived content binding before the pure broker is invoked.
            row = archive.db.execute(
                "SELECT * FROM records WHERE id=?", (prediction_record["record_id"],)
            ).fetchone()
            if read_json_record(archive, row, True) != predictions:
                raise ValueError("Prediction archive differs from committed decisions")
            proof = {**prediction_record, "decisions_sha256": digest(predictions["decisions"])}
            labels = label_rows_before(
                [r["opportunity"] for r in predictions["predictions"]],
                private,
                stamp(config["account_end"]),
                config["scenarios"][0],
            )
            label_record = put(archive, "e032_evaluation_labels_gzip", f"{identifier}:{name}", labels)
            summaries, result_records = {}, {}
            candidate_accounts = {}
            for scenario in config["scenarios"]:
                resolved = resolve_intents(
                    predictions["decisions"],
                    private,
                    scenario,
                    stamp(config["account_end"]),
                    prediction_artifact=proof,
                )
                intent_record = put(
                    archive, "e032_intents_gzip", f"{identifier}:{name}:{scenario['name']}", resolved
                )
                result = replay_candidate(resolved["orders"], config, name, scenario)
                verified = replay_candidate(resolved["orders"], config, name, scenario, "verified")
                if verified["account"]["accepted_trades"] or verified["account"]["cash"] != 200:
                    raise ValueError("Late historical receipts incorrectly passed verified replay")
                candidate_accounts[f"{name}:{scenario['name']}"] = result
                summaries[scenario["name"]] = {
                    **summarize_account(result),
                    "endpoint_statuses": resolved["status_counts"],
                    "receipt_verified_ending_cash": verified["account"]["cash"],
                }
                result_records[scenario["name"]] = put(
                    archive,
                    "e032_account_gzip",
                    f"{identifier}:{name}:{scenario['name']}",
                    {**result, "intent_record": intent_record, "verified_account": verified},
                )
            result = {
                "candidate_id": name,
                "prediction_sha256": digest(path.read_bytes()),
                "label_artifact_sha256": digest(labels),
                "evaluation_end": plan["evaluation_end"],
                "metrics": summaries,
                "forecast_metrics": forecast_metrics(predictions["predictions"], labels),
                "prediction_record": prediction_record,
                "label_record": label_record,
                "account_records": result_records,
                "elapsed_seconds": time.monotonic() - start,
                "promotion_eligible": False,
            }
            put(archive, "e032_trial_result_gzip", f"{identifier}:{name}", result)
            signal.alarm(0)
            ledger.finish_trial(name, result)
            terminal_committed = True
            all_accounts.update(candidate_accounts)
            trials.append({"status": "completed", **result})
            print(
                canonical(
                    {
                        "candidate": name,
                        "status": "completed",
                        "cash": {k: v["cash"] for k, v in summaries.items()},
                    }
                ),
                flush=True,
            )
        except Exception as error:
            if terminal_committed:
                raise  # An operational failure after commit cannot rewrite a successful trial as failed.
            failure = {
                "candidate_id": name,
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
                "elapsed_seconds": time.monotonic() - start,
            }
            if not isinstance(error, TrialRejected):
                ledger.fail_trial(name, failure["error"])
            put(archive, "e032_trial_failure", f"{identifier}:{name}", failure, False)
            trials.append(failure)
            print(canonical(failure), flush=True)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, prior_alarm)
    report = {
        "experiment": config["experiment"],
        "registration_record_id": identifier,
        "config": config,
        "coverage": built["census"],
        "feature_record": feature_record,
        "trials": trials,
        "comparison": compare_accounts(all_accounts, config),
        "campaign_summary": ledger.summary(),
        "campaign_verification": ledger.verify(),
        "annual_account_scope": "365-day restricted research account; cash before March2026 and whenever unsupported/unselected. No historical data read from the sealed 2025 quarter. Not an optimal full-year opportunity result.",
        "profitability_proven": False,
        "promotion_eligible": False,
        "actual_fills": 0,
        "resource_acceptance": "Pending separate parent supervisor receipt; a worker report alone is not an accepted campaign.",
    }
    report_record = put(archive, "e032_report_gzip", identifier, report)
    write_json(directory / "report.json", {**report, "report_record": report_record})
    return {
        "report_record": report_record,
        "report_path": str(directory / "report.json"),
        "campaign": ledger.summary(),
    }


def supervised_execute(archive, identifier):
    protocol = load_protocol(archive, identifier)
    config = protocol["config"]
    if archive.latest("e032_supervisor_started", str(identifier)):
        raise ValueError("Registered supervised attempt already used")
    put(
        archive,
        "e032_supervisor_started",
        identifier,
        {"command": "fixed E032 worker", "automatic_retry": False},
        False,
    )
    directory = Path(f"reports/E032-supervisor-{identifier}")
    result = supervise(
        [
            sys.executable,
            "-m",
            "research.experiments.e032_statistical_lab",
            "--worker-record-id",
            str(identifier),
        ],
        Path.cwd(),
        directory,
        config["max_total_seconds"],
        config["max_memory_bytes"],
        "data/STOP_E032",
    )
    receipt = put(archive, "e032_resources", identifier, result, False)
    if result["status"] != "completed":
        put(archive, "e032_campaign_failure", identifier, result, False)
    else:
        report_row = archive.latest("e032_report_gzip", str(identifier))
        if report_row is None:
            raise ValueError("Worker exited without an immutable campaign report")
        report = read_json_record(archive, report_row, True)
        if report["campaign_summary"]["running"]:
            raise ValueError("Campaign report retains unfinished trials")
        accepted = {
            "worker_report_record_id": report_row["id"],
            "worker_report_body_sha256": report_row["body_sha256"],
            "resource_record": receipt,
            "resource_limits_accepted": True,
            "complete_candidate_family": report["campaign_summary"]["results"] == config["max_trials"],
            "promotion_eligible": False,
        }
        put(archive, "e032_campaign_accepted", identifier, accepted, False)
    return {
        "status": result["status"],
        "resources_record": receipt,
        "report_path": f"reports/E032-run-{identifier}/report.json",
        "elapsed_seconds": result["elapsed_seconds"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--register", action="store_true")
    group.add_argument("--run-record-id", type=int)
    group.add_argument("--worker-record-id", type=int)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    archive = Archive()
    try:
        if args.register:
            print(canonical({"registration_record_id": register(archive, args.config)}), flush=True)
        elif args.worker_record_id:
            print(canonical(execute(archive, args.worker_record_id)), flush=True)
        else:
            print(canonical(supervised_execute(archive, args.run_record_id)), flush=True)
    finally:
        archive.close()


if __name__ == "__main__":
    main()
