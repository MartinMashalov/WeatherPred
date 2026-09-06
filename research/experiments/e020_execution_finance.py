"""Prospectively specified capacity/exit diagnostic, never actual order submission."""

import hashlib
import json
from pathlib import Path

from research.experiments.e019_rain_pairs import books, context
from weatherpred.archive import Archive, canonical
from weatherpred.books import purchase_cost
from weatherpred.execution_finance import D, capital_sensitivity, liquidation_value, order_intent
from weatherpred.http import PublicClient
from weatherpred.paper import PaperLedger
from weatherpred.timeutil import iso, utcnow


def main():
    archive = Archive()
    client = PublicClient(archive, interval=0.5)
    try:
        protocol = {
            "experiment": "E020-execution-finance-diagnostic-v1",
            "registered_at": iso(utcnow()),
            "parent_paper_run_record_id": 72680,
            "events": ["KXRAIN-26SEP06", "KXRAINWKND-26SEP05"],
            "tickers": ["KXRAIN-26SEP06-NYC", "KXRAINWKND-26SEP05-NYC"],
            "sides": ["yes", "no"],
            "quantities": list(range(1, 101)),
            "capital_caps_dollars": [5, 10, 25, 50, 100],
            "scenarios": json.loads(Path("config/e019_rain_pairs.json").read_text())["scenarios"],
            "minimum_conditional_surplus_per_pair": ".03",
            "purpose": "Measure same-snapshot capacity curves, capital sensitivity, current fee-aware exits and reviewable V2 payloads. No higher-risk recommendation, new simulated fills, reallocation, source-failure estimate or historical holdout access.",
        }
        source_paths = [Path(__file__), Path("weatherpred/execution_finance.py")]
        protocol["code_sha256"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
        protocol["source_record_ids"] = {
            str(p): archive.append("research_source", str(p), utcnow(), {}, p.read_bytes())
            for p in source_paths
        }
        run_id = archive.append(
            "experiment_protocol", protocol["experiment"], utcnow(), {}, canonical(protocol).encode()
        )
        contexts = [context(client, event) for event in protocol["events"]]
        markets = [
            next(m for m in data["markets"] if m["ticker"] == ticker)
            for (data, _, _), ticker in zip(contexts, protocol["tickers"], strict=True)
        ]
        snapshot, book_id, _, received = books(client, set(protocol["tickers"]))
        evidence = [book_id, *[r for _, _, refs in contexts for r in refs]]
        curves, caps = [], []
        for scenario in protocol["scenarios"]:
            candidate_rows = []
            for quantity in protocol["quantities"]:
                quotes = [
                    purchase_cost(
                        snapshot[t],
                        side,
                        quantity,
                        context_row[1],
                        scenario["depth_retained"],
                        scenario["slippage"],
                    )
                    for t, side, context_row in zip(
                        protocol["tickers"], protocol["sides"], contexts, strict=True
                    )
                ]
                row = {
                    "scenario": scenario["name"],
                    "quantity": quantity,
                    "full_displayed_size_available": all(c is not None for c in quotes),
                }
                if row["full_displayed_size_available"]:
                    total = sum(q.total for q in quotes)
                    net = quantity - total
                    row.update(
                        total_cost=str(total),
                        conditional_surplus=str(net),
                        conditional_surplus_per_pair=str(net / quantity),
                        fees=str(sum(q.fees for q in quotes)),
                        worst_prices=[str(q.worst_price) for q in quotes],
                    )
                    if net / quantity >= D(protocol["minimum_conditional_surplus_per_pair"]):
                        candidate_rows.append(row)
                curves.append(row)
            for cap in protocol["capital_caps_dollars"]:
                affordable = [r for r in candidate_rows if D(r["total_cost"]) <= cap]
                best = max(
                    affordable, key=lambda r: (D(r["conditional_surplus"]), -r["quantity"]), default=None
                )
                caps.append(
                    {
                        "scenario": scenario["name"],
                        "capital_cap": cap,
                        "best_displayed_surplus_candidate": best,
                        "is_allocation_recommendation": False,
                    }
                )
        # Current exits use the actual paper holdings, without modifying them.
        paper = PaperLedger(archive, protocol["parent_paper_run_record_id"])
        exits, sensitivities, intents = [], [], []
        for account in paper.state["accounts"]:
            positions = [p for p in paper.state["positions"].values() if p["account"] == account]
            if not positions:
                continue
            quantity = min(D(p["quantity"]) for p in positions)
            cost = sum(D(p["cost"]) for p in positions)
            for scenario in protocol["scenarios"]:
                legs = []
                for p in positions:
                    index = protocol["tickers"].index(p["ticker"])
                    legs.append(
                        {
                            "ticker": p["ticker"],
                            **liquidation_value(
                                snapshot[p["ticker"]],
                                p["side"],
                                p["quantity"],
                                contexts[index][1],
                                scenario["depth_retained"],
                                scenario["slippage"],
                            ),
                        }
                    )
                full = all(l["full_displayed_exit_available"] for l in legs)
                proceeds = sum(D(l["proceeds_after_fees"]) for l in legs)
                exits.append(
                    {
                        "paper_account": account,
                        "exit_scenario": scenario["name"],
                        "legs": legs,
                        "both_legs_have_full_displayed_exit": full,
                        "cash_proceeds_if_all_quoted_slices_execute": str(proceeds),
                        "conditional_exit_pnl_if_fully_exited": str(proceeds - cost) if full else None,
                        "holding_conditional_payout": str(quantity),
                        "forgone_normal_payout_for_early_exit": str(quantity - proceeds) if full else None,
                    }
                )
            if 0 < cost < quantity and cost < 100:
                sensitivities.append({"paper_account": account, **capital_sensitivity(quantity, cost)})
        for i, m in enumerate(markets):
            for tif in ("immediate_or_cancel", "fill_or_kill"):
                intents.append(
                    {
                        "economic_action": "buy_" + protocol["sides"][i],
                        "payload": order_intent(
                            m,
                            protocol["sides"][i],
                            "buy",
                            1,
                            ".10" if i == 0 else ".81",
                            key=f"E020:{run_id}:{i}:{tif}",
                            time_in_force=tif,
                        ),
                        "submitted": False,
                        "atomic_pair_guarantee": False,
                    }
                )
        result = {
            "run_record_id": run_id,
            "generated_at": iso(utcnow()),
            "book_received_at": iso(received),
            "raw_source_ids": evidence,
            "capacity_curves": curves,
            "capital_caps": caps,
            "current_position_exit_diagnostics": exits,
            "source_failure_sensitivity": sensitivities,
            "example_order_intents": intents,
            "real_money_orders": 0,
            "new_simulated_fills": 0,
            "profitability_proven": False,
            "limits": protocol["purpose"],
        }
        identifier = archive.append(
            "experiment_report", "E020_execution_finance", utcnow(), {}, canonical(result).encode()
        )
        Path("reports/E020_execution_finance.json").write_text(
            json.dumps({"record_id": identifier, **result}, indent=2)
        )
        print(
            json.dumps(
                {
                    "record_id": identifier,
                    "run_record_id": run_id,
                    "book_received_at": iso(received),
                    "capital_caps": caps,
                    "exits": [{k: v for k, v in e.items() if k != "legs"} for e in exits],
                    "source_failure_sensitivity": sensitivities,
                },
                indent=2,
            )
        )
    finally:
        client.close()
        archive.close()


if __name__ == "__main__":
    main()
