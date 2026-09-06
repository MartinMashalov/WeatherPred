"""Independent synthetic archives: no real annual prices, downloads, or scores."""

import copy
import hashlib
from urllib.parse import urlencode

import pytest

import research.probes.bankroll_dataset as dataset
from research.probes.bankroll_acquisition_amendment import EVENTS, EXPERIMENT
from weatherpred.archive import Archive, canonical

BEFORE = "2026-09-06T17:00:00.000000+00:00"
REGISTERED = "2026-09-06T18:00:00.000000+00:00"
REQUEST = "2026-09-06T18:01:00.000000+00:00"
RECEIPT = "2026-09-06T18:01:01.000000+00:00"
FINISHED = "2026-09-06T18:02:00.000000+00:00"
FAILURES = [{"type": "ValueError", "message": "Cross-tier conflicting market metadata"}]


def append_json(archive, kind, key, body, at=BEFORE, metadata=None):
    return archive.append(kind, key, at, metadata or {}, canonical(body).encode())


def append_http(archive, path, query, body, *, key=None, after=False, fault=None):
    url = "https://external-api.kalshi.com/trade-api/v2" + path + "?" + urlencode(query)
    at = RECEIPT if after else BEFORE
    metadata = {
        "url": url,
        "status": 200,
        "received_at": at,
        "request_started_at": REQUEST if after else BEFORE,
    }
    if fault == "early_request":
        metadata["request_started_at"] = BEFORE
    elif fault == "receipt_mismatch":
        metadata["received_at"] = FINISHED
    elif fault == "request_after_receipt":
        metadata["request_started_at"] = FINISHED
    return append_json(
        archive,
        "wrong_http_kind" if fault == "wrong_kind" else "bankroll_acquisition_http",
        key or url,
        body,
        at,
        metadata,
    )


def fixture_archive(tmp_path, *, fault=None):
    """A complete seven-event repair with six distinct raw candle receipts each."""
    archive = Archive(tmp_path / "archive")
    source_path = tmp_path / "synthetic_frozen_source.py"
    source_path.write_text("# Synthetic source; deliberately not imported.\n")
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    source_id = archive.append("research_source", str(source_path), BEFORE, {}, source_path.read_bytes())
    original_index, entries = {}, []
    for event in EVENTS:
        primary = {
            "cursor": "",
            "markets": [
                {
                    "ticker": f"{event}-B{i}",
                    "event_ticker": event,
                    "open_interest_fp": "10.00",
                    "result": "yes",
                    "yes_ask_dollars": "0.4200",
                    "rules_primary": "Synthetic temperature rule",
                    "status": "finalized",
                    "settlement_ts": "2026-07-07T12:00:00Z",
                }
                for i in range(6)
            ],
        }
        secondary = copy.deepcopy(primary)
        for member in secondary["markets"]:
            member["open_interest_fp"] = "0.00"
        if event == EVENTS[0] and fault in ("result", "yes_ask_dollars", "rules_primary"):
            secondary["markets"][0][fault] = "different"
        primary_id = append_http(
            archive,
            "/markets" if event == EVENTS[0] and fault == "route" else "/historical/markets",
            {"event_ticker": event, "limit": 1000},
            primary,
            fault=fault
            if event == EVENTS[0] and fault in ("receipt_mismatch", "request_after_receipt")
            else None,
        )
        secondary_id = append_http(archive, "/markets", {"event_ticker": event, "limit": 1000}, secondary)
        failed_id = append_json(
            archive,
            "bankroll_acquisition_event",
            event,
            {
                "event": event,
                "protocol_record_id": dataset.ACQUISITION_PROTOCOL_ID,
                "complete": False,
                "failures": FAILURES,
            },
        )
        original_index[event] = failed_id
        entry = {
            "event": event,
            "original_failed_record_id": failed_id,
            "primary_record_id": primary_id,
            "secondary_record_id": secondary_id,
            "primary_body_sha256": hashlib.sha256(canonical(primary).encode()).hexdigest(),
            "secondary_body_sha256": hashlib.sha256(canonical(secondary).encode()).hexdigest(),
            "differences": {m["ticker"]: ["open_interest_fp"] for m in primary["markets"]},
        }
        if event == EVENTS[0] and fault == "declared_body_hash":
            entry["primary_body_sha256"] = "0" * 64
        entries.append(entry)
    protocol = {
        "experiment": EXPERIMENT,
        "parent_acquisition_protocol_record_id": dataset.ACQUISITION_PROTOCOL_ID,
        "canonical_metadata_route": "recent" if fault == "canonical_declaration" else "historical",
        "allowed_difference": "open_interest_fp",
        "field_is_strategy_input": False,
        "strategy_scores_authorized": False,
        "source_hashes": {str(source_path): source_sha},
        "source_record_ids": {str(source_path): source_id},
        "events": entries,
    }
    identifier = append_json(
        archive, "bankroll_acquisition_amendment_protocol", EXPERIMENT, protocol, REGISTERED
    )
    checkpoint_ids = []
    for item in entries:
        event, members = item["event"], []
        for ticker in item["differences"]:
            first = event == EVENTS[0] and not members
            key = f"amendment:{identifier}:{ticker}"
            if first and fault == "candle_key":
                key = "unregistered:" + key
            source_id = append_http(
                archive,
                f"/historical/markets/{ticker}/candlesticks",
                {"start_ts": 1783296001, "end_ts": 1783382340, "period_interval": 60},
                {"ticker": ticker, "candlesticks": []},
                key=key,
                after=True,
                fault=fault if first and fault in ("early_request", "wrong_kind") else None,
            )
            ids = [source_id]
            if first and fault == "candle_before_registration":
                ids = [item["primary_record_id"]]
            elif first and fault == "candle_after_checkpoint":
                ids = [1000000]
            elif first and fault == "multiple_candle_sources":
                ids.append(source_id)
            members.append({"ticker": ticker, "candle_record_ids": ids, "candle_route": "historical"})
        checkpoint = {
            "event": event,
            "protocol_record_id": dataset.ACQUISITION_PROTOCOL_ID,
            "amendment_protocol_record_id": identifier,
            "original_failed_record_id": item["original_failed_record_id"],
            "metadata_record_ids": [item["primary_record_id"]],
            "diagnostic_metadata_record_ids": [item["secondary_record_id"]],
            "amended_acquisition": True,
            "complete": True,
            "failures": [],
            "markets": members,
        }
        if event == EVENTS[0] and fault == "checkpoint_lineage":
            checkpoint["diagnostic_metadata_record_ids"] = [item["primary_record_id"]]
        if event == EVENTS[0] and fault == "incomplete":
            continue
        copies = 2 if event == EVENTS[0] and fault == "duplicate" else 1
        for _ in range(copies):
            checkpoint_ids.append(
                append_json(
                    archive,
                    "bankroll_acquisition_amended_event",
                    event,
                    checkpoint,
                    FINISHED,
                    {"amendment_protocol_record_id": identifier},
                )
            )
    if fault == "source_changed":
        source_path.write_text("# Changed after declaration.\n")
    archive.close()
    return (
        dataset.ArchiveReader(tmp_path / "archive", checkpoint_ids[-1], FINISHED),
        identifier,
        original_index,
    )


def both_phases(reader, identifier, original_index):
    repairs = dataset.approved_amendments(reader, [identifier], original_index)
    dataset.verify_amendment_sources(reader, [identifier])
    return repairs


def test_exact_seven_event_repair_preserves_original_versions_and_receipts(tmp_path):
    reader, identifier, original_index = fixture_archive(tmp_path)
    try:
        repairs = both_phases(reader, identifier, original_index)
        assert sorted(repairs) == EVENTS
        assert sum(len(checkpoint["markets"]) for _, checkpoint in repairs.values()) == 42
        for event, (_, checkpoint) in repairs.items():
            old, _ = reader.read(original_index[event], "bankroll_acquisition_event")
            assert old["complete"] is False and old["failures"] == FAILURES
            assert checkpoint["metadata_record_ids"] != checkpoint["diagnostic_metadata_record_ids"]
            for source_id in checkpoint["metadata_record_ids"] + checkpoint["diagnostic_metadata_record_ids"]:
                assert reader.references[source_id]["available_at"] == BEFORE
                assert (
                    dataset.http_scope(reader.record(source_id), event=event)[
                        "historical_availability_verified"
                    ]
                    is False
                )
            for member in checkpoint["markets"]:
                assert reader.record(member["candle_record_ids"][0])["available_at"] == RECEIPT
    finally:
        reader.close()


@pytest.mark.parametrize(
    "fault,match",
    [
        ("incomplete", "incomplete or duplicated"),
        ("duplicate", "incomplete or duplicated"),
        ("canonical_declaration", "Unregistered acquisition amendment semantics"),
        ("checkpoint_lineage", "declared lineage"),
        ("source_changed", "Frozen amendment source changed"),
        ("candle_before_registration", "pre-checkpoint registered acquisition"),
        ("candle_after_checkpoint", "pre-checkpoint registered acquisition"),
        ("multiple_candle_sources", "pre-checkpoint registered acquisition"),
        ("candle_key", "registered identity/time"),
        ("early_request", "registered identity/time"),
        ("wrong_kind", "unexpected record kind"),
        ("route", "canonical source route changed"),
        ("result", "beyond the exact declared"),
        ("yes_ask_dollars", "beyond the exact declared"),
        ("rules_primary", "beyond the exact declared"),
        ("declared_body_hash", "metadata source differs"),
        ("receipt_mismatch", "Receipt metadata differs"),
        ("request_after_receipt", "Request starts after"),
    ],
)
def test_inconsistent_repair_is_rejected(tmp_path, fault, match):
    reader, identifier, original_index = fixture_archive(tmp_path, fault=fault)
    try:
        with pytest.raises(ValueError, match=match):
            both_phases(reader, identifier, original_index)
    finally:
        reader.close()


def test_unregistered_or_repeated_protocol_is_rejected(tmp_path):
    reader, identifier, original_index = fixture_archive(tmp_path)
    try:
        with pytest.raises(ValueError, match="unexpected record kind"):
            dataset.approved_amendments(reader, [next(iter(original_index.values()))], original_index)
        with pytest.raises(ValueError, match="Repeated acquisition amendment"):
            dataset.approved_amendments(reader, [identifier, identifier], original_index)
        changed_index = dict(original_index, **{EVENTS[0]: identifier})
        with pytest.raises(ValueError, match="undeclared original checkpoint"):
            dataset.approved_amendments(reader, [identifier], changed_index)
    finally:
        reader.close()


def test_blob_corruption_is_rejected_without_fabricating_new_record_hash(tmp_path):
    reader, identifier, original_index = fixture_archive(tmp_path)
    try:
        protocol, _ = reader.read(identifier)
        raw = reader.record(protocol["events"][0]["primary_record_id"])
        blob = reader.root / "blobs" / raw["body_sha256"]
        blob.write_bytes(b'{"changed":true}')
        with pytest.raises(ValueError, match="Archive body hash mismatch"):
            both_phases(reader, identifier, original_index)
    finally:
        reader.close()


def test_checkpoint_phase_never_opens_any_raw_price_metadata_or_candle_body(tmp_path):
    reader, identifier, original_index = fixture_archive(tmp_path)
    try:
        # Make actual value reads impossible at the filesystem boundary. The real
        # ArchiveReader and amendment functions remain in use and unmocked.
        paths = [
            reader.root / "blobs" / row["body_sha256"]
            for row in reader.db.execute(
                "SELECT body_sha256 FROM records WHERE kind='bankroll_acquisition_http'"
            )
        ]
        saved = {path: path.read_bytes() for path in paths}
        for path in saved:
            path.unlink()
        repairs = dataset.approved_amendments(reader, [identifier], original_index)
        assert sorted(repairs) == EVENTS
        assert all(record["kind"] != "bankroll_acquisition_http" for record in reader.references.values())
        with pytest.raises(FileNotFoundError):
            dataset.verify_amendment_sources(reader, [identifier])
        for path, body in saved.items():
            path.write_bytes(body)
        dataset.verify_amendment_sources(reader, [identifier])
    finally:
        reader.close()


def reserve_next_id(archive, next_id):
    """Only in a temporary fixture, add a hash-valid sparse-ID sentinel.

    This preserves the real hardcoded parent/manifest IDs without mocking the
    normalizer, modifying constants, or constructing 99,000 unrelated records.
    """
    body = b"synthetic sparse archive sentinel"
    digest = hashlib.sha256(body).hexdigest()
    (archive.root / "blobs" / digest).write_bytes(body)
    prior = archive.db.execute("SELECT record_sha256 FROM records ORDER BY id DESC LIMIT 1").fetchone()[0]
    fields = ["synthetic_sentinel", str(next_id), FINISHED, "{}", digest, prior]
    archive.db.execute(
        "INSERT INTO records(id,kind,key,available_at,metadata,body_sha256,previous_sha256,record_sha256) VALUES(?,?,?,?,?,?,?,?)",
        [next_id - 1, *fields, hashlib.sha256(canonical(fields).encode()).hexdigest()],
    )
    archive.db.commit()


def test_full_census_with_one_other_failure_blocks_all_repair_value_reads(tmp_path):
    reader, identifier, original_index = fixture_archive(tmp_path)
    reader.close()
    archive = Archive(tmp_path / "archive")
    remaining = sorted(dataset.expected_events() - set(original_index))
    failed_event = remaining[-1]
    for event in remaining:
        append_json(
            archive,
            "bankroll_acquisition_event",
            event,
            {
                "event": event,
                "protocol_record_id": dataset.ACQUISITION_PROTOCOL_ID,
                "complete": event != failed_event,
                "failures": [{"message": "Unrepaired acquisition failure"}] if event == failed_event else [],
            },
            FINISHED,
        )
    # Non-nested census metadata contains identities only, never prices/outcomes.
    all_events = sorted(dataset.expected_events())
    census_url = "https://external-api.kalshi.com/trade-api/v2/events?with_nested_markets=false"
    census_id = append_json(
        archive,
        "bankroll_event_metadata",
        census_url,
        {"events": [{"event_ticker": event} for event in all_events]},
        FINISHED,
        {"url": census_url, "status": 200, "received_at": FINISHED, "request_started_at": FINISHED},
    )
    events = sorted(
        [
            {
                "event_ticker": event,
                "day": dataset.event_date(event).isoformat(),
                "source_record_id": census_id,
            }
            for event in all_events
        ],
        key=lambda value: (value["day"], value["event_ticker"]),
    )
    cutoff = {"market_settled_ts": "2026-08-01T00:00:00Z"}
    cutoff_id = append_json(
        archive,
        "bankroll_historical_cutoff",
        "cutoff",
        cutoff,
        FINISHED,
        {"url": "https://external-api.kalshi.com/trade-api/v2/historical/cutoff"},
    )
    census_protocol = append_json(archive, "bankroll_event_metadata_protocol", "synthetic", {}, FINISHED)
    manifest = {
        "complete": True,
        "protocol_record_id": census_protocol,
        "source_record_ids": [census_id, cutoff_id],
        "series": [
            {"events": [event for event in events if event["event_ticker"].startswith(series + "-")]}
            for series in dataset.SERIES
        ],
    }
    reserve_next_id(archive, dataset.MANIFEST_ID)
    assert (
        append_json(archive, "bankroll_event_metadata_report", "synthetic", manifest, FINISHED)
        == dataset.MANIFEST_ID
    )
    protocol = {
        "config": {
            "manifest_record_id": dataset.MANIFEST_ID,
            "events": 1736,
            "series": list(dataset.SERIES),
            "manifest_body_sha256": hashlib.sha256(canonical(manifest).encode()).hexdigest(),
            "events_sha256": hashlib.sha256(canonical(events).encode()).hexdigest(),
            "cutoff": cutoff,
        }
    }
    reserve_next_id(archive, dataset.ACQUISITION_PROTOCOL_ID)
    assert (
        append_json(archive, "bankroll_acquisition_protocol", "synthetic", protocol, FINISHED)
        == dataset.ACQUISITION_PROTOCOL_ID
    )
    for row in archive.db.execute(
        "SELECT DISTINCT body_sha256 FROM records WHERE kind='bankroll_acquisition_http'"
    ):
        (archive.root / "blobs" / row["body_sha256"]).unlink()
    archive.close()
    reader = dataset.ArchiveReader(tmp_path / "archive", dataset.ACQUISITION_PROTOCOL_ID, FINISHED)
    try:
        # All 1,736 original keys and all seven complete replacements exist, yet
        # a different failed event prevents even the first repaired value read.
        with pytest.raises(ValueError, match="includes an incomplete/failed checkpoint"):
            dataset.build_dataset(reader, {}, [identifier])
        assert all(record["kind"] != "bankroll_acquisition_http" for record in reader.references.values())
    finally:
        reader.close()
