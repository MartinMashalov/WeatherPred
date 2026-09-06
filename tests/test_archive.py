import sqlite3

import pytest

from weatherpred.archive import Archive
from weatherpred.timeutil import parse_time


def test_asof_uses_receipt_not_observation_or_model_valid_time(tmp_path):
    store = Archive(tmp_path)
    store.append(
        "observation", "KNYC:10:00", "2026-09-06T10:03:00Z", {"observed_at": "2026-09-06T10:00:00Z"}, b"20 C"
    )
    assert store.latest("observation", "KNYC:10:00", "2026-09-06T10:02:59Z") is None
    assert store.body(store.latest("observation", "KNYC:10:00", "2026-09-06T10:03:00Z")) == b"20 C"
    store.close()


def test_correction_is_unavailable_before_received(tmp_path):
    store = Archive(tmp_path)
    store.append("obs", "station:valid", "2026-09-06T10:03:00Z", {}, b"20")
    store.append("obs", "station:valid", "2026-09-06T11:00:00Z", {"correction": True}, b"21")
    assert store.body(store.latest("obs", "station:valid", "2026-09-06T10:59:59Z")) == b"20"
    assert store.body(store.latest("obs", "station:valid", "2026-09-06T11:00:00Z")) == b"21"
    assert store.verify()["records_verified"] == 2
    store.close()


def test_asof_normalizes_offsets_and_rejects_naive(tmp_path):
    store = Archive(tmp_path)
    store.append("x", "y", "2026-09-06T12:03:00+02:00", {}, b"x")
    assert store.latest("x", "y", "2026-09-06T10:02:59Z") is None
    with pytest.raises(ValueError, match="Naive"):
        parse_time("2026-09-06T10:02:59")
    store.close()


def test_append_only_and_blob_tamper_detection(tmp_path):
    store = Archive(tmp_path)
    store.append("x", "y", "2026-09-06T10:03:00Z", {}, b"original")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("UPDATE records SET available_at='2000'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.db.execute("DELETE FROM records")
    row = store.latest("x", "y")
    (tmp_path / "blobs" / row["body_sha256"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity"):
        store.verify()
    store.close()
