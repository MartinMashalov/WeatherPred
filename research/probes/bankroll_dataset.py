"""Audit protocol 99015 into an exact, provenance-preserving daily dataset.

There is deliberately no command-line normalization action. A separately frozen
E024 driver must supply an archive cutoff and source-window configuration before
calling build_dataset. Importing this module reads no archive or dataset.
"""

import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from weatherpred.archive import canonical
from weatherpred.contracts import historical_predicate, nws_standard_day, yes_at
from weatherpred.timeutil import iso, parse_time

ACQUISITION_PROTOCOL_ID = 99015
MANIFEST_ID = 92494
FIRST = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 9, 6, tzinfo=UTC)
SERIES = ("KXHIGHAUS", "KXHIGHCHI", "KXHIGHDEN", "KXHIGHLAX", "KXHIGHMIA", "KXHIGHNY", "KXHIGHPHIL")


def event_date(event):
    match = re.fullmatch(r"([A-Z0-9]+)-(\d{2}[A-Z]{3}\d{2})", event)
    if not match or match[1] not in SERIES:
        raise ValueError("Unregistered daily event identity")
    day = datetime.strptime(match[2], "%y%b%d").replace(tzinfo=UTC).date()
    if not FIRST.date() <= day < END.date():
        raise ValueError("Protected or out-of-scope event date")
    return day


def expected_events():
    return {
        f"{series}-{(FIRST + timedelta(days=i)).strftime('%y%b%d').upper()}"
        for series in SERIES
        for i in range((END - FIRST).days)
    }


def complete_event_index(rows):
    """Check checkpoint presence before opening any checkpoint or market body."""
    expected = expected_events()
    index = {}
    for row in rows:
        event_date(row["key"])
        if row["key"] in index:
            raise ValueError("Duplicate latest checkpoint keys")
        index[row["key"]] = row["id"]
    if set(index) != expected:
        raise ValueError(
            f"Incomplete census: missing={len(expected - set(index))}, extra={len(set(index) - expected)}"
        )
    return index


class ArchiveReader:
    """Read-only bounded archive access; verify referenced blobs and record links.

    This checks the immediate predecessor's stored hash, not every earlier blob:
    traversing all archive bodies would unnecessarily expose the sealed quarter.
    """

    def __init__(self, root, source_cutoff_id, source_cutoff_at):
        self.root = Path(root)
        self.cutoff_id = source_cutoff_id
        self.cutoff_at = parse_time(source_cutoff_at)
        self.db = sqlite3.connect(f"file:{self.root / 'archive.sqlite'}?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self.references = {}
        if not self.db.execute("SELECT 1 FROM records WHERE id=?", (source_cutoff_id,)).fetchone():
            self.db.close()
            raise ValueError("Frozen archive cutoff ID does not exist")

    def close(self):
        self.db.close()

    def record(self, identifier, kind=None):
        if not isinstance(identifier, int) or identifier > self.cutoff_id:
            raise ValueError("Source record exceeds frozen archive cutoff")
        row = self.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
        if row is None or (kind is not None and row["kind"] != kind):
            raise ValueError("Missing source record or unexpected record kind")
        if parse_time(row["available_at"]) > self.cutoff_at:
            raise ValueError("Source receipt exceeds frozen archive cutoff time")
        fields = [
            row[k] for k in ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")
        ]
        if hashlib.sha256(canonical(fields).encode()).hexdigest() != row["record_sha256"]:
            raise ValueError("Archive record hash mismatch")
        prior = self.db.execute(
            "SELECT record_sha256 FROM records WHERE id<? ORDER BY id DESC LIMIT 1", (identifier,)
        ).fetchone()
        if row["previous_sha256"] != (prior[0] if prior else "0" * 64):
            raise ValueError("Archive predecessor link mismatch")
        return dict(row)

    def read(self, identifier, kind=None):
        row = self.record(identifier, kind)
        body = (self.root / "blobs" / row["body_sha256"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != row["body_sha256"]:
            raise ValueError("Archive body hash mismatch")
        self.references[identifier] = {
            k: row[k] for k in ("id", "kind", "key", "available_at", "body_sha256", "record_sha256")
        }
        return json.loads(body), row

    def checkpoints(self):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT key,max(id) AS id FROM records WHERE kind='bankroll_acquisition_event' AND id<=? AND available_at<=? GROUP BY key",
                (self.cutoff_id, iso(self.cutoff_at)),
            )
        ]


def http_scope(row, *, event=None, route=None, tickers=None, window=None):
    """Check request identity/time using record metadata before decoding values."""
    metadata = json.loads(row["metadata"])
    if metadata.get("status") != 200:
        raise ValueError("Source is not a successful public response")
    if iso(metadata["received_at"]) != iso(row["available_at"]):
        raise ValueError("Receipt metadata differs from archive availability")
    if parse_time(metadata["request_started_at"]) > parse_time(row["available_at"]):
        raise ValueError("Request starts after its receipt")
    url = urlparse(metadata["url"])
    if url.scheme != "https" or url.netloc != "external-api.kalshi.com":
        raise ValueError("Unexpected public data origin")
    path = url.path.removeprefix("/trade-api/v2")
    query = parse_qs(url.query)
    if event is not None:
        event_date(event)
        if path not in ("/markets", "/historical/markets") or query.get("event_ticker") != [event]:
            raise ValueError("Market request was not restricted to the exact safe event")
    if route is not None:
        if (
            window.get("period_interval") != 60
            or not FIRST.timestamp() < window["start_ts"] <= window["end_ts"] <= END.timestamp()
        ):
            raise ValueError("Protected or invalid requested candle window")
        if route == "historical":
            if len(tickers) != 1 or path != f"/historical/markets/{tickers[0]}/candlesticks":
                raise ValueError("Historical request ticker mismatch")
        elif route == "recent_batch":
            requested = query.get("market_tickers", [""])[0].split(",")
            if (
                path != "/markets/candlesticks"
                or len(set(requested)) != len(requested)
                or set(requested) != set(tickers)
            ):
                raise ValueError("Recent request ticker set mismatch")
            if query.get("include_latest_before_start") != ["false"]:
                raise ValueError("Synthetic carried candle was not disabled")
        else:
            raise ValueError("Unregistered candle route")
        for field in ("start_ts", "end_ts", "period_interval"):
            if query.get(field) != [str(window[field])]:
                raise ValueError("Candle query differs from declared window")
    return {
        "source_record_id": row["id"],
        "body_sha256": row["body_sha256"],
        "record_sha256": row["record_sha256"],
        "available_at": row["available_at"],
        "received_at": metadata["received_at"],
        "request_started_at": metadata["request_started_at"],
        "url": metadata["url"],
        "historical_availability_verified": False,
    }


def fixed_dollars(distribution, route):
    if not isinstance(distribution, dict):
        raise TypeError("Missing quote-side object")
    fields = ("close", "close_dollars") if route == "historical" else ("close_dollars",)
    values = [distribution[field] for field in fields if distribution.get(field) is not None]
    for value in values:
        if not isinstance(value, str) or re.fullmatch(r"(?:0\.\d{4}|1\.0000)", value) is None:
            raise ValueError("Quote is not an exact four-decimal dollar string")
    if len({Decimal(v) for v in values}) > 1:
        raise ValueError("Historical/live quote aliases conflict")
    return values[0] if values else None


def normalize_candles(candles, route, window, provenance):
    quotes, sources, statuses = {}, {}, Counter()
    for index, candle in enumerate(candles):
        end = candle["end_period_ts"]
        # Gate actual input time before looking at bid, ask or other value fields.
        if isinstance(end, bool) or not isinstance(end, int) or end % 3600:
            raise ValueError("Candle endpoint is not an exact UTC hour")
        if not max(FIRST.timestamp(), window["start_ts"] - 1) < end <= min(END.timestamp(), window["end_ts"]):
            raise ValueError("Protected or out-of-window candle timestamp")
        if end in quotes:
            raise ValueError("Duplicate candle timestamp")
        bid = fixed_dollars(candle.get("yes_bid", {}), route)
        ask = fixed_dollars(candle.get("yes_ask", {}), route)
        status = (
            "one_sided_or_missing"
            if bid is None or ask is None
            else "two_sided"
            if 0 < Decimal(bid) < Decimal(ask) < 1
            else "non_executable_endpoint"
        )
        statuses[status] += 1
        quotes[end] = {"bid": bid, "ask": ask}
        sources[end] = dict(provenance, source_row_index=index, quote_status=status)
    expected = set(range(math.ceil(window["start_ts"] / 3600) * 3600, window["end_ts"] + 1, 3600))
    return {
        "quotes": quotes,
        "quote_provenance": sources,
        "quote_status_counts": dict(statuses),
        "missing_candle_endpoints": sorted(expected - set(quotes)),
    }


def market_metadata(market, event, provenance, windows):
    day = event_date(event)
    if market["event_ticker"] != event or not market["ticker"].startswith(event + "-"):
        raise ValueError("Contract crossed the safe event identity boundary")
    series = event.split("-", 1)[0]
    opened, closed = parse_time(market["open_time"]), parse_time(market["close_time"])
    if opened >= closed:
        raise ValueError("Invalid market open/close interval")
    primary = market.get("rules_primary", "")
    source_is_nws = bool(re.search(r"\bnws\b|national weather service", primary, re.IGNORECASE))
    conflicting_source = bool(
        re.search(r"weather company|synoptic|kalshi weather index", primary, re.IGNORECASE)
    )
    supported = source_is_nws and not conflicting_source and series in windows["series"]
    period_end = None
    if supported:
        _, period_end = nws_standard_day(day, windows["series"][series]["standard_utc_offset_hours"])
    settlement_text = market.get("settlement_ts")
    settlement = parse_time(settlement_text) if settlement_text else None
    if settlement and (settlement < opened or settlement > parse_time(provenance["available_at"])):
        raise ValueError("Settlement timestamp is before opening or after source receipt")
    final = market.get("status") in ("finalized", "settled") and market.get("result") in ("yes", "no")
    eligible = final and settlement is not None
    status = (
        "final_binary_with_settlement_time"
        if eligible
        else "missing_settlement_time"
        if final
        else "pending_or_nonbinary"
    )
    predicate = historical_predicate(market)
    if (
        eligible
        and market.get("expiration_value") not in (None, "")
        and yes_at(predicate, market["expiration_value"]) != (market["result"] == "yes")
    ):
        raise ValueError("Final settlement value contradicts primary contract predicate")
    return {
        "ticker": market["ticker"],
        "event": event,
        "series": series,
        "day": str(day),
        "source_period_end": iso(period_end) if period_end else None,
        "source_window_status": "nws_standard_time_mapping"
        if supported
        else "unsupported_primary_source_window",
        "source_window_assumption": windows.get("window"),
        "open_ts": opened.timestamp(),
        "close_ts": closed.timestamp(),
        "open_time": iso(opened),
        "close_time": iso(closed),
        "reported_settlement_ts": settlement_text,
        "settled_ts": settlement.timestamp() if eligible else None,
        "outcome": int(market["result"] == "yes") if eligible else None,
        "reported_result": market.get("result"),
        "reported_status": market.get("status"),
        "settlement_status": status,
        "settlement_after_year_end": bool(settlement and settlement >= END),
        "rules_primary": primary,
        "rules_secondary": market.get("rules_secondary", ""),
        "expiration_value": market.get("expiration_value"),
        "strike_type": predicate["strike_type"],
        "floor_strike": predicate.get("floor_strike"),
        "cap_strike": predicate.get("cap_strike"),
        "predicate_provenance": predicate["predicate_provenance"],
        "source_record_id": provenance["source_record_id"],
        "metadata_provenance": provenance,
        "historical_availability_verified": False,
    }


def normalize_event(reader, checkpoint, windows, *, historical_cutoff=None):
    event = checkpoint["event"]
    event_date(event)
    if (
        checkpoint.get("protocol_record_id") != ACQUISITION_PROTOCOL_ID
        or not checkpoint.get("complete")
        or checkpoint.get("failures")
    ):
        raise ValueError("Incomplete or foreign event checkpoint")
    listed = checkpoint["markets"]
    if not listed or len({m["ticker"] for m in listed}) != len(listed):
        raise ValueError("Missing or duplicated checkpoint contracts")
    raw_markets, metadata_sources, metadata_versions = {}, {}, {}
    for identifier in checkpoint["metadata_record_ids"]:
        row = reader.record(identifier, "bankroll_acquisition_http")
        provenance = http_scope(row, event=event)
        data, _ = reader.read(identifier, "bankroll_acquisition_http")
        if data.get("cursor"):
            raise ValueError("Unfinished market metadata pagination")
        seen = set()
        for market in data["markets"]:
            if market["event_ticker"] != event:
                raise ValueError("Raw metadata includes a different event")
            ticker = market["ticker"]
            if ticker in seen:
                raise ValueError("Duplicate contract within metadata response")
            seen.add(ticker)
            if ticker in raw_markets:
                if market != raw_markets[ticker]:
                    raise ValueError("Conflicting contract metadata across sources")
                metadata_versions[ticker].append(provenance)
                continue
            raw_markets[ticker] = market
            metadata_sources[ticker] = provenance
            metadata_versions[ticker] = [provenance]
    if set(raw_markets) != {m["ticker"] for m in listed}:
        raise ValueError("Raw metadata/checkpoint contract census mismatch")
    candle_members = {}
    for member in listed:
        if len(member["candle_record_ids"]) != 1:
            raise ValueError("Expected exactly one complete candle response per contract")
        identifier = member["candle_record_ids"][0]
        candle_members.setdefault(identifier, []).append(member)
    candle_payloads, candle_provenance = {}, {}
    for identifier, members in candle_members.items():
        route, window = members[0]["candle_route"], members[0]["window"]
        if any(m["candle_route"] != route or m["window"] != window for m in members):
            raise ValueError("Shared candle source has conflicting routes or windows")
        tickers = sorted(m["ticker"] for m in members)
        row = reader.record(identifier, "bankroll_acquisition_http")
        provenance = http_scope(row, route=route, tickers=tickers, window=window)
        data, _ = reader.read(identifier, "bankroll_acquisition_http")
        if route == "historical":
            if data.get("ticker") != tickers[0]:
                raise ValueError("Historical response ticker mismatch")
            candles = {data["ticker"]: data["candlesticks"]}
        else:
            candles = {m["market_ticker"]: m["candlesticks"] for m in data["markets"]}
            if len(candles) != len(data["markets"]):
                raise ValueError("Duplicate recent batch contract")
        if set(candles) != set(tickers):
            raise ValueError("Candle response omitted or added contracts")
        candle_payloads.update(candles)
        candle_provenance.update({ticker: provenance for ticker in tickers})
    normalized = []
    for member in sorted(listed, key=lambda m: m["ticker"]):
        ticker, window = member["ticker"], member["window"]
        metadata = market_metadata(raw_markets[ticker], event, metadata_sources[ticker], windows)
        metadata["metadata_provenance_versions"] = metadata_versions[ticker]
        expected = {
            "start_ts": math.floor(max(metadata["open_ts"], FIRST.timestamp())) + 1,
            "end_ts": math.floor(min(metadata["close_ts"], END.timestamp())),
            "period_interval": 60,
        }
        if any(window[field] != value for field, value in expected.items()):
            raise ValueError("Checkpoint window differs from actual market lifetime and UTC boundary")
        if member.get("settlement_ts") != raw_markets[ticker].get("settlement_ts"):
            raise ValueError("Checkpoint substituted a different settlement timestamp")
        if member.get("status") != raw_markets[ticker].get("status"):
            raise ValueError("Checkpoint substituted a different market status")
        if historical_cutoff is not None:
            settlement = raw_markets[ticker].get("settlement_ts")
            expected_route = (
                "historical"
                if settlement and parse_time(settlement) < parse_time(historical_cutoff)
                else "recent_batch"
            )
            if member["candle_route"] != expected_route:
                raise ValueError("Candle tier differs from primary settlement timestamp and frozen cutoff")
        candles = candle_payloads[ticker]
        if len(candles) != member["candles"]:
            raise ValueError("Checkpoint candle count mismatch")
        provenance = candle_provenance[ticker]
        metadata["candle_source_record_id"] = provenance["source_record_id"]
        normalized.append(
            {
                "metadata": metadata,
                "window": window,
                **normalize_candles(candles, member["candle_route"], window, provenance),
            }
        )
    return normalized


def verify_census_sources(reader, events):
    by_source = {}
    for event in events:
        by_source.setdefault(event["source_record_id"], []).append(event["event_ticker"])
    for identifier, tickers in sorted(by_source.items()):
        row = reader.record(identifier, "bankroll_event_metadata")
        metadata = json.loads(row["metadata"])
        url = urlparse(metadata["url"])
        query = parse_qs(url.query)
        if (
            url.scheme != "https"
            or url.netloc != "external-api.kalshi.com"
            or url.path != "/trade-api/v2/events"
            or query.get("with_nested_markets") != ["false"]
        ):
            raise ValueError("Census request was not non-nested public event metadata")
        if metadata.get("status") != 200 or iso(metadata["received_at"]) != iso(row["available_at"]):
            raise ValueError("Invalid census receipt")
        data, _ = reader.read(identifier, "bankroll_event_metadata")
        if "markets" in data or any("markets" in event for event in data["events"]):
            raise ValueError("Unexpected price-bearing nested census response")
        returned = {event["event_ticker"] for event in data["events"]}
        if len(returned) != len(data["events"]) or not set(tickers) <= returned:
            raise ValueError("Manifest membership differs from original event census")


def approved_amendments(reader, protocol_ids, original_index):
    """Consume only explicit registered repairs, preserving both source versions."""
    from research.probes.bankroll_acquisition_amendment import EVENTS, EXPERIMENT

    if len(set(protocol_ids)) != len(protocol_ids):
        raise ValueError("Repeated acquisition amendment")
    amendments = {}
    for identifier in protocol_ids:
        protocol, protocol_row = reader.read(identifier, "bankroll_acquisition_amendment_protocol")
        if (
            protocol["experiment"] != EXPERIMENT
            or protocol["parent_acquisition_protocol_record_id"] != ACQUISITION_PROTOCOL_ID
            or protocol["canonical_metadata_route"] != "historical"
            or protocol["allowed_difference"] != "open_interest_fp"
            or protocol["field_is_strategy_input"] is not False
            or protocol["strategy_scores_authorized"] is not False
            or [item["event"] for item in protocol["events"]] != EVENTS
        ):
            raise ValueError("Unregistered acquisition amendment semantics")
        for path, expected in protocol["source_hashes"].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                raise ValueError("Frozen amendment source changed")
            source_id = protocol["source_record_ids"][path]
            source = reader.record(source_id, "research_source")
            body = (reader.root / "blobs" / source["body_sha256"]).read_bytes()
            if (
                source_id >= identifier
                or source["body_sha256"] != expected
                or hashlib.sha256(body).hexdigest() != expected
            ):
                raise ValueError("Amendment source archive changed")
            reader.references[source_id] = {
                k: source[k] for k in ("id", "kind", "key", "available_at", "body_sha256", "record_sha256")
            }
        records = reader.db.execute(
            "SELECT id,key FROM records WHERE kind='bankroll_acquisition_amended_event' AND id<=? AND available_at<=? AND json_extract(metadata,'$.amendment_protocol_record_id')=? ORDER BY key",
            (reader.cutoff_id, iso(reader.cutoff_at), identifier),
        ).fetchall()
        if [row["key"] for row in records] != EVENTS:
            raise ValueError("Acquisition amendment is incomplete or duplicated")
        for item, row in zip(protocol["events"], records, strict=True):
            event = item["event"]
            if event in amendments or original_index[event] != item["original_failed_record_id"]:
                raise ValueError("Amendment replaced an undeclared original checkpoint")
            old, _ = reader.read(item["original_failed_record_id"], "bankroll_acquisition_event")
            if old["complete"] or old["failures"] != [
                {"type": "ValueError", "message": "Cross-tier conflicting market metadata"}
            ]:
                raise ValueError("Amendment original failure changed")
            checkpoint, _ = reader.read(row["id"], "bankroll_acquisition_amended_event")
            if (
                not identifier < row["id"]
                or checkpoint.get("amendment_protocol_record_id") != identifier
                or checkpoint.get("original_failed_record_id") != item["original_failed_record_id"]
                or checkpoint.get("metadata_record_ids") != [item["primary_record_id"]]
                or checkpoint.get("diagnostic_metadata_record_ids") != [item["secondary_record_id"]]
                or checkpoint.get("amended_acquisition") is not True
            ):
                raise ValueError("Amended checkpoint changed its explicitly declared lineage")
            for member in checkpoint["markets"]:
                sources = member["candle_record_ids"]
                if len(sources) != 1 or not identifier < sources[0] < row["id"]:
                    raise ValueError("Amended candle lacks pre-checkpoint registered acquisition")
                source = reader.record(sources[0], "bankroll_acquisition_http")
                if source["key"] != f"amendment:{identifier}:{member['ticker']}" or parse_time(
                    json.loads(source["metadata"])["request_started_at"]
                ) < parse_time(protocol_row["available_at"]):
                    raise ValueError("Amended candle request differs from its registered identity/time")
            amendments[event] = (row["id"], checkpoint)
    return amendments


def verify_amendment_sources(reader, protocol_ids):
    """Read raw paired metadata only after the full checkpoint completion gate."""
    from research.probes.bankroll_acquisition_amendment import metadata_pair

    for identifier in protocol_ids:
        protocol, _ = reader.read(identifier, "bankroll_acquisition_amendment_protocol")
        for item in protocol["events"]:
            event, pair = item["event"], []
            for prefix in ("primary", "secondary"):
                raw = reader.record(item[prefix + "_record_id"], "bankroll_acquisition_http")
                provenance = http_scope(raw, event=event)
                if raw["body_sha256"] != item[prefix + "_body_sha256"]:
                    raise ValueError("Amendment metadata source differs")
                expected_path = (
                    "/trade-api/v2/historical/markets" if prefix == "primary" else "/trade-api/v2/markets"
                )
                if urlparse(provenance["url"]).path != expected_path:
                    raise ValueError("Amendment canonical source route changed")
                payload, _ = reader.read(raw["id"], "bankroll_acquisition_http")
                pair.append(payload)
            _, differences = metadata_pair(*pair, event)
            if differences != item["differences"]:
                raise ValueError("Amendment original metadata differences changed")


def build_dataset(reader, windows, amendment_protocol_ids=()):
    """Call only from a frozen continuation driver after complete acquisition."""
    protocol, protocol_row = reader.read(ACQUISITION_PROTOCOL_ID, "bankroll_acquisition_protocol")
    config = protocol["config"]
    if (
        config["manifest_record_id"] != MANIFEST_ID
        or config["events"] != 1736
        or set(config["series"]) != set(SERIES)
    ):
        raise ValueError("Unexpected acquisition universe")
    manifest, manifest_row = reader.read(MANIFEST_ID, "bankroll_event_metadata_report")
    if manifest_row["body_sha256"] != config["manifest_body_sha256"] or not manifest["complete"]:
        raise ValueError("Pinned event manifest changed or is incomplete")
    events = sorted(
        (event for series in manifest["series"] for event in series["events"]),
        key=lambda e: (e["day"], e["event_ticker"]),
    )
    if len(events) != 1736 or {e["event_ticker"] for e in events} != expected_events():
        raise ValueError("Manifest does not contain all seven series and 248 dates")
    if hashlib.sha256(canonical(events).encode()).hexdigest() != config["events_sha256"]:
        raise ValueError("Pinned manifest event hash differs")
    # No price/outcome response bodies are opened until both checks finish.
    index = complete_event_index(reader.checkpoints())
    amendments = approved_amendments(reader, amendment_protocol_ids, index)
    verify_census_sources(reader, events)
    reader.read(manifest["protocol_record_id"], "bankroll_event_metadata_protocol")
    census_ids = {e["source_record_id"] for e in events}
    cutoff_ids = set(manifest["source_record_ids"]) - census_ids
    if len(cutoff_ids) != 1:
        raise ValueError("Expected the single frozen historical-cutoff receipt")
    cutoff_id = next(iter(cutoff_ids))
    cutoff, cutoff_row = reader.read(cutoff_id, "bankroll_historical_cutoff")
    cutoff_metadata = json.loads(cutoff_row["metadata"])
    if (
        cutoff != config["cutoff"]
        or urlparse(cutoff_metadata["url"]).path != "/trade-api/v2/historical/cutoff"
    ):
        raise ValueError("Historical cutoff differs from original public receipt")
    checkpoints = []
    for event, identifier in sorted(index.items()):
        if event in amendments:
            identifier, checkpoint = amendments[event]
        else:
            checkpoint, _ = reader.read(identifier, "bankroll_acquisition_event")
        if (
            checkpoint.get("event") != event
            or checkpoint.get("protocol_record_id") != ACQUISITION_PROTOCOL_ID
            or not checkpoint.get("complete")
            or checkpoint.get("failures")
        ):
            raise ValueError("Full event census exists but includes an incomplete/failed checkpoint")
        checkpoints.append((identifier, checkpoint))
    verify_amendment_sources(reader, amendment_protocol_ids)
    markets = []
    for identifier, checkpoint in checkpoints:
        for market in normalize_event(
            reader, checkpoint, windows, historical_cutoff=config["cutoff"]["market_settled_ts"]
        ):
            market["metadata"]["checkpoint_record_id"] = identifier
            markets.append(market)
    return {
        "acquisition_protocol_record_id": ACQUISITION_PROTOCOL_ID,
        "acquisition_amendment_protocol_ids": list(amendment_protocol_ids),
        "acquisition_protocol_sha256": protocol_row["body_sha256"],
        "event_manifest_record_id": MANIFEST_ID,
        "event_manifest_sha256": manifest_row["body_sha256"],
        "source_cutoff_id": reader.cutoff_id,
        "source_cutoff_at": iso(reader.cutoff_at),
        "source_window_config_sha256": hashlib.sha256(canonical(windows).encode()).hexdigest(),
        "complete_event_census": True,
        "events": len(checkpoints),
        "contracts": len(markets),
        "markets": markets,
        "source_records": sorted(reader.references.values(), key=lambda r: r["id"]),
        "source_window_status_counts": dict(Counter(m["metadata"]["source_window_status"] for m in markets)),
        "settlement_status_counts": dict(Counter(m["metadata"]["settlement_status"] for m in markets)),
        "actual_fills": 0,
        "historical_availability_verified": False,
        "new_strategy_scores_computed": 0,
    }


def trading_inputs(dataset):
    """Explicit adapter for the existing float-based E013 policy functions.

    The canonical dataset retains every exact dollar string and its provenance.
    Missing sides are omitted here, as in E013, without filling missing times.
    """
    if not dataset.get("complete_event_census") or dataset.get("events") != 1736:
        raise ValueError("Trading adapter refuses incomplete census")
    markets, events = {}, {}
    for row in dataset["markets"]:
        metadata = row["metadata"]
        event_date(metadata["event"])
        if metadata["source_window_status"] != "nws_standard_time_mapping":
            raise ValueError("A primary source needs a separately frozen source-window mapping")
        ticker = metadata["ticker"]
        if ticker in markets:
            raise ValueError("Duplicate dataset contract")
        quotes = {
            int(t): {"bid": float(q["bid"]), "ask": float(q["ask"])}
            for t, q in row["quotes"].items()
            if q["bid"] is not None and q["ask"] is not None
        }
        markets[ticker] = {"metadata": metadata, "quotes": quotes}
        events.setdefault(metadata["event"], []).append(ticker)
    if set(events) != expected_events():
        raise ValueError("Dataset market rows do not cover the complete event census")
    return markets, {e: sorted(tickers) for e, tickers in sorted(events.items())}


if __name__ == "__main__":
    raise SystemExit(
        "No normalization was run. Invoke from a separately frozen E024 driver after acquisition completes."
    )
