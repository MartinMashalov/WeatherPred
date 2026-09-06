"""Export compact public evidence; large raw archives and operator files stay local."""

import hashlib
import json
import shutil
from pathlib import Path


def main():
    root = Path("evidence")
    root.mkdir(exist_ok=True)
    e13 = json.loads(Path("reports/E013_autoresearch.json").read_text())
    e14 = json.loads(Path("reports/E014_intraday_bounds.json").read_text())
    audit = json.loads(Path("reports/E013_audit.json").read_text())
    paper = json.loads(Path("reports/E009_audit.json").read_text())
    results = []
    for batch, report in (("E013", e13), ("E014", e14)):
        for row in report["results"]:
            splits = {
                name: {k: v for k, v in value.items() if k not in ("days", "daily_log_growth")}
                for name, value in row["split_results"].items()
            }
            results.append(
                {
                    "batch": batch,
                    "policy": row["policy"],
                    "scenario": row["scenario"],
                    "status_counts": row["status_counts"],
                    "split_results": splits,
                    "development_diagnostic": row["development_diagnostic"],
                }
            )
    result_body = json.dumps(results, indent=2)
    (root / "strategy-results.json").write_text(result_body)
    selected = e13["monthly_selection"]
    summary = {
        "research_date": "2026-09-06",
        "purpose": "Dated public research evidence, not live investment performance",
        "unique_policy_definitions": 612,
        "policy_cost_comparisons": len(results),
        "development_contracts": e13["source_markets"],
        "development_events": e13["source_events"],
        "train_period": "2025-01-01 through 2025-06-30",
        "development_validation_period": "2025-07-01 through 2025-09-30",
        "final_holdout": "October-December2025 remains sealed",
        "costed_positive_e013_validation_policies": sum(
            r["scenario"] == "costed" and r["split_results"]["validation"]["conditional_pnl"] > 0
            for r in e13["results"]
        ),
        "monthly_selector_conditional_pnl_usd": selected["result"]["conditional_pnl"],
        "monthly_selector_initial_bankroll_usd": 100,
        "monthly_choices": selected["choices"],
        "raw_quote_audit": audit,
        "paper_execution_audit": paper,
        "e014_calibration": e14["calibration"],
        "e014_partial_reports": e14["partial_reports"],
        "experiment_protocols": {"E013": e13["protocol_record_id"], "E014": e14["protocol_record_id"]},
        "published_results_sha256": hashlib.sha256(result_body.encode()).hexdigest(),
        "real_money_orders": 0,
        "profitability_proven": False,
        "limitations": [
            "Historical candles do not establish fills, depth or original public receipt.",
            "Historical fee cases are assumptions, not verified contemporaneous2025schedules.",
            "Candidate trades reuse weather outcomes across alternative policies; counts are not independent sample sizes.",
            "Cash accounting carries unresolved positions at cost; realized drawdown understates possible liquidation risk.",
            "Unit tests and raw-data replay establish engineering checks, not profitability.",
        ],
    }
    (root / "summary.json").write_text(json.dumps(summary, indent=2))
    for name in ("E013_audit.json", "E014_source_diagnostics.json", "E009_audit.json"):
        shutil.copyfile(Path("reports") / name, root / name)
    shutil.copyfile("reports/verification-20260906-1428.txt", root / "verification.txt")
    shutil.copyfile("reports/autoresearch.html", root / "explorer.html")
    print(
        json.dumps(
            {
                "exported_comparisons": len(results),
                "raw_archive_included": False,
                "profitability_proven": False,
            }
        )
    )


if __name__ == "__main__":
    main()
