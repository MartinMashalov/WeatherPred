from datetime import UTC, datetime

import pytest

from weatherpred.nbh import merged_ranges, parse_cards


def synthetic_card(station="KNYC", *, tmp=True, prefix=" ", version="5.0"):
    lines = [f"{prefix}{station}   NBM V{version} NBH GUIDANCE    12/31/2025  2300 UTC"]
    fields = {"UTC": list(range(24)) + [0], "TSD": [2] * 25}
    if tmp:
        fields["TMP"] = [-98, -99, 100, None, 998] + [61] * 20
    for name, values in fields.items():
        lines.append(prefix + name + " " + "".join("   " if v is None else f"{v:3d}" for v in values))
    return ("\n".join([*lines, prefix + "SOL " + "  0" * 25, " " * 80, ""])).encode()


def test_exact_station_year_rollover_hour25_and_missing_values():
    first = synthetic_card("KBOS")
    data = parse_cards(first + synthetic_card(), {"KNYC"}, body_offset=700)["KNYC"]
    assert data["byte_offset"] == 700 + len(first)
    assert data["rows"][0]["valid_ms"] == int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()) * 1000
    assert data["rows"][24]["valid_ms"] == int(datetime(2026, 1, 2, tzinfo=UTC).timestamp()) * 1000
    assert [r["tmp_f"] for r in data["rows"][:5]] == [-98, None, 100, None, 998]
    assert data["raw_card_bytes"] == len(synthetic_card())


def test_missing_element_is_retained_and_unpadded_official_layout_accepted():
    data = parse_cards(synthetic_card(tmp=False, prefix=""), {"KNYC"})["KNYC"]
    assert all(r["tmp_f"] is None for r in data["rows"])
    assert data["missing_elements"] == ["missing_tmp_element"]
    assert parse_cards(synthetic_card(), {"KORD"}) == {}


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda b: b.rsplit(b"\n", 2)[0], "Incomplete"),
        (lambda b: b + b, "Duplicate NBH station"),
        (lambda b: b.replace(b" UTC   0", b" UTC   1"), "UTC columns disagree"),
        (lambda b: b.replace(b"100", b"999", 1), "Out-of-format"),
        (lambda b: b.replace(b"100", b" NA", 1), "Malformed NBH numeric"),
        (lambda b: b.replace(b" 61", b"61", 1), "Malformed NBH field width"),
        (lambda b: b.replace(b"V5.0", b"V6.0"), "Unregistered"),
    ],
)
def test_rejects_corrupted_or_unregistered_source(change, message):
    body = synthetic_card()
    altered = change(body)
    assert altered != body
    with pytest.raises(ValueError, match=message):
        parse_cards(altered, {"KNYC"})


def test_runtime_and_duplicate_field_cannot_silently_pass():
    with pytest.raises(ValueError, match="runtime differs"):
        parse_cards(synthetic_card(), {"KNYC"}, expected_run_ms=0)
    body = synthetic_card()
    line = next(line for line in body.splitlines(keepends=True) if line.startswith(b" TMP "))
    with pytest.raises(ValueError, match="Duplicate NBH field"):
        parse_cards(body.replace(line, line + line), {"KNYC"})


def test_merged_inclusive_ranges_clip_edges_and_never_bridge_gaps():
    assert merged_ranges([0, 10, 19, 90, 99], 100, 10) == [(0, 28), (80, 99)]
    with pytest.raises(ValueError, match="outside"):
        merged_ranges([100], 100, 10)
