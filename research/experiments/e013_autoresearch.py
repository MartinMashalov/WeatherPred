"""Preregister, resume and compare a finite batch of trading-policy experiments."""

import argparse
import fcntl
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np

from weatherpred.archive import Archive, canonical
from weatherpred.historical_candles import normalize_historical_candles
from weatherpred.timeutil import iso, parse_time, utcnow
from weatherpred.trading_research import candidates, conditional_trade, portfolio, select_trade


def timestamp(day):
    return int(datetime.combine(date.fromisoformat(day), datetime.min.time(), UTC).timestamp())


def calendar(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days)]


def load_inputs(archive, config, as_of):
    dataset = archive.latest("experiment_dataset", "E002_development_markets", as_of)
    if archive.body(dataset) != Path("reports/E002_development_markets.jsonl").read_bytes():
        raise ValueError("Development metadata differs from original archive")
    markets, by_event, raw_cache, sources, failures = {}, defaultdict(list), {}, {dataset["id"]}, []
    for line in archive.body(dataset).decode().splitlines():
        m = json.loads(line)
        # Reject dates BEFORE consulting labels or additional raw market metadata.
        if not config["development_start"] <= m["day"] < config["validation_end_exclusive"]:
            raise ValueError("Sealed or unregistered date reached trading research")
        rec = archive.latest("e002_candles", m["ticker"], as_of)
        if rec is None:
            failures.append({"ticker": m["ticker"], "reason": "missing_candles"})
            continue
        source_id = m["source_record_id"]
        if source_id not in raw_cache:
            raw = archive.db.execute("SELECT * FROM records WHERE id=?", (source_id,)).fetchone()
            raw_cache[source_id] = {v["ticker"]: v for v in archive.json(raw)["markets"]}
        original = raw_cache[source_id][m["ticker"]]
        if original["event_ticker"] != m["event"] or (original["result"] == "yes") != bool(m["outcome"]):
            raise ValueError("Original settlement membership mismatch")
        settled = original.get("settlement_ts")
        m["settled_ts"] = parse_time(settled).timestamp() if settled else None
        m["candle_source_record_id"] = rec["id"]
        data = archive.json(rec)
        if data["ticker"] != m["ticker"]:
            raise ValueError("Candle identity mismatch")
        quotes = {}
        for candle in normalize_historical_candles(data["candlesticks"]):
            ts = candle["end_period_ts"]
            if ts in quotes:
                raise ValueError("Duplicate hourly candle")
            bid, ask = candle["yes_bid"].get("close_dollars"), candle["yes_ask"].get("close_dollars")
            if bid is None or ask is None:
                continue
            quotes[ts] = {"bid": float(bid), "ask": float(ask)}
        markets[m["ticker"]] = {"metadata": m, "quotes": quotes}
        by_event[m["event"]].append(m["ticker"])
        sources.update((source_id, rec["id"]))
    return markets, dict(by_event), sorted(sources), failures


def evaluate(markets, events, policy, scenario, config):
    decisions, trades = [], []
    for event, tickers in sorted(events.items()):
        m = markets[tickers[0]]["metadata"]
        signal_ts = int(parse_time(m["source_period_end"]).timestamp()) - policy["horizon_hours"] * 3600
        signal_rows = []
        for ticker in tickers:
            current = markets[ticker]
            if not current["metadata"]["open_ts"] <= signal_ts < current["metadata"]["close_ts"]:
                continue
            quote = current["quotes"].get(signal_ts)
            if quote is None:
                continue
            past = current["quotes"].get(signal_ts - policy["lookback_hours"] * 3600, {})
            signal_rows.append(
                dict(ticker=ticker, **quote, past_bid=past.get("bid"), past_ask=past.get("ask"))
            )
        selection = select_trade(signal_rows, policy)
        context = {"event": event, "day": m["day"], "series": m["series"], "signal_ts": signal_ts}
        if selection is None:
            decisions.append(dict(context, status="no_signal", available_contracts=len(signal_rows)))
            continue
        chosen = markets[selection["ticker"]]
        result = dict(
            context,
            **{
                k: v
                for k, v in conditional_trade(
                    selection, chosen["metadata"], chosen["quotes"], signal_ts, policy, scenario
                ).items()
                if k not in context
            },
        )
        decisions.append(result)
        if result["status"] == "conditional_trade":
            trades.append(result)
    results = {}
    for name, start, end in (
        ("train", config["development_start"], config["train_end_exclusive"]),
        ("validation", config["train_end_exclusive"], config["validation_end_exclusive"]),
    ):
        results[name] = summarize(
            portfolio(trades, config, timestamp(start), timestamp(end)), start, end, config
        )
    return {
        "policy": policy,
        "scenario": scenario["name"],
        "status_counts": dict(Counter(r["status"] for r in decisions)),
        "split_results": results,
        "trades": trades,
        "decisions": decisions,
        "decision_sha256": hashlib.sha256(canonical(decisions).encode()).hexdigest(),
    }


def summarize(account, start, end, config):
    days = calendar(start, end)
    pnl = np.asarray([account["daily_realized_pnl"].get(d, 0) for d in days])
    equity = np.r_[float(config["bankroll"]), float(config["bankroll"]) + np.cumsum(pnl)]
    if np.any(equity <= 0):
        raise ValueError("Conditional bankroll ruined; logarithm undefined")
    log_growth = np.diff(np.log(equity))
    trades = account["released_trades"]
    return {
        "calendar_days": len(days),
        "released_trades": len(trades),
        "trade_days": len({datetime.fromtimestamp(t["exit_ts"], UTC).date().isoformat() for t in trades}),
        "conditional_pnl": float(pnl.sum()),
        "mean_daily_log_growth": float(log_growth.mean()),
        "daily_log_growth": log_growth.tolist(),
        "days": days,
        "cash": account["cash"],
        "locked_cost": account["locked_cost"],
        "cost_basis_equity": account["cost_basis_equity"],
        "pending_trades": account["pending_trades"],
        "risk_rejections": account["risk_rejections"],
        "maximum_realized_drawdown": float(np.max(1 - equity / np.maximum.accumulate(equity))),
        "exit_kinds": dict(Counter(t["exit_kind"] for t in trades)),
        "entry_fees": sum(t["entry_fee"] for t in trades),
        "exit_fees": sum(t["exit_fee"] for t in trades),
    }


def simultaneous_diagnostics(results, config):
    # Shared resampling keeps both cross-city and cross-strategy dependence.
    x = np.array([r["split_results"]["validation"]["daily_log_growth"] for r in results]).T
    n, count = x.shape
    rng = np.random.default_rng(config["seed"])
    starts = rng.integers(0, n, size=(config["bootstrap_resamples"], (n + 6) // 7))
    indices = ((starts[..., None] + np.arange(7)) % n).reshape(len(starts), -1)[:, :n]
    weights = np.zeros((len(indices), n))
    for i, row in enumerate(indices):
        weights[i] = np.bincount(row, minlength=n) / n
    boot = weights @ x
    means, se = x.mean(axis=0), boot.std(axis=0, ddof=1)
    active = se > 1e-14
    standardized = np.zeros_like(boot)
    standardized[:, active] = (boot[:, active] - means[active]) / se[active]
    null_max = standardized.max(axis=1)
    lower, upper = np.quantile(boot, [0.025, 0.975], axis=0)
    for j in range(count):
        statistic = means[j] / se[j] if active[j] else 0
        p = (1 + np.count_nonzero(null_max >= statistic)) / (len(null_max) + 1) if active[j] else 1.0
        results[j]["development_diagnostic"] = {
            "unadjusted_lower_mean_daily_log_growth": float(lower[j]),
            "unadjusted_upper_mean_daily_log_growth": float(upper[j]),
            "familywise_max_statistic_pvalue": float(p),
            "family_size": count,
            "block_days": 7,
            "resamples": len(null_max),
            "promotion_eligible": False,
        }


def monthly_choices(results, config):
    costed = {r["policy"]["id"]: r for r in results if r["scenario"] == "costed"}
    stress = {r["policy"]["id"]: r for r in results if r["scenario"] == "stress"}
    choices, selected = [], []
    for month in range(3, 10):
        start = f"2025-{month:02}-01"
        end = f"2025-{month + 1:02}-01"
        ranked = []
        for key, r in costed.items():
            # portfolio stops at cutoff before observing later release amounts.
            a = portfolio(r["trades"], config, timestamp(config["development_start"]), timestamp(start))
            s = summarize(a, config["development_start"], start, config)
            if s["trade_days"] < config["minimum_selection_trade_days"]:
                continue
            daily = np.array(s["daily_log_growth"])
            blocks = np.array([daily[i : i + 7].sum() for i in range(0, len(daily) - 6, 7)])
            standard_error = blocks.std(ddof=1) / np.sqrt(len(blocks)) / 7
            criterion = daily.mean() - config["selection_penalty_standard_errors"] * standard_error
            stress_account = portfolio(
                stress[key]["trades"], config, timestamp(config["development_start"]), timestamp(start)
            )
            if criterion > 0 and stress_account["cost_basis_equity"] > config["bankroll"]:
                ranked.append((float(criterion), key, s))
        ranked.sort(key=lambda row: (-row[0], row[1]))
        if not ranked:
            choices.append(
                {"month": start, "selected_policy": None, "action": "cash", "eligible_candidates": 0}
            )
            continue
        criterion, key, training = ranked[0]
        chosen = costed[key]
        # Selection frozen from earlier releases; add the selected future-month orders afterward.
        selected.extend(t for t in chosen["trades"] if timestamp(start) <= t["entry_ts"] < timestamp(end))
        choices.append(
            {
                "month": start,
                "selected_policy": chosen["policy"],
                "training_criterion": criterion,
                "training_trade_days": training["trade_days"],
                "eligible_candidates": len(ranked),
            }
        )
    return {
        "choices": choices,
        "result": summarize(
            portfolio(selected, config, timestamp("2025-03-01"), timestamp("2025-10-01")),
            "2025-03-01",
            "2025-10-01",
            config,
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int)
    args = parser.parse_args()
    archive = Archive()
    try:
        with (archive.root / "autoresearch.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            paths = [
                Path(__file__),
                Path("config/e013_autoresearch.json"),
                Path("weatherpred/trading_research.py"),
                Path("weatherpred/historical_candles.py"),
                Path("weatherpred/fees.py"),
            ]
            hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
            config = json.loads(Path("config/e013_autoresearch.json").read_text())
            policies = candidates(config)
            if args.run_record_id:
                rec = archive.db.execute("SELECT * FROM records WHERE id=?", (args.run_record_id,)).fetchone()
                protocol = archive.json(rec)
                if (
                    rec["kind"] != "experiment_protocol"
                    or protocol["source_hashes"] != hashes
                    or protocol["config"] != config
                ):
                    raise ValueError("Resume code/config differs from frozen batch")
                registration = args.run_record_id
            else:
                source_ids = {
                    str(p): archive.append("research_source", str(p), utcnow(), {}, p.read_bytes())
                    for p in paths
                }
                registration = archive.append(
                    "experiment_protocol",
                    config["experiment"],
                    utcnow(),
                    {},
                    canonical(
                        {
                            "config": config,
                            "policies": policies,
                            "source_hashes": hashes,
                            "source_record_ids": source_ids,
                        }
                    ).encode(),
                )
            print(
                json.dumps(
                    {
                        "registration": registration,
                        "policies": len(policies),
                        "cost_scenario_experiments": len(policies) * len(config["scenarios"]),
                    }
                ),
                flush=True,
            )
            registered_record = archive.db.execute(
                "SELECT * FROM records WHERE id=?", (registration,)
            ).fetchone()
            source_cutoff = parse_time(registered_record["available_at"])
            markets, events, sources, failures = load_inputs(archive, config, source_cutoff)
            results = []
            for policy in policies:
                for scenario in config["scenarios"]:
                    if Path(config["stop_file"]).exists():
                        print(
                            json.dumps(
                                {"stopped": True, "registration": registration, "completed": len(results)}
                            ),
                            flush=True,
                        )
                        return
                    key = f"{registration}:{policy['id']}:{scenario['name']}"
                    cached = archive.latest("autoresearch_candidate", key)
                    if cached:
                        result = archive.json(cached)
                    else:
                        result = evaluate(markets, events, policy, scenario, config)
                        archive.append(
                            "autoresearch_candidate", key, utcnow(), {}, canonical(result).encode()
                        )
                    results.append(result)
                if len(results) % 72 == 0:
                    print(json.dumps({"registration": registration, "completed": len(results)}), flush=True)
            simultaneous_diagnostics(results, config)
            walkforward = monthly_choices(results, config)
            summary = [{k: v for k, v in r.items() if k not in ("trades", "decisions")} for r in results]
            report = {
                "generated_at": iso(utcnow()),
                "protocol_record_id": registration,
                "input_source_cutoff": iso(source_cutoff),
                "policies": len(policies),
                "experiments": len(results),
                "source_record_ids": sources,
                "source_failures": failures,
                "source_events": len(events),
                "source_markets": len(markets),
                "results": summary,
                "monthly_selection": walkforward,
                "actual_fills": 0,
                "network_requests": 0,
                "holdout_accessed": False,
                "profitability_proven": False,
                "limitations": [config[k] for k in ("holdout", "fees", "execution_limit", "promotion")]
                + [
                    "Cost-basis equity defers unrealized gains/losses and fees until release; reported drawdown is realized-only, not a full mark-to-market risk estimate.",
                    "Missing signal/entry quotes produce no conditional trade; report coverage explicitly. Missing scheduled exit quotes trigger the registered settlement fallback.",
                ],
            }
            archive.append("experiment_report", "E013_autoresearch", utcnow(), {}, canonical(report).encode())
            Path("reports/E013_autoresearch.json").write_text(json.dumps(report, indent=2))
            for scenario in config["scenarios"]:
                rows = [r for r in results if r["scenario"] == scenario["name"]]
                ordered = sorted(rows, key=lambda r: -r["split_results"]["validation"]["conditional_pnl"])
                print(
                    json.dumps(
                        {
                            "scenario": scenario["name"],
                            "positive_validation_pnl": sum(
                                r["split_results"]["validation"]["conditional_pnl"] > 0 for r in rows
                            ),
                            "best_exploratory_result": {
                                "policy": ordered[0]["policy"],
                                "validation": {
                                    k: v
                                    for k, v in ordered[0]["split_results"]["validation"].items()
                                    if k not in ("days", "daily_log_growth")
                                },
                                "diagnostic": ordered[0]["development_diagnostic"],
                            },
                        }
                    ),
                    flush=True,
                )
            print(
                json.dumps(
                    {
                        "monthly_choices": walkforward["choices"],
                        "monthly_selection_pnl": walkforward["result"]["conditional_pnl"],
                        "actual_fills": 0,
                        "profitability_proven": False,
                    }
                ),
                flush=True,
            )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
