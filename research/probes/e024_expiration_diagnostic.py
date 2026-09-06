"""One declared metadata-only expiration-value audit after E024 preparation failure.

No candle response, price field, account, policy selection or strategy score is
read/computed. Registration precedes all market metadata-body inspection.
"""

import argparse
import hashlib
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from research.probes.bankroll_dataset import ArchiveReader, complete_event_index, http_scope
from weatherpred.archive import Archive, canonical
from weatherpred.contracts import historical_predicate, yes_at
from weatherpred.timeutil import utcnow

EXPERIMENT = "E024-expiration-value-metadata-diagnostic-v1"
CUTOFF_ID = 123234
FILES = [
    Path(__file__),
    Path("research/probes/bankroll_dataset.py"),
    Path("weatherpred/contracts.py"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
]


def sha(body):
    return hashlib.sha256(body).hexdigest()


def classify(present, value):
    if not present:
        return "absent"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean_invalid"
    if isinstance(value, str) and not value:
        return "empty_string"
    if isinstance(value, str) and not value.strip():
        return "whitespace_only"
    if not isinstance(value, (str, int, float)):
        return "non_scalar_invalid"
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return "non_numeric"
    return "finite_numeric" if number.is_finite() else "nonfinite_numeric"


def register(archive):
    if archive.latest("e024_expiration_diagnostic_protocol", EXPERIMENT):
        raise ValueError("Diagnostic already registered")
    cutoff = archive.db.execute("SELECT * FROM records WHERE id=?", (CUTOFF_ID,)).fetchone()
    reader = ArchiveReader(archive.root, CUTOFF_ID, cutoff["available_at"])
    try:
        originals = complete_event_index(reader.checkpoints())
        amendments = [
            dict(r)
            for r in reader.db.execute(
                "SELECT id,key,body_sha256,record_sha256 FROM records WHERE kind='bankroll_acquisition_amended_event' AND id<=? AND available_at<=? AND json_extract(metadata,'$.amendment_protocol_record_id')=116002 ORDER BY key",
                (CUTOFF_ID, cutoff["available_at"]),
            )
        ]
        if len(amendments) != 7 or len({r["key"] for r in amendments}) != 7:
            raise ValueError("Expected the exact seven registered amendments")
        replacement = {r["key"]: r["id"] for r in amendments}
        checkpoints = [
            reader.record(replacement.get(event, identifier))
            for event, identifier in sorted(originals.items())
        ]
        pins = {str(path): sha(path.read_bytes()) for path in FILES}
        sources = {
            str(path): archive.append("research_source", str(path), utcnow(), {}, path.read_bytes())
            for path in FILES
        }
        protocol = {
            "experiment": EXPERIMENT,
            "preparation_protocol_id": 123242,
            "preparation_failure_id": 124068,
            "source_cutoff_id": CUTOFF_ID,
            "source_cutoff_at": cutoff["available_at"],
            "source_hashes": pins,
            "source_record_ids": sources,
            "amendment_protocol_ids": [116002],
            "checkpoint_records": [
                {k: r[k] for k in ("id", "kind", "key", "body_sha256", "record_sha256")} for r in checkpoints
            ],
            "original_checkpoint_ids": originals,
            "scope": "All1736 canonical completed/replacement checkpoint metadata only. Classify expiration_value representations; test finite numeric corroboration against official binary result and exact contract predicate. Preserve all exceptional identities and primary response hashes. Never access candle blobs, quotes, returns, account results or policy selections.",
            "maximum_unique_contracts": 34720,
            "strategy_scores_authorized": False,
            "network_requests_authorized": 0,
            "models_authorized": 0,
        }
        return archive.append(
            "e024_expiration_diagnostic_protocol", EXPERIMENT, utcnow(), {}, canonical(protocol).encode()
        )
    finally:
        reader.close()


def execute(archive, identifier):
    row = archive.db.execute(
        "SELECT * FROM records WHERE id=? AND kind='e024_expiration_diagnostic_protocol'", (identifier,)
    ).fetchone()
    if row is None:
        raise ValueError("Missing declared diagnostic")
    raw = archive.body(row)
    if sha(raw) != row["body_sha256"]:
        raise ValueError("Diagnostic declaration changed")
    protocol = json.loads(raw)
    if protocol["source_hashes"] != {str(path): sha(path.read_bytes()) for path in FILES}:
        raise ValueError("Diagnostic source changed")
    if archive.latest("e024_expiration_diagnostic_started", str(identifier)):
        raise ValueError("Diagnostic cannot silently retry")
    archive.append("e024_expiration_diagnostic_started", str(identifier), utcnow(), {}, b"{}")
    reader = ArchiveReader(archive.root, protocol["source_cutoff_id"], protocol["source_cutoff_at"])
    counts, type_counts, exceptional, numeric_mismatches, predicate_errors = Counter(), Counter(), [], [], []
    source_ids, unique = set(), set()
    try:
        for pinned in protocol["checkpoint_records"]:
            checkpoint, record = reader.read(pinned["id"], pinned["kind"])
            if (
                any(record[k] != v for k, v in pinned.items())
                or checkpoint.get("event") != pinned["key"]
                or not checkpoint.get("complete")
                or checkpoint.get("failures")
            ):
                raise ValueError("Incomplete or changed pinned checkpoint")
            event, seen = pinned["key"], {}
            for source_id in checkpoint["metadata_record_ids"]:
                source = reader.record(source_id, "bankroll_acquisition_http")
                http_scope(source, event=event)
                metadata, _ = reader.read(source_id, "bankroll_acquisition_http")
                source_ids.add(source_id)
                if metadata.get("cursor"):
                    raise ValueError("Incomplete metadata pagination")
                for market in metadata["markets"]:
                    ticker = market["ticker"]
                    if market["event_ticker"] != event or not ticker.startswith(event + "-"):
                        raise ValueError("Wrong event metadata")
                    selected = {
                        k: market.get(k)
                        for k in (
                            "event_ticker",
                            "expiration_value",
                            "strike_type",
                            "floor_strike",
                            "cap_strike",
                            "rules_primary",
                            "status",
                            "result",
                            "settlement_ts",
                        )
                    }
                    if ticker in seen:
                        if selected != seen[ticker]:
                            raise ValueError("Diagnostic fields conflict across canonical sources")
                        continue
                    seen[ticker] = selected
                    if ticker in unique:
                        raise ValueError("Duplicate contract across events")
                    unique.add(ticker)
                    if len(unique) > protocol["maximum_unique_contracts"]:
                        raise ValueError("Declared metadata bound exceeded")
                    value = market.get("expiration_value")
                    category = classify("expiration_value" in market, value)
                    counts[category] += 1
                    type_counts[type(value).__name__] += 1
                    identity = {
                        "ticker": ticker,
                        "event": event,
                        "checkpoint_id": pinned["id"],
                        "metadata_record_id": source_id,
                        "metadata_body_sha256": source["body_sha256"],
                        "expiration_value_type": type(value).__name__,
                        "expiration_value_repr": repr(value),
                        "category": category,
                        "reported_status": market.get("status"),
                        "reported_result": market.get("result"),
                        "settlement_ts_present": bool(market.get("settlement_ts")),
                    }
                    if category != "finite_numeric":
                        exceptional.append(identity)
                    elif (
                        market.get("status") in ("finalized", "settled")
                        and market.get("result") in ("yes", "no")
                        and market.get("settlement_ts")
                    ):
                        try:
                            agrees = yes_at(historical_predicate(market), value) == (
                                market["result"] == "yes"
                            )
                        except (ValueError, InvalidOperation) as error:
                            predicate_errors.append({**identity, "error": f"{type(error).__name__}: {error}"})
                        else:
                            if not agrees:
                                numeric_mismatches.append(identity)
            if set(seen) != {m["ticker"] for m in checkpoint["markets"]}:
                raise ValueError("Canonical metadata/checkpoint contract census differs")
        if protocol["source_hashes"] != {str(path): sha(path.read_bytes()) for path in FILES}:
            raise ValueError("Diagnostic source changed during execution")
        result = {
            "experiment": EXPERIMENT,
            "protocol_id": identifier,
            "events": len(protocol["checkpoint_records"]),
            "contracts": len(unique),
            "metadata_responses": len(source_ids),
            "classification_counts": dict(counts),
            "value_type_counts": dict(type_counts),
            "exceptional_contracts": exceptional,
            "finite_numeric_predicate_mismatches": numeric_mismatches,
            "finite_numeric_predicate_errors": predicate_errors,
            "source_records": sorted(reader.references.values(), key=lambda r: r["id"]),
            "candle_bodies_read": 0,
            "strategy_scores_computed": 0,
            "network_requests": 0,
            "frozen_failure_preserved": True,
        }
        report_id = archive.append(
            "e024_expiration_diagnostic_report", str(identifier), utcnow(), {}, canonical(result).encode()
        )
        path = Path("reports") / f"E024_expiration_diagnostic_{identifier}.json"
        with path.open("x") as handle:
            json.dump({**result, "report_record_id": report_id}, handle, indent=2, allow_nan=False)
        return {
            "protocol_id": identifier,
            "report_id": report_id,
            "report_path": str(path),
            "events": result["events"],
            "contracts": result["contracts"],
            "classification_counts": dict(counts),
            "value_type_counts": dict(type_counts),
            "exceptional_contracts": exceptional,
            "finite_numeric_predicate_mismatches": numeric_mismatches,
            "finite_numeric_predicate_errors": predicate_errors,
            "candle_bodies_read": 0,
            "strategy_scores_computed": 0,
        }
    finally:
        reader.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--run-record-id", type=int)
    args = parser.parse_args()
    archive = Archive("data")
    try:
        if args.register:
            print(json.dumps({"diagnostic_protocol_id": register(archive), "market_metadata_bodies_read": 0}))
        elif args.run_record_id:
            print(json.dumps(execute(archive, args.run_record_id), sort_keys=True))
        else:
            raise ValueError("Choose a registered diagnostic")
    finally:
        archive.close()


if __name__ == "__main__":
    main()
