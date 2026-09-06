from copy import deepcopy

import pytest

from research.probes.bankroll_acquisition_amendment import EVENTS, metadata_pair


def pair():
    first = {
        "markets": [
            {
                "ticker": EVENTS[0] + f"-B{i}",
                "event_ticker": EVENTS[0],
                "open_interest_fp": "10.00",
                "result": "yes",
                "yes_ask_dollars": "0.9990",
            }
            for i in range(6)
        ]
    }
    second = deepcopy(first)
    for row in second["markets"]:
        row["open_interest_fp"] = "0.00"
    return first, second


def test_only_exact_non_input_difference_is_accepted_without_changing_either_source():
    a, b = pair()
    before = deepcopy((a, b))
    chosen, differences = metadata_pair(a, b, EVENTS[0])
    assert chosen == a["markets"]
    assert {tuple(v) for v in differences.values()} == {("open_interest_fp",)}
    assert (a, b) == before


@pytest.mark.parametrize(
    "field,value", [("result", "no"), ("yes_ask_dollars", "0.2000"), ("rules_primary", "different source")]
)
def test_changes_to_outcomes_prices_or_rules_are_rejected(field, value):
    a, b = pair()
    b["markets"][0][field] = value
    with pytest.raises(ValueError, match="beyond"):
        metadata_pair(a, b, EVENTS[0])


def test_missing_duplicate_or_out_of_scope_members_cannot_enter_amendment():
    a, b = pair()
    with pytest.raises(ValueError, match="Out-of-scope"):
        metadata_pair(a, b, EVENTS[0].replace("JUL06", "JUL07"))
    b["markets"].append(b["markets"][0])
    with pytest.raises(ValueError, match="membership"):
        metadata_pair(a, b, EVENTS[0])
    a, b = pair()
    b["markets"].pop()
    with pytest.raises(ValueError, match="membership"):
        metadata_pair(a, b, EVENTS[0])
