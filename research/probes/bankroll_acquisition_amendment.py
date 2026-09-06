"""Explicit seven-event acquisition amendment for a non-input metadata conflict.

The original acquisition and failed checkpoints remain unchanged. Only the
July 6 cross-tier open-interest discrepancy is permitted; all other metadata
must be identical. Historical metadata is the canonical source for these
already-settled historical-tier contracts. No strategy scores are computed.
"""

import argparse
import fcntl
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from research.probes.bankroll_acquisition import SERIES, quote_window, validate_candles
from weatherpred.archive import Archive, canonical
from weatherpred.http import PublicClient
from weatherpred.timeutil import parse_time, utcnow

EXPERIMENT = "bankroll-July6-open-interest-metadata-amendment-v1"
EVENTS = sorted(series + "-26JUL06" for series in SERIES)
FILES = [
    Path(__file__),
    Path("research/probes/bankroll_acquisition.py"),
    Path("weatherpred/http.py"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
    Path("tests/test_bankroll_acquisition_amendment.py"),
]


def hashes():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in FILES}


def checked(archive, identifier, kind):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
    if row is None or row["kind"] != kind:
        raise ValueError("Missing amendment source")
    body = archive.body(row)
    fields = [row[k] for k in ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")]
    prior = archive.db.execute(
        "SELECT record_sha256 FROM records WHERE id<? ORDER BY id DESC LIMIT 1", (identifier,)
    ).fetchone()
    if (
        hashlib.sha256(body).hexdigest() != row["body_sha256"]
        or hashlib.sha256(canonical(fields).encode()).hexdigest() != row["record_sha256"]
        or row["previous_sha256"] != (prior[0] if prior else "0" * 64)
    ):
        raise ValueError("Amendment source integrity failure")
    return dict(row), json.loads(body)


def metadata_pair(primary, secondary, event):
    """Only open_interest_fp may differ; it is not an E024 signal or cash input."""
    if event not in EVENTS or primary.get("cursor") or secondary.get("cursor"):
        raise ValueError("Out-of-scope or unfinished metadata pair")
    left, right = ({m["ticker"]: m for m in value["markets"]} for value in (primary, secondary))
    if (
        len(left) != 6
        or len(left) != len(primary["markets"])
        or len(right) != len(secondary["markets"])
        or set(left) != set(right)
    ):
        raise ValueError("Cross-tier contract membership changed")
    differences = {}
    for ticker, a in left.items():
        b = right[ticker]
        if a["event_ticker"] != event or not ticker.startswith(event + "-"):
            raise ValueError("Cross-tier event identity changed")
        changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k) or (k in a) != (k in b))
        if changed != ["open_interest_fp"]:
            raise ValueError("Metadata differs beyond the exact declared open-interest field")
        differences[ticker] = changed
    return [left[t] for t in sorted(left)], differences


def register(archive):
    if archive.latest("bankroll_acquisition_amendment_protocol", EXPERIMENT):
        raise ValueError("Amendment already registered")
    _, parent = checked(archive, 99015, "bankroll_acquisition_protocol")
    sources = []
    for event in EVENTS:
        failed = archive.latest("bankroll_acquisition_event", event)
        if failed is None:
            raise ValueError("Missing original failure")
        _, old = checked(archive, failed["id"], "bankroll_acquisition_event")
        if old["complete"] or old["failures"] != [
            {"type": "ValueError", "message": "Cross-tier conflicting market metadata"}
        ]:
            raise ValueError("Original failure has a different cause")
        rows = archive.db.execute(
            "SELECT * FROM records WHERE kind='bankroll_acquisition_http' AND metadata LIKE ?",
            ("%" + event + "%",),
        ).fetchall()
        pair = {}
        for row in rows:
            meta = json.loads(row["metadata"])
            url = urlparse(meta["url"])
            if url.netloc != "external-api.kalshi.com" or parse_qs(url.query).get("event_ticker") != [event]:
                continue
            if url.path not in ("/trade-api/v2/historical/markets", "/trade-api/v2/markets"):
                continue
            route = "historical" if "/historical/" in url.path else "recent"
            if route in pair or meta["status"] != 200:
                raise ValueError("Ambiguous cross-tier source pair")
            source, body = checked(archive, row["id"], "bankroll_acquisition_http")
            pair[route] = (source, body)
        if set(pair) != {"historical", "recent"}:
            raise ValueError("Incomplete cross-tier source pair")
        markets, changed = metadata_pair(pair["historical"][1], pair["recent"][1], event)
        if any(
            not m.get("settlement_ts")
            or parse_time(m["settlement_ts"]) >= parse_time(parent["config"]["cutoff"]["market_settled_ts"])
            for m in markets
        ):
            raise ValueError("Amendment may only use contracts in the frozen historical tier")
        sources.append(
            {
                "event": event,
                "original_failed_record_id": failed["id"],
                "primary_record_id": pair["historical"][0]["id"],
                "secondary_record_id": pair["recent"][0]["id"],
                "primary_body_sha256": pair["historical"][0]["body_sha256"],
                "secondary_body_sha256": pair["recent"][0]["body_sha256"],
                "differences": changed,
            }
        )
    pins = hashes()
    source_records = {
        str(p): archive.append("research_source", str(p), utcnow(), {}, p.read_bytes()) for p in FILES
    }
    protocol = {
        "experiment": EXPERIMENT,
        "parent_acquisition_protocol_record_id": 99015,
        "source_hashes": pins,
        "source_record_ids": source_records,
        "events": sources,
        "canonical_metadata_route": "historical",
        "allowed_difference": "open_interest_fp",
        "field_is_strategy_input": False,
        "http_operations": 42,
        "attempts_per_operation_maximum": 3,
        "request_interval_seconds": 0.5,
        "execution_budget_seconds": 600,
        "stop_file": "data/STOP_BANKROLL_AMENDMENT",
        "cwd": str(Path.cwd()),
        "original_failures_preserved": True,
        "strategy_scores_authorized": False,
        "interpretation": "Explicit acquisition repair, not the unchanged original run. All fields except non-input open interest agree. No outcome-dependent source choice or strategy change.",
    }
    return archive.append(
        "bankroll_acquisition_amendment_protocol", EXPERIMENT, utcnow(), {}, canonical(protocol).encode()
    )


def execute(archive, identifier):
    _, protocol = checked(archive, identifier, "bankroll_acquisition_amendment_protocol")
    if hashes() != protocol["source_hashes"] or str(Path.cwd()) != protocol["cwd"]:
        raise ValueError("Amendment source or environment changed")
    if archive.latest("bankroll_amendment_started", str(identifier)):
        raise ValueError("Amendment attempt already exists; no silent retry")
    archive.append("bankroll_amendment_started", str(identifier), utcnow(), {}, b"{}")
    deadline = time.monotonic() + protocol["execution_budget_seconds"]
    client = PublicClient(archive, interval=protocol["request_interval_seconds"])
    completed, requests = [], 0
    try:
        for item in protocol["events"]:
            if hashes() != protocol["source_hashes"]:
                raise ValueError("Amendment source changed during acquisition")
            _, primary = checked(archive, item["primary_record_id"], "bankroll_acquisition_http")
            _, secondary = checked(archive, item["secondary_record_id"], "bankroll_acquisition_http")
            markets, _ = metadata_pair(primary, secondary, item["event"])
            members = []
            for market in markets:
                if time.monotonic() >= deadline or Path(protocol["stop_file"]).exists():
                    raise TimeoutError("Amendment budget/stop reached")
                window = quote_window(market)
                query = {k: window[k] for k in ("start_ts", "end_ts", "period_interval")}
                requests += 1
                if requests > protocol["http_operations"]:
                    raise ValueError("Amendment request family exceeded")
                payload, source = client.json(
                    f"/historical/markets/{market['ticker']}/candlesticks",
                    query,
                    kind="bankroll_acquisition_http",
                    key=f"amendment:{identifier}:{market['ticker']}",
                )
                if payload.get("ticker") != market["ticker"]:
                    raise ValueError("Amendment candle identity differs")
                members.append(
                    {
                        "ticker": market["ticker"],
                        "status": market["status"],
                        "settlement_ts": market["settlement_ts"],
                        "window": window,
                        "candles": validate_candles(payload["candlesticks"], query),
                        "candle_record_ids": [source],
                        "candle_route": "historical",
                    }
                )
            checkpoint = {
                "protocol_record_id": 99015,
                "amendment_protocol_record_id": identifier,
                "original_failed_record_id": item["original_failed_record_id"],
                "event": item["event"],
                "day": "2026-07-06",
                "metadata_record_ids": [item["primary_record_id"]],
                "diagnostic_metadata_record_ids": [item["secondary_record_id"]],
                "markets": members,
                "complete": True,
                "failures": [],
                "finished_at": utcnow().isoformat(),
                "amended_acquisition": True,
                "strategy_scores_computed": 0,
            }
            completed.append(
                archive.append(
                    "bankroll_acquisition_amended_event",
                    item["event"],
                    utcnow(),
                    {"amendment_protocol_record_id": identifier},
                    canonical(checkpoint).encode(),
                )
            )
        if hashes() != protocol["source_hashes"]:
            raise ValueError("Amendment source changed before final report")
        report = {
            "amendment_protocol_record_id": identifier,
            "amended_event_record_ids": completed,
            "events": len(completed),
            "contracts": sum(6 for _ in completed),
            "http_operations": requests,
            "strategy_scores_computed": 0,
            "original_failures_preserved": True,
        }
        archive.append("bankroll_amendment_report", str(identifier), utcnow(), {}, canonical(report).encode())
        return report
    except BaseException as error:
        archive.append(
            "bankroll_amendment_failed",
            str(identifier),
            utcnow(),
            {},
            canonical(
                {"error": f"{type(error).__name__}: {error}", "completed": completed, "requests": requests}
            ).encode(),
        )
        raise
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--register", action="store_true")
    mode.add_argument("--run-record-id", type=int)
    args = parser.parse_args()
    archive = Archive("data")
    try:
        with (archive.root / "bankroll_amendment.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(
                canonical(
                    {"amendment_protocol_record_id": register(archive), "network_requests": 0}
                    if args.register
                    else execute(archive, args.run_record_id)
                )
            )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
