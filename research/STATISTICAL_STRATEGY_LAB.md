# A statistical trading laboratory with one fixed historical referee

**Original design preserved, 6 September 2026. The initial E032 batch is
implemented and evaluated; [results are reported separately](STATISTICAL_LAB_RESULTS.md)
in report169816. The additional strategy families below remain proposals.**
Build a simulator in which learned strategies receive only information available
at a simulated decision, propose orders, and are charged for their later
execution and capital use. The first batch should compare a learned settlement
probability with a learned **net dollar return per order attempt**. This is a
different research question from varying price thresholds in hand-written rules.

The [draft batch configuration](../config/e032_statistical_lab.json)
fixes two fitted models plus cash/midpoint references, one decision horizon and one allocation policy. It is not a
registration or authorization to score the 2026 panel. Larger models and new
strategy families enter subsequent finite, declared batches under the same
evaluator. The loop can continue, but its evaluated months do not become fresh
holdouts each time a new idea is tried.

## What the earlier search covered

[E013](../config/e013_autoresearch.json) generated **576 policies** from six
rules: momentum, reversal, favorite YES/NO and longshot YES/NO. It varied the
lookback, price/move threshold, hours before the source-day end, spread cap and
exit horizon. It did not train supervised future-return predictors, latent
regime models or distributional trees. [E023](../config/e023_bankroll_replay.json)
reused those policies with four risk fractions and two cost scenarios: 2,304
policy/size pairs, not 2,304 different statistical models. E024 continues the
same family in monthly selection.

There are additional relevant baselines. E002 fitted an intercept and slope
on market log-odds separately by horizon. E003 fitted numerical-weather-model
temperature bias, empirical residual distributions and a linear mean/spread
model. E029 combined hourly station quantiles with fixed weights; those are a
different target from daily-maximum contracts. Reusing their mechanisms must
be identified as a baseline, not counted as a newly invented model.

The shared core currently has NumPy and SciPy. A local package check found no
scikit-learn, Torch, XGBoost or LightGBM in the base environment. The initial
two-model batch needs no additional dependency. Optional tree libraries require
a separately pinned environment before their queued batch, not an undocumented
installation inside a trial.

## Six distinct statistical families

Each family below has one economical baseline and one specific increase in
complexity. These are bounded future batches, not twelve models authorized to
run now. All compare with cash and use the same broker/account rules.

| Family | Learned target and mechanism | Eligible features and available data | Baseline → one larger candidate | Gate before spending more compute |
| --- | --- | --- | --- | --- |
| **F1. Conditional settlement calibration** | Estimate `P(final YES | information at D)`. Learn when the same market midpoint has different outcome frequencies, instead of assuming a price threshold is an edge. | Current log-odds, spread, prior 1h/3h price changes, past-only volatility, city, season and contract kind. Quotes/final results exist in the 2025 and 2026 daily panels. | Existing two-coefficient E002 logistic calibration → penalized logistic regression with the fixed expanded feature vector below. | Same event/day census, explicit probability-coherence diagnostics, and incremental conditional account growth after costs. Better Brier loss alone cannot select a trading winner. |
| **F2. Filtered market regimes** | Forecast the next hourly log-odds change from a changing latent price state: persistent information arrival versus transitory movement. This learns dynamics, not a momentum/reversal threshold. | Exact earlier quotes for the same contract; spread and past innovation size. A latent state is an estimated feature, not a weather observation or settlement label. Seven exact endpoints D−6h through D produce six changes; gaps remain gaps. | Pooled AR(1) control → two-state switching AR(1), exactly20 generalized EM updates and fixed regularization. | Predict with the forward filter only. Smoothing over later evaluation prices is prohibited. Advance only if a small state-based return correction improves the unchanged E032 settlement account beyond its baseline and the one-state control. |
| **F3. Supervised execution-adjusted return** | Estimate the expected net dollars from submitting one fixed order attempt. Failed limits are part of the target, and entry cost comes from the later execution scenario rather than the signal midpoint. | Same base quote/calendar vector as F1, with separate YES and NO output heads. Future execution/settlement data are labels only, never features. Both daily panels support a conditional version; neither has historical depth. | Zero expected edge/cash → penalized linear return regression in the first batch. A later nonlinear candidate is 50 depth-2 gradient-boosted trees, learning rate .05, fixed minimum 30 distinct training days per leaf, with no parameter sweep. | Include known rejections with zero P&L; retain unknown/missing labels separately. Reject a gain caused by fitting only orders that eventually fill or by predicting an unbuyable midpoint return. |
| **F4. Distributional weather boosting** | Predict a coherent distribution of the exact numeric daily maximum, then integrate the listed contract predicates. Learn nonlinear forecast bias and conditional spread. | Original eligible NBS TMP-grid maximum, TXN-minus-grid maximum, XND, city and season; exact CLI/settlement target. Current original daily NBS overlap is 2025, not the 2026 quote panel. TXN remains an 18h proxy. | Existing E003 linear Gaussian mean/spread model → 50 depth-2 boosted updates for location and log-scale, scale floor .5°F, fixed penalties. | Same weather-eligible event cohort and exact rounding cells; numerical labels released before fitting. No winning-bracket midpoint labels or point-forecast substitution for a distribution. Require a costed trading improvement and calibration diagnostics before larger trees. |
| **F5. Cross-fitted residual ensemble** | Combine independent market and physical-weather probability estimates where their past errors differ; optionally learn a small state-dependent mixing weight. | Out-of-fold F1 probabilities, E003 weather probabilities, their disagreement, forecast age and spread. Every component must target the same daily event. Existing hourly E029 outputs cannot substitute. | Fixed 50/50 daily market/weather probability pool → one logistic gate using disagreement, spread and forecast age, L2 penalty1. No more than three gate coefficients plus intercept. | Train the gate only on chronologically generated out-of-fold component predictions. In-sample component residuals are not valid stacking data. Compare with both components and the fixed pool on the same cases. |
| **F6. Cross-sectional relative value** | Test whether a contract's price change relative to a common market factor predicts its later net return, after controlling for its own history. It models cross-market dependence rather than a deterministic logical floor. | At D, a fixed seven-city panel of same-day, same-predicate-kind contracts chosen by a predeclared ordinal/rank rule; all quotes must be available at D. Standardization and factor loadings use training only. No later city decision-time quote is borrowed. | F3 using own-contract features → F3 plus one training-fitted principal factor and the own-contract residual. Require at least four eligible peers; otherwise retain a fixed baseline fallback. | Peer selection cannot use future returns or replace cities after errors. Run a fixed one-hour-lagged-peer control. Keep one-position execution initially; adding offsetting legs would require a new partial-fill/capital experiment. No claim of weather neutrality. |

The [bounded F2 follow-up](REGIME_RETURN_EXPERIMENT.md) retains E032's
settlement broker and tests only a state-based return correction. No new exit
rule is proposed. F4/F5 currently need a
separate **matched 2025 weather cohort**. Do not rank their scores against a
2026 quote-only cohort or pretend missing 2026 NBS history already exists.
F6 is exploratory until its deterministic peer mapping is implemented and
synthetically checked; it is not part of the initial two-model search.

## Initial comparison: probability learning versus return learning

Use the already acquired seven-city, 1,736-event **2026-01-01 through
2026-09-05** daily-high universe only after root pins the normalized dataset
and registers this new experiment. This is reused development data, including
any access by E024, not an untouched financial validation sample. The original
October–December 2025 holdout remains inaccessible. The current proposal does
not change the previous annual account, its mandatory cash periods or its
reported result.

Fit on **March 1, April 1, May 1, June 1, July 1 and August 1, 2026**,
with expanding earlier history from January 1. Require at least45 distinct
eligible training days **independently for each fitted target head**. A deficient
fold fails the whole candidate trial; earlier fold artifacts remain retained,
rather than silently skipping the failed fold. Eligible training decisions stop five days before
each fit; every label must separately be known strictly before that fit.
The buffer does not substitute for actual maturity checks. Each model remains
frozen until the next scheduled fit; the August artifact continues through
September5, with no September refit. The requested account starts September6,
2025 at $200, with cash before2026 and January–February warmup. The sealed
2025 quarter is never read. Unsupported source-window cases, including the
known later-August coverage limitation, remain abstentions on their original dates.

At each event's exact **source-period end minus 12 hours**, consider every
listed contract and both sides. The same current and earlier quote gates
apply to both models: `0 < bid < ask < 1`, current spread at most 8¢, and
exact own-contract endpoints at D, D−1h and D−3h. Missing context is an explicit
model abstention; no carried quote or future interpolation. Require the
contract to be open at D and at the scheduled entry. A future missing entry
quote must not suppress the original model prediction or order intention.

The base feature vector is fixed:

```text
intercept
logit(clip(current_midpoint, 1e-6, 1-1e-6))
current_spread
midpoint(D) - midpoint(D-1h)
midpoint(D) - midpoint(D-3h)
observed_panel_mid_mass = sum of available valid current event midpoints
panel_quote_fraction = available current quotes / event contracts open at D
bracket_rank = ordered-bound rank among contracts open at D / (eligible_count-1)
sin(2*pi*source_day_of_year/365), cos(2*pi*source_day_of_year/365)
six fixed city indicators, with Austin as reference
```

The seven cities are the actual E024 series, not profitable subgroups.
Continuous features are centered/scaled using the eligible training rows
only; a constant training feature gets scale1. Series identities and supported contract types are predeclared. Partial event
quote panels retain their coverage feature rather than inventing missing prices. Do not add future weather, final
expiration value, future spread or eventual fill/maturity status to the vector.
Contracts whose opening is after D are excluded from panel mass, coverage,
rank and feature provenance as well as from their own decision eligibility.
Their later listings cannot alter an earlier feature vector.

**Model A: expanded logistic settlement probability.** Fit

`p_yes(x) = sigmoid(raw_market_logit + standardized_x·β + intercept)`

with day/event-balanced binary log loss plus `.5*||β_nonintercept||²`.
Each source day gets total weight1, divided equally across eligible events,
then contracts. Use L-BFGS-B, analytic gradient, fixed maximum 200 iterations,
and report nonconvergence as a failed fit. NO probability is `1−p_yes`.
The score for a side is its predicted payout minus the frozen maximum unit
entry debit including the assumed fee. It is conditional on entry and does
not assert a fill probability.

**Model B: linear expected net return per attempt.** Fit two independent
ridge heads, YES then NO, from the exact same base X used by Model A.
They share one training-only scaler but retain independent known-label masks
and maturity gates. Fit weighted squared net-dollar error plus
`||β_nonintercept||²`, with known side heads sharing each contract's training
weight. Unknown finite placeholders never enter the loss or scaler. Use a deterministic linear solve,
not a hyperparameter search. Train on the fixed costed scenario only; do not
refit to the stress scenario's evaluation returns.

For each potential one-contract attempt, freeze the limit at the side ask at D plus the fixed1¢ allowance, capped
at .99, in both execution scenarios. At the exact future
entry endpoint:

- If the observed valid ask plus slippage exceeds the original limit, the
  hypothetical instruction is rejected and has return0, known at that entry
  time under the historical-availability assumption.
- If entry is admissible, its label is the eventual ordinary settlement
  payout minus the actual modeled entry debit, including exact fees. Its
  label is known only at settlement under the explicit availability assumption.
- Missing entry quotes, unknown terminal values or unsupported resolutions
  are **unknown targets**, never zero-P&L training examples. Their rows remain
  in the acquisition/coverage ledger and evaluation cases remain present.

Generate these training targets for **all** feature-eligible contract/side
attempts, not just the model's eventual signals, winners or fills. Report
training exclusions and label-delay distributions. Fit-on-observed-label
selection remains a limitation if missingness is informative; no claim of
random missing data is made. Labels are counterfactual endpoint outcomes,
not observed fills or independent examples from separate weather events.

Both models use one shared policy: select the highest score exceeding **3¢**
per one-contract attempt, tie by ticker then side; at most one contract per
event and one decision per event. Model B's expected-return score already
includes its modeled fee/entry costs, so do not subtract them again. This
fixed score threshold is an engineering decision, not a fitted confidence
bound. It is not searched alongside the models.

## One evaluator owns costs, cash and selection

The first batch includes exactly four candidates: the two fitted models,
**$200 cash**, and raw midpoint probability. There is no fifth fitted calibration
reference in E032. The older two-coefficient model remains a historical baseline
for the F1 roadmap, not an extra hidden trial. They share the same calendar and execution rules; a raw
midpoint model may correctly produce no trades after costs.

Use the existing two declared endpoint scenarios: costed entry at D+1h with
1¢ slippage, and stress entry at D+2h with 2¢. Both use the assumed .07
quadratic fee coefficient and cent cash rounding. These are historical
sensitivity assumptions, not verified contemporaneous fees or executable
depth. Stress changes entry time and which limits are met, not merely the
fee on an identical fill list. Keep its decisions and outcomes separately.

Train and screen expected edge for **one contract**, then let the shared
integer-order account size a request using the fixed1% risk fraction, with
all-weather cluster exposure10%, event5%, total25%, and a20% drawdown stop.
Each candidate/scenario has one $200 account; the hypothetical size cap100
is not evidence of historical depth. Aggregate fees and nonlinear integer
sizing are recomputed by the account, not obtained by multiplying unit P&L. Do not reset the account at monthly refits.
Cash, reservations, held principal and released P&L are distinct; preserve
old positions when a model changes or the policy stops. Release proceeds
only at the declared terminal event, not on the source-day end or a favorable
mark. Zero-mark and cost-basis drawdown are diagnostics, not liquidation
equity. No sizing grid or leverage in this first comparison.

Report the complete calendar, every prediction/abstention, order/limit,
entry rejection, missing quote, fee, capital rejection and terminal status.
Primary economics are daily account log growth, final free/locked cash,
drawdown, release-day count and capital-hours. Also report realized P&L,
turnover and model runtime. Sharpe-like ratios require the full calendar and
an explicit scaling convention; do not annualize a short profitable segment
into an expected return. Calibration/return losses diagnose the model,
while the account determines whether its decisions pay after costs.

The continuous account has a September2025 cash prefix and first permitted
model decisions in **March2026**, not July. Each fold's predictions and decisions
are archived before any later fit. The full candidate prediction bundle is
committed before broker resolution; refits never reset money or positions.

For any later automated candidate choice, use only completed earlier
walk-forward account segments and released cash flows. Require the original
minimum history/release-day, positive costed/stress and simultaneous block
uncertainty gates before choosing a trading policy over cash. A fixed first
batch can compare its two monthly-refitted tracks without selecting a winner
using the same month's final bankroll. Additional trials consume the declared
multiplicity budget; a new batch does not erase the earlier search.
The referee checks identical contiguous daily timestamps across account
columns. A failed or missing planned candidate/scenario blocks **every research
gate**: bounds computed on completed columns cannot turn an incomplete campaign
into a successful smaller search. Budget refusals retain not-started statuses
and earlier completed evidence.

## Concrete interfaces: models never receive a future-filled market dictionary

```python
DecisionFrame = {
    "case_id": str, "decision_ts": int, "event": str, "ticker": str,
    "station": str, "source_day": str, "source_period_end_ts": int,
    "contract_kind": str, "open_ts": int, "close_ts": int,
    "bid": str, "ask": str,                 # exact decimal strings
    "past_quotes": tuple,                  # exact timestamps <= decision
    "features": tuple[float, ...],
    "feature_status": str, "provenance_ids": tuple[int, ...],
    "historical_availability_verified": bool,
}

ReleasedLabel = {
    "case_id": str, "side": str | None,
    "target_kind": "binary_settlement" | "net_attempt_dollars",
    "value": float | None, "status": str,
    "label_known_ts": int | None, "economic_release_ts": int | None,
    "actual_source_received_ts": int, "assumed_available_ts": int | None,
    "source_ids": tuple[int, ...],
}

# Accessor checks times before reading corresponding value fields.
features = feature_store.at(decision_ts, event_ids)
training = label_store.released_before(fit_ts, decision_end=fit_ts-5*DAY)
artifact = model.fit(training, fixed_model_config)
prediction = model.predict(features, artifact)
intent = policy.decide(prediction, features, account.public_state())
broker.schedule(intent)       # later entry quote/outcome access only here
```

`fit` returns source/config/feature-order hashes, scaler coefficients,
parameters, counts and convergence status. `predict` returns every input
case, its predicted YES probability and/or side net-dollar scores, input
cutoff/hash and explicit fallback/failure. It has no account-mutating handle.
The policy receives an immutable prediction and current cash state, never
labels. The broker freezes the intention before looking up an entry endpoint.

The [existing account kernel](../weatherpred/bankroll_replay.py) already
supports delayed entry, Decimal cash, reservations and late terminal events.
The [E024 generator](experiments/e024_annual_replay_v2.py) demonstrates useful
timestamp-first gates. Reuse these mechanisms through a **new adapter**, without
editing frozen files. E013's old `load_inputs` eagerly reads outcome values;
it must not be handed directly to candidate model code as an as-of feature API.

Keep actual receipt timestamps (September 2026) alongside the historical
endpoint/settlement availability assumption. `verified` mode must refuse
unproven historical availability/depth. `hypothetical` mode can replay the
explicit assumptions but cannot manufacture earlier receipts. A simulated
July fit time is distinct from the actual time the model was fitted today.

## Finite loop and required failure tests

First budget: **four candidate IDs, six fit dates, at most twelve learned
fits**. Cash and midpoint make no fits. The E032 draft caps each trial at600
seconds and the batch at1,800seconds, with two CPU threads and a4GiB memory
limit. No network or large model weights. Stop-file/deadline failures consume a trial and stay recorded. Pin
the evaluator, dataset manifest, feature schema, broker, models and config
before the first fit. No resumed trial may change those pins.

Required synthetic checks before real data:

- Appending future quotes, settlements or later revisions changes neither an
  earlier feature vector nor its prediction/intention hash.
- A training outcome released after the cutoff is excluded even when its
  event day precedes the cutoff; a known rejected order has label0, while a
  missing quote or pending settlement has an unknown label.
- Every source day/event retains its total training weight despite unequal
  contract counts; YES and NO heads do not double the day weight.
- Scaling, regime filtering, factor estimation and stacking predictions use
  training/past information only. Whole-panel standardization and backward
  smoothing must fail a boundary test.
- Later missing entry/exit data cannot remove a previously committed intent;
  changing model at a month boundary cannot release old locked cash or cancel
  its losses. Cash remains a fully evaluated reference.
- Conditional quotes never claim actual size, partial-fill history or maker
  priority. Shared uncertainty calculations cluster weather events by day,
  not by the number of nested contracts or candidate models.

After each finite batch, the scout proposes a new mechanism against its
measured bottleneck; the referee remains fixed. Keep all candidates and the
reason for rejecting them. A promising reused-development result gets an
independent chronological/prospective test under the unchanged global gates,
not an automatic trade or an ever-expanding search until the same period wins.
