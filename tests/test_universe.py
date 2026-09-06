import pytest

from weatherpred.universe import family


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Las Vegas Max Daily Temperature", "temperature_max"),
        ("Low  temperature Minnesota", "temperature_min"),
        ("Min NYC temp", "temperature_min"),
        ("Hourly Directional NYC Temperature", "temperature_hourly_or_index"),
    ],
)
def test_observed_title_variants_are_included(title, expected):
    assert family({"title": title}) == expected
