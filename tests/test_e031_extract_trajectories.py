"""Synthetic raw NBH cells only; no actual new trajectory values are read."""

from datetime import UTC, datetime

import pytest

from research.probes.e031_extract_trajectories import HOUR, cell_path, sha


def fixture(horizon=6, *, tmp=True, missing=None, ending=b"\n", utc_suffix=b" "):
    run = datetime(2026, 7, 6, 23, tzinfo=UTC)
    run_ms = int(run.timestamp() * 1000)
    utc = b" UTC " + b"".join(f"{(run.hour + lead) % 24:3d}".encode() for lead in range(1, 26)) + utc_suffix
    cells = [f"{70 + lead:3d}".encode() for lead in range(1, 26)]
    if missing:
        for lead, raw in missing.items():
            cells[lead - 1] = raw
    lines = [f" KMIA   NBM V5.0 NBH GUIDANCE   {run:%m/%d/%Y}  {run:%H}00 UTC".encode(), utc]
    if tmp:
        lines.append(b" TMP " + b"".join(cells))
    raw = ending.join(lines) + ending * 2
    prefix = b"SYNTHETIC PREFIX" + ending
    body = prefix + raw
    offset = 12345
    card = {
        "station_id": "KMIA",
        "run_ms": run_ms,
        "version": "5.0",
        "byte_offset": offset + len(prefix),
        "raw_card_bytes": len(raw),
        "raw_card_sha256": sha(raw),
    }
    case = {
        "case_id": "synthetic_case",
        "station_id": "KMIA",
        "split": "development",
        "decision_ms": run_ms + 2 * HOUR,
        "target_ms": run_ms + (horizon + 2) * HOUR,
        "horizon_hours": horizon,
        "run_ms": run_ms,
    }
    modified = run_ms + HOUR
    source = {
        "response_id": 123,
        "response_sha256": sha(body),
        "response_record_sha256": "a" * 64,
        "conditional_eligible_at_ms": modified,
        "conditional_eligible_at": datetime.fromtimestamp(modified / 1000, UTC).isoformat(),
        "object_last_modified": datetime.fromtimestamp(modified / 1000, UTC).isoformat(),
        "object_last_modified_ms": modified,
        "actual_received_at": "2026-09-06T21:00:00+00:00",
    }
    return body, {"body_offset": offset, "card": card, "case": case, "source": source}


@pytest.mark.parametrize("horizon", [1, 3, 6])
@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
@pytest.mark.parametrize("utc_suffix", [b"", b" "])
def test_exact_required_cells_offsets_midnight_rollover_and_padding(horizon, ending, utc_suffix):
    body, kwargs = fixture(horizon, ending=ending, utc_suffix=utc_suffix)
    result = cell_path(body, **kwargs)
    assert result["required_leads"] == list(range(2, horizon + 3))
    assert result["future_tmp_f"] == list(range(72, 73 + horizon)) + [None] * (6 - horizon)
    assert result["available"] is True and result["reason"] is None
    assert len(result["cells"]) == horizon + 1
    for i, cell in enumerate(result["cells"]):
        assert cell["forecast_hour"] == i + 2
        assert cell["valid_ms"] == kwargs["case"]["decision_ms"] + i * HOUR
        assert cell["run_ms"] == kwargs["case"]["decision_ms"] - 2 * HOUR
        assert cell["raw_lexeme"] == f"{72 + i:3d}"
        assert cell["station_id"] == "KMIA"
        assert cell["actual_received_at"] == kwargs["source"]["actual_received_at"]
        assert cell["conditional_eligible_at_ms"] < kwargs["case"]["decision_ms"]
        assert cell["historical_public_availability_verified"] is False
        relative = cell["cell_offset"] - kwargs["body_offset"]
        assert body[relative : relative + 3].decode() == cell["raw_lexeme"]
        relative = cell["utc_cell_offset"] - kwargs["body_offset"]
        assert body[relative : relative + 3].decode() == cell["utc_raw_lexeme"]
        assert cell["field_line_offset"] < cell["cell_offset"]


@pytest.mark.parametrize("missing,reason", [(b"   ", "blank_tmp_cell"), (b"-99", "missing_tmp_sentinel")])
def test_any_missing_required_cell_retains_path_and_forces_unavailable(missing, reason):
    body, kwargs = fixture(6, missing={4: missing})
    result = cell_path(body, **kwargs)
    assert result["available"] is False
    assert result["reason"] == "missing_required_tmp_cells"
    assert result["future_tmp_f"][2] is None
    assert result["future_tmp_f"][:2] == [72, 73]
    assert result["future_tmp_f"][3:] == [75, 76, 77, 78]
    assert result["cells"][2]["missing_reason"] == reason
    assert result["cells"][2]["raw_lexeme"] == missing.decode()


def test_missing_tmp_row_has_all_required_provenance_and_null_offsets():
    body, kwargs = fixture(3, tmp=False)
    result = cell_path(body, **kwargs)
    assert result["available"] is False and result["future_tmp_f"] == [None] * 7
    assert len(result["cells"]) == 4
    assert all(row["missing_reason"] == "absent_tmp_row" for row in result["cells"])
    assert all(
        row["raw_lexeme"] is None and row["cell_offset"] is None and row["field_line_offset"] is None
        for row in result["cells"]
    )
    assert all(
        row["utc_raw_lexeme"] is not None and row["response_sha256"] == sha(body) for row in result["cells"]
    )


def test_cells_beyond_target_are_not_decoded_or_supplied():
    body, kwargs = fixture(1, missing={4: b"BAD", 8: b"BAD", 25: b"BAD"})
    result = cell_path(body, **kwargs)
    assert result["available"] is True
    assert result["future_tmp_f"] == [72, 73, None, None, None, None, None]


@pytest.mark.parametrize(
    "mutation",
    [
        "response_hash",
        "card_hash",
        "station",
        "run",
        "late_storage",
        "header_version",
        "duplicate_station",
        "duplicate_tmp",
        "extra_tmp_column",
        "two_utc_spaces",
        "required_bad_cell",
        "required_wrong_utc",
    ],
)
def test_malformed_identity_clock_hash_or_required_field_is_fatal(mutation):
    body, kwargs = fixture(3)
    prefix = body[: kwargs["card"]["byte_offset"] - kwargs["body_offset"]]
    raw = body[len(prefix) :]
    if mutation == "response_hash":
        kwargs["source"]["response_sha256"] = "0" * 64
    elif mutation == "card_hash":
        kwargs["card"]["raw_card_sha256"] = "0" * 64
    elif mutation == "station":
        kwargs["case"]["station_id"] = "KJFK"
    elif mutation == "run":
        kwargs["case"]["run_ms"] += HOUR
    elif mutation == "late_storage":
        kwargs["source"]["object_last_modified_ms"] = kwargs["case"]["decision_ms"] + 1
    else:
        if mutation == "header_version":
            raw = raw.replace(b"V5.0", b"V6.0")
        elif mutation == "duplicate_station":
            body += raw
        elif mutation == "duplicate_tmp":
            tmp = next(line for line in raw.splitlines() if line.startswith(b" TMP "))
            raw = raw[:-1] + tmp + b"\n\n"
        elif mutation == "extra_tmp_column":
            raw = raw.replace(b" 95\n", b" 95 96\n")
        elif mutation == "two_utc_spaces":
            utc = next(line for line in raw.splitlines() if line.startswith(b" UTC "))
            raw = raw.replace(utc + b"\n", utc + b" \n")
        elif mutation == "required_bad_cell":
            raw = raw.replace(b" 72", b"BAD")
        else:
            utc = next(line for line in raw.splitlines() if line.startswith(b" UTC "))
            changed = utc[:8] + b" 22" + utc[11:]
            assert changed != utc
            raw = raw.replace(utc, changed)
        if mutation != "duplicate_station":
            body = prefix + raw
            kwargs["card"]["raw_card_bytes"] = len(raw)
            kwargs["card"]["raw_card_sha256"] = sha(raw)
        kwargs["source"]["response_sha256"] = sha(body)
    with pytest.raises(ValueError):
        cell_path(body, **kwargs)


def test_late_storage_rejected_before_malformed_raw_body_is_inspected():
    body, kwargs = fixture()
    kwargs["source"]["object_last_modified_ms"] = kwargs["case"]["decision_ms"] + 1
    with pytest.raises(ValueError, match="storage eligibility is after decision"):
        cell_path(b"not a card", **kwargs)
    assert body
