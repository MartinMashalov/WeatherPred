import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from research.experiments.e027_prospective_capture import (
    CaptureStop,
    ReceiptClient,
    capture_books,
    capture_index,
    capture_metar,
    metar_details,
    refresh_markets,
    remember_version,
    select_contracts,
    slot_number,
    temperature_series,
    validate_config,
)
from weatherpred.archive import Archive
from weatherpred.timeutil import iso, utcnow


def config():
    value = json.loads(Path("config/e027_prospective_capture.json").read_text())
    value.update(minimum_free_disk_bytes=0, minimum_request_interval_seconds=0)
    return value


def protocol():
    now = utcnow()
    return {
        "config": config(),
        "registered_at": iso(now - timedelta(seconds=30)),
        "start_at": iso(now),
        "stop_at": iso(now + timedelta(hours=24)),
    }


def response(value, status=200):
    body = json.dumps(value).encode()
    return httpx.Response(status, headers={"content-length": str(len(body))}, stream=httpx.ByteStream(body))


def test_exact_station_and_finite_protocol_validation():
    cfg = config()
    validate_config(cfg)
    assert len(cfg["stations"]) == 21 and {"KORD", "KMDW"} <= set(cfg["stations"])
    cfg["stations"].remove("KORD")
    with pytest.raises(ValueError, match="exact20"):
        validate_config(cfg)


def test_report_bucket_time_is_not_confused_with_publication():
    report = {
        "icaoId": "KMDW",
        "obsTime": 1788699360,
        "receiptTime": "2026-09-06T13:00:00Z",
        "reportTime": "2026-09-06T14:00:00Z",
    }
    details = metar_details(report, "2026-09-06T13:01:00Z", "2026-09-06T13:00:30Z", True)
    assert details["provider_receipt_at"].startswith("2026-09-06T13:00:00")
    assert details["report_time_at"].startswith("2026-09-06T14:00:00")
    assert details["is_initial_or_older_backfill"]
    assert not details["report_time_is_publication_proof"]
    assert "provider_receipt_after_local_receipt" not in details["timestamp_errors"]


def test_full_revision_first_local_receipt_dedup_and_raw_change(tmp_path):
    archive = Archive(tmp_path)
    value = {"rawOb": "synthetic METAR", "receiptTime": "2026-01-01T00:01:00Z"}
    first, created = remember_version(archive, 1, "metar", value, 10, 0, "2026-01-01T00:02:00Z", {})
    repeated, is_new = remember_version(archive, 1, "metar", value, 11, 3, "2026-01-01T00:03:00Z", {})
    assert created and not is_new and repeated == first
    changed, is_new = remember_version(
        archive,
        1,
        "metar",
        {**value, "receiptTime": "2026-01-01T00:01:30Z"},
        12,
        0,
        "2026-01-01T00:04:00Z",
        {},
    )
    assert is_new and changed != first
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (first,)).fetchone()
    event = archive.json(row)
    assert event["first_local_received_at"] == "2026-01-01T00:02:00Z"
    assert event["source_record_id"] == 10
    archive.close()


def test_market_cap_uses_ticker_order_and_retains_all_exclusions():
    now = datetime(2026, 9, 6, tzinfo=UTC)
    rows = [
        {
            "ticker": ticker,
            "status": "active",
            "open_time": "2026-09-05T00:00:00Z",
            "close_time": "2026-09-07T00:00:00Z",
            "yes_ask_dollars": price,
        }
        for ticker, price in [("C", "0.01"), ("A", "0.99"), ("B", "0.50")]
    ]
    selected, states = select_contracts(rows, now, 2)
    assert [r["ticker"] for r in selected] == ["A", "B"]
    assert states[-1]["reason"] == "contract_cap" and len(states) == 3
    for row in rows:
        row["yes_ask_dollars"] = "0.01" if row["ticker"] == "A" else "0.99"
    assert [r["ticker"] for r in select_contracts(rows, now, 2)[0]] == ["A", "B"]
    rows[0]["close_time"] = "2026-09-05T23:59:00Z"
    assert select_contracts(rows, now, 3)[1][-1]["reason"] == "closed_by_timestamp"


def test_temperature_catalog_preserves_unsupported_and_capped_rows():
    cfg = config()
    cfg["maximum_temperature_series"] = 1
    catalog = [
        {"ticker": ticker, "title": title, "category": category}
        for ticker, title, category in [
            ("C", "Temperature at NYC", "Climate and Weather"),
            ("A", "Maximum Temperature", "Climate and Weather"),
            ("B", "Rainfall", "Climate and Weather"),
            ("D", "Temperature", "Other"),
        ]
    ]
    selected, excluded = temperature_series(catalog, cfg)
    assert [s["ticker"] for s in selected] == ["A"]
    assert {r["reason"] for r in excluded} == {
        "series_cap",
        "unsupported_non_temperature_metadata",
        "outside_weather_category",
    }


def test_each_successful_http_poll_has_a_new_receipt_even_for_identical_bytes(tmp_path):
    archive = Archive(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        return response({"timeseries": []})

    client = ReceiptClient(archive, 2, protocol(), transport=httpx.MockTransport(handler))
    _, first, _ = client.get("/live_data/weather/miami", None, role="index", slot=0)
    _, second, _ = client.get("/live_data/weather/miami", None, role="index", slot=1)
    assert first != second and len(calls) == 2 and client.requests == 2
    client.close()
    resumed = ReceiptClient(archive, 2, protocol(), transport=httpx.MockTransport(handler))
    assert resumed.requests == 2 and resumed.bytes_used == client.bytes_used
    resumed.close()
    archive.close()


def test_interrupted_payload_and_request_limit_are_durable(tmp_path):
    archive = Archive(tmp_path)
    archive.append("e027_request_started", "3:0:metar", utcnow(), {"reserved_bytes": 12345}, b"")
    p = protocol()
    p["config"]["maximum_requests"] = 1
    calls = []
    client = ReceiptClient(
        archive, 3, p, transport=httpx.MockTransport(lambda request: calls.append(request))
    )
    assert client.requests == 1 and client.bytes_used == 12345
    with pytest.raises(CaptureStop, match="request_limit"):
        client.get("/series", None, role="catalog", slot=1)
    assert not calls
    client.close()
    archive.close()


def test_last_allowed_request_completes_and_next_is_refused(tmp_path):
    archive = Archive(tmp_path)
    p = protocol()
    p["config"]["maximum_requests"] = 1
    client = ReceiptClient(archive, 31, p, transport=httpx.MockTransport(lambda _: response({"ok": True})))
    assert client.get("/series", None, role="catalog", slot=0)[0] == {"ok": True}
    with pytest.raises(CaptureStop, match="request_limit"):
        client.get("/series", None, role="catalog", slot=1)
    client.close()
    archive.close()


def test_oversized_payload_headers_rejected_before_body_read(tmp_path):
    archive = Archive(tmp_path)
    p = protocol()
    p["config"]["response_limit_bytes"] = 3
    client = ReceiptClient(
        archive, 32, p, transport=httpx.MockTransport(lambda _: response({"too_large": True}))
    )
    with pytest.raises(ValueError, match="Content-Length exceeds"):
        client.get("/live_data/weather/miami", None, role="index", slot=0)
    assert client.requests == 1 and client.bytes_used == 0
    row = archive.latest("e027_http", "32:0:index")
    assert not json.loads(row["metadata"])["complete"] and archive.body(row) == b""
    client.close()
    archive.close()


def test_stop_file_and_expired_deadline_prevent_requests(tmp_path):
    archive = Archive(tmp_path)
    p = protocol()
    client = ReceiptClient(archive, 4, p)
    (tmp_path / p["config"]["stop_file"]).touch()
    with pytest.raises(CaptureStop, match="stop_file"):
        client.get("/series", None, role="catalog", slot=1)
    (tmp_path / p["config"]["stop_file"]).unlink()
    p["stop_at"] = iso(utcnow() - timedelta(seconds=1))
    with pytest.raises(CaptureStop, match="registered_deadline"):
        client.get("/series", None, role="catalog", slot=1)
    client.close()
    archive.close()


def test_future_metar_timestamps_retained_as_errors_not_clamped():
    report = {
        "icaoId": "KMIA",
        "obsTime": 1893456000,
        "receiptTime": "2030-01-01T00:00:00Z",
        "reportTime": "2030-01-01T01:00:00Z",
    }
    result = metar_details(report, "2026-09-06T00:00:00Z", "2026-09-05T23:59:00Z", False)
    assert result["observation_at"].startswith("2030")
    assert {"observation_after_local_receipt", "provider_receipt_after_local_receipt"} <= set(
        result["timestamp_errors"]
    )


def test_slot_numbers_are_fixed_to_registration_window_not_resume_time():
    p = protocol()
    start = datetime.fromisoformat(p["start_at"])
    assert slot_number(p, start - timedelta(seconds=1)) == -1
    assert slot_number(p, start + timedelta(seconds=61)) == 1
    assert slot_number(p, start + timedelta(hours=24)) == 1440


def test_metar_capture_keeps_missing_ids_and_first_successful_station_batch(tmp_path):
    archive = Archive(tmp_path)
    p = protocol()
    report = {
        "icaoId": "KMDW",
        "obsTime": int(utcnow().timestamp()) - 600,
        "receiptTime": iso(utcnow() - timedelta(minutes=5)),
        "reportTime": iso(utcnow()),
        "temp": None,
        "rawOb": "synthetic",
    }
    client = ReceiptClient(archive, 5, p, transport=httpx.MockTransport(lambda _: response([report])))
    seen = set()
    first = capture_metar(client, 7, seen)
    second = capture_metar(client, 8, seen)
    assert len(first["missing_stations"]) == 20 and "KORD" in first["missing_stations"]
    assert len(first["new_version_record_ids"]) == 1 and not second["new_version_record_ids"]
    row = archive.db.execute(
        "SELECT * FROM records WHERE id=?", (first["new_version_record_ids"][0],)
    ).fetchone()
    assert archive.json(row)["first_successful_station_batch"]
    assert archive.json(row)["raw_version"]["temp"] is None
    client.close()
    archive.close()


def test_index_absent_minute_is_not_imputed_or_labeled_unavailable(tmp_path):
    archive = Archive(tmp_path)
    p = protocol()
    stamp = int(utcnow().timestamp()) * 1000 - 300000
    data = {
        "city": "miami",
        "units": "F",
        "config_version": "synthetic",
        "timeseries": [
            {"t": stamp, "status": "normal", "v": 80.0, "stations": []},
            {"t": stamp + 120000, "status": "incomplete", "stations": []},
        ],
    }
    client = ReceiptClient(archive, 6, p, transport=httpx.MockTransport(lambda _: response(data)))
    result = capture_index(client, 9)
    assert len(result["point_versions"]) == 2 and not result["missing_minutes_imputed"]
    assert result["observed_timestamp_gaps"] == [
        {"previous_ms": stamp, "next_ms": stamp + 120000, "missing_minutes": 1}
    ]
    client.close()
    archive.close()


def test_dynamic_discovery_two_sided_books_and_missing_capped_cases(tmp_path):
    archive = Archive(tmp_path)
    p = protocol()
    p["config"]["maximum_contracts"] = 1
    book_calls = []
    now = utcnow()

    def handler(request):
        if request.url.path.endswith("/series"):
            return response(
                {
                    "series": [
                        {"ticker": "TMP", "title": "Hourly temperature", "category": "Climate and Weather"},
                        {"ticker": "RAIN", "title": "Rainfall", "category": "Climate and Weather"},
                    ]
                }
            )
        if request.url.path.endswith("/markets"):
            assert request.url.params["series_ticker"] == "TMP" and request.url.params["status"] == "open"
            return response(
                {
                    "markets": [
                        {
                            "ticker": ticker,
                            "status": "active",
                            "open_time": iso(now - timedelta(days=1)),
                            "close_time": iso(now + timedelta(days=1)),
                        }
                        for ticker in ("TMP-B", "TMP-A")
                    ],
                    "cursor": "",
                }
            )
        assert request.url.path.endswith("/markets/orderbooks")
        assert request.url.params.get_list("tickers") == ["TMP-A"]
        book_calls.append(request)
        return response(
            {
                "orderbooks": [
                    {
                        "ticker": "TMP-A",
                        "orderbook_fp": {"yes_dollars": [["0.40", "5.00"]], "no_dollars": [["0.50", "6.00"]]},
                    }
                ]
                if len(book_calls) == 1
                else []
            }
        )

    client = ReceiptClient(archive, 7, p, transport=httpx.MockTransport(handler))
    panel, panel_id = refresh_markets(client, 0)
    assert len(panel["market_states"]) == 2 and panel["market_states"][1]["reason"] == "contract_cap"
    assert not panel["selection_uses_prices"]
    first = capture_books(client, 0, panel, panel_id)
    second = capture_books(client, 1, panel, panel_id)
    assert first["records"][0]["book_states"] == [{"ticker": "TMP-A", "reason": None}]
    assert second["records"][0]["missing_tickers"] == ["TMP-A"]
    client.close()
    archive.close()
