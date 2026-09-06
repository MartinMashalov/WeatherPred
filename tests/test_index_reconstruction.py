import pytest

from weatherpred.index_reconstruction import reconstruct


def configuration():
    return [
        {
            "published_at_ms": 0,
            "effective_at_ms": 0,
            "config_version": "fixture",
            "city_reference_c": -0.1,
            "stations": [
                {"station_id": f"S{i}", "offset_c": -0.5 if i == 1 else 0, "weight": 0.2} for i in range(5)
            ],
        }
    ]


def point(provisional=False):
    return {
        "t": 1000000,
        "status": "incomplete" if provisional else "normal",
        **({} if provisional else {"v": 77}),
        "stations": [
            {
                "station_id": f"S{i}",
                "temp_f": 77,
                "received_at_ms": 1100000,
                "source": "hf_asos",
                "code": "pending" if provisional else "ok",
            }
            for i in range(5)
        ],
    }


def test_full_roster_offsets_cancel_but_missing_station_uses_full_city_reference():
    p = point()
    assert reconstruct(p, configuration(), 1400000)["nearest_half_up_f"] == "77.00"
    p["stations"].pop()
    result = reconstruct(p, configuration(), 1400000)
    # Missing zero-offset member: corrected network temperature = 77 + .045 F.
    assert result["nearest_half_up_f"] == "77.05"
    assert result["nearest_half_even_f"] == "77.04"
    assert result["rounding_tie_disagreement"]


def test_pending_is_explicit_and_requires_all_members_before_deadline():
    p = point(True)
    with pytest.raises(ValueError, match="canonical"):
        reconstruct(p, configuration(), 1200000)
    assert reconstruct(p, configuration(), 1200000, provisional=True)["provisional"]
    with pytest.raises(ValueError, match="arrived after"):
        reconstruct(p, configuration(), 1050000, provisional=True)
    p["stations"].pop()
    with pytest.raises(ValueError, match="all five"):
        reconstruct(p, configuration(), 1200000, provisional=True)


def test_backdated_config_and_rejected_readings_cannot_enter_arithmetic():
    config = configuration()
    config[0]["published_at_ms"] = 1400001
    with pytest.raises(ValueError, match="No configuration"):
        reconstruct(point(), config, 1400000)
    p = point()
    p["stations"][0]["code"] = "range"
    p["stations"][0]["temp_f"] = -999
    p["stations"][1]["code"] = "provider_qc"
    with pytest.raises(ValueError, match="quorum"):
        reconstruct(p, configuration(), 1400000)
