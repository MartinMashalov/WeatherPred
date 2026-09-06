"""Index arithmetic conditional on source QC, plus explicitly provisional inputs.

This module does not reproduce hidden provider flags or the full temporal QC
history. Pending station readings remain a forecast feature, never a label.
"""

from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal

from weatherpred.index import calibration_at, canonical_point

D = Decimal


def reconstruct(point, calibrations, decision_ms, provisional=False):
    if point.get("receipt_basis") or point["t"] > decision_ms:
        raise ValueError("Backfilled/future points are ineligible")
    if provisional:
        if point.get("status") != "incomplete" or "v" in point:
            raise ValueError("Provisional input must be an unpublished incomplete point")
    elif not canonical_point(point):
        raise ValueError("A canonical value is required for arithmetic verification")
    config = calibration_at(calibrations, point["t"], decision_ms)
    if config is None:
        raise ValueError("No configuration published before the decision and effective at the point")
    members = {s["station_id"]: s for s in config["stations"]}
    if len(members) != 5 or sum(D(str(s["weight"])) for s in members.values()) != 1:
        raise ValueError("Unsupported or malformed member configuration")
    reference = sum(D(str(s["weight"])) * D(str(s["offset_c"])) for s in members.values())
    if abs(reference - D(str(config["city_reference_c"]))) > D("0.000000000001"):
        raise ValueError("Published city reference conflicts with its defining weights and offsets")
    readings = point.get("stations", [])
    if len({s["station_id"] for s in readings}) != len(readings):
        raise ValueError("Duplicate station input")
    total, weight = D(0), D(0)
    accepted = []
    for station in readings:
        code = station.get("code")
        if code not in (("pending", "ok") if provisional else ("ok",)):
            continue
        member = members.get(station["station_id"])
        if member is None:
            raise ValueError("Unknown index station")
        source = station.get("source")
        if source not in ("hf_asos", "metar") or (provisional and source != "hf_asos"):
            raise ValueError("Unsupported source for this reconstruction mode")
        received = station.get("received_at_ms")
        if received is None or received > min(decision_ms, point["t"] + 300_000):
            raise ValueError("Station reading arrived after the decision or eligibility deadline")
        temperature_f = D(str(station["temp_f"]))
        celsius = (temperature_f - 32) * 5 / 9
        if not temperature_f.is_finite() or not -40 <= celsius <= 50:
            raise ValueError("Invalid or out-of-range source temperature")
        w, offset = D(str(member["weight"])), D(str(member["offset_c"]))
        total += w * (celsius - offset + reference)
        weight += w
        accepted.append(station["station_id"])
    if len(accepted) < 4 or weight < D("0.8"):
        raise ValueError("Insufficient index quorum")
    if provisional and set(accepted) != set(members):
        raise ValueError("Initial provisional hypothesis requires all five primary members")
    value = total / weight * 9 / 5 + 32
    up = value.quantize(D("0.01"), rounding=ROUND_HALF_UP)
    even = value.quantize(D("0.01"), rounding=ROUND_HALF_EVEN)
    return {
        "unrounded_f": str(value),
        "nearest_half_up_f": str(up),
        "nearest_half_even_f": str(even),
        "rounding_tie_disagreement": up != even,
        "contributors": len(accepted),
        "available_weight": str(weight),
        "config_version": config["config_version"],
        "provisional": provisional,
        "independent_qc_reconstruction": False,
    }
