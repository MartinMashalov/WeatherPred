import pytest

from weatherpred.nws_climate import parse_climate_report


def report(day="JANUARY 1 2025", maximum="51", partial=False, header="020709"):
    return (
        f"CDUS41 KOKX {header}\nCLINYC\n"
        f"...THE CENTRAL PARK NY CLIMATE SUMMARY FOR {day}...\n"
        + ("VALID TODAY AS OF 0400 PM LOCAL TIME.\n" if partial else "")
        + f"TEMPERATURE (F)\n YESTERDAY\n  MAXIMUM         {maximum}    216 PM   63 1965\n"
        "  MINIMUM 40\nPRECIPITATION (IN)\nMAXIMUM TEMPERATURE (F) 999\n"
    )


def parse(text, filename="CLINYC_202501020709.txt"):
    return parse_climate_report(text, filename, "CLINYC", "2025-01-01", "2025-10-01", -5)


def test_nws_uses_observed_value_and_rejects_partial_period():
    row = parse(report())
    assert row["maximum_f"] == 51
    assert row["station_name"] == "CENTRAL PARK NY"
    assert row["day"] == "2025-01-01"
    assert row["issued_at"] == "2025-01-02T07:09:00+00:00"
    assert parse(report(maximum="-3R"))["maximum_f"] == -3
    assert parse(report(maximum="MM"))["status"] == "missing_maximum"
    assert parse(report(partial=True))["status"] == "partial_day_report"
    assert parse(report(header="020400"), "CLINYC_202501020400.txt")["status"] == "partial_day_report"


def test_nws_gates_holdout_before_numeric_values_and_checks_issue_timestamp():
    assert parse(report(day="OCTOBER 1 2025", maximum="forbidden"))["status"] == "outside_development_dates"
    with pytest.raises(ValueError, match="WMO header"):
        parse(report(header="020710"))
    with pytest.raises(ValueError, match="identifier mismatch"):
        parse(report(), "CLIMDW_202501020709.txt")


def test_nws_correction_uses_later_wmo_time_not_reused_archive_filename():
    text = report(header="020718 CCA")
    row = parse(text)
    assert row["issued_at"] == "2025-01-02T07:18:00+00:00"
    assert row["archive_index_issue_at"] == "2025-01-02T07:09:00+00:00"
    assert row["correction_marker"] == "CCA"
    with pytest.raises(ValueError, match="manual date"):
        parse(report(header="020700 CCA"))
