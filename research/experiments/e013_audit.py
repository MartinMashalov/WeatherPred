"""Independent raw-quote and cent-fee audit of the trading research batch."""

import json
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import iso, parse_time, utcnow


def main():
    a = Archive()
    try:
        report_record = a.latest("experiment_report", "E013_autoresearch")
        report = a.json(report_record)
        registration = a.db.execute(
            "SELECT * FROM records WHERE id=?", (report["protocol_record_id"],)
        ).fetchone()
        protocol = a.json(registration)
        scenarios = {s["name"]: s for s in protocol["config"]["scenarios"]}
        quotes, original_markets = {}, {}
        for source_id in report["source_record_ids"]:
            r = a.db.execute("SELECT * FROM records WHERE id=?", (source_id,)).fetchone()
            if r["available_at"] > registration["available_at"]:
                raise ValueError("Post-registration source reached the batch")
            if r["kind"] != "experiment_dataset":
                payload = a.json(r)
                for market in payload.get("markets", []):
                    original_markets[market["ticker"]] = market
            if r["kind"] != "e002_candles":
                continue
            data = a.json(r)
            quotes[data["ticker"]] = {
                row["end_period_ts"]: (Decimal(row["yes_bid"]["close"]), Decimal(row["yes_ask"]["close"]))
                for row in data["candlesticks"]
                if row["yes_bid"].get("close") is not None and row["yes_ask"].get("close") is not None
            }
        counts = {
            "candidates": 0,
            "decisions": 0,
            "conditional_trades": 0,
            "quoted_exits": 0,
            "settlement_exits": 0,
        }
        maximum_error = 0.0
        for summary in report["results"]:
            key = f"{report['protocol_record_id']}:{summary['policy']['id']}:{summary['scenario']}"
            candidate = a.json(a.latest("autoresearch_candidate", key))
            scenario = scenarios[summary["scenario"]]
            counts["candidates"] += 1
            counts["decisions"] += len(candidate["decisions"])
            coefficient = Decimal("0.07") * Decimal(scenario["fee_multiplier"])
            slip = Decimal(scenario["slippage"])
            for t in candidate["trades"]:
                counts["conditional_trades"] += 1
                raw = quotes[t["ticker"]]
                signal_bid, signal_ask = raw[t["signal_ts"]]
                bid, ask = raw[t["entry_ts"]]
                if t["entry_ts"] != t["signal_ts"] + scenario["entry_delay_hours"] * 3600:
                    raise ValueError("Wrong entry delay")
                if not Decimal(0) < bid < ask < Decimal(1):
                    raise ValueError("Unquoted or crossed entry")
                side = t["side"]
                entry = (ask if side == "yes" else 1 - bid) + slip
                limit = min(Decimal("0.99"), (signal_ask if side == "yes" else 1 - signal_bid) + slip)
                if entry > limit:
                    raise ValueError("Conditional order exceeded its prior limit")
                fee = (coefficient * entry * (1 - entry)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
                cost = entry + fee
                expected = {"entry_price": entry, "entry_limit": limit, "entry_fee": fee, "entry_cost": cost}
                if t["exit_kind"] == "scheduled_quote":
                    counts["quoted_exits"] += 1
                    if t["exit_ts"] != t["entry_ts"] + summary["policy"]["exit_hours"] * 3600:
                        raise ValueError("Wrong scheduled exit timestamp")
                    bid, ask = raw[t["exit_ts"]]
                    price = (bid if side == "yes" else 1 - ask) - slip
                    fee = (coefficient * price * (1 - price)).quantize(
                        Decimal("0.01"), rounding=ROUND_CEILING
                    )
                    proceeds = price - fee
                    expected.update(exit_price=price, exit_fee=fee, exit_proceeds=proceeds)
                else:
                    counts["settlement_exits"] += 1
                    proceeds = Decimal(str(t["exit_proceeds"]))
                    if proceeds not in (0, 1) or t["exit_ts"] <= t["entry_ts"]:
                        raise ValueError("Invalid terminal payout or release time")
                    original = original_markets[t["ticker"]]
                    yes_won = original["result"] == "yes"
                    payout = int(yes_won if side == "yes" else not yes_won)
                    if (
                        proceeds != payout
                        or t["exit_ts"] != parse_time(original["settlement_ts"]).timestamp()
                    ):
                        raise ValueError("Raw settlement outcome or timestamp differs")
                expected["pnl"] = proceeds - cost
                for field, value in expected.items():
                    error = abs(float(value) - t[field])
                    maximum_error = max(maximum_error, error)
                    if error > 1e-10:
                        raise ValueError(f"Independent raw price/fee mismatch: {key}:{field}")
        result = {
            "generated_at": iso(utcnow()),
            "protocol_record_id": report["protocol_record_id"],
            "report_record_id": report_record["id"],
            **counts,
            "raw_candle_contracts": len(quotes),
            "maximum_arithmetic_difference": maximum_error,
            "network_requests": 0,
            "actual_fills": 0,
            "profitability_proven": False,
            "audit_scope": "All saved conditional entry prices/limits and scheduled exit prices independently reproduced from raw candles; cent fees recomputed without execution fee code. Does not establish depth, availability, optimal selection or independent statistical success.",
        }
        a.append("experiment_report", "E013_raw_quote_audit", utcnow(), {}, canonical(result).encode())
        Path("reports/E013_audit.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        a.close()


if __name__ == "__main__":
    main()
