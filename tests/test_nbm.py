import httpx
import pytest

from weatherpred.archive import Archive
from weatherpred.http import PublicClient
from weatherpred.nbm import station_cards


def card(station="KNYC"):
    hours = list(range(5, 72, 3))
    fields = {
        "UTC": [(hour + 1) % 24 for hour in hours],
        "FHR": hours,
        "TMP": [-3, 100, 999] + [50] * 20,
        "TSD": [2] * 23,
        "TXN": [None] * 6 + [55] + [None] * 16,
        "XND": [None] * 6 + [3] + [None] * 16,
    }
    lines = [f" {station}    NBM V4.2 NBS GUIDANCE    1/01/2025  0100 UTC"]
    for name, values in fields.items():
        lines.append(" " + name + " " + "".join("   " if v is None else f"{v:3}" for v in values))
    return ("\n".join([*lines, " SOL" + " " * 70, " " * 74, ""])).encode()


def test_nbm_fixed_width_negative_adjacent_three_digits_and_missing():
    parsed = station_cards(card("KMIA") + card(), {"KNYC"})
    assert set(parsed) == {"KNYC"}
    data = parsed["KNYC"]
    assert data["relative_byte_offset"] == len(card("KMIA"))
    assert data["runtime"] == "2025-01-01T01:00:00+00:00"
    assert [row["tmp"] for row in data["rows"][:3]] == [-3, 100, None]
    assert data["rows"][6]["txn"] == 55
    assert data["rows"][6]["valid_at"] == "2025-01-02T00:00:00+00:00"
    assert len(data["rows"]) == 23


def test_nbm_rejects_truncated_duplicate_or_inconsistent_cards():
    with pytest.raises(ValueError, match="Incomplete"):
        station_cards(card().split(b" SOL")[0], {"KNYC"})
    with pytest.raises(ValueError, match="Duplicate"):
        station_cards(card() + card(), {"KNYC"})
    inconsistent = card().replace(b" UTC   6", b" UTC   7")
    assert inconsistent != card()
    with pytest.raises(ValueError, match="disagree"):
        station_cards(inconsistent, {"KNYC"})
    assert station_cards(card(), {"KMDW"}) == {}


def test_conditional_byte_range_is_archived_and_exactly_verified(tmp_path):
    archive = Archive(tmp_path)
    client = PublicClient(archive, interval=0)
    client.client.close()

    def handler(request):
        assert request.headers["range"] == "bytes=5-7"
        assert request.headers["if-match"] == '"fixed"'
        return httpx.Response(
            206, content=b"abc", headers={"content-range": "bytes 5-7/100", "etag": '"fixed"'}
        )

    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    response, rec = client.get("https://example.test/source", byte_range=(5, 7), expected_etag='"fixed"')
    assert response.content == b"abc"
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (rec,)).fetchone()
    assert archive.body(row) == b"abc"
    assert '"Range":"bytes=5-7"' in row["metadata"]
    client.client.close()
    client.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"abc", headers={"etag": '"fixed"'})
        )
    )
    with pytest.raises(ValueError, match="does not match"):
        client.get("https://example.test/source", byte_range=(5, 7), expected_etag='"fixed"')
    client.close()
    archive.close()
