"""E005: provisional-index arithmetic and first-seen latency, no trade simulation."""

import json
from bisect import bisect_right
from collections import Counter
from decimal import Decimal
from pathlib import Path

import numpy as np

from weatherpred.archive import Archive, canonical
from weatherpred.index import canonical_point
from weatherpred.index_reconstruction import reconstruct
from weatherpred.timeutil import iso, parse_time, utcnow


def numeric_summary(values):
    if not values:
        return None
    return {
        "count": len(values),
        "min": float(np.min(values)),
        "median": float(np.median(values)),
        "max": float(np.max(values)),
        "mean": float(np.mean(values)),
    }


def main():
    archive = Archive()
    protocol = json.loads(Path("config/e005_pending_index.json").read_text())
    source_ids = []
    for path in (
        Path(__file__),
        Path("weatherpred/index_reconstruction.py"),
        Path("config/e005_pending_index.json"),
    ):
        source_ids.append(archive.append("research_source", str(path), utcnow(), {}, path.read_bytes()))
    protocol_id = archive.append(
        "experiment_protocol",
        "E005-v1",
        utcnow(),
        {"source_record_ids": source_ids},
        canonical(protocol).encode(),
    )
    acquisition = json.loads(Path("reports/E004_acquisition.json").read_text())
    calibration_row = archive.db.execute(
        "SELECT * FROM records WHERE id=?", (acquisition["calibration_record_id"],)
    ).fetchone()
    configs = archive.json(calibration_row)["calibrations"]
    historical, exceptions = Counter(), []
    for day in acquisition["days"]:
        row = archive.db.execute("SELECT * FROM records WHERE id=?", (day["record_id"],)).fetchone()
        for p in archive.json(row)["timeseries"]:
            historical["points"] += 1
            try:
                value = reconstruct(p, configs, p["t"] + 300_000)
                observed = Decimal(str(p["v"]))
                historical["half_up_matches"] += observed == Decimal(value["nearest_half_up_f"])
                historical["half_even_matches"] += observed == Decimal(value["nearest_half_even_f"])
                historical["rounding_alternatives_differ"] += value["rounding_tie_disagreement"]
                if observed not in (
                    Decimal(value["nearest_half_up_f"]),
                    Decimal(value["nearest_half_even_f"]),
                ):
                    historical["neither_rounding_matches"] += 1
                    exceptions.append(
                        {
                            "point_ms": p["t"],
                            "value": p["v"],
                            "reconstruction": value,
                            "source_record_id": row["id"],
                        }
                    )
            except (ValueError, KeyError) as exc:
                historical["reconstruction_rejected"] += 1
                exceptions.append({"point_ms": p["t"], "error": str(exc), "source_record_id": row["id"]})
    # Actual first-receipt records, unlike the historical retrospective fetches.
    calibration_records = list(
        archive.db.execute(
            "SELECT * FROM records WHERE json_extract(metadata,'$.url') LIKE '%/live_data/weather/miami/calibrations' "
            "AND json_extract(metadata,'$.status')=200 ORDER BY available_at,id"
        )
    )
    calibration_times = [parse_time(r["available_at"]).timestamp() for r in calibration_records]
    capture_records = list(
        archive.db.execute(
            "SELECT * FROM records WHERE kind='index_capture' AND key='miami' ORDER BY available_at,id"
        )
    )
    first_pending, first_final, changed_pending, changed_final = {}, {}, set(), set()
    rejected = Counter()
    for row in capture_records:
        received = parse_time(row["available_at"])
        received_ms = int(received.timestamp() * 1000)
        cfg_index = bisect_right(calibration_times, received.timestamp()) - 1
        configs_asof = archive.json(calibration_records[cfg_index])["calibrations"] if cfg_index >= 0 else []
        for p in archive.json(row)["timeseries"]:
            t = p["t"]
            if canonical_point(p):
                if t not in first_final:
                    first_final[t] = {"point": p, "received_at": row["available_at"], "record_id": row["id"]}
                elif first_final[t]["point"]["v"] != p["v"]:
                    changed_final.add(t)
            elif p.get("status") == "incomplete":
                try:
                    estimate = reconstruct(p, configs_asof, received_ms, provisional=True)
                except (ValueError, KeyError) as exc:
                    rejected[str(exc)] += 1
                    continue
                value = {
                    "estimate": estimate,
                    "point_ms": t,
                    "received_at": row["available_at"],
                    "record_id": row["id"],
                    "calibration_record_id": calibration_records[cfg_index]["id"],
                }
                if t not in first_pending:
                    first_pending[t] = value
                elif first_pending[t]["estimate"]["nearest_half_up_f"] != estimate["nearest_half_up_f"]:
                    changed_pending.add(t)
    comparisons = []
    for t, pending in sorted(first_pending.items()):
        final = first_final.get(t)
        if final is None:
            comparisons.append({**pending, "status": "right_censored_no_observed_canonical_point"})
            continue
        lead = (parse_time(final["received_at"]) - parse_time(pending["received_at"])).total_seconds()
        comparisons.append(
            {
                **pending,
                "status": "paired",
                "canonical_value": final["point"]["v"],
                "canonical_status": final["point"]["status"],
                "canonical_record_id": final["record_id"],
                "canonical_first_received_at": final["received_at"],
                "first_seen_lead_seconds": lead,
                "error_f": float(
                    Decimal(pending["estimate"]["nearest_half_up_f"]) - Decimal(str(final["point"]["v"]))
                ),
                "canonical_changed_in_later_capture": t in changed_final,
                "pending_estimate_changed": t in changed_pending,
            }
        )
    pairs = [r for r in comparisons if r["status"] == "paired"]
    result = {
        "experiment": "E005-v1",
        "generated_at": iso(utcnow()),
        "protocol_record_id": protocol_id,
        "historical_conditional_arithmetic": dict(historical),
        "historical_exceptions": exceptions,
        "live_capture_records": len(capture_records),
        "live_first_pending_minutes": len(first_pending),
        "live_paired_minutes": len(pairs),
        "live_censored_minutes": len(comparisons) - len(pairs),
        "exact_provisional_matches": sum(r["error_f"] == 0 for r in pairs),
        "first_seen_lead_seconds": numeric_summary([r["first_seen_lead_seconds"] for r in pairs]),
        "absolute_provisional_error_f": numeric_summary([abs(r["error_f"]) for r in pairs]),
        "changed_pending_minutes": len(changed_pending),
        "changed_canonical_minutes": len(changed_final),
        "rejected_pending_snapshots": dict(rejected),
        "profitability_proven": False,
        "limitations": [
            "Arithmetic conditioned on source-reported QC; full independent QC not reconstructed",
            "First-seen lead depends on this polling channel; not exclusive or market-relative information",
            "Overlapping minute observations come from one day; not independent trading trials",
            "Pending station inputs can fail final QC or change before deadline",
            "No predictive probability advantage, fills or P&L evaluated",
        ],
    }
    archive.append("experiment_report", "E005_pending_index", utcnow(), {}, canonical(result).encode())
    body = "\n".join(canonical(r) for r in comparisons) + "\n"
    archive.append("experiment_dataset", "E005_pending_pairs", utcnow(), {}, body.encode())
    Path("reports/E005_pending_index.json").write_text(json.dumps(result, indent=2))
    Path("reports/E005_pending_pairs.jsonl").write_text(body)
    print(json.dumps({k: v for k, v in result.items() if k != "historical_exceptions"}, indent=2))
    archive.close()


if __name__ == "__main__":
    main()
