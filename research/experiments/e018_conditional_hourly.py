"""August-only conditional model selection and a separate netted forward cohort."""

import argparse
import fcntl
import hashlib
import json
import logging
import time
from datetime import timedelta
from pathlib import Path

import httpx

from research.experiments.e009_paper import (
    arrival_orders,
    expire_orders,
    make_orders,
    record,
    service_makers,
    settle_and_mark,
)
from weatherpred.archive import Archive, canonical
from weatherpred.conditional_forecasts import location, probability, select_and_fit
from weatherpred.forecasts import IndexSeries, ResidualDistribution
from weatherpred.http import PublicClient
from weatherpred.market_making import inventory_report
from weatherpred.netted_paper import NettedPaperLedger
from weatherpred.shadow import validate_lineage
from weatherpred.timeutil import iso, parse_time, utcnow

CONFIG = Path("config/e018_conditional_hourly.json")


def verify_sources(protocol):
    for path, expected in protocol["code_sha256"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen E018 source changed: " + path)


def register(archive):
    cfg = json.loads(CONFIG.read_text())
    first = parse_time(cfg["first_decision_at"])
    if utcnow() >= first or archive.latest("experiment_protocol", cfg["experiment"]):
        raise ValueError("Late or duplicate conditional registration")
    parent = archive.json(record(archive, cfg["parent_paper_run_record_id"]))
    verify_sources(parent)
    cfg["scenarios"] = parent["config"]["scenarios"]
    cfg["initial_cash_per_account"] = "100"
    slots = [
        s
        for s in parent["slots"]
        if first <= parse_time(s["decision_at"]) <= parse_time(cfg["last_decision_at"])
    ]
    if len(slots) != 3:
        raise ValueError("Expected three future original-snapshot slots")
    sources, hashes = dict(parent["source_record_ids"]), dict(parent["code_sha256"])
    for path in [
        CONFIG,
        Path(__file__),
        *[
            Path("weatherpred") / name
            for name in (
                "conditional_forecasts.py",
                "netted_paper.py",
                "market_making.py",
                "archive.py",
                "timeutil.py",
            )
        ],
    ]:
        body = path.read_bytes()
        hashes[str(path)] = hashlib.sha256(body).hexdigest()
        sources[str(path)] = archive.append("research_source", str(path), utcnow(), {}, body)
    protocol = {
        "config": cfg,
        "slots": slots,
        "code_sha256": hashes,
        "source_record_ids": sources,
        "registered_at": iso(utcnow()),
    }
    run_id = archive.append(
        "experiment_protocol", cfg["experiment"], utcnow(), {}, canonical(protocol).encode()
    )
    Path("reports/E018_registration.json").write_text(
        json.dumps({"run_record_id": run_id, **protocol}, indent=2)
    )

    # Registration is durable before any candidate sees the August target rows.
    training_model = archive.json(record(archive, cfg["training_model_record_id"]))
    training_row = record(archive, cfg["training_record_id"])
    if (
        training_row["kind"] != "research_dataset"
        or training_model["training_record_id"] != training_row["id"]
        or hashlib.sha256(archive.body(training_row)).hexdigest() != training_model["training_sha256"]
        or training_model["protocol"]["historical_publication_lag_minutes"] != 5
    ):
        raise ValueError("August-only training lineage differs from E006")
    fit = select_and_fit(archive.json(training_row), cfg)
    prediction_id = archive.append(
        "research_dataset",
        "E018_august_selection_predictions",
        utcnow(),
        {},
        canonical(fit.pop("predictions")).encode(),
    )
    artifact = {
        "run_record_id": run_id,
        "training_record_id": training_row["id"],
        "training_sha256": training_row["body_sha256"],
        "selection_predictions_record_id": prediction_id,
        "baseline_model_record_id": cfg["training_model_record_id"],
        "config": cfg,
        "code_sha256": hashes,
        "published_at": iso(utcnow()),
        "historical_validation_scored": False,
        "promotion_eligible": False,
        **fit,
    }
    if utcnow() >= first:
        raise ValueError("Model fitting crossed the forward deadline; registration retained")
    model_id = archive.append(
        "model_artifact", cfg["experiment"] + ":" + str(run_id), utcnow(), {}, canonical(artifact).encode()
    )
    Path("reports/E018_model.json").write_text(
        json.dumps({"model_record_id": model_id, **artifact}, indent=2)
    )
    paper = NettedPaperLedger(archive, run_id)
    for model in cfg["models"]:
        for scenario in cfg["scenarios"]:
            paper.emit("account", {"account": model + ":" + scenario["name"], "initial_cash": "100"})
    return run_id, model_id


def paired_prediction(archive, parent_row, model_row, published):
    parent, model = archive.json(parent_row), archive.json(model_row)
    cfg = model["config"]
    scheduled, target = parse_time(parent["scheduled_at"]), parse_time(parent["settlement_at"])
    run = archive.json(record(archive, model["run_record_id"]))
    if not any(
        s["decision_at"] == parent["scheduled_at"]
        and s["settlement_at"] == parent["settlement_at"]
        and s["horizon_minutes"] == parent["horizon_minutes"]
        for s in run["slots"]
    ):
        raise ValueError("Unregistered conditional forecast slot")
    if (
        parent["protocol"] != "E004-forward-v1"
        or parent["model_record_id"] != cfg["parent_original_model_record_id"]
        or parent_row["available_at"] != parent["published_at"]
        or parse_time(model_row["available_at"]) >= scheduled
        or not 0 <= (published - scheduled).total_seconds() <= cfg["maximum_publication_lateness_seconds"]
    ):
        raise ValueError("Parent/model timing or identity differs from registration")
    if archive.latest("shadow_invalidated", str(parent_row["id"])):
        raise ValueError("Parent forecast invalidated")
    validate_lineage(
        archive,
        parent["evidence_record_ids"],
        parse_time(parent["published_at"]),
        target,
        parent["model_record_id"],
    )
    evidence = list(
        dict.fromkeys(
            [
                *parent["evidence_record_ids"],
                parent_row["id"],
                parent["model_record_id"],
                model["baseline_model_record_id"],
            ]
        )
    )
    validate_lineage(archive, evidence, published, target, model_row["id"])
    indices = [
        record(archive, r)
        for r in parent["evidence_record_ids"]
        if record(archive, r)["kind"] == "shadow_index"
    ]
    if len(indices) != 1:
        raise ValueError("Ambiguous original index source")
    snapshot_ms, target_ms = (
        int(parse_time(parent[k]).timestamp() * 1000) for k in ("snapshot_at", "settlement_at")
    )
    feature = IndexSeries(archive.json(indices[0])["timeseries"]).features(
        snapshot_ms, target_ms, 300_000, 600_000, 1_800_000
    )
    if feature is None:
        raise ValueError("Missing five-minute-lag features")
    row = {
        "features": feature,
        "decision_ms": snapshot_ms,
        "settlement_ms": target_ms,
        "horizon_minutes": parent["horizon_minutes"],
    }
    conditional = model["models"][str(parent["horizon_minutes"])]
    baseline = archive.json(record(archive, model["baseline_model_record_id"]))
    markets = []
    for market in parent["markets"]:
        probabilities = {"conditional_selected": probability(conditional, row, market["floor_strike"])}
        for name in cfg["models"][1:]:
            fit = baseline["models"][name.removeprefix("fresh_5m_") + ":" + str(parent["horizon_minutes"])]
            probabilities[name] = ResidualDistribution(tuple(fit["residuals"]), fit["kind"]).probability(
                feature[fit["base"]], {**market, "strike_type": "greater"}
            )
        markets.append(
            {
                "ticker": market["ticker"],
                "floor_strike": market["floor_strike"],
                "probabilities": probabilities,
            }
        )
    return {
        "protocol": cfg["experiment"],
        "model_record_id": model_row["id"],
        "parent_forecast_record_id": parent_row["id"],
        "scheduled_at": parent["scheduled_at"],
        "snapshot_at": parent["snapshot_at"],
        "published_at": iso(published),
        "settlement_at": parent["settlement_at"],
        "horizon_minutes": parent["horizon_minutes"],
        "event": parent["event"],
        "evidence_record_ids": evidence,
        "features": feature,
        "conditional_mean": location(conditional, row),
        "conditional_scale": conditional["distribution_scale"],
        "chosen_candidate": model["chosen_candidate"],
        "markets": markets,
        "real_money_recommended_size": 0,
        "profitability_proven": False,
        "limits": cfg["limits"],
    }


def report(paper, model_id):
    result = {
        "generated_at": iso(utcnow()),
        "run_record_id": int(paper.run_id),
        "model_record_id": model_id,
        "accounts": [inventory_report(paper.state, a) for a in sorted(paper.state["accounts"])],
        "orders": list(paper.state["orders"].values()),
        "fills": paper.state["fills"],
        "nettings": paper.state.get("nettings", []),
        "positions": list(paper.state["positions"].values()),
        "settlements": paper.state["settlements"],
        "processed_slots": paper.state["slots"],
        "real_money_orders": 0,
        "profitability_proven": False,
    }
    path = Path("reports/E018_paper_state.json.tmp")
    path.write_text(json.dumps(result, indent=2))
    path.replace("reports/E018_paper_state.json")
    return {k: result[k] for k in ("generated_at", "run_record_id", "model_record_id")} | {
        "slots": len(paper.state["slots"]),
        "orders": len(result["orders"]),
        "fills": len(result["fills"]),
        "nettings": len(result["nettings"]),
        "settled_events": result["settlements"],
    }


def run_loop(client, run_id, model_id):
    archive = client.archive
    run = archive.json(record(archive, run_id))
    verify_sources(run)
    cfg, model_row = run["config"], record(archive, model_id)
    if archive.json(model_row)["run_record_id"] != run_id or model_row["kind"] != "model_artifact":
        raise ValueError("Model does not belong to this registration")
    paper = NettedPaperLedger(archive, run_id)
    expire_orders(paper, "restart_cancels_unobserved_orders")
    last_mark, last_tape = 0.0, 0.0
    try:
        while utcnow() < parse_time(cfg["stop_at"]):
            if (archive.root / "STOP_E018").exists():
                break
            expire_orders(paper)
            for slot in run["slots"]:
                if slot["decision_at"] in paper.state["slots"] or utcnow() < parse_time(slot["decision_at"]):
                    continue
                deadline = parse_time(slot["decision_at"]) + timedelta(
                    seconds=cfg["maximum_publication_lateness_seconds"]
                )
                try:
                    if utcnow() > deadline:
                        raise ValueError("Missed conditional slot; no backfill")
                    matches = [
                        r
                        for r in archive.db.execute(
                            "SELECT * FROM records WHERE kind='shadow_forecast' AND available_at>=? AND available_at<=?",
                            (slot["decision_at"], iso(deadline)),
                        )
                        if archive.json(r)["scheduled_at"] == slot["decision_at"]
                    ]
                    if not matches:
                        continue
                    if len(matches) != 1:
                        raise ValueError("Duplicate original snapshot")
                    forecast = paired_prediction(archive, matches[0], model_row, utcnow())
                    publication = utcnow()
                    if publication > deadline:
                        raise ValueError("Conditional computation crossed deadline")
                    forecast["published_at"] = iso(publication)
                    identifier = archive.append(
                        "e018_forecast", str(run_id), publication, {}, canonical(forecast).encode()
                    )
                    if utcnow() >= parse_time(forecast["settlement_at"]):
                        archive.append(
                            "e018_invalidated", str(identifier), utcnow(), {}, b"Persisted too late"
                        )
                        raise ValueError("Conditional persistence crossed settlement")
                    make_orders(paper, forecast, identifier, cfg)
                    print(
                        json.dumps({"forecast_record_id": identifier, **report(paper, model_id)}), flush=True
                    )
                except (ValueError, KeyError, httpx.HTTPError) as exc:
                    paper.emit(
                        "error",
                        {
                            "stage": "conditional_forecast",
                            "scheduled_at": slot["decision_at"],
                            "error": str(exc),
                        },
                    )
                    expire_orders(paper, "conditional_slot_error")
                    if slot["decision_at"] not in paper.state["slots"]:
                        paper.emit(
                            "slot", {"scheduled_at": slot["decision_at"], "skipped": True, "reason": str(exc)}
                        )
            arrival_orders(client, paper)
            if time.monotonic() - last_tape >= 5:
                service_makers(client, paper)
                last_tape = time.monotonic()
            if time.monotonic() - last_mark >= 60:
                settle_and_mark(client, paper)
                print(json.dumps(report(paper, model_id)), flush=True)
                last_mark = time.monotonic()
            time.sleep(0.2)
    finally:
        expire_orders(paper, "bounded_conditional_stop")
        paper.emit(
            "stopped",
            {"reason": "stop_time_file_or_error", "pending_positions": len(paper.state["positions"])},
        )
        result = report(paper, model_id)
        archive.append("experiment_report", "E018_forward_stop", utcnow(), {}, canonical(result).encode())
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int)
    parser.add_argument("--model-record-id", type=int)
    parser.add_argument("--register-only", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    archive = Archive()
    client = PublicClient(archive)
    try:
        with (archive.root / "e018.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if bool(args.run_record_id) != bool(args.model_record_id):
                raise ValueError("Resume requires both run and model record IDs")
            run_id, model_id = (
                (args.run_record_id, args.model_record_id) if args.run_record_id else register(archive)
            )
            print(json.dumps({"run_record_id": run_id, "model_record_id": model_id}), flush=True)
            if not args.register_only:
                print(json.dumps(run_loop(client, run_id, model_id)), flush=True)
    finally:
        client.close()
        archive.close()


if __name__ == "__main__":
    main()
