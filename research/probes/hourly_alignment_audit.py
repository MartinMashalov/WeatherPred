"""Read-only exact-hour/source audit of four frozen Miami paper events.

No model fitting, strategy selection, production imports, or archive writes.
The fixed record ceiling preserves the evidence available for this investigation.
"""

import argparse
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

EVENTS = [f"KXTEMPMIAH-26SEP06{hour}" for hour in range(10, 14)]
TRAIN_START = 1787184000000
TRAIN_END = 1788220800000
CEILING = 100119


def milliseconds(value):
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def iso_ms(value):
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def canonical(point):
    return point.get("status") in ("normal", "degraded") and "v" in point and not point.get("receipt_basis")


def run(output):
    sources = {}
    with sqlite3.connect("file:data/archive.sqlite?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row

        def read_record(row):
            if row is None or row["id"] > CEILING:
                raise ValueError("Missing source or source beyond fixed audit ceiling")
            data = (Path("data/blobs") / row["body_sha256"]).read_bytes()
            if hashlib.sha256(data).hexdigest() != row["body_sha256"]:
                raise ValueError("Source hash differs from archive")
            sources[str(row["id"])] = {
                "kind": row["kind"],
                "key": row["key"],
                "available_at": row["available_at"],
                "body_sha256": row["body_sha256"],
            }
            return json.loads(data)

        def read_id(identifier):
            return read_record(db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone())

        # All selected raw index captures are prospective September 6 receipts.
        index_rows = db.execute(
            "SELECT * FROM records WHERE kind IN ('index_capture','shadow_index') AND id<=? ORDER BY available_at,id",
            (CEILING,),
        ).fetchall()
        points = defaultdict(list)
        canonical_versions = defaultdict(set)
        for row in index_rows:
            source = read_record(row)
            for point in source["timeseries"]:
                if canonical(point):
                    canonical_versions[point["t"]].add(str(Decimal(str(point["v"]))))
                if point["t"] in (1788703200000, 1788706800000, 1788710400000, 1788714000000):
                    points[point["t"]].append(
                        {
                            "record_id": row["id"],
                            "received_at": row["available_at"],
                            "config_version": source.get("config_version"),
                            "point": point,
                        }
                    )

        twc = read_id(78077)
        twc_miami = next(station for station in twc["stations"] if station["icaoId"] == "KMIA")
        events = []
        for event in EVENTS:
            event_rows = db.execute(
                "SELECT * FROM records WHERE kind IN ('paper_settlement_source','shadow_outcome_source') "
                "AND key=? AND id<=? ORDER BY available_at,id",
                (event, CEILING),
            ).fetchall()
            transitions, previous = [], None
            for record in event_rows:
                source = read_record(record)
                markets = source["markets"]
                states = {(m["status"], m["expiration_value"]) for m in markets}
                if len(states) != 1:
                    raise ValueError("Mixed event finalization needs contract-specific investigation")
                state = next(iter(states))
                if state != previous:
                    market = markets[0]
                    transitions.append(
                        {
                            "record_id": record["id"],
                            "received_at": record["available_at"],
                            "status": state[0],
                            "expiration_value": state[1],
                            "exchange_updated_time": market["updated_time"],
                            "exchange_settlement_ts": market.get("settlement_ts"),
                        }
                    )
                    previous = state
            final = next(t for t in transitions if t["status"] == "finalized")
            final_data = read_id(final["record_id"])
            market = final_data["markets"][0]
            target = milliseconds(market["close_time"])
            local = datetime.fromtimestamp(target / 1000, ZoneInfo("America/New_York"))
            if (
                event[-2:] != local.strftime("%H")
                or milliseconds(final_data["event"]["strike_date"]) != target
            ):
                raise ValueError("Ticker, strike date, and close-time conversion differ")
            target_points = points[target]
            published = [p for p in target_points if canonical(p["point"])]
            if not published:
                raise ValueError("No exact target-minute canonical evidence")
            first = published[0]
            value = Decimal(str(first["point"]["v"]))
            if value != Decimal(final["expiration_value"]):
                raise ValueError("Final market value differs from initial observed canonical target minute")
            if any(Decimal(str(p["point"]["v"])) != value for p in published):
                raise ValueError("Forward target canonical value changed")
            provider_values = [Decimal(str(s["temp_f"])) for s in first["point"]["stations"]]
            if first["point"]["status"] == "normal" and sum(provider_values) / len(provider_values) != value:
                raise ValueError("All-five equal-weight exact primary average differs from target")
            for m in final_data["markets"]:
                if m["strike_type"] != "greater" or (value > Decimal(str(m["floor_strike"]))) != (
                    m["result"] == "yes"
                ):
                    raise ValueError("Final binary predicate does not match canonical target")
            twc_point = next(
                (p for p in twc_miami["observations"] if milliseconds(p["reportTimeUTC"]) == target),
                None,
            )
            events.append(
                {
                    "event": event,
                    "title": final_data["event"]["title"],
                    "target_utc": iso_ms(target),
                    "target_local": local.isoformat(),
                    "source_agencies": final_data["event"]["settlement_sources"],
                    "rules_primary": market["rules_primary"],
                    "rules_secondary": market["rules_secondary"],
                    "expected_expiration_time": market["expected_expiration_time"],
                    "settlement_timer_seconds": market["settlement_timer_seconds"],
                    "final_expiration_value": str(value),
                    "market_contracts_checked": len(final_data["markets"]),
                    "first_archived_canonical": first,
                    "first_canonical_receipt_delay_seconds": (milliseconds(first["received_at"]) - target)
                    / 1000,
                    "last_archived_noncanonical": next(
                        (p for p in reversed(target_points) if not canonical(p["point"])), None
                    ),
                    "canonical_target_receipts_checked": len(published),
                    "transitions": transitions,
                    "final_settlement_delay_seconds": (milliseconds(final["exchange_settlement_ts"]) - target)
                    / 1000,
                    "twc_78077_single_station_comparator": twc_point,
                    "twc_is_contract_authority": False,
                }
            )

        # Training target and feature mapping: August rows only; no refitting.
        training_model = read_id(12323)
        training = read_id(12322)
        raw = {}
        for identifier in training_model["history_record_ids"]:
            for point in read_id(identifier)["timeseries"]:
                if TRAIN_START - 3_600_000 <= point["t"] < TRAIN_END and canonical(point):
                    if point["t"] in raw and raw[point["t"]]["v"] != point["v"]:
                        raise ValueError("Conflicting August raw canonical values")
                    raw[point["t"]] = point
        label_offsets, feature_ages, maximum_trend_error = Counter(), Counter(), 0.0
        for row in training:
            target, decision, features = row["settlement_ms"], row["decision_ms"], row["features"]
            if not TRAIN_START <= target < TRAIN_END or target - decision != row["horizon_minutes"] * 60000:
                raise ValueError("Training target/horizon outside fixed August mapping")
            label = raw[row["label_point_ms"]]
            if not target - 3_600_000 <= label["t"] <= target or label["v"] != row["observed"]:
                raise ValueError("Training raw label mismatch")
            label_offsets[(target - label["t"]) // 60000] += 1
            last = raw[features["last_point_ms"]]
            if last["t"] > decision - 300000 or last["v"] != features["persistence"]:
                raise ValueError("Training five-minute feature cutoff or value mismatch")
            if max(t for t in raw if t <= decision - 300000) != last["t"]:
                raise ValueError("Training latest eligible input differs from raw history")
            feature_ages[(decision - last["t"]) // 60000] += 1
            window = [raw[t] for t in sorted(raw) if last["t"] - 1800000 <= t <= last["t"]]
            x = [(p["t"] - last["t"]) / 60000 for p in window]
            y = [p["v"] for p in window]
            xm, ym = sum(x) / len(x), sum(y) / len(y)
            slope = sum((a - xm) * (b - ym) for a, b in zip(x, y, strict=True)) / sum(
                (a - xm) ** 2 for a in x
            )
            trend = last["v"] + slope * (target - last["t"]) / 60000
            maximum_trend_error = max(maximum_trend_error, abs(trend - features["trend"]))
            if abs(trend - features["trend"]) > 1e-10 or len(window) != features["points"]:
                raise ValueError("Training trend reconstruction mismatch")
        original = read_id(3768)
        original_residuals = defaultdict(list)
        for row in training:
            target, decision = row["settlement_ms"], row["decision_ms"]
            last_time = max(t for t in raw if t <= decision - 600000)
            last = raw[last_time]
            window = [raw[t] for t in sorted(raw) if last_time - 1800000 <= t <= last_time]
            x = [(p["t"] - last_time) / 60000 for p in window]
            y = [p["v"] for p in window]
            xm, ym = sum(x) / len(x), sum(y) / len(y)
            slope = sum((a - xm) * (b - ym) for a, b in zip(x, y, strict=True)) / sum(
                (a - xm) ** 2 for a in x
            )
            for base, value in (
                ("persistence", last["v"]),
                ("trend", last["v"] + slope * (target - last_time) / 60000),
            ):
                original_residuals[(base, row["horizon_minutes"])].append(row["observed"] - value)
        e004_residual_count, e004_maximum_difference = 0, 0.0
        for key, model in original["models"].items():
            expected = original_residuals[(model["base"], int(key.split(":")[1]))]
            if len(expected) != len(model["residuals"]):
                raise ValueError("Original E004 training residual length differs")
            differences = [abs(a - b) for a, b in zip(expected, model["residuals"], strict=True)]
            e004_residual_count += len(expected)
            e004_maximum_difference = max(e004_maximum_difference, max(differences))
            if max(differences) > 1e-10:
                raise ValueError(
                    "Original E004 training residual differs from independently aligned August raw data"
                )
        august_markets = {}
        for identifier in original["acquisition"]["market_record_ids"]:
            for market in read_id(identifier)["markets"]:
                if TRAIN_START <= milliseconds(market["close_time"]) < TRAIN_END:
                    august_markets[market["ticker"]] = market
        listed_targets = set()
        for market in august_markets.values():
            target = milliseconds(market["close_time"])
            label = raw[max(t for t in raw if t <= target)]
            if Decimal(str(label["v"])) != Decimal(market["expiration_value"]):
                raise ValueError("August listed contract and exact historical target disagree")
            local = datetime.fromtimestamp(target / 1000, ZoneInfo("America/New_York"))
            if market["event_ticker"][-2:] != local.strftime("%H"):
                raise ValueError("Historical local-hour ticker alignment mismatch")
            listed_targets.add(target)

        forward = []
        for record in db.execute(
            "SELECT * FROM records WHERE kind IN ('shadow_forecast','paper_forecast','e018_forecast') AND id<=? ORDER BY id",
            (CEILING,),
        ).fetchall():
            forecast = read_record(record)
            if forecast["event"] not in EVENTS:
                continue
            target = milliseconds(forecast["settlement_at"])
            scheduled = milliseconds(forecast["scheduled_at"])
            feature = forecast["features"]
            if target - scheduled != forecast["horizon_minutes"] * 60000:
                raise ValueError("Forward scheduled horizon mismatch")
            index_sources = []
            for identifier in forecast["evidence_record_ids"]:
                source_record = db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
                if source_record["kind"] == "shadow_index":
                    index_sources.append(source_record)
            if len(index_sources) != 1:
                raise ValueError("Ambiguous forward input source")
            index_source = index_sources[0]
            source_points = read_record(index_source)["timeseries"]
            last = next(p for p in source_points if p["t"] == feature["last_point_ms"])
            if not canonical(last) or last["v"] != feature["persistence"]:
                raise ValueError("Forward features did not use exact canonical source point")
            snapshot = milliseconds(forecast["snapshot_at"])
            assumed_lag = 600000 if record["kind"] == "shadow_forecast" else 300000
            if last["t"] > snapshot - assumed_lag or milliseconds(
                index_source["available_at"]
            ) > milliseconds(forecast["published_at"]):
                raise ValueError("Forward input did not meet actual receipt and canonical lag gates")
            outcome = next(e for e in events if e["event"] == forecast["event"])
            if target != milliseconds(outcome["target_utc"]):
                raise ValueError("Forward model target differs from its own event close")
            forward.append(
                {
                    "record_id": record["id"],
                    "kind": record["kind"],
                    "event": forecast["event"],
                    "scheduled_at": forecast["scheduled_at"],
                    "published_at": forecast["published_at"],
                    "target_at": forecast["settlement_at"],
                    "nominal_horizon_minutes": forecast["horizon_minutes"],
                    "source_record_id": index_source["id"],
                    "source_received_at": index_source["available_at"],
                    "last_input_event_at": iso_ms(last["t"]),
                    "last_input_to_target_minutes": (target - last["t"]) / 60000,
                    "persistence_f": feature["persistence"],
                    "trend_f": feature["trend"],
                    "conditional_mean_f": forecast.get("conditional_mean"),
                    "final_outcome_f": float(outcome["final_expiration_value"]),
                    "conditional_error_f": forecast["conditional_mean"]
                    - float(outcome["final_expiration_value"])
                    if "conditional_mean" in forecast
                    else None,
                }
            )
    result = {
        "audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "archive_record_ceiling": CEILING,
        "events": events,
        "training_alignment": {
            "model_record_id": 12323,
            "dataset_record_id": 12322,
            "rows_reproduced": len(training),
            "distinct_hour_targets": len({r["settlement_ms"] for r in training}),
            "label_age_minutes_counts": dict(label_offsets),
            "assumed_input_age_minutes_counts": dict(feature_ages),
            "maximum_independent_trend_difference_f": maximum_trend_error,
            "original_e004_model_record_id": 3768,
            "original_e004_residuals_reproduced": e004_residual_count,
            "original_e004_maximum_residual_difference_f": e004_maximum_difference,
            "listed_august_contracts_matched": len(august_markets),
            "listed_august_events_matched": len(listed_targets),
            "historical_initial_publication_verified": False,
            "dst_transition_dates_present": False,
        },
        "forward_forecasts": forward,
        "canonical_capture_consistency": {
            "raw_capture_records_checked": len(index_rows),
            "distinct_canonical_event_minutes": len(canonical_versions),
            "different_numeric_canonical_versions": {
                str(t): sorted(v) for t, v in canonical_versions.items() if len(v) > 1
            },
            "scope": "Captured September 6 versions only; no guarantee about unobserved publication editions or August revisions.",
        },
        "source_records": sources,
        "archive_writes": 0,
        "network_requests_by_probe": 0,
        "models_fitted": 0,
        "sealed_2025_data_accessed": False,
        "profitability_proven": False,
    }
    if any(not math.isfinite(r["last_input_to_target_minutes"]) for r in forward):
        raise ValueError("Nonfinite horizon")
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "events_reconciled": len(events),
                "training_alignment": result["training_alignment"],
                "forward_forecasts_reconciled": len(forward),
                "canonical_numeric_revision_count": len(
                    result["canonical_capture_consistency"]["different_numeric_canonical_versions"]
                ),
                "source_records_verified": len(sources),
                "archive_writes": 0,
                "models_fitted": 0,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("reports/HOURLY_ALIGNMENT_AUDIT.json"))
    run(parser.parse_args().output)
