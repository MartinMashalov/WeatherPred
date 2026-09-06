import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from research.experiments.e025_acquire_nbh import (
    AcquisitionPause,
    BoundedClient,
    acquire_object,
    authorize_full,
    make_manifest,
    sha,
    validate_headers,
    verify_source_hashes,
)
from weatherpred.archive import Archive
from weatherpred.timeutil import utcnow


def config():
    value = json.loads(Path("config/e025_physical_baseline.json").read_text())
    value.update(request_interval_seconds=0, minimum_free_disk_bytes=0)
    return value


class CountedStream(httpx.SyncByteStream):
    def __init__(self, data):
        self.data, self.iterations = data, 0

    def __iter__(self):
        self.iterations += 1
        yield self.data


def fixed_object():
    return {"etag": '"pinned"', "size": 100, "last_modified_ms": 0}


def object_headers(*, length="3", content_range="bytes 5-7/100"):
    return {
        "etag": '"pinned"',
        "last-modified": "Thu, 01 Jan 1970 00:00:00 GMT",
        "content-length": length,
        "content-range": content_range,
    }


def test_real_frozen_manifest_expands_without_values_or_forecast_calls():
    manifest = make_manifest(config())
    assert len(manifest["objects"]) == 498
    assert len(manifest["bindings"]) == 9870
    assert {c["forecast_hour"] for c in manifest["bindings"]} == {3, 5, 8}
    assert all("point_f" not in c and "observed_f" not in c for c in manifest["bindings"])


def test_runtime_source_hash_check_detects_concurrent_change(tmp_path):
    source = tmp_path / "source.py"
    source.write_bytes(b"original")
    protocol = {"source_sha256": {str(source): sha(b"original")}}
    verify_source_hashes(protocol)
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="Registered source/input changed"):
        verify_source_hashes(protocol)


def test_excess_stream_bytes_remain_charged_and_archived(tmp_path):
    archive = Archive(tmp_path)
    client = BoundedClient(
        archive,
        101,
        config(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(206, headers=object_headers(), stream=CountedStream(b"overflow"))
        ),
    )
    with pytest.raises(ValueError, match="exceeds reserved payload"):
        client.get("fixed", "range", obj=fixed_object(), byte_range=(5, 7))
    assert client.used == client.restore_budget() == len(b"overflow")
    row = archive.latest("e025_response", "101:fixed:range")
    assert archive.body(row) == b"overflow"
    client.close()
    archive.close()


def test_conditional_range_receipt_hash_and_cached_resume(tmp_path):
    archive = Archive(tmp_path)
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["range"] == "bytes=5-7"
        assert request.headers["if-match"] == '"pinned"'
        return httpx.Response(206, headers=object_headers(), stream=CountedStream(b"abc"))

    client = BoundedClient(archive, 99, config(), transport=httpx.MockTransport(handler))
    body, meta, record_id = client.get("fixed", "range:5:7", obj=fixed_object(), byte_range=(5, 7))
    assert body == b"abc" and meta["raw_sha256"] == sha(b"abc")
    assert meta["actual_received_at"] != "1970-01-01T00:00:00+00:00"
    assert client.used == 3
    client.close()
    resumed = BoundedClient(archive, 99, config(), transport=httpx.MockTransport(handler))
    assert resumed.used == 3
    assert resumed.get("fixed", "range:5:7", obj=fixed_object(), byte_range=(5, 7))[2] == record_id
    assert len(requests) == 1
    resumed.close()
    archive.close()


def test_ignored_range_never_reads_full_response_body(tmp_path):
    archive = Archive(tmp_path)
    stream = CountedStream(b"x" * 100)
    client = BoundedClient(
        archive,
        2,
        config(),
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers=object_headers(length="100"), stream=stream)
        ),
    )
    with pytest.raises(ValueError, match="Unexpected HTTP status"):
        client.get("fixed", "range", obj=fixed_object(), byte_range=(5, 7))
    assert stream.iterations == 0
    assert client.used == 0
    records = archive.db.execute("SELECT * FROM records WHERE kind='e025_response'").fetchall()
    assert len(records) == 1 and not json.loads(records[0]["metadata"])["complete"]
    client.close()
    archive.close()


def test_interrupted_intent_remains_charged_and_budget_refuses_network(tmp_path):
    archive = Archive(tmp_path)
    archive.append("e025_request_started", "3:interrupted", utcnow(), {"reserved_payload_bytes": 65536}, b"")
    cfg = config()
    cfg["maximum_total_payload_bytes"] = 65536
    called = []
    client = BoundedClient(
        archive, 3, cfg, transport=httpx.MockTransport(lambda request: called.append(request))
    )
    assert client.used == 65536
    with pytest.raises(AcquisitionPause, match="total_payload_budget"):
        client.get("source", "listing", params={"list-type": "2"})
    assert not called
    client.close()
    archive.close()


def test_bounded_chunked_listing_and_stop_file(tmp_path):
    archive = Archive(tmp_path)
    client = BoundedClient(
        archive,
        31,
        config(),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=CountedStream(b"<metadata/>"))),
    )
    assert client.get("object", "listing", params={"list-type": "2"})[0] == b"<metadata/>"
    assert client.used == len(b"<metadata/>")
    (tmp_path / "STOP_E025_NBH").touch()
    with pytest.raises(AcquisitionPause, match="stop_file"):
        client.get("another", "listing", params={"list-type": "2"})
    client.close()
    archive.close()


def test_attempt_limit_survives_interrupted_invocations(tmp_path):
    archive = Archive(tmp_path)
    for _ in range(3):
        archive.append(
            "e025_request_started", "32:fixed:listing", utcnow(), {"reserved_payload_bytes": 65536}, b""
        )
    called = []
    client = BoundedClient(
        archive, 32, config(), transport=httpx.MockTransport(lambda request: called.append(request))
    )
    with pytest.raises(ValueError, match="attempt limit already exhausted"):
        client.get("fixed", "listing", params={"list-type": "2"})
    assert not called and client.used == 3 * 65536
    client.close()
    archive.close()


@pytest.mark.parametrize(
    "mutation",
    [
        {"etag": '"changed"'},
        {"last-modified": "Thu, 01 Jan 1970 01:00:00 GMT"},
        {"content-range": "bytes 5-7/101"},
        {"content-length": "4"},
        {"content-encoding": "gzip"},
    ],
)
def test_header_changes_rejected_before_read(mutation):
    with pytest.raises(ValueError):
        validate_headers(206, {**object_headers(), **mutation}, fixed_object(), (5, 7), 3)


def test_full_seed_and_recovery_authorizations_are_durable(tmp_path):
    archive = Archive(tmp_path)
    cfg = config()
    cfg["full_recovery_cap"] = 1
    client = BoundedClient(archive, 4, cfg)
    assert authorize_full(client, "first", seed=True) == "seed"
    assert authorize_full(client, "second", seed=True) == "recovery"
    assert authorize_full(client, "second", seed=False) == "recovery"
    with pytest.raises(AcquisitionPause, match="full_object_recovery_cap"):
        authorize_full(client, "third", seed=False)
    client.close()
    archive.close()


def test_late_original_object_refused_using_only_listing_metadata(tmp_path):
    archive = Archive(tmp_path)
    cfg = config()
    key = "fixed"
    xml = (
        f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<IsTruncated>false</IsTruncated><Contents><Key>{key}</Key>"
        '<LastModified>2026-07-06T00:00:01Z</LastModified><ETag>"x"</ETag>'
        "<Size>500</Size></Contents></ListBucketResult>"
    ).encode()
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-length": str(len(xml))}, stream=CountedStream(xml))

    client = BoundedClient(archive, 5, cfg, transport=httpx.MockTransport(handler))
    decision = int(datetime(2026, 7, 6, tzinfo=UTC).timestamp()) * 1000
    entry = {"key": key, "run_ms": decision - 7200000, "cases": [{"decision_ms": decision}]}
    with pytest.raises(ValueError, match="stored after"):
        acquire_object(client, entry, {})
    assert len(seen) == 1 and "list-type=2" in str(seen[0].url)
    client.close()
    archive.close()


def test_synthetic_whole_object_then_merged_ranges_preserve_exact_case_status(tmp_path):
    archive = Archive(tmp_path)
    cfg = config()
    cfg["stations"] = ["KNYC", "KORD"]
    run_ms = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()) * 1000
    cards = []
    for station in cfg["stations"]:
        lines = [f" {station} NBM V5.0 NBH GUIDANCE 1/1/2026 0000 UTC"]
        for field, values in {
            "UTC": list(range(1, 24)) + [0, 1],
            "TMP": [41, 42, 43 if station == "KNYC" else -99] + [44] * 22,
        }.items():
            lines.append(" " + field + " " + "".join(f"{value:3d}" for value in values))
        cards.append(("\n".join(lines) + "\n" + " " * 80 + "\n").encode())
    payload = b"".join(cards)
    xml = (
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<IsTruncated>false</IsTruncated><Contents><Key>synthetic</Key>"
        '<LastModified>2026-01-01T00:20:00Z</LastModified><ETag>"fixed"</ETag>'
        f"<Size>{len(payload)}</Size></Contents></ListBucketResult>"
    ).encode()
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.params:
            return httpx.Response(200, headers={"content-length": str(len(xml))}, stream=CountedStream(xml))
        headers = {"etag": '"fixed"', "last-modified": "Thu, 01 Jan 2026 00:20:00 GMT"}
        data, status = payload, 200
        if "range" in request.headers:
            low, high = map(int, request.headers["range"].removeprefix("bytes=").split("-"))
            data, status = payload[low : high + 1], 206
            headers["content-range"] = f"bytes {low}-{high}/{len(payload)}"
        headers["content-length"] = str(len(data))
        return httpx.Response(status, headers=headers, stream=CountedStream(data))

    client = BoundedClient(archive, 6, cfg, transport=httpx.MockTransport(handler))
    entry = {
        "key": "synthetic",
        "run_ms": run_ms,
        "cases": [
            {
                "case_id": station,
                "station_id": station,
                "target_ms": run_ms + 3 * 3600000,
                "decision_ms": run_ms + 2 * 3600000,
                "forecast_hour": 3,
            }
            for station in cfg["stations"]
        ],
    }
    first = acquire_object(client, entry, {})
    assert [(c["available"], c["point_f"]) for c in first["cases"]] == [(True, 43), (False, None)]
    assert first["full_object_reason"] == "seed"
    assert first["cases"][1]["reason"] == "missing_tmp"
    assert not first["historical_public_availability_verified"]
    offsets = {station: card["byte_offset"] for station, card in first["cards"].items()}
    second = acquire_object(client, entry, offsets)
    assert second["full_object_reason"] is None
    assert first["cases"] == second["cases"]
    assert len(seen) == 3  # one listing, one full body, one merged range
    assert client.restore_budget() == client.used == len(xml) + 2 * len(payload)
    client.close()
    archive.close()
