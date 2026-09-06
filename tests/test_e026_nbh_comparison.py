import copy
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from research.experiments.e026_nbh_comparison import (
    case_binding,
    check_original_card,
    compare_panel,
    digest,
    merge_pins,
    original_response,
    original_sources,
    read_record,
    shared_intervals,
    validate_acquisition,
)
from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import utcnow


def card_fixture(padded=False, missing=False, run=None):
    run = run or datetime(2026, 12, 31, 23, tzinfo=UTC)
    utc = " UTC " + "".join(f"{(run.hour + i + 1) % 24:3d}" for i in range(25))
    utc += " " if padded else ""
    temp = " TMP " + "".join(f"{(-99 if missing and i == 0 else 70):3d}" for i in range(25))
    raw = (
        f" KMIA   NBM V5.0 NBH GUIDANCE   {run:%m/%d/%Y}  {run:%H}00 UTC\n" + utc + "\n" + temp + "\n\n"
    ).encode()
    card = {"byte_offset": 10, "raw_card_bytes": len(raw), "raw_card_sha256": digest(raw)}
    case = {
        "station_id": "KMIA",
        "forecast_hour": 1,
        "target_ms": int(run.timestamp() * 1000) + 3600000,
        "card_sha256": digest(raw),
        "point_f": None if missing else 70,
        "available": not missing,
    }
    return raw, card, case, int(run.timestamp() * 1000)


@pytest.mark.parametrize("padded", [False, True])
@pytest.mark.parametrize("missing", [False, True])
def test_independent_card_checks_exact_cells_and_year_rollover(padded, missing):
    raw, card, case, run = card_fixture(padded, missing)
    assert check_original_card(raw, card, case, run, 10)
    assert not check_original_card(raw, card, case, run, 11)
    assert check_original_card(b"0123456789" + raw, card, case, run)
    with pytest.raises(ValueError, match="differs from original"):
        check_original_card(raw, card, {**case, "point_f": 71}, run, 10)


def test_independent_card_rejects_duplicate_field_and_extra_column():
    raw, card, case, run = card_fixture(True)
    bad = raw.replace(b"\n TMP", b" 00\n TMP")
    h = digest(bad)
    with pytest.raises(ValueError, match="field width"):
        check_original_card(
            bad,
            {**card, "raw_card_bytes": len(bad), "raw_card_sha256": h},
            {**case, "card_sha256": h},
            run,
            10,
        )
    line = next(line for line in raw.splitlines() if line.startswith(b" TMP"))
    bad = raw[:-1] + line + b"\n\n"
    h = digest(bad)
    with pytest.raises(ValueError, match="Duplicate"):
        check_original_card(
            bad,
            {**card, "raw_card_bytes": len(bad), "raw_card_sha256": h},
            {**case, "card_sha256": h},
            run,
            10,
        )


def panel_fixture():
    config = json.loads(Path("config/e026_nbh_comparison.json").read_bytes())
    parent = json.loads(Path(config["parent_config_path"]).read_bytes())
    cases, physical, observations = [], [], {}
    for split, days in (
        ("calibration", [date(2026, 7, 6) + timedelta(days=i) for i in range(8)]),
        ("development", [date(2026, 7, 20), date(2026, 7, 21)]),
    ):
        for day in days:
            for hour in (6, 12):
                at = int(datetime(day.year, day.month, day.day, hour, tzinfo=UTC).timestamp() * 1000)
                for station in ("KMIA", "KNYC"):
                    observations[station, at] = {
                        "temperature_f": 70 + hour / 10,
                        "status": "settled",
                        "source_record_ids": [123],
                    }
                    for horizon in (1, 3, 6):
                        identity = f"{station}:{at}:{horizon}"
                        case = {
                            "case_id": identity,
                            "station_id": station,
                            "target_ms": at,
                            "decision_ms": at - horizon * 3600000,
                            "horizon_hours": horizon,
                            "eligible": True,
                            "split": split,
                            "target_source_record_ids": [123],
                        }
                        cases.append(case)
                        physical.append(
                            {**case_binding(case), "available": True, "point_f": 70.0, "reason": None}
                        )
    predictions = {
        model: [
            {"case_id": c["case_id"], "point_f": 69.0 + i / 10, "quantiles_f": [69.0 + i / 10] * 13}
            for c in cases
        ]
        for i, model in enumerate(config["model_ids"])
    }
    return cases, physical, predictions, observations, config, parent


def test_all_nine_candidates_share_subset_and_do_not_reuse_old_full_scores():
    fixture = panel_fixture()
    full = compare_panel(*fixture)
    assert full["full_panel_complete"] and len(full["candidates"]) == 9
    assert full["independent_score_max_error"] < 1e-10
    assert full["bootstrap"]["status"] == "unsupported_missing_calendar_days"
    # Remove one development forecast; every candidate must lose that exact case.
    cases, physical, *_ = fixture
    omitted = next(c for c in cases if c["split"] == "development")["case_id"]
    next(p for p in physical if p["case_id"] == omitted).update(
        available=False, point_f=None, reason="missing_tmp"
    )
    partial = compare_panel(*fixture)
    assert partial["classification"] == "common_available_subset_only"
    assert partial["missing"] == [{"case_id": omitted, "reason": "missing_tmp"}]
    assert {c["evaluation"]["development_cases"] for c in partial["candidates"]} == {23}
    assert {c["evaluation"]["development_cases"] for c in full["candidates"]} == {24}


def test_development_labels_cannot_change_calibration_and_missing_support_stops():
    fixture = panel_fixture()
    original = compare_panel(*fixture)
    altered = copy.deepcopy(fixture)
    cases, physical, _, observations, *_ = altered
    for case in cases:
        if case["split"] == "development":
            observations[case["station_id"], case["target_ms"]]["temperature_f"] = 99
    future_changed = compare_panel(*altered)
    assert [c["evaluation"]["calibration_offsets_f"] for c in original["candidates"]] == [
        c["evaluation"]["calibration_offsets_f"] for c in future_changed["candidates"]
    ]
    cal_ids = {c["case_id"] for c in cases if c["split"] == "calibration"}
    for row in physical:
        if row["case_id"] in cal_ids:
            row.update(available=False, point_f=None, reason="absent_object")
    assert compare_panel(*altered)["status"] == "insufficient_common_calibration"


def test_family_bootstrap_uses_all_eight_comparisons_and_shared_days():
    settings = json.loads(Path("config/e026_nbh_comparison.json").read_bytes())["bootstrap"]
    days = [(date(2026, 7, 20) + timedelta(days=i)).isoformat() for i in range(28)]
    x = np.arange(28)[:, None] * np.arange(8)[None, :] / 100
    result = shared_intervals(x, days, settings)
    assert result == shared_intervals(x, days, settings)
    assert result["active"] == [False, True, True, True, True, True, True, True]
    assert result["intervals"][0] is None
    assert len(result["intervals"]) == 8
    with pytest.raises(ValueError, match="matrix"):
        shared_intervals(x[:, :7], days, settings)


def test_failed_sources_cannot_smuggle_forecasts_and_source_hashes_are_checked(tmp_path):
    expected = {"case_id": "c", "station_id": "KMIA"}
    value = {
        "key": "k",
        "status": "failed",
        "cases": [{**expected, "available": False, "point_f": None, "reason": "missing"}],
    }
    entry = {"key": "k", "cases": [expected]}
    settings = json.loads(Path("config/e026_nbh_comparison.json").read_bytes())
    record = {
        "kind": "e025v2_object",
        "key": f"{settings['acquisition_registration_id']}:k",
        "id": settings["acquisition_registration_id"] + 1,
    }
    assert original_sources(None, value, entry, settings, record)[1] == []
    value["cases"][0]["point_f"] = 1
    with pytest.raises(ValueError, match="Failed object"):
        original_sources(None, value, entry, settings, record)
    archive = Archive(tmp_path)
    rec = archive.append("fixture", "x", utcnow(), {}, b'{"a":1}')
    row, decoded = read_record(archive, rec, "fixture")
    assert decoded == {"a": 1}
    (archive.root / "blobs" / row["body_sha256"]).write_bytes(b'{"a":2}')
    with pytest.raises(ValueError, match="integrity"):
        read_record(archive, rec)
    archive.close()


class NoLabels(dict):
    def get(self, *args, **kwargs):
        raise AssertionError("A label was consulted before validating the original case binding")


@pytest.mark.parametrize(
    "field", ["station_id", "target_ms", "decision_ms", "split", "run_ms", "forecast_hour", "key"]
)
def test_wrong_physical_binding_is_rejected_before_any_label_lookup(field):
    fixture = panel_fixture()
    physical = fixture[1]
    original = physical[0][field]
    physical[0][field] = original + 1 if isinstance(original, int) else "wrong-binding"
    with pytest.raises(ValueError, match="binding differs from original"):
        compare_panel(fixture[0], physical, fixture[2], NoLabels(), fixture[4], fixture[5])


def test_acquisition_object_bindings_and_fixed_bootstrap_settings_cannot_drift():
    cases, physical, _, _, settings, _ = panel_fixture()
    acquisition_config = json.loads(Path("config/e025_physical_baseline_v2.json").read_bytes())
    grouped = {}
    bindings = [case_binding(case) for case in sorted(cases, key=lambda row: row["case_id"])]
    for binding in bindings:
        grouped.setdefault(binding["key"], {"key": binding["key"], "run_ms": binding["run_ms"], "cases": []})[
            "cases"
        ].append(binding)
    settings.update(expected_cases=len(cases), expected_objects=len(grouped))
    acquisition_config.update(expected_cases=len(cases), expected_objects=len(grouped))
    acquisition = {
        "config": acquisition_config,
        "manifest": {"bindings": bindings, "objects": [grouped[key] for key in sorted(grouped)]},
    }
    validate_acquisition(acquisition, settings, cases)
    changed = copy.deepcopy(acquisition)
    changed["manifest"]["objects"][0]["cases"][0]["target_ms"] += 3600000
    with pytest.raises(ValueError, match="original E022"):
        validate_acquisition(changed, settings, cases)
    for key, bad in (("block_days", 1), ("resamples", 100), ("comparison_count", 7), ("seed", 1)):
        controls = {**settings["bootstrap"], key: bad}
        with pytest.raises(ValueError, match="fixed eight-comparison"):
            shared_intervals([], [], controls)
    assert merge_pins({"same": "abc"}, {"same": "abc", "new": "xyz"}) == {"same": "abc", "new": "xyz"}
    with pytest.raises(ValueError, match="Conflicting"):
        merge_pins({"same": "abc"}, {"same": "altered"})
    assert len(physical) == len(cases)


def response_fixture(archive, owner, kind, entry, role, body, headers, *, obj=None):
    settings = json.loads(Path("config/e026_nbh_comparison.json").read_bytes())
    listing = role == "listing"
    url = settings["bucket"] + ("" if listing else entry["key"])
    params = {"list-type": "2", "prefix": entry["key"], "max-keys": 2} if listing else None
    request_headers = {} if listing else {"If-Match": obj["etag"]}
    cap = 65536 if listing else obj["size"]
    signature = digest(
        canonical({"url": url, "params": params, "headers": request_headers, "cap": cap}).encode()
    )
    identity = f"{owner}:{entry['key']}:{role}"
    base = datetime(2026, 9, 6, 12, tzinfo=UTC)
    count = archive.db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    started = base + timedelta(seconds=count * 2)
    received = started + timedelta(seconds=1)
    request_meta = {
        "request_signature": signature,
        "url": url,
        "params": params,
        "request_headers": request_headers,
        "request_started_at": started.isoformat(),
    }
    request_id = archive.append(
        kind.replace("response", "request_started"), identity, started, request_meta, b""
    )
    metadata = {
        **request_meta,
        "request_started_record_id": request_id,
        "actual_received_at": received.isoformat(),
        "complete": True,
        "error": None,
        "status": 200,
        "headers": headers,
        "payload_bytes": len(body),
        "raw_sha256": digest(body),
    }
    identifier = archive.append(kind, identity, received, metadata, body)
    row = dict(archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone())
    return row, metadata


def acquired_fixture(archive, reuse=False):
    settings = json.loads(Path("config/e026_nbh_comparison.json").read_bytes())
    previous = archive.append(
        "e025_protocol", "synthetic-v1", datetime(2026, 9, 6, 10, tzinfo=UTC), {}, b"{}"
    )
    settings["prior_acquisition_registration_id"] = previous
    if not reuse:
        current = archive.append(
            "e025v2_protocol", "synthetic-v2", datetime(2026, 9, 6, 11, tzinfo=UTC), {}, b"{}"
        )
    run = datetime(2026, 7, 20, 3, tzinfo=UTC)
    raw, card, selected, run_ms = card_fixture(padded=True, run=run)
    original = {
        "case_id": "synthetic-original",
        "station_id": "KMIA",
        "split": "development",
        "target_ms": run_ms + 3 * 3600000,
        "decision_ms": run_ms + 2 * 3600000,
        "horizon_hours": 1,
    }
    binding = case_binding(original)
    entry = {"key": binding["key"], "run_ms": run_ms, "cases": [binding]}
    modified = run + timedelta(minutes=30)
    etag = '"synthetic-pinned-etag"'
    obj = {
        "key": binding["key"],
        "run_ms": run_ms,
        "etag": etag,
        "size": len(raw),
        "last_modified": modified.isoformat(),
        "last_modified_ms": int(modified.timestamp() * 1000),
    }
    listing = (
        f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<IsTruncated>false</IsTruncated><Contents><Key>{entry['key']}</Key>"
        f"<LastModified>{modified.isoformat()}</LastModified><ETag>{etag}</ETag>"
        f"<Size>{len(raw)}</Size></Contents></ListBucketResult>"
    ).encode()
    owner = previous if reuse else current
    kind = "e025_response" if reuse else "e025v2_response"
    listed, listing_meta = response_fixture(
        archive, owner, kind, entry, "listing", listing, {"content-length": str(len(listing))}
    )
    payload, payload_meta = response_fixture(
        archive,
        owner,
        kind,
        entry,
        "full",
        raw,
        {
            "content-length": str(len(raw)),
            "etag": etag,
            "last-modified": modified.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        },
        obj=obj,
    )
    if reuse:
        current = archive.append(
            "e025v2_protocol", "synthetic-v2", datetime(2026, 9, 6, 13, tzinfo=UTC), {}, b"{}"
        )
    settings["acquisition_registration_id"] = current
    settings["reuse_sources"] = (
        [
            {
                "record_id": row["id"],
                "record_key": row["key"],
                "body_sha256": row["body_sha256"],
                "record_sha256": row["record_sha256"],
                "request_signature": meta["request_signature"],
                "etag": meta["headers"].get("etag"),
                "last_modified": meta["headers"].get("last-modified"),
                "actual_received_at": meta["actual_received_at"],
            }
            for row, meta in ((listed, listing_meta), (payload, payload_meta))
        ]
        if reuse
        else []
    )
    obj.update(listing_record_id=listed["id"], listing_actual_received_at=listing_meta["actual_received_at"])
    card.update(byte_offset=0, station_id="KMIA", version="5.0", run_ms=run_ms)
    physical = {
        **selected,
        **binding,
        "last_modified_ms": obj["last_modified_ms"],
        "historical_public_availability_verified": False,
        "reason": None,
    }
    result = {
        "key": entry["key"],
        "status": "acquired",
        "scores_computed": False,
        "object": obj,
        "cases": [physical],
        "cards": {"KMIA": card},
        "sources": [
            {
                "record_id": payload["id"],
                "raw_sha256": payload["body_sha256"],
                "headers": payload_meta["headers"],
                "request_headers": payload_meta["request_headers"],
                "actual_received_at": payload_meta["actual_received_at"],
            }
        ],
    }
    identifier = archive.append(
        "e025v2_object",
        f"{current}:{entry['key']}",
        datetime(2026, 9, 6, 14, tzinfo=UTC),
        {},
        canonical(result).encode(),
    )
    object_record = dict(archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone())
    return settings, result, entry, object_record


@pytest.mark.parametrize("reuse", [False, True])
def test_actual_archive_sources_keep_v2_or_explicit_v1_receipts_and_validate_listing(tmp_path, reuse):
    archive = Archive(tmp_path)
    try:
        settings, result, entry, object_record = acquired_fixture(archive, reuse)
        cases, sources = original_sources(archive, result, entry, settings, object_record)
        assert cases == result["cases"]
        assert sources == [result["object"]["listing_record_id"], result["sources"][0]["record_id"]]
        assert cases[0]["point_f"] == 70 and cases[0]["forecast_hour"] == 3
        changed = copy.deepcopy(result)
        changed["object"]["last_modified"] = "2026-07-20T03:31:00+00:00"
        with pytest.raises(ValueError, match="listing no longer matches"):
            original_sources(archive, changed, entry, settings, object_record)
        changed = copy.deepcopy(result)
        changed["sources"][0]["headers"]["etag"] = '"altered"'
        with pytest.raises(ValueError, match="source provenance"):
            original_sources(archive, changed, entry, settings, object_record)
        changed_settings = copy.deepcopy(settings)
        changed_settings["bucket"] = "https://wrong.example/"
        with pytest.raises(ValueError, match="pin changed|provenance changed"):
            original_sources(archive, result, entry, changed_settings, object_record)
        if reuse:
            altered = copy.deepcopy(settings)
            altered["reuse_sources"][0]["actual_received_at"] = "2026-09-06T00:00:00Z"
            with pytest.raises(ValueError, match="receipt pin changed"):
                original_sources(archive, result, entry, altered, object_record)
            with pytest.raises(ValueError, match="explicitly pinned"):
                original_sources(archive, result, entry, {**settings, "reuse_sources": []}, object_record)
        else:
            altered = {**settings, "acquisition_registration_id": settings["acquisition_registration_id"] + 1}
            with pytest.raises(ValueError, match="provenance changed"):
                original_response(
                    archive, result["object"]["listing_record_id"], entry, "listing", altered, object_record
                )
    finally:
        archive.close()
