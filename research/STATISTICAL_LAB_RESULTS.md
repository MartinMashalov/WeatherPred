# E032: the first shared statistical trading campaign

The first campaign runs in **49.96 seconds**, retains all four candidates,
and rejects both learned strategies. It connects monthly model training,
immutable predictions, delayed orders and continuous cash accounting.
Historical fills remain conditional.

Registration **169699** precedes the new fits. Report **169816** retains
every result; resource receipt **169817** and acceptance **169818** confirm
the supervised run completed within its declared limits.

| Candidate | Costed ending cash | Stress ending cash | Costed fills | Costed fees | Maximum cost-basis drawdown |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cash | $200.00 | $200.00 | 0 | $0.00 | 0.00% |
| Raw midpoint probability | $200.00 | $200.00 | 0 | $0.00 | 0.00% |
| Penalized probability calibration | $194.26 | $196.17 | 20 | $0.84 | 2.90% |
| Ridge return per order attempt | $188.70 | $190.82 | 101 | $2.18 | 6.43% |

These are separate accounts, not combined allocations. Each retains 365 days
from September 6, 2025 through September 5, 2026, holding cash before March
2026 and during unsupported coverage. This is not an optimal full-year
trading result. The sealed 2025 quarter is never read. Every account requiring
verified historical receipts remains at $200 with no trades.

## Models and execution

The old 576-policy family varied momentum, reversal, favorites and longshots.
This batch learns two statistical targets: a correction to market settlement
probabilities, and net dollars from a fixed order attempt. Both share fifteen
quote/calendar features. Cash and midpoint are explicit no-fit baselines.

Six monthly fits run from March through August. Training decisions stop five
days before each fit; every label must separately be released before that fit.
Each fitted head needs 45 distinct training days. Every month's predictions
are archived before the next month's training reads newly released outcomes.
All 2026 data are reused development, not untouched validation.

The common policy decides twelve hours before the source-day end, requires a
spread at most eight cents and a predicted score above three cents, and selects
one side/contract per event. Its limit is the decision ask plus one cent.
Costed entry uses the exact next hourly quote plus one cent; stress uses two
hours plus two cents against the same limit. Integer sizing reserves at most
1% of current cost-basis equity per order, with event and weather exposure
caps. Cash and holdings persist between monthly fits. Fees use the declared
.07 coefficient and aggregate account rounding.

## Diagnosis

Probability calibration slightly worsens the common scores: Brier loss
**0.129219** versus midpoint **0.128024**; log loss **0.392577** versus
**0.391923**. Lower is better. The 166 evaluated source days receive equal
weight, then events and contracts share their day's weight.

Its 72 intentions produce 20 costed fills, 46 known limit rejections and six
unknown entry endpoints. The same fills lose $4.90 before the recorded $0.84
fees. This is arithmetic on those fills, not a new fee-free strategy.

The return model emits 318 intentions: costed execution produces 101 fills,
138 rejections and 79 unknown endpoints; stress produces 54, 125 and 139.
Its costed trades win 67.3% of the time yet lose money because small gains do
not offset losses. The same fills lose $9.12 before their $2.18 fees.

Missing endpoints are **unknown evidence**. Training masks them out; the
conditional cash engine refunds their reservations as unfilled. This does
not establish that an actual order would fail. A separate diagnostic will
distinguish absent records from available side prices excluded by the strict
two-sided quote gate. E032's original source and result remain unchanged.

All three day-block lower bounds for the learned accounts are negative.
Probability calibration has only 18/19 release days under the two scenarios;
the return model has 65/40. Neither passes the fixed research gate. Longer
latency can lose less by filling a different subset; the stress result is not
required to be monotonically worse.

## Coverage and verification

The dataset contains 10,416 contracts across 1,736 events. Source windows
support 9,450 contracts across 1,575 events, January 1–August 13. All 966 later
contracts remain explicit exclusions. Before the spread gate, 5,150 supported
rows have complete features. The evaluation panel retains 6,972 rows per
candidate, of which 3,638 have eligible features and spreads.

Final preflight: **680 tests passed**, 169,679 archive records verified, and
149 basket scenarios reproduced from 48 raw events. Tests cover future
prices/outcomes, inactive-contract insertion and strike changes, label
maturity, missing endpoints, fixed limits, failed campaigns and cash
reconciliation. A future-contract rank/provenance leak was fixed and tested
before registration.

The account auditor separately reconstructs each account's quantities, fees,
cash and journals inside the run. A further result audit is being implemented
for model coefficients, cutoff gates, archive ordering and all accounts.
Neither audit establishes unobserved historical fills.

## Run the loop

    # Engineering checks: no credentials or historical archive required.
    bash scripts/verify.sh

    # With the pinned archive present, register a reviewed new campaign.
    uv run python -m research.experiments.e032_statistical_lab --register

    # Execute the returned registration ID once.
    uv run python -m research.experiments.e032_statistical_lab --run-record-id ID

Registration refuses the same experiment identity twice. Source/configuration
changes need a new declared version. Each finite batch retains its candidates,
predictions, daily accounts, failures and uncertainty comparisons. New
hypotheses use new batches; failed seeds are not silently retried. Model
arguments are restricted, but arbitrary generated programs are not OS-sandboxed.

[Full result](../evidence/E032_statistical_lab.json),
[all daily accounts](../evidence/E032_account_curves.csv),
[verification output](../evidence/verification-2026-09-06-statistical-lab.txt),
[configuration](../config/e032_statistical_lab.json),
[model families](STATISTICAL_STRATEGY_LAB.md),
[mathematics](../docs/MATHEMATICS.md).
