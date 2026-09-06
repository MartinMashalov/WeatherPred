import pytest

from weatherpred.intraday_bounds import below_observed_bound, conservative_bound_probability, partial_high


def test_partial_high_retains_source_day_and_does_not_read_holdout_value():
    text = "CDUS41 KOKX 012100\nCLINYC\n...THE CENTRAL PARK NY CLIMATE SUMMARY FOR JANUARY 1 2025...\nVALID TODAY AS OF 0400 PM LOCAL TIME.\nTEMPERATURE (F)\n TODAY\n MAXIMUM 51 216 PM\nPRECIPITATION\n"
    args = ("CLINYC_202501012100.txt", "CLINYC", "2025-01-01", "2025-10-01", -5)
    r = partial_high(text, *args)
    assert r["maximum_so_far_f"] == 51 and r["issued_at"] == "2025-01-01T21:00:00+00:00"
    assert (
        partial_high(text.replace("JANUARY 1", "OCTOBER 1").replace("MAXIMUM 51", "MAXIMUM forbidden"), *args)
        is None
    )
    with pytest.raises(ValueError, match="no numerical"):
        partial_high(text.replace("MAXIMUM 51", "MAXIMUM MM"), *args)


def test_constraint_respects_inclusive_brackets_and_does_not_claim_certainty():
    assert below_observed_bound({"strike_type": "less", "cap_strike": 80}, 80)
    assert not below_observed_bound({"strike_type": "between", "cap_strike": 80}, 80)
    assert below_observed_bound({"strike_type": "between", "cap_strike": 79}, 80)
    assert not below_observed_bound({"strike_type": "greater", "floor_strike": 70}, 80)
    assert conservative_bound_probability([False] * 99) is None
    assert conservative_bound_probability([False] * 100) == pytest.approx(0.05**0.01)
    assert conservative_bound_probability([False] * 99 + [True]) < conservative_bound_probability(
        [False] * 100
    )
