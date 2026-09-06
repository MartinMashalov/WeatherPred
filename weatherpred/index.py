"""Miami index provenance gates; no estimated public availability from event time."""


def calibration_at(calibrations, event_ms, decision_ms):
    eligible = [
        c for c in calibrations if c["effective_at_ms"] <= event_ms and c["published_at_ms"] <= decision_ms
    ]
    return max(eligible, key=lambda c: (c["effective_at_ms"], c["published_at_ms"]), default=None)


def canonical_point(point):
    return point.get("status") in ("normal", "degraded") and "v" in point and not point.get("receipt_basis")


def settlement_point(points, settlement_ms, decision_ms):
    """Offline label selector under TEMPH/MIAWINDEX, never a pre-close feature.

    The caller must supply the initial canonical publications, not restatements.
    The selector alone does not prove when points became publicly available.
    """
    if decision_ms < settlement_ms + 300_000:
        raise ValueError("Index settlement cannot be selected before the 5-minute deadline")
    eligible = [
        p for p in points if canonical_point(p) and settlement_ms - 3_600_000 <= p["t"] <= settlement_ms
    ]
    return max(eligible, key=lambda p: p["t"], default=None)
