from collections import defaultdict
from decimal import Decimal as D
from decimal import localcontext

from research.experiments.e015_audit import realize_return, remove_offset_basis


def test_realization_preserves_exact_recorded_operation_order():
    # Actual E016 fill 54934: improve_one_cent:realistic. The prior balance
    # contains a repeating average-cost allocation from earlier partial fills.
    previous = D("0.0910442477876106194690265487")
    with localcontext() as context:
        context.prec = 28
        result = realize_return(previous, D(1), D("1.0300"))
        assert result == D("0.061044247787610619469026549")
        assert result - (previous + (D(1) - D("1.0300"))) == D("3E-28")


def test_settlement_credits_payout_then_removes_cost_without_losing_money():
    # Returning the remaining position's principal closes the bookkeeping
    # identity: prior realized + payout - basis. Both winning and losing
    # settlements use that same sequence, with no tolerance or quantization.
    with localcontext() as context:
        context.prec = 28
        assert realize_return(D(".1250"), D("1.25"), D(".7250")) == D(".6500")
        assert realize_return(D(".1250"), D(0), D(".7250")) == D("-.6000")
        prior = D("0.0910442477876106194690265487")
        assert realize_return(prior, D(1), D("1.0300")) == D("0.061044247787610619469026549")


def test_fully_closed_basis_cannot_contaminate_a_later_position():
    # E016 record 72403 fully closed a 0.48-contract lot. A proportional
    # Decimal calculation left -1e-28 in the old auditor's zero-size lot,
    # which contaminated the new fill at record 72998.
    with localcontext() as context:
        context.prec = 28
        quantities = defaultdict(lambda: D(0), yes=D(".48"), no=D(".48"))
        costs = defaultdict(lambda: D(0), yes=D(".16"), no=D("0.3086270270270270270270270270"))
        old_basis = costs["no"]
        assert old_basis - old_basis * D(".48") / D(".48") == D("-1E-28")
        remove_offset_basis(quantities, costs, ("yes", "no"), D(".48"))
        assert dict(quantities) == {}
        assert dict(costs) == {}
        quantities["no"] += D(".22")
        costs["no"] += D(".1496")
        assert costs["no"] == D(".1496")
        assert quantities["no"] == D(".22")


def test_partial_offset_preserves_unmatched_basis():
    quantities = {"yes": D(3), "no": D(1)}
    costs = {"yes": D("1.2"), "no": D(".5")}
    assert remove_offset_basis(quantities, costs, ("yes", "no"), D(1)) == D(".9")
    assert quantities == {"yes": D(2)}
    assert costs == {"yes": D(".8")}
