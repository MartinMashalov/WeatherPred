"""Register and fit once, then consume future E004 snapshots without network calls."""

import argparse
import fcntl
import hashlib
import json
import logging
import time
from datetime import timedelta
from pathlib import Path

from weatherpred.archive import Archive, canonical
from weatherpred.freshness import paired_forecast, train_fresh_model
from weatherpred.http import PublicClient
from weatherpred.shadow_outcomes import score_shadow
from weatherpred.timeutil import iso, parse_time, utcnow

LOG = logging.getLogger(__name__)
SOURCE_PATHS = [
    "config/e006_fresh_index.json",
    "weatherpred/freshness.py",
    "weatherpred/forecasts.py",
    "weatherpred/index.py",
    "weatherpred/shadow.py",
    "weatherpred/shadow_outcomes.py",
    "research/experiments/e006_fresh_index.py",
]


def register_model(archive):
    protocol = json.loads(Path(SOURCE_PATHS[0]).read_text())
    if archive.latest("model_artifact", protocol["experiment"]) is not None:
        raise ValueError("Model already registered; resume with --model-record-id rather than refitting")
    first, last = (
        parse_time(protocol[k]) for k in ("first_scheduled_decision_at", "last_scheduled_decision_at")
    )
    if utcnow() >= first:
        raise ValueError("Registration deadline has passed; retain the missed experiment")
    parent_row = archive.db.execute(
        "SELECT * FROM records WHERE id=? AND kind='experiment_protocol'",
        (protocol["parent_protocol_record_id"],),
    ).fetchone()
    if parent_row is None:
        raise ValueError("Missing registered parent run")
    parent = archive.json(parent_row)
    if parent["model_record_id"] != protocol["parent_model_record_id"]:
        raise ValueError("Parent model does not match registration")
    slots = [s for s in parent["slots"] if first <= parse_time(s["decision_at"]) <= last]
    if not slots:
        raise ValueError("No registered prospective parent slots")
    source_ids, hashes = [], {}
    for path in SOURCE_PATHS:
        body = Path(path).read_bytes()
        hashes[path] = hashlib.sha256(body).hexdigest()
        source_ids.append(archive.append("research_source", path, utcnow(), {}, body))
    registration = {
        "protocol": protocol,
        "slots": slots,
        "code_sha256": hashes,
        "source_record_ids": source_ids,
        "registered_at": iso(utcnow()),
    }
    registration_id = archive.append(
        "experiment_protocol", protocol["experiment"], utcnow(), {}, canonical(registration).encode()
    )
    acquisition = json.loads(Path("reports/E004_acquisition.json").read_text())
    start, end = (parse_time(protocol[k]) for k in ("training_start", "training_end_exclusive"))
    points, history_ids = [], []
    for day in acquisition["days"]:
        if parse_time(day["from"]) >= end or parse_time(day["to_exclusive"]) <= start - timedelta(hours=1):
            continue
        row = archive.db.execute("SELECT * FROM records WHERE id=?", (day["record_id"],)).fetchone()
        points.extend(archive.json(row)["timeseries"])
        history_ids.append(row["id"])
    models, rows, exclusions = train_fresh_model(points, protocol)
    training_bytes = canonical(rows).encode()
    training_id = archive.append("research_dataset", "E006_training", utcnow(), {}, training_bytes)
    artifact = {
        **registration,
        "protocol_record_id": registration_id,
        "models": models,
        "history_record_ids": history_ids,
        "training_record_id": training_id,
        "training_sha256": hashlib.sha256(training_bytes).hexdigest(),
        "training_rows": len(rows),
        "training_exclusions": exclusions,
        "published_at": iso(utcnow()),
        "historical_validation_scored": False,
        "promotion_eligible": False,
        "real_money_size": 0,
    }
    if utcnow() >= first:
        raise ValueError("Model fitting crossed the registration deadline")
    rec = archive.append("model_artifact", protocol["experiment"], utcnow(), {}, canonical(artifact).encode())
    Path("reports/E006_model.json").write_text(json.dumps({**artifact, "record_id": rec}, indent=2))
    LOG.info("Registered model=%s training_rows=%s future_slots=%s", rec, len(rows), len(slots))
    return rec


def run(archive, model_id):
    model_row = archive.db.execute(
        "SELECT * FROM records WHERE id=? AND kind='model_artifact'", (model_id,)
    ).fetchone()
    if model_row is None:
        raise ValueError("Missing model artifact")
    artifact = archive.json(model_row)
    if artifact["protocol"]["experiment"] != "E006-v1":
        raise ValueError("Wrong experiment model")
    for path, digest in artifact["code_sha256"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError("Source differs from pinned artifact: " + path)
    try:
        for slot in artifact["slots"]:
            key = slot["decision_at"]
            if archive.latest("e006_forecast", key) or archive.latest("e006_skip", key):
                continue
            scheduled = parse_time(key)
            deadline = scheduled + timedelta(
                seconds=artifact["protocol"]["maximum_publication_lateness_seconds"]
            )
            parent = None
            while utcnow() <= deadline:
                if (archive.root / "STOP_E006").exists():
                    return {"stop_reason": "stop_file"}
                if utcnow() >= scheduled:
                    candidates = archive.db.execute(
                        "SELECT * FROM records WHERE kind='shadow_forecast' AND available_at>=? AND available_at<=?",
                        (key, iso(deadline)),
                    ).fetchall()
                    matches = [r for r in candidates if archive.json(r)["scheduled_at"] == key]
                    if len(matches) > 1:
                        raise ValueError("Duplicate parent forecasts for a registered slot")
                    if matches:
                        parent = matches[0]
                        break
                time.sleep(
                    0.2 if utcnow() >= scheduled else max(0, min(1, (scheduled - utcnow()).total_seconds()))
                )
            if parent is None:
                result = {"scheduled_at": key, "reason": "parent_missing_or_consumer_late"}
                archive.append("e006_skip", key, utcnow(), {}, canonical(result).encode())
                LOG.warning("Skipped %s", canonical(result))
                continue
            try:
                # Use the actual publication time after computation, then revalidate the bound.
                result = paired_forecast(archive, parent, model_row, utcnow())
                publication = utcnow()
                if publication > deadline or publication >= parse_time(slot["settlement_at"]):
                    raise ValueError("Computation crossed its publication deadline")
                result["published_at"] = iso(publication)
                rec = archive.append("e006_forecast", key, publication, {}, canonical(result).encode())
                if utcnow() >= parse_time(slot["settlement_at"]):
                    archive.append("e006_invalidated", str(rec), utcnow(), {}, b"Persisted after settlement")
                    raise ValueError("Persistence crossed settlement")
                status = {
                    "record_id": rec,
                    "model_record_id": model_id,
                    "parent_forecast_record_id": parent["id"],
                    "scheduled_at": key,
                    "published_at": result["published_at"],
                    "event": result["event"],
                    "contracts": len(result["markets"]),
                    "quantity": 0,
                }
                Path("reports/E006_status.json").write_text(json.dumps(status, indent=2))
                LOG.info("Prospective paired forecast %s", canonical(status))
            except (ValueError, KeyError) as exc:
                result = {"scheduled_at": key, "reason": str(exc)}
                archive.append("e006_skip", key, utcnow(), {}, canonical(result).encode())
                LOG.warning("Skipped %s", canonical(result))
        return {"stop_reason": "slot_limit"}
    finally:
        archive.append(
            "e006_stopped", str(model_id), utcnow(), {}, b"Runner exited; inspect skips and forecasts"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-record-id", type=int)
    parser.add_argument("--score", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    archive = Archive()
    if args.score:
        client = PublicClient(archive)
        try:
            print(
                json.dumps(
                    score_shadow(
                        client,
                        forecast_kind="e006_forecast",
                        invalid_kind="e006_invalidated",
                        report_key="E006_shadow_outcomes",
                    ),
                    indent=2,
                )
            )
        finally:
            client.close()
            archive.close()
        return
    with (archive.root / "e006.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another E006 consumer holds the lock") from None
        try:
            model_id = args.model_record_id or register_model(archive)
            print(json.dumps(run(archive, model_id)), flush=True)
        finally:
            archive.close()


if __name__ == "__main__":
    main()
