"""Registered entry-quote missingness diagnosis; no outcomes, models or replay.

An interior chosen-side price is identifiable even if the opposite side fails
the original two-sided quote gate. Identifiability does not establish a fill.
"""

import argparse
import fcntl
import gzip
import hashlib
import json
import resource
import signal
import sys
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import parse_time, utcnow

CONFIG = Path("config/e032_endpoint_diagnostic.json")
FILES = [
    Path(__file__),
    Path("tests/test_e032_endpoint_diagnostic.py"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
    Path("pyproject.toml"),
    Path("uv.lock"),
]


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def fingerprint(row):
    return {k: row[k] for k in ("id", "kind", "key", "available_at", "body_sha256", "record_sha256")}


def checked_row(archive, identifier, pin=None):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
    if row is None or (pin is not None and fingerprint(row) != pin):
        raise ValueError("Pinned source record identity changed")
    fields = [row[k] for k in ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")]
    previous = archive.db.execute(
        "SELECT record_sha256 FROM records WHERE id<? ORDER BY id DESC LIMIT 1", (identifier,)
    ).fetchone()
    if sha(fields) != row["record_sha256"] or row["previous_sha256"] != (
        previous[0] if previous else "0" * 64
    ):
        raise ValueError("Archive metadata or predecessor fingerprint changed")
    return row


def read_record(archive, row):
    body = archive.body(row)
    if sha(body) != row["body_sha256"]:
        raise ValueError("Pinned archive body changed")
    return json.loads(gzip.decompress(body) if row["kind"].endswith("_gzip") else body)


def number(value):
    if value is None:
        return None, "null"
    if isinstance(value, bool):
        return None, "invalid"
    try:
        parsed = Decimal(str(value))
    except InvalidOperation:
        return None, "invalid"
    if not parsed.is_finite() or not 0 <= parsed <= 1:
        return None, "invalid"
    return parsed, "boundary" if parsed in (0, 1) else "interior"


def classify_endpoint(quote, side, slippage, limit):
    """Independent descriptive decomposition; deliberately does not return a fill."""
    if side not in ("yes", "no"):
        raise ValueError("Unrecognized fixed decision side")
    slip, limit = Decimal(str(slippage)), Decimal(str(limit))
    if slip not in (Decimal(".01"), Decimal(".02")) or not 0 < limit < 1:
        raise ValueError("Invalid registered slippage or original limit")
    required = "ask" if side == "yes" else "bid"
    other = "bid" if side == "yes" else "ask"
    if quote is None:
        return {
            "category": "absent_row",
            "flags": ["absent_row"],
            "required_field": required,
            "required_state": "absent",
            "other_state": "absent",
            "old_two_sided_valid": False,
            "chosen_price_identifiable": False,
            "interior_chosen_price": False,
            "chosen_price_comparison": "unknown",
            "quote_sha256": None,
        }
    if not isinstance(quote, dict):
        raise TypeError("Malformed normalized endpoint row")
    chosen, chosen_state = number(quote.get(required))
    opposite, other_state = number(quote.get(other))
    bid, ask = (chosen, opposite) if side == "no" else (opposite, chosen)
    flags = []
    if chosen_state != "interior":
        flags.append("required_side_" + chosen_state)
    if other_state != "interior":
        flags.append("other_side_" + other_state)
    relation = None
    if bid is not None and ask is not None:
        relation = "locked" if bid == ask else "crossed" if bid > ask else "ordered"
        if relation != "ordered":
            flags.append(relation)
    valid = chosen_state == other_state == "interior" and relation == "ordered"
    if valid:
        category = "strict_two_sided"
    elif chosen_state != "interior":
        category = "required_side_" + chosen_state
    elif other_state != "interior":
        category = "other_side_" + other_state
    else:
        category = relation
    if chosen is None:
        comparison = "unknown"
    elif chosen_state == "boundary":
        comparison = "base_price_boundary_no_fill_inference"
    else:
        side_ask = chosen if side == "yes" else 1 - chosen
        price = side_ask + slip
        comparison = (
            "interior_price_above_original_limit" if price > limit else "interior_price_within_original_limit"
        )
    return {
        "category": category,
        "flags": flags,
        "required_field": required,
        "required_state": chosen_state,
        "other_state": other_state,
        "old_two_sided_valid": valid,
        "chosen_price_identifiable": chosen is not None,
        "interior_chosen_price": chosen_state == "interior",
        "chosen_price_comparison": comparison,
        "quote_sha256": sha(quote),
    }


def check_identity(decision, order, status, market, candidate, scenario, prediction_pin):
    opportunity = decision["opportunity"]
    if (
        sha({k: v for k, v in opportunity.items() if k != "opportunity_sha256"})
        != opportunity["opportunity_sha256"]
    ):
        raise ValueError("Original sealed opportunity changed")
    if sha({k: v for k, v in decision.items() if k != "decision_sha256"}) != decision["decision_sha256"]:
        raise ValueError("Original decision changed")
    meta = market["metadata"]
    for key in ("ticker", "event", "day", "series", "open_ts", "close_ts"):
        if meta[key] != opportunity[key]:
            raise ValueError("Decision/dataset identity changed")
    expected_decision = int(parse_time(meta["source_period_end"]).timestamp()) - 12 * 3600
    identity = f"{opportunity['ticker']}:{expected_decision}"
    trade = f"{candidate}:{identity}:{decision['side']}"
    checks = {
        "trade_id": trade,
        "policy_id": candidate,
        "event": opportunity["event"],
        "side": decision["side"],
        "decision_ts": expected_decision,
        "entry_ts": expected_decision + scenario["entry_delay_hours"] * 3600,
        "prediction_record_id": prediction_pin["id"],
        "prediction_body_sha256": prediction_pin["body_sha256"],
    }
    if (
        any(order.get(k) != v for k, v in checks.items())
        or status["trade_id"] != trade
        or status["opportunity_id"] != identity
    ):
        raise ValueError("Saved order/status/prediction binding changed")
    if opportunity["opportunity_id"] != identity or opportunity["decision_ts"] != expected_decision:
        raise ValueError("Original decision clock changed")
    original_limit = min(Decimal(".99"), Decimal(opportunity[decision["side"] + "_ask"]) + Decimal(".01"))
    if Decimal(order["limit_price"]) != original_limit or Decimal(decision["limit_price"]) != original_limit:
        raise ValueError("Original limit changed")
    return checks["entry_ts"]


def inspect_case(decision, order, status, market, candidate, scenario, prediction_pin, verify_provenance):
    entry = check_identity(decision, order, status, market, candidate, scenario, prediction_pin)
    meta = market["metadata"]
    first = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    last = datetime(2026, 9, 6, tzinfo=UTC).timestamp()
    base = {
        "candidate": candidate,
        "scenario": scenario["name"],
        "opportunity_id": status["opportunity_id"],
        "trade_id": order["trade_id"],
        "side": decision["side"],
        "entry_ts": entry,
        "original_status": status["reason"],
    }
    if entry >= last or not meta["open_ts"] <= entry < meta["close_ts"]:
        expected = "entry_not_released" if entry >= last else "no_admissible_attempt"
        if status["reason"] != expected:
            raise ValueError("Original non-price endpoint gate changed")
        return {**base, "category": expected, "flags": [], "chosen_price_comparison": "not_inspected"}
    if not first < entry < last or entry % 3600:
        raise ValueError("Protected or nonhourly entry endpoint")
    key = entry if entry in market["quotes"] else str(int(entry))
    quote = market["quotes"].get(key)
    source = None
    if quote is not None:
        source = market["quote_provenance"][key]
        verify_provenance(source)
    detail = classify_endpoint(quote, decision["side"], scenario["slippage"], order["limit_price"])
    was_unknown = status["reason"] == "unknown_missing_entry_endpoint"
    if was_unknown != (not detail["old_two_sided_valid"]):
        raise ValueError("Independent old two-sided gate does not reproduce saved missingness")
    if detail["old_two_sided_valid"]:
        rejected = detail["chosen_price_comparison"] == "interior_price_above_original_limit"
        if rejected != (status["reason"] == "known_limit_rejection"):
            raise ValueError("Known original limit rejection does not reproduce")
    return {
        **base,
        **detail,
        "quote_provenance": source,
        "original_unknown_with_identifiable_interior_ask": was_unknown and detail["interior_chosen_price"],
    }


def summarize(cases):
    groups = {}
    for candidate in ("cash", "midpoint", "logistic_offset", "ridge_net_return"):
        for scenario in ("costed", "stress"):
            selected = [r for r in cases if r["candidate"] == candidate and r["scenario"] == scenario]
            unknown = [r for r in selected if r["original_status"] == "unknown_missing_entry_endpoint"]
            groups[f"{candidate}:{scenario}"] = {
                "intents": len(selected),
                "original_statuses": dict(Counter(r["original_status"] for r in selected)),
                "categories_all": dict(Counter(r["category"] for r in selected)),
                "categories_original_unknown": dict(Counter(r["category"] for r in unknown)),
                "flags_original_unknown": dict(Counter(flag for r in unknown for flag in r["flags"])),
                "chosen_price_comparisons_original_unknown": dict(
                    Counter(r["chosen_price_comparison"] for r in unknown)
                ),
                "unknown_with_identifiable_interior_ask": sum(
                    r.get("original_unknown_with_identifiable_interior_ask", False) for r in selected
                ),
            }
    return groups


def file_pins(path):
    return {str(p.resolve()): sha(p.read_bytes()) for p in [*FILES, Path(path)]}


def verify_files(pins):
    if any(sha(Path(path).read_bytes()) != value for path, value in pins.items()):
        raise ValueError("Registered source/config bytes changed")


def validate_config(config):
    if (
        config["experiment"] != "E032-entry-endpoint-diagnostic-v1"
        or config["parent_registration_id"] != 169699
        or config["candidates"] != ["cash", "midpoint", "logistic_offset", "ridge_net_return"]
        or config["scenarios"]
        != [
            {"name": "costed", "entry_delay_hours": 1, "slippage": "0.01"},
            {"name": "stress", "entry_delay_hours": 2, "slippage": "0.02"},
        ]
        or config["attempts"] != 1
        or config["execution_seconds"] != 300
        or config["maximum_peak_memory_bytes"] != 4294967296
        or config["network_requests"] != 0
        or not all(config[k] is True for k in ("no_new_models", "no_resimulation", "no_outcome_calculations"))
    ):
        raise ValueError("Unregistered endpoint diagnostic scope")
    labels = {"parent_protocol", "parent_report", "dataset"}
    labels.update("prediction:" + c for c in config["candidates"])
    labels.update(f"intent:{c}:{s['name']}" for c in config["candidates"] for s in config["scenarios"])
    if set(config["inputs"]) != labels:
        raise ValueError("Incomplete original candidate/scenario census")
    if [config["inputs"][k]["id"] for k in ("parent_protocol", "parent_report", "dataset")] != [
        169699,
        169816,
        128360,
    ]:
        raise ValueError("Registered parent/report/dataset changed")
    for label, item in config["inputs"].items():
        if label.startswith("prediction:"):
            expected = ("e032_predictions_gzip", "169699:" + label.removeprefix("prediction:"))
        elif label.startswith("intent:"):
            expected = ("e032_intents_gzip", "169699:" + label.removeprefix("intent:"))
        else:
            continue
        if (item["kind"], item["key"]) != expected:
            raise ValueError("Candidate/scenario source namespace changed")


def register(archive, path=CONFIG):
    path = Path(path).resolve()
    config = json.loads(path.read_bytes())
    validate_config(config)
    if archive.latest("experiment_protocol", config["experiment"]):
        raise ValueError("Diagnostic already registered; no silent retry")
    pins = {}
    for label, item in config["inputs"].items():
        row = checked_row(archive, item["id"])
        if any(row[k] != item[k] for k in ("kind", "key", "body_sha256")):
            raise ValueError("Declared released input changed")
        pins[label] = fingerprint(row)
    hashes = file_pins(path)
    copies = {
        p: archive.append("e032_endpoint_source", p, utcnow(), {}, Path(p).read_bytes()) for p in hashes
    }
    declaration = {
        "config": config,
        "config_path": str(path),
        "source_hashes": hashes,
        "source_record_ids": copies,
        "input_pins": pins,
        "python_executable": sys.executable,
        "cwd": str(Path.cwd()),
        "registered_before_new_endpoint_inspection": True,
        "known_prior_results": config["known_prior_results"],
        "performance_calculations": 0,
    }
    return archive.append(
        "experiment_protocol", config["experiment"], utcnow(), {}, canonical(declaration).encode()
    )


def execute(archive, identifier):
    registration = checked_row(archive, identifier)
    protocol = read_record(archive, registration)
    config = protocol["config"]
    validate_config(config)
    if registration["kind"] != "experiment_protocol" or registration["key"] != config["experiment"]:
        raise ValueError("Wrong diagnostic registration")
    if protocol["python_executable"] != sys.executable or protocol["cwd"] != str(Path.cwd()):
        raise ValueError("Registered interpreter/cwd changed")
    verify_files(protocol["source_hashes"])
    if archive.latest("e032_endpoint_started", str(identifier)):
        raise ValueError("Diagnostic attempt already started; no retry")
    lock_path = Path(config["lock_file"])
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        archive.append("e032_endpoint_started", str(identifier), utcnow(), {}, b'{"attempt":1}')
        old = signal.getsignal(signal.SIGALRM)

        def timeout(_signal, _frame):
            raise TimeoutError("Registered diagnostic wall budget exhausted")

        def resources():
            if Path(config["stop_file"]).exists():
                raise InterruptedError("Diagnostic stop file present")
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            peak = int(peak if sys.platform == "darwin" else peak * 1024)
            if peak > config["maximum_peak_memory_bytes"]:
                raise MemoryError("Diagnostic resident-memory budget exceeded")
            return peak

        signal.signal(signal.SIGALRM, timeout)
        signal.alarm(config["execution_seconds"])
        try:
            resources()
            payloads = {}
            for label, pin in protocol["input_pins"].items():
                if pin["id"] >= identifier:
                    raise ValueError("Diagnostic input was not fixed before registration")
                payloads[label] = read_record(archive, checked_row(archive, pin["id"], pin))
            parent = payloads["parent_protocol"]
            verify_files(parent["source_hashes"])
            if parent["dataset_record"] != {
                k: protocol["input_pins"]["dataset"][k] for k in parent["dataset_record"]
            }:
                raise ValueError("Parent dataset binding changed")
            report = payloads["parent_report"]
            if report["registration_record_id"] != config["parent_registration_id"]:
                raise ValueError("Released report parent changed")
            dataset = payloads["dataset"]
            if not dataset["complete_event_census"] or dataset["contracts"] != len(dataset["markets"]):
                raise ValueError("Original dataset census changed")
            markets = {m["metadata"]["ticker"]: m for m in dataset["markets"]}
            if len(markets) != dataset["contracts"]:
                raise ValueError("Duplicate dataset market")
            verified_sources = {}

            def verify_provenance(source):
                source_id = source["source_record_id"]
                if source_id >= protocol["input_pins"]["dataset"]["id"]:
                    raise ValueError("Quote source postdates normalized dataset")
                row = checked_row(archive, source_id)
                if row["kind"] != "bankroll_acquisition_http":
                    raise ValueError("Unexpected original candle response kind")
                if any(source[k] != row[k] for k in ("body_sha256", "record_sha256", "available_at")):
                    raise ValueError("Actual quote source receipt/hash binding changed")
                metadata = json.loads(row["metadata"])
                if any(
                    source.get(k) != metadata.get(k) for k in ("received_at", "request_started_at", "url")
                ):
                    raise ValueError("Original quote request/receipt metadata changed")
                if parse_time(source["request_started_at"]) > parse_time(source["received_at"]) or parse_time(
                    source["received_at"]
                ) != parse_time(row["available_at"]):
                    raise ValueError("Actual original quote receipt clock is inconsistent")
                verified_sources[source_id] = fingerprint(row)

            cases = []
            trials = {t["candidate_id"]: t for t in report["trials"]}
            for candidate in config["candidates"]:
                prediction_pin = protocol["input_pins"]["prediction:" + candidate]
                predictions = payloads["prediction:" + candidate]
                trial = trials[candidate]
                if (
                    trial["status"] != "completed"
                    or trial["prediction_record"]["record_id"] != prediction_pin["id"]
                ):
                    raise ValueError("Released trial/prediction link changed")
                if predictions["candidate_id"] != candidate:
                    raise ValueError("Saved candidate identity changed")
                decisions = {d["opportunity_id"]: d for d in predictions["decisions"]}
                if len(decisions) != len(predictions["decisions"]):
                    raise ValueError("Duplicate saved immutable decision")
                for scenario in config["scenarios"]:
                    resources()
                    verify_files(protocol["source_hashes"])
                    label = f"intent:{candidate}:{scenario['name']}"
                    intent_pin, intents = protocol["input_pins"][label], payloads[label]
                    if (
                        not prediction_pin["id"]
                        < intent_pin["id"]
                        < protocol["input_pins"]["parent_report"]["id"]
                    ):
                        raise ValueError("Prediction/intent/report chronology changed")
                    orders = {o["trade_id"]: o for o in intents["orders"]}
                    statuses = {s["opportunity_id"]: s for s in intents["statuses"]}
                    if (
                        len(orders) != len(intents["orders"])
                        or len(statuses) != len(intents["statuses"])
                        or set(statuses) != set(decisions)
                        or len(orders) != len(decisions)
                    ):
                        raise ValueError("Full saved intent/decision census changed")
                    if dict(Counter(s["reason"] for s in statuses.values())) != intents["status_counts"]:
                        raise ValueError("Original retained status counts changed")
                    for identity in sorted(decisions):
                        decision, status = decisions[identity], statuses[identity]
                        cases.append(
                            inspect_case(
                                decision,
                                orders[status["trade_id"]],
                                status,
                                markets[decision["opportunity"]["ticker"]],
                                candidate,
                                scenario,
                                prediction_pin,
                                verify_provenance,
                            )
                        )
            verify_files(protocol["source_hashes"])
            report = {
                "experiment": config["experiment"],
                "registration_record_id": identifier,
                "input_pins": protocol["input_pins"],
                "cases": cases,
                "case_count": len(cases),
                "counts": summarize(cases),
                "verified_quote_sources": list(verified_sources.values()),
                "maximum_observed_peak_rss_bytes": resources(),
                "known_prior_results": config["known_prior_results"],
                "performance_calculations": 0,
                "model_calls": 0,
                "network_requests": 0,
                "interpretation": "Chosen-price identifiability only. No simulated fills, revised returns or P&L. Boundary and crossed quotes remain separately flagged. Actual September receipts are not historical decision availability.",
            }
            body = canonical(report).encode()
            record_id = archive.append(
                "e032_endpoint_report_gzip", str(identifier), utcnow(), {}, gzip.compress(body, mtime=0)
            )
            output = Path(config["report_path"])
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x") as stream:
                stream.write(canonical({**report, "record_id": record_id}))
            return {"record_id": record_id, "payload_sha256": sha(body), "counts": report["counts"]}
        except BaseException as error:
            archive.append(
                "e032_endpoint_failure",
                str(identifier),
                utcnow(),
                {},
                canonical({"error": f"{type(error).__name__}: {error}", "automatic_retry": False}).encode(),
            )
            raise
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--register", action="store_true")
    group.add_argument("--run-record-id", type=int)
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args()
    archive = Archive()
    try:
        print(
            canonical(
                {"registration_record_id": register(archive, args.config)}
                if args.register
                else execute(archive, args.run_record_id)
            )
        )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
