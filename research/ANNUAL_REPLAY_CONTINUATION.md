# A defensible continuation of the $200 annual replay

Proposed protocol, 6 September 2026. **Document only: no registration, archive
mutation, policy execution, or new trading scores were performed for this
review.** Neither October–December 2025 protected prices/outcomes nor the newly
acquired 2026 prices/outcomes were inspected. The acquisition implementation,
existing E023 result, and validation rules were reviewed.

**Keep the original cash result. Prepare a separate, bounded 2026 development
study if we want to test later policy changes.** E023 protocol 93872 selected
cash in record 98829 before evaluating its requested-period panel. None of its
2,304 policy/size pairs had a positive simultaneous lower growth bound. The
4,608 training streams included both cost assumptions; they were alternatives,
not 4,608 independent samples. The actual empty-account replay covers all 365
requested days and ends at **$200**, with zero trades, fees, and drawdown under
the stated no-interest/no-external-cash-flow assumptions. The available market
panel covered 25 days. E023's `annual_ending_trading_bankroll` remains null and
its frozen report must not be rewritten as a different strategy.
[E023 protocol](../config/e023_bankroll_replay.json),
[E023 result](../evidence/E023_bankroll.json).

A later monthly selector would be a different policy. Its rule can be fixed
before the newly acquired prices are scored, but it cannot be retroactively
described as the algorithm registered by E023 or actually available to this
researcher in September 2025. New wealth calculations should be labeled
**retrospective development under explicit execution assumptions**. Selecting
the best annual endpoint after seeing the year would invalidate the claimed
chronology even if each underlying trade used earlier quotes.

**Preserve the sealed quarter.** The binding E002 rule states, “DENIED until
candidate and code are frozen in an append-only release record.” The sealed
interval is `[2025-10-01, 2026-01-01)`. The goal additionally says that an examined
holdout is no longer a holdout and that subsequent development requires a new
forward test. Acquisition protocol 99015 authorizes public 2026 acquisition;
its implementation explicitly sets `new_evaluation_scoring_authorized=false`.
It is not a scoring registration or a holdout release.
[E002 access rule](../config/e002_market_baseline.json),
[objective and evidence requirements](../docs/REQUIREMENTS.md),
[2026 acquisition implementation](probes/bankroll_acquisition.py).

The proposed continuation should not request a holdout release. Do not read
protected candles, outcomes, settlements, cached summaries, or features derived
from those observations. A market labeled January 1 must not silently contribute
a December quote or weather feature. Gate both event dates and each actual input
timestamp before inspecting values. Missing pre-January context causes an
explicit abstention. Do not connect September and January into a fictitious
continuous bootstrap calendar.

If a future candidate merits a one-time final test, freeze its whole selection
algorithm, candidate family, costs, and stop rules in the required release
record first. Treat every examined holdout segment as consumed. A predeclared
algorithm might adapt sequentially using already revealed observations, but
that is one adaptive policy evaluation, not multiple fresh tests of its
components. Do not redesign it after intermediate holdout results. The proposed
development continuation avoids that separate decision entirely.

**Strongest next experiment: one selector, three scheduled decisions.** Freeze
the following schedule in a new append-only experiment before any 2026 return
ranking. The requested account interval remains
`[2025-09-06 00:00 UTC, 2026-09-06 00:00 UTC)`.

| Account interval / decision | Registered action and permissible information |
| --- | --- |
| September 6–30, 2025 | Preserve E023's cash selection. No inherited orders or positions. |
| October–December 2025 | Mandatory cash for this new policy; protected data remains unread. This is an explicit policy restriction. |
| January 1–June 30, 2026 | Mandatory cash while the new training window accumulates. Acquire/audit 2026 inputs without choosing by returns. |
| July 1, 2026, 00:00 UTC | Train on the 181 calendar days from January 1, considering only decisions before June 26 and cash flows released strictly before July 1. Freeze July's choice. |
| August 1, 2026, 00:00 UTC | Repeat the identical rule over 212 calendar days, decisions before July 27, releases strictly before August 1. Freeze August's choice. |
| September 1, 2026, 00:00 UTC | Repeat over 243 calendar days, decisions before August 27, releases strictly before September 1. Freeze the final September 1–5 choice. |
| September 6, 2026, 00:00 UTC | End the requested-year account measurement. Report unresolved inventory separately; stop new decisions. |

The decision cutoff is five days before each selection, providing the same
settlement buffer convention as E023. A buffer is not proof that settlement
finished: verify every release timestamp and exclude unresolved candidate
training accounts. None of the month's later quotes or outcomes may enter that
month's selection, including through preliminary summaries or missing-data
filters chosen after seeing returns. Archive each selection before exposing
that month's prices/outcomes to its portfolio evaluator.

This design has **298 mandatory cash days and at most 67 active days**. That is
intentional and visible. Its advantage is a contiguous 2026 training history
of at least the existing 180-day minimum without consuming Q4. Starting monthly
reselections in October to make the annual chart more active would either need
the sealed data or require a different, explicitly frozen data-exclusion policy.
That is outside this proposal.

**Freeze the existing family instead of choosing a new subset from results.**
Reuse all 576 E013 definitions unchanged, covering its six price-based strategy
families. Reuse the four E023 risk fractions, 0.5%, 1%, 2.5%, and 5%, and its
costed/stress assumptions. Do not add E022 forecast outputs, weather features,
reinforcement learning, new cities, strikes, thresholds, or newly chosen holding
periods to this batch. Those require separate source lineage and registration.
[E013 definitions](../config/e013_autoresearch.json),
[E023 sizing and cost configuration](../config/e023_bankroll_replay.json).

The maximum budget is 2,304 policy/size pairs × two cost scenarios × three
selection dates = **13,824 training account evaluations**. Count failed,
degenerate, and no-trade evaluations. Use the same frozen market census from
acquisition 99015, including all its eligible contracts, rather than a list
of today's surviving or best-performing cities. Freeze the census, source
cutoff, all raw record IDs/hashes, exact scenario definitions, tie-break, seeds,
code, and resource limit in the new registration. New acquisitions cannot alter
a resumed run. An incomplete census or broken data chain stops scoring; it
does not trigger a search for a better substitute market.

**Use uncertainty at the weather-day level.** For candidate/scenario column
\(j\), retain the entire contiguous training calendar and compute
\(g_{t,j}=\log(W_{t,j}/W_{t-1,j})\), where \(W\) is the explicitly labeled
cost-basis account equity used by E023. Known, deliberately inactive days have
zero growth. Missing source coverage is not automatically a zero-growth day.
Aggregate all cities before resampling days; repeated strikes and cost variants
do not create independent observations.

Register 10,000 shared circular block draws at each selection, with seven-day
blocks as the ranking statistic and one-/fourteen-day sensitivity checks. Use
the same drawn days for every one of the 4,608 columns within a draw. At look
\(m\), estimate the simultaneous lower bound
\(L_{m,j}=\bar g_{m,j}-c_m\,\widehat{SE}_{m,j}\), with \(c_m\) determined by
the maximum centered standardized bootstrap statistic across the entire
candidate/scenario family. Fix look-specific seeds, for example 6202401,
6202402, and 6202403, and allocate the nominal 5% look budget equally across the
three dates. This is an approximate, dependence-aware development diagnostic;
it does not erase the earlier research search or provide a guaranteed error
rate under arbitrary nonstationarity.

Use this stricter, predeclared acceptance gate for the new selector:

- At least 180 contiguous training calendar days with an audited coverage
  status, and at least 60 distinct release days in **each** cost scenario.
- Nondegenerate bootstrap variance and positive simultaneous lower growth
  bounds in both costed and stress accounts, including the one-/fourteen-day
  checks. All required checks must pass; do not select the favorable block size.
- No pending orders or unresolved holdings at the training cutoff in either
  scenario, positive net cash profit in both, and no unresolved accounting or
  source-lineage audit failure.
- Among eligible pairs, choose the largest seven-day costed lower bound;
  ties use policy ID then numeric risk fraction. If none qualify, choose cash.

The same rule applies at all three dates, without threshold changes after a
cash month. This is deliberately stronger than E023's positive-stress-profit
gate and must be identified as a new selector. The nine bootstrap checks
(three dates × three block lengths) and all candidate comparisons remain
visible. No nominal within-batch result substitutes for the global Holm
correction or independent final/forward tests required by
[validation.json](../config/validation.json).

**Carry one account through every transition.** Internal training alternatives
may each start with the registered $200 for comparability. The selected policy's
actual simulated account starts with $200 exactly once on September 6, 2025.
It receives no monthly reset, deposits, borrowing, or transfers from other
candidate accounts. Costed and stress accounts are separate alternative
histories, never balances to add together.

At a month boundary, a new choice governs only new orders. Preserve earlier
reservations, holdings, exit instructions, fees, cash-release timestamps,
drawdown state, and global exposure limits. Cash selection stops new entries;
it does not erase losses or liquidate holdings at an unavailable price. Whole
contract quantity must be fixed from then-available cash, a then-fixed limit,
aggregate-order fees, and shared event/weather/total caps. Retain E023's
5% event, 10% weather cluster, 25% total caps and 20% drawdown halt. Existing
frozen kernels must remain untouched; any continuous-account wrapper requires
new source pins and an independent ledger audit before execution.

**Keep execution claims conditional until the missing evidence exists.**
Historical candlesticks document bid/ask summaries, transaction-price summaries,
volume, and open interest. Their public schema does not supply historical
queue position or order-book depth. An exact later candle quote is not a proved
fill; candle highs/lows and volume cannot manufacture one. Preserve the declared
100-contract ceiling as an arbitrary sensitivity limit only, not measured
capacity. A verified-execution lane must refuse unavailable receipt/depth proof.
[Official historical candlestick schema](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks).

Retain the historical fee assumptions visibly unless dated, effective schedules
and overrides are recovered for each relevant series. The fee-change endpoint
provides a route to investigate changes, but an empty change history does not
establish the initial schedule. Do not apply a current schedule retroactively
or rename an assumption “realistic” because the resulting return is attractive.
[Official fee-change API](https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes),
[earlier execution/fee review](BANKROLL_REPLAY_PROTOCOL_REVIEW.md).

**Publish distinguishable account answers.** Continue reporting E023's fixed
cash baseline as $200 and its full annual trading estimate as unavailable.
For the new study, publish the July–September conditional account path, all
three selection records, and the full policy calendar with the 298 cash days.
Because that new policy explicitly forbids trading through the sealed quarter
and enters it flat, its cash path there requires no protected price or outcome
values. If all permitted active periods are adequately reconstructed, a full
365-day **restricted-policy conditional account result** is computable without
opening Q4. Describe the restriction prominently; this does not complete a
market-opportunity audit for the entire year or revise E023's scope.

If any permitted active interval cannot be reconstructed, the new annual
account estimate remains unavailable. Report the supported interval and the
reason; do not retrospectively convert missing periods into intentional cash.
At year-end show free cash, reserved cash, open principal, cost-basis equity,
realized P&L, fees, and available liquidation evidence separately. Pending
holdings are not worth zero or their eventual payout merely because one makes
the headline easier. Show conservative payout bounds and any later settlement
runoff separately from year-end spendable cash.

Report every monthly choice and rejection, all candidate results, fills as
conditional assumptions, turnover, utilization, drawdown, market/day coverage,
and net daily log growth. Any retrospective best policy may appear only as a
clearly exploratory comparison, never as the simulated selection rule. No
100× target, ruin probability, or claim of optimal annual wealth follows from
this experiment.

**Terminate the batch after the third fixed selection and year-end audit.**
Also stop on the registered stop file, source-hash mismatch, protected-date
access, invalid cash reconciliation, exhausted registered compute budget, or
unrecoverable coverage failure. Preserve errors and partial outputs; resumption
may reproduce the same pinned attempt, not silently refit a new rule. A
drawdown halt cancels/blocks entries under the registered order rules and still
accounts for outstanding settlements. Negative or all-cash results do not
extend the family, dates, or number of looks.

Even 67 successful active days would be fewer than the global minimum 90 final
holdout days and would provide no 90-day forward shadow record. The unchanged
requirements also include 60 event days, two seasonal regimes, contemporaneous
execution evidence, positive adjusted lower growth bounds in final holdout and
forward shadow separately, perturbation/placebo checks, and the specified risk
constraints. A positive continuation would justify a separately frozen future
paper test. It would not establish profitability or authorize live trading.

## Registered result, September 6, 2026

Execution **129856**, report **165655**, evaluates all 13,824 training accounts
and nine bootstrap cases. No policy-and-size pair qualifies on July 1, August 1
or September 1. Every annual account variant therefore has **$200 cash and
zero selected trades** through the requested end date. The full 365-day path
includes the deliberately restricted 298-day cash prefix; this does not answer
what an optimal strategy with every historical market opportunity would earn.

Separate audit **166183**, report **166371**, reconstructs all account arithmetic
from retained immutable intents and all monthly choices. It checks twelve
annual prefixes and repeats all nine fixed-seed bootstrap results using the
original bootstrap routine. It does not independently reimplement that routine,
reconstruct raw quote signals, verify historical fills, or establish historical
publication times. Original E023 remains unchanged.

[Annual result](../evidence/E024_annual_replay.json),
[separate audit and method](../evidence/E024_annual_audit.json).
