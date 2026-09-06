"""Synthetic archive fixtures only; never open newly acquired 2026 values."""

import copy
import sqlite3
from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest

from research.probes.bankroll_dataset import (
    ACQUISITION_PROTOCOL_ID,
    END,
    FIRST,
    ArchiveReader,
    complete_event_index,
    event_date,
    expected_events,
    fixed_dollars,
    http_scope,
    market_metadata,
    normalize_candles,
    normalize_event,
    trading_inputs,
    verify_census_sources,
)
from weatherpred.archive import Archive, canonical

RECEIPT = "2026-09-06T18:00:00.000000+00:00"
EVENT = "KXHIGHNY-26JAN01"
WINDOWS = {
    "window": "midnight to midnight local standard time",
    "series": {"KXHIGHNY": {"standard_utc_offset_hours": -5}},
}


def market(ticker=None):
    return {
        "ticker": ticker or EVENT + "-B30.5",
        "event_ticker": EVENT,
        "open_time": "2025-12-31T15:00:00Z",
        "close_time": "2026-01-02T04:59:00Z",
        "settlement_ts": "2026-01-02T13:01:45.201799Z",
        "status": "finalized",
        "result": "yes",
        "rules_primary": "The National Weather Service highest temperature is between 30-31° F.",
        "rules_secondary": "Synthetic test only",
        "strike_type": "between",
        "floor_strike": 30,
        "cap_strike": 31,
        "expiration_value": "31",
    }


def window():
    return {
        "start_ts": int(FIRST.timestamp()) + 1,
        "end_ts": int(datetime(2026, 1, 2, 4, 59, tzinfo=UTC).timestamp()),
        "period_interval": 60,
    }


def candle(endpoint=None, *, route="historical", bid="0.1234", ask="0.2345"):
    key = "close" if route == "historical" else "close_dollars"
    return {
        "end_period_ts": endpoint or int(FIRST.timestamp()) + 3600,
        "yes_bid": {key: bid},
        "yes_ask": {key: ask},
    }


def append_http(archive, path, params, body, kind="bankroll_acquisition_http"):
    url = "https://external-api.kalshi.com/trade-api/v2" + path + "?" + urlencode(params)
    return archive.append(
        kind,
        url,
        RECEIPT,
        {
            "url": url,
            "status": 200,
            "received_at": RECEIPT,
            "request_started_at": "2026-09-06T17:59:59.000000+00:00",
        },
        canonical(body).encode(),
    )


def event_fixture(tmp_path, *, route="historical", raw_markets=None, raw_candles=None, batch_markets=None):
    archive = Archive(tmp_path)
    raw_markets = raw_markets or [market()]
    metadata_id = append_http(
        archive,
        "/historical/markets",
        {"event_ticker": EVENT, "limit": 1000},
        {"markets": raw_markets, "cursor": ""},
    )
    tickers = [m["ticker"] for m in raw_markets]
    raw_candles = [candle(route=route)] if raw_candles is None else raw_candles
    query = window()
    if route == "historical":
        source_id = append_http(
            archive,
            f"/historical/markets/{tickers[0]}/candlesticks",
            query,
            {"ticker": tickers[0], "candlesticks": raw_candles},
        )
    else:
        source_id = append_http(
            archive,
            "/markets/candlesticks",
            dict(query, market_tickers=",".join(tickers), include_latest_before_start="false"),
            {
                "markets": batch_markets
                if batch_markets is not None
                else [{"market_ticker": t, "candlesticks": raw_candles} for t in tickers]
            },
        )
    checkpoint = {
        "protocol_record_id": ACQUISITION_PROTOCOL_ID,
        "event": EVENT,
        "day": "2026-01-01",
        "metadata_record_ids": [metadata_id],
        "complete": True,
        "failures": [],
        "markets": [
            {
                "ticker": t,
                "window": query,
                "status": "finalized",
                "settlement_ts": m["settlement_ts"],
                "candles": len(raw_candles),
                "candle_route": route,
                "candle_record_ids": [source_id],
            }
            for t, m in zip(tickers, raw_markets, strict=True)
        ],
    }
    checkpoint_id = archive.append(
        "bankroll_acquisition_event", EVENT, RECEIPT, {}, canonical(checkpoint).encode()
    )
    archive.close()
    return ArchiveReader(tmp_path, checkpoint_id, RECEIPT), checkpoint, source_id


@pytest.mark.parametrize("route", ["historical", "recent_batch"])
def test_archive_event_normalization_keeps_exact_quotes_and_real_receipts(tmp_path, route):
    reader, checkpoint, source_id = event_fixture(tmp_path, route=route)
    rows = normalize_event(reader, checkpoint, WINDOWS)
    assert len(rows) == 1
    row = rows[0]
    endpoint = int(FIRST.timestamp()) + 3600
    assert row["quotes"][endpoint] == {"bid": "0.1234", "ask": "0.2345"}
    assert row["quote_provenance"][endpoint]["source_record_id"] == source_id
    assert row["quote_provenance"][endpoint]["available_at"] == RECEIPT
    assert row["quote_provenance"][endpoint]["historical_availability_verified"] is False
    assert len(row["quote_provenance"][endpoint]["body_sha256"]) == 64
    assert row["metadata"]["reported_settlement_ts"] == "2026-01-02T13:01:45.201799Z"
    assert row["metadata"]["settled_ts"] != row["metadata"]["close_ts"]
    assert row["metadata"]["source_period_end"] == "2026-01-02T05:00:00.000000+00:00"
    assert endpoint + 3600 in row["missing_candle_endpoints"]
    reader.close()


@pytest.mark.parametrize("events", [[EVENT, EVENT], [EVENT]])
def test_incomplete_or_duplicate_census_refused_without_any_archive_access(events):
    rows = [{"key": e, "id": i + 1} for i, e in enumerate(events)]
    with pytest.raises(ValueError, match="Duplicate|Incomplete"):
        complete_event_index(rows)


def test_full_frozen_event_calendar_has_every_city_and_date():
    events = expected_events()
    assert len(events) == 1736
    assert len(complete_event_index([{"key": e, "id": i + 1} for i, e in enumerate(sorted(events))])) == 1736
    assert "KXHIGHNY-26JAN01" in events and "KXHIGHPHIL-26SEP05" in events


@pytest.mark.parametrize("event", ["KXHIGHNY-25DEC31", "KXHIGHNY-26SEP06", "KXHIGHHOU-26JAN01"])
def test_protected_or_unregistered_event_rejected(event):
    with pytest.raises(ValueError):
        event_date(event)


@pytest.mark.parametrize(
    "endpoint", [int(FIRST.timestamp()), int(END.timestamp()) + 3600, int(FIRST.timestamp()) + 3660]
)
def test_timestamp_gate_precedes_price_value_parsing(endpoint):
    bad = candle(endpoint, bid=object())
    with pytest.raises(ValueError, match="timestamp|hour"):
        normalize_candles([bad], "historical", window(), {})


def test_final_year_bucket_allowed_and_no_carried_or_missing_quotes_fabricated():
    end = int(END.timestamp())
    result = normalize_candles(
        [candle(end, bid=None)], "historical", {"start_ts": end - 7200 + 1, "end_ts": end}, {}
    )
    assert result["quotes"] == {end: {"bid": None, "ask": "0.2345"}}
    assert result["missing_candle_endpoints"] == [end - 3600]
    assert result["quote_status_counts"] == {"one_sided_or_missing": 1}


def test_duplicate_candle_rejected_even_when_values_identical():
    with pytest.raises(ValueError, match="Duplicate candle"):
        normalize_candles([candle(), candle()], "historical", window(), {})


@pytest.mark.parametrize("value", [12, 0.12, "12", "0.12", "NaN", "1.0001", "-0.0100"])
def test_exact_dollar_schema_never_guesses_cents_or_coerces_floats(value):
    with pytest.raises(ValueError, match="four-decimal"):
        fixed_dollars({"close": value}, "historical")


def test_subcent_precision_and_alias_conflicts():
    assert fixed_dollars({"close": "0.0001"}, "historical") == "0.0001"
    with pytest.raises(ValueError, match="aliases conflict"):
        fixed_dollars({"close": "0.1234", "close_dollars": "0.1235"}, "historical")


@pytest.mark.parametrize("status,settled", [("active", "2026-01-02T13:00:00Z"), ("finalized", None)])
def test_pending_or_missing_settlement_never_invents_terminal_cash(status, settled):
    raw = market()
    raw.update(status=status, settlement_ts=settled)
    result = market_metadata(raw, EVENT, {"available_at": RECEIPT, "source_record_id": 1}, WINDOWS)
    assert result["outcome"] is None and result["settled_ts"] is None
    assert result["reported_result"] == "yes"


def test_postyear_settlement_timestamp_retained_without_early_release():
    raw = market("KXHIGHNY-26SEP05-B30.5")
    raw.update(
        event_ticker="KXHIGHNY-26SEP05",
        open_time="2026-09-04T15:00:00Z",
        close_time="2026-09-06T05:00:00Z",
        settlement_ts="2026-09-06T13:01:45.201799Z",
    )
    result = market_metadata(
        raw, raw["event_ticker"], {"available_at": RECEIPT, "source_record_id": 1}, WINDOWS
    )
    assert result["settlement_after_year_end"] is True
    assert result["settled_ts"] > END.timestamp()


def test_changed_primary_source_does_not_inherit_nws_window():
    raw = market()
    raw["rules_primary"] = "The Weather Company highest temperature is between 30-31° F."
    result = market_metadata(raw, EVENT, {"available_at": RECEIPT, "source_record_id": 1}, WINDOWS)
    assert result["source_period_end"] is None
    with pytest.raises(ValueError, match="primary source"):
        trading_inputs({"complete_event_census": True, "events": 1736, "markets": [{"metadata": result}]})


def test_settlement_predicate_conflict_is_not_silently_dropped():
    raw = market()
    raw["expiration_value"] = "32"
    with pytest.raises(ValueError, match="contradicts"):
        market_metadata(raw, EVENT, {"available_at": RECEIPT, "source_record_id": 1}, WINDOWS)


def test_complete_checkpoint_must_still_match_raw_contract_census(tmp_path):
    reader, checkpoint, _ = event_fixture(tmp_path)
    checkpoint["markets"][0]["ticker"] = EVENT + "-T32"
    with pytest.raises(ValueError, match="census mismatch"):
        normalize_event(reader, checkpoint, WINDOWS)
    reader.close()


@pytest.mark.parametrize("mode", ["missing", "duplicate", "extra"])
def test_recent_batch_contract_membership_is_exact(tmp_path, mode):
    response = [{"market_ticker": market()["ticker"], "candlesticks": [candle(route="recent_batch")]}]
    response = (
        []
        if mode == "missing"
        else response * 2
        if mode == "duplicate"
        else response + [{"market_ticker": EVENT + "-T32", "candlesticks": []}]
    )
    reader, checkpoint, _ = event_fixture(tmp_path, route="recent_batch", batch_markets=response)
    with pytest.raises(ValueError, match="Duplicate|omitted or added"):
        normalize_event(reader, checkpoint, WINDOWS)
    reader.close()


def test_two_recent_contracts_keep_separate_rows_with_shared_source(tmp_path):
    raw = [market(), market(EVENT + "-T32")]
    reader, checkpoint, source = event_fixture(tmp_path, route="recent_batch", raw_markets=raw)
    rows = normalize_event(reader, checkpoint, WINDOWS)
    assert len(rows) == 2
    assert {r["metadata"]["candle_source_record_id"] for r in rows} == {source}
    reader.close()


def test_archive_blob_tampering_and_cutoff_are_rejected(tmp_path):
    reader, checkpoint, source = event_fixture(tmp_path)
    row = reader.record(source)
    (tmp_path / "blobs" / row["body_sha256"]).write_bytes(b"{}")
    with pytest.raises(ValueError, match="body hash"):
        normalize_event(reader, checkpoint, WINDOWS)
    with pytest.raises(ValueError, match="cutoff"):
        reader.record(reader.cutoff_id + 1)
    reader.close()


def test_http_query_boundary_gate_runs_before_body_access(tmp_path):
    reader, _, source = event_fixture(tmp_path)
    row = reader.record(source)
    bad = copy.deepcopy(window())
    bad["start_ts"] = int(FIRST.timestamp())
    with pytest.raises(ValueError, match="requested candle window"):
        http_scope(row, route="historical", tickers=[market()["ticker"]], window=bad)
    reader.close()


def test_census_receipts_are_pinned_and_membership_checked(tmp_path):
    archive = Archive(tmp_path)
    source = append_http(
        archive,
        "/events",
        {"series_ticker": "KXHIGHNY", "with_nested_markets": "false"},
        {"events": [{"event_ticker": EVENT}]},
        "bankroll_event_metadata",
    )
    archive.close()
    reader = ArchiveReader(tmp_path, source, RECEIPT)
    verify_census_sources(reader, [{"event_ticker": EVENT, "source_record_id": source}])
    assert reader.references[source]["available_at"] == RECEIPT
    with pytest.raises(ValueError, match="membership"):
        verify_census_sources(reader, [{"event_ticker": "KXHIGHNY-26JAN02", "source_record_id": source}])
    reader.close()


def test_record_metadata_tampering_fails_record_hash(tmp_path):
    reader, _, source = event_fixture(tmp_path)
    # Corrupt only a disposable synthetic archive, never the shared archive.
    with sqlite3.connect(tmp_path / "archive.sqlite") as writer:
        writer.execute("DROP TRIGGER records_no_update")
        writer.execute("UPDATE records SET metadata='{}' WHERE id=?", (source,))
    with pytest.raises(ValueError, match="record hash"):
        reader.record(source)
    reader.close()


def test_receipt_time_cutoff_is_separate_from_record_id_cutoff(tmp_path):
    reader, checkpoint, source = event_fixture(tmp_path)
    reader.close()
    earlier = ArchiveReader(tmp_path, source + 1, "2026-09-06T17:59:59+00:00")
    with pytest.raises(ValueError, match="receipt exceeds"):
        normalize_event(earlier, checkpoint, WINDOWS)
    earlier.close()


def test_duplicate_raw_contract_is_not_hidden_by_dictionary_conversion(tmp_path):
    reader, checkpoint, _ = event_fixture(tmp_path, raw_markets=[market(), market()])
    checkpoint["markets"] = checkpoint["markets"][:1]
    with pytest.raises(ValueError, match="Duplicate contract within"):
        normalize_event(reader, checkpoint, WINDOWS)
    reader.close()


def test_failed_checkpoint_is_refused_before_dereferencing_sources(tmp_path):
    reader, checkpoint, _ = event_fixture(tmp_path)
    checkpoint.update(complete=False, metadata_record_ids=[999999999])
    with pytest.raises(ValueError, match="Incomplete or foreign"):
        normalize_event(reader, checkpoint, WINDOWS)
    reader.close()


def test_actual_settlement_time_selects_tier_not_event_day_or_market_close(tmp_path):
    reader, checkpoint, _ = event_fixture(tmp_path, route="recent_batch")
    with pytest.raises(ValueError, match="tier differs"):
        normalize_event(reader, checkpoint, WINDOWS, historical_cutoff="2026-07-08T00:00:00Z")
    # A cutoff preceding this settlement requires the recent tier even though
    # event day and market close are earlier than the cutoff.
    assert len(normalize_event(reader, checkpoint, WINDOWS, historical_cutoff="2026-01-02T06:00:00Z")) == 1
    reader.close()
