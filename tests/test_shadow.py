from datetime import UTC, datetime, timedelta

import pytest

from weatherpred.archive import Archive
from weatherpred.shadow import slots_after, validate_lineage


def test_shadow_never_backdates_missed_slots():
    start = datetime(2026, 9, 6, 10, 56, tzinfo=UTC)
    slots = slots_after(start, 2)
    assert [(d.hour, d.minute, s.hour, h) for d, s, h in slots] == [
        (11, 30, 12, 30),
        (11, 45, 12, 15),
        (11, 55, 12, 5),
    ]
    assert all(start < d < s for d, s, _ in slots)


def test_shadow_rejects_unavailable_model_or_feature(tmp_path):
    archive = Archive(tmp_path)
    decision = datetime(2026, 9, 6, 11, 55, tzinfo=UTC)
    model = archive.append("model_artifact", "test", decision - timedelta(minutes=5), {}, b"model")
    old = archive.append("http", "old", decision - timedelta(seconds=2), {}, b"old")
    future = archive.append("http", "future", decision + timedelta(seconds=1), {}, b"future")
    validate_lineage(archive, [old], decision, decision + timedelta(minutes=5), model)
    with pytest.raises(ValueError, match="not available"):
        validate_lineage(archive, [future], decision, decision + timedelta(minutes=5), model)
    with pytest.raises(ValueError, match="not available"):
        validate_lineage(archive, [old], decision, decision + timedelta(minutes=5), future)
    with pytest.raises(ValueError, match="precede settlement"):
        validate_lineage(archive, [old], decision, decision, model)
    archive.close()
