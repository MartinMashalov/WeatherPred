# Innovation queue: improve the information, target and execution model

Research round dated **6 September 2026**. This is a ranked set of proposed
experiments, written after the listed development results were known. **No new
forecast or strategy scores were computed, no annual 2026 prices or outcomes
were inspected, no sealed October–December 2025 data were read, and no orders
were submitted.** Nothing below changes a frozen experiment. “Accepted” means
accepted for a concrete specification in this queue, not a validated edge or
an authorization to score new data. An append-only registration must still
freeze any experiment before its evaluation begins.

The best next investment is to connect better information to the **exact
settlement target**, then establish that an executable quote pays for the
remaining uncertainty. A larger model is useful only if it improves that chain.
These ideas have substantial prior art; the papers cited below prevent us from
calling established methods unprecedented. Their applicability to this
particular source, contract and small account remains a testable hypothesis.

## Evidence that determines the priorities

- E022's fixed fine-tuned station model achieved day-weighted absolute error
  **1.8872°F**, pretrained Chronos **1.9072°F**, and ridge-100 **2.1501°F** on
  6,599 development forecasts spanning **28 UTC days and 20 stations**. The
  fine-tuned model's mean signed error was **+0.4523°F**, versus pretrained
  **+0.1721°F**. Lower average absolute error does not establish unbiased tail
  probabilities. These are retrospective station observations, not the Miami
  five-station index and not untouched financial validation. Sources: E022
  registration **105314**, report **105811**, diagnostic declaration **107159**;
  [forecast evidence](../evidence/E022_station_forecasts.json),
  [diagnostics](../evidence/E022_station_diagnostics.json).
- The hourly alignment audit rejected a one-hour target shift. The target is
  the canonical index **minute at close**. Historical inputs were five minutes
  old in **863/864** cases; fresh paper inputs were six minutes old. This is a
  measurable information-age difference, not an established explanation of
  the loss. Four normal index targets reproduced the equal-weight average of
  five primary stations. KMIA alone was a different target. Canonical result,
  exchange determination and spendable settlement cash arrived at different
  times. [Alignment audit](HOURLY_ALIGNMENT_AUDIT.md), primary terms raw
  **1900**, SHA256
  `83ccfd50b032e9b1f350890decd9e16917b6aa0f35670f5a2b75547156837025`.
- E023's strongest costed training account grew $200 to **$341.87**, but Miami
  supplied **75.4%** of net profit and May–June **64.5%** across cities. Its
  average winning event made $1.73; its average losing event lost $7.09.
  **All 2,304 policy/size pairs failed the simultaneous lower-bound gate.**
  Whole contracts, shared risk caps and same-time ordering changed allocations;
  a three-hour-exit policy's “stress” result exceeded its costed result because
  entry delay changed fills and subsequent portfolio decisions, not just fees.
  Diagnostic declaration **100153**, report **101221**;
  [account diagnostics](BANKROLL_DIAGNOSTICS.md).
- The live expansion census contained 446 contracts, but **541** logical
  comparisons produced no positive payout floor after fees. Only three of
  fifty weekly contracts had two-sided books, and the small weekly lead failed
  half-depth plus one-cent-slippage stress. Better weather predictions do not
  create opposing liquidity. [Market expansion](MARKET_EXPANSION.md), census
  **78957**, weekly screen **80070**, monthly-rain screen **80450**.
- Weekly station history contains 50,771 retained hours from June through
  August, with May snapshots empty. Actual receipts are in September; they do
  not establish historical publication. There is no complete year of archived
  book depth. [Station history](STATION_HISTORY.md), coverage **89421**;
  [annual coverage audit](BANKROLL_DATA_AUDIT.md).

These results motivate source-aware uncertainty, physical guidance and better
portfolio/execution accounting. They do not authorize selecting Miami alone,
increasing risk, or recycling a known development interval as a fresh test.

## Ranked queue

Information value is a qualitative judgment about how directly a small test
can resolve a known uncertainty. It is not an estimated probability of profit.
Compute amounts below are proposed hard budgets, not measured runtimes.

| Rank / ID | Hypothesis | Complexity / information value | Status and first dependency |
| --- | --- | --- | --- |
| 1 / I01 | Uncertainty depends on observation age and source state | Low / high | Accepted for specification; prospective receipt coverage |
| 2 / I02 | Simultaneous-order allocation is materially distorted by ordering | Low / high | Accepted as an accounting diagnostic; immutable intent fixture |
| 3 / I03 | Physical forecast plus learned residual beats either alone | High / high | Pending the independent E025 physical baseline already being prepared |
| 4 / I04 | Component forecasts better explain the exact hourly index | Medium / high | Pending primary component and fallback-source coverage |
| 5 / I05 | Upwind observations explain local residual changes | High / medium-high | Pending a bounded neighborhood data census |
| 6 / I06 | Fixed abstention rules improve usable probability reliability | Low / medium-high | Accepted for specification; independent calibration and fresh cases |
| 7 / I07 | Weather information arrival predicts both fills and adverse moves | High / high, data limited | Pending prospective depth/tape alignment; snapshots alone insufficient |
| 8 / I08 | Rainfall amount and timing improve related daily/monthly pricing | Medium-high / medium | Pending exact gauge definitions and fresh usable books |
| 9 / I09 | Coherent weather paths improve extrema and heat-streak probabilities | Medium-high / medium | Pending source-specific paths and settlement-rule compiler |
| 10 / I10 | Conditional model inference preserves skill while reducing quote age | Low-medium / conditional | Pending measured latency bottleneck; defer model distillation |

## Common experiment contract

These are **templates for preregistration**, not registrations already made.
Activate at most two mechanism studies initially: I01 and the prerequisite
audit for I03. I02 can run independently on synthetic or already released
training intents without reading new outcomes. Other entries stay queued.

For a prospective mechanism pilot, define `T0` as the first 00:00 UTC strictly
after registration. Freeze the exact source/market manifest, existing training
artifact, code hashes, feature availability rules, candidate count and seed.
Use days `T0..T0+13` only for the named calibration operation, then freeze its
output before a single evaluation on `T0+14..T0+41`. Do not choose a better
start date or extend an unsuccessful pilot until it passes. Report insufficient
coverage when it occurs. The first fourteen days cannot create adequate model
training history from nothing: entries needing unavailable training data stay
pending. All acquired future data remain new development once examined.

Forty-two days is a bounded mechanism diagnostic, **not** a substitute for the
[validation protocol's](../config/validation.json) minimum training, final-test, forward-test
and seasonal validation gates. Existing E022/E023 data may support known-data
implementation checks and training, but cannot be described as untouched.
The separate [annual continuation](ANNUAL_REPLAY_CONTINUATION.md) and E024
selection family must remain unchanged by this queue.

Keep observation valid time, model initialization, provider edition, successful
receipt, decision, target, determination and actual cash release distinct.
For prospective inputs require successful receipt before the decision. An old
file name or a current download of historical data is not a historical receipt.
Pin all missing/corrected/late-source cases before outcome access, and retain
them with the reason for abstention. Apply published settlement rounding and
strict/inclusive comparisons exactly. No fallback to a more favorable station,
forecast cycle, source edition or outcome definition.

Forecast studies report error, interval width and coverage, plus proper
distribution scores on their entire fixed case grid. For a forecast CDF `F`
and realized temperature `y`, use

`CRPS(F, y) = integral (F(z) - 1[y <= z])^2 dz`.

For a strict threshold contract `Y > k`, its probability is `1 - F(k)`;
discrete rounding and non-strict predicates require their actual mass at the
boundary. Brier loss is `(p - 1[Y > k])^2`. Freeze a strike grid from the
decision-time market census, not the final day's profitable strikes. Report
all hours and days; repeated stations, strikes and forecasts of one outcome
are not independent samples. Use paired day-level comparisons and shared
seven-day blocks as uncertainty diagnostics, retaining sensitivity and all
candidate failures. Small pilots cannot establish a strong family-wide edge.

Any eventual trading test must value **joint execution and outcome risk**:

`E[1_fill * (terminal_value - entry_price) - fees - exit_costs | information at decision]`.

The fill indicator and later value may be dependent. Forecast skill cannot
replace a conservative fill model. Report unfilled/canceled orders, zero-trade
days, actual available depth, position exposure, whole-contract sizing and
cash locked until the correct release. Cash remains a candidate. No standalone
pilot changes the accepted risk limits or the eventual family-wide selection
correction. Do not extrapolate annual growth or ruin probabilities from these
unvalidated edges.

## I01 — Model the age of information before adding model size

**Mechanism.** Effective forecast span is `target_time - latest_valid_time`,
not merely `target_time - decision_time`. Model residual scale using this
span, successful-receipt delay and normal/degraded/pending source state. Hold
the conditional mean fixed in the first test. The hypothesis is miscalibrated
uncertainty under stale inputs, not that one minute explains the observed loss.

**Data and baseline.** Current canonical captures provide valid and receipt
times, but only a small live source-state sample. Historical first versions
are unverified. Baseline: the existing fixed mean with one pooled residual
distribution per horizon. Compare exactly two alternatives: a fixed
age-conditioned scale regression, and that same regression with source-state
indicators. Freeze regularization, minimum support and pooled fallback before
calibration; no outcome-selected age bins.

**Bounded experiment.** Use the common 14+28-day receipt-based grid and three
total candidates. Fit at most a linear log-scale model with twenty parameters;
cap total fit/score CPU time at ten minutes. Report common-case CRPS, strike
Brier loss and coverage by predeclared age classes 0–5, 6–10 and over 10
minutes, without choosing the best class for a strategy afterward.

**Failure test and benefit.** Replay fixed additional receipt delays of one
and three minutes, dropping inputs not yet received; never shift labels.
Reject an explanation supported only by changed case inclusion or one losing
hour. Report unsupported source states rather than extrapolating certainty.
Potential benefit is fewer false edges with almost no inference cost; there
may be fewer trades, not greater capacity. Informative masks and elapsed-time
features are established ideas, including [GRU-D](https://arxiv.org/abs/1606.01865),
whose clinical results do not demonstrate weather-market performance.

## I02 — Remove accidental allocation from ticker ordering

**Mechanism.** Under $200 integer sizing, several same-time orders compete
for a shared weather exposure cap. The first order can consume the budget.
An account's city exposure can therefore reflect ordering rather than a
deliberate preference. This is visible in E023; it is not new alpha.

**Data and baseline.** Use immutable, already released training intents and
synthetic simultaneous-order fixtures only for the first diagnostic. Retain
the frozen lexicographic allocator as baseline. Compare one fixed round-robin
allocator and one joint integer allocator that maximizes predeclared
conservative expected log growth, subject to the identical cash/risk caps.
The latter stays cash if no defensible lower probability estimate is available.
Do not supply realized profits as allocation scores.

**Bounded experiment.** First freeze a checksum of the intent fixture and
ten deterministic ordering permutations. Measure allocation sensitivity,
unused budget and constraint compliance, with a thirty-minute CPU budget.
This invariance diagnostic does not select the highest-return permutation.
Any later return comparison requires a new prospective registration of exactly
the three allocators and the same timestamped intents, prices and fills.

**Failure test and benefit.** Renaming tickers while preserving economic
identity must not change the joint solution except an explicit tie-break.
Test one-cent remaining cash, simultaneous fees, correlated payouts, discrete
losses past the kill threshold and cash not yet released. No extra capital or
proportional scaling of earlier $100 results. The benefit is controlled scarce
capital allocation; it cannot rescue a negative edge. The
[risk-constrained Kelly literature](https://stanford.edu/~boyd/papers/kelly.html)
already studies growth/risk optimization. Its model-based bounds do not give
our unvalidated probabilities a real-world ruin guarantee.

## I03 — Predict the residual of operational weather guidance

**Mechanism.** Write `Y = m_NWP + r`, where `m_NWP` is the eligible operational
numerical weather prediction and `r` is its local error. Supply recent observed
residuals, wind/cloud forecasts, forecast age and ensemble spread to a small
temporal model. Physics supplies weather evolution; learning corrects local
and source-specific errors. Compare whole predictive distributions, not just
temperature means.

**Data and baseline.** E022 provides station history and frozen Chronos
outputs, but it lacks a complete matched operational guidance panel.
[E025's physical baseline](PHYSICAL_STATION_BASELINE.md) is already being
prepared: exact NOAA NBH station cards, fixed two-hour-old cycles, matched
valid times and explicit storage/receipt limitations. That comparison comes
first. Baselines for a later residual study are eligible raw guidance and a
fixed ridge residual correction; alternatives are a fixed linear
distributional correction and one Chronos-2-small residual adaptation.

**Bounded experiment.** Four total candidates, one seed and one fixed
adaptation schedule, at most two CPU hours of fitting and no new large
weights. Register new training/calibration/evaluation targets; do not promote
a hybrid by trying it repeatedly on E022's known July–August results. Start
with NBH temperature. Add HRRR covariates only after a separate small
point/range acquisition proves coverage and correct units.

**Failure test and benefit.** Use only forecast covariates published before
decision, never future realized wind/cloud. Compare identical targets and
report both shared-source cases and all intended cases. Gate run, valid time,
edition and actual receipt separately. Test bias, probability tails and missing
physical input. If the hybrid cannot beat a simple bias-corrected operational
forecast, reject its complexity. The expected benefit is improved weather
information with a small model, not guaranteed trading margin.
[Chronos-2](https://arxiv.org/abs/2510.15821) already supports related series
and covariates; the official
[small model card](https://huggingface.co/autogluon/chronos-2-small) documents
the 28-million-parameter model. Statistical ensemble correction and coherent
uncertainty are established in
[ensemble copula coupling](https://arxiv.org/abs/1302.7149). NOAA's
[HRRR archive](https://registry.opendata.aws/noaa-hrrr-pds/) offers public
operational products; archive presence alone is not first-publication evidence.

## I04 — Forecast the actual five-station index and its source states

**Mechanism.** Forecast a correlated five-component vector and apply the
literal contractual function: `I = g(components, eligible ages, source state)`.
The normal equal-weight average is one branch. Missing, degraded and fallback
states must use verified primary rules; do not invent an average of whichever
stations happen to be available. Preserve the dependence between stations.

**Data and baseline.** Four audited normal target receipts establish the
component calculation, not long-term coverage of every source state. Current
component captures exist; sufficiently long primary histories and fallback
edition logs are missing. Baselines are direct-index persistence and the
existing fixed direct-index model. Compare one component-level linear model
with a fixed shrinkage residual covariance, and one shared small temporal model
using the same contractual aggregation: four total candidates.

**Bounded experiment.** Build a settlement-function fixture before fitting,
including strict strike boundaries, stale components, midnight and daylight
saving transitions. Once historical training and live receipt prerequisites
are met, use the common prospective pilot with a two-hour training cap and
1,000 shared-seed joint trajectories per target.

**Failure test and benefit.** The function must reproduce every included
canonical target from that edition's components without using later revisions.
Unrepresented states cause abstention. Compare marginal component improvements
against final index Brier/CRPS; improvements can cancel or concentrate after
aggregation. This could exploit asynchronous information already arriving at
the components, but it cannot assume publication before receipt. Relevant
prior art is [multivariate ensemble calibration](https://arxiv.org/abs/1302.7149);
the target function comes from the
[actual TEMPH terms](https://assets.kalshi.com/contract_terms/TEMPH.pdf), not a
generic weather model. No KMIA-to-index substitution.

## I05 — A wind-directed local graph for moving weather changes

**Mechanism.** An upstream temperature/cloud change can precede the same
change at the target station. Build directed edges from fixed geometry and
decision-time wind, with causal lag `distance / along-edge wind speed` clipped
to a registered 0.5–6-hour range. This is a candidate transport feature, not a
physical guarantee: sea breezes, convection and terrain can violate it.

**Data and baseline.** The weekly airport dataset is geographically broad;
it does not establish a dense local upwind network or matching wind/cloud
fields. Census all public stations within 150 km of fixed target airports,
using dated coordinates, observation cadence, provider terms and successful
receipt coverage. Baseline is the fixed local NWP/ridge residual predictor.
Compare a deterministic upwind linear residual and a two-layer directed graph
with a small temporal residual head: three total candidates.

**Bounded experiment.** Freeze neighbors using geography and source coverage
before outcomes; cap the graph at twelve neighbors and 500,000 trainable
parameters, one seed and two CPU hours. Begin with two target neighborhoods
chosen for source availability, never E023 city profit. No network or large
model downloads beyond an explicitly bounded acquisition manifest.

**Failure test and benefit.** Reverse wind directions and shuffle neighbor
identities as fixed placebos. Require input receipts before decision; a nearby
station's observation valid time alone is insufficient. Evaluate all weather
days and a predeclared held-out station, with missing-neighbor masks and no
test-derived correlation graph. Benefits would be front/sea-breeze timing,
where local persistence misses an incoming change. Graph-based weather
prediction is established by [GraphCast](https://arxiv.org/abs/2212.12794);
its global medium-range performance does not establish short-horizon station
skill or justify importing its large model for this task.

## I06 — Learn when to abstain, without hiding the hard cases

**Mechanism.** A model may be useful only when its source state and input
features resemble calibration conditions. Test a fixed reliability filter:
source quality, interval width and distance from the training feature
distribution. This is selective prediction, meaning the model can decline a
decision instead of supplying unwarranted precision.

**Data and baseline.** Existing forecasts and source metadata supply a
starting schema, but independent calibration and fresh cases are required.
Use one frozen predictor with three policies: all eligible cases, a fixed
75% calibration-coverage filter, and a fixed 50% filter. Freeze the score and
training-only standardization. Quantile cutoffs come only from calibration;
do not choose between filters by the later return plot.

**Bounded experiment.** Common 14+28-day pilot, under ten CPU minutes, no
refitting the mean. Report losses and Brier calibration both on retained cases
and on the entire case population, plus abstained-case outcomes separately.
Count missed favorable trades and usable depth; losing half the opportunities
can erase a conditional accuracy improvement.

**Failure test and benefit.** Test calibration after filtering and specifically
near quoted thresholds. A narrow interval is not proof of correct coverage.
If an adaptive interval variant is later proposed, it must only update from
labels actually received before that decision and counts as a new candidate.
[Adaptive conformal inference](https://arxiv.org/abs/2106.00170) studies
coverage under changing distributions; marginal or long-run coverage does
not guarantee conditional trading profits. Potential benefit is reduced false
confidence, with negligible compute and explicitly reduced capacity.

## I07 — Jointly model passive fills and post-fill losses

**Mechanism.** A resting order may fill precisely when a better-informed
counterparty wants it. Model competing rates of trades, cancellations and price
changes conditional on book state and successful weather-update receipts.
Evaluate joint fill-and-profit expectations; do not multiply a fill rate by an
unconditional weather edge as if they were independent.

**Data and baseline.** The current archive lacks full-year depth and actual
own-order queue positions. Candles and occasional snapshots cannot identify
those quantities. First acquire prospective public book updates and trade
prints aligned to weather receipts and immutable shadow intents. Baseline is
the existing conservative passive-fill rule. Compare a fixed queue-state
hazard model without weather features and the same model with a single
weather-update-state feature: three candidates including baseline.

**Bounded experiment.** After registration, collect at most fourteen days for
coverage/parameter calibration, then twenty-eight evaluation days for a fixed
market list. Cap fitting at thirty CPU minutes. Report trade, cancel and price
event likelihoods; report hypothetical fill intervals rather than fabricated
own-fill labels. A first study may legitimately conclude that fill uncertainty
is too wide to rank the alternatives.

**Failure test and benefit.** Unknown cancellations ahead must not improve a
conservative queue bound. Touching a price is not a fill. Reconstruct book
sequence gaps, include canceled/unfilled intents and measure adverse price
moves after 5/30/300 seconds, including paired public-weather release windows.
Shift weather-receipt features forward as a leakage placebo. Do not backdate a
quote/cancel to avoid a move. Potential benefit is avoiding toxic fills and
recycling inventory only where executable exits exist, without increasing
fees or speculative size. Both the
[queue-reactive model](https://arxiv.org/abs/1312.0563) and
[Avellaneda–Stoikov market making](https://www.tandfonline.com/doi/abs/10.1080/14697680701381228)
are prior art, not evidence that a shadow Kalshi queue is observable.

## I08 — Forecast remaining rainfall amount, not only occurrence

**Mechanism.** Decompose a monthly total into `verified accumulation + future
rainfall`. Future rainfall needs both a point mass at zero and a distribution
of positive amounts, often called a hurdle model. Couple amount with storm
arrival timing so daily and monthly questions use one physically plausible
rainfall path. A daily “rain occurs” binary does not supply monthly inches.

**Data and baseline.** The current monthly screen found already crossed
thresholds with no available asks. Exact gauge, trace handling, time windows,
correction editions and executable books are prerequisites. Houston rain uses
KHOU/CLIHOU, while the weekly heat source is KIAH; do not merge them. Baseline
is eligible NBM guidance plus a fixed logistic occurrence/gamma-amount model.
Compare that model with simple radar-advection features and one small learned
residual for a local radar patch: three candidates, never a global new model.

**Bounded experiment.** First inventory one fixed gauge's public MRMS/rain
source coverage without selecting rainy outcomes. If available, freeze a local
patch, thirty-minute update grid and at most a one-million-parameter model,
one seed and two CPU hours. Use independently sourced training and the common
prospective pilot, recording zero-rain days and all monthly contracts even
when no quote permits a trade.

**Failure test and benefit.** Radar intensity is not the contractual gauge
reading. Check calibration of zero mass, heavy-tail amounts and calendar
aggregation; no source correction may be used before receipt. Reject if skill
exists only where the book has no size. Monthly thresholds may provide an
additional application for better amount forecasts; existing E019/E020
calendar execution and early-exit work is not duplicated here. Physical and
learned radar nowcasting already coexist in
[NowcastNet](https://www.nature.com/articles/s41586-023-06184-4). The
[NOAA MRMS registry](https://registry.opendata.aws/noaa-mrms-pds/) documents
public products, not proven gauge-settlement accuracy or complete historical
first editions.

## I09 — Convert coherent weather paths into contract state probabilities

**Mechanism.** Daily maxima/minima and weekly heat streaks are functions of
an entire path, not isolated marginal forecasts. Generate correlated hourly
trajectories, then apply a source-specific settlement function to each path.
For a daily maximum `M`, this estimates `P(max_h T_h > k)` without treating
hours as independent. For a weekly streak, track the run length and the
literal daily eligibility rule, including the eighteen-valid-hour requirement
where that contract specifies it.

**Data and baseline.** Hourly station histories and weekly definitions exist,
but they do not automatically match every daily extrema source or official
observation window. Baseline uses identical calibrated marginals with
independent draws. Compare a fixed residual autoregressive dependence model
and ensemble rank dependence, holding marginals constant: three candidates.
Only contracts with exactly verified source mappings enter the manifest.

**Bounded experiment.** Freeze the calendar, missing-hour rule, rounding,
timezone and source function with synthetic path fixtures. Use 1,000 common
seed trajectories per target, under thirty CPU minutes per daily batch, and
the common prospective window after adequate source-specific training exists.
Weekly observations will be few; report that limitation, not an inflated count
of independent strike-level outcomes.

**Failure test and benefit.** Check monotone threshold probabilities and
mutually exclusive bracket sums. Preserve observed extrema and verified weekly
state constraints without treating provisional readings as certain. Compare
joint-event Brier loss and actual spread/fee hurdles; coherent probabilities
alone do not create arbitrage. Use exact implication constraints only across
contracts with the same source/window, not superficially similar city labels.
[Ensemble copula coupling](https://arxiv.org/abs/1302.7149) explicitly provides
weather dependence after marginal correction. The potential benefit is better
portfolio risk and cross-market probabilities, not repeating the already
negative 541-comparison screen with different thresholds.

## I10 — Spend model compute only when it changes a decision

**Mechanism.** A cheap predictor can handle observations far from a
cost-adjusted decision boundary. Invoke a larger residual model only when the
cheap model's calibrated probability interval straddles that boundary, or a
new weather receipt materially changes its inputs. The proposed trigger is
fixed before evaluation. Its first purpose is to test whether compute causes
economically relevant quote aging at all.

**Data and baseline.** Existing CPU timings show small batches can run in
seconds, but do not show model runtime is the binding end-to-end delay.
Instrument receipt-to-feature, model, quote-read and intent-publication times
using synthetic/current permitted inputs without scoring new outcomes.
Baselines are always run the fixed teacher and always run the cheap model;
the third candidate is the fixed trigger. Defer a new student until profiling
establishes value.

**Bounded experiment.** A thirty-minute replay of immutable permitted input
fixtures measures warm/cold inference latency and memory, not profits. If a
bottleneck is established, register a separate common prospective pilot with
the three schedules, identical source cases and actual decision-time books.
Any student training is limited to previously declared training examples and
teacher outputs from those examples, with a thirty-minute fit cap.

**Failure test and benefit.** A forecast generated after the opportunity
cannot use the earlier quote. Cache entries require input/version identities
and expiry rules. Include trigger misses, compute failures and teacher call
costs; do not drop hard cases from a latency comparison. Reject the project if
network/publication delays dominate and faster inference does not preserve
usable quotes. [Knowledge distillation](https://arxiv.org/abs/1503.02531) is
established model compression; novelty is not a reason to implement it.

## Rejected or deferred directions

| Direction | Current decision and evidence needed to reconsider |
| --- | --- |
| Train an RL trader immediately on candles or optimistic simulated fills | Deferred. Rewards would depend on missing depth, latent queue state and unobserved counterfactual actions. Offline RL itself recognizes distribution-shift/value-overestimation problems, as in [Conservative Q-Learning](https://arxiv.org/abs/2006.04779). A conservative algorithm cannot manufacture those missing observations. First validate I07's execution bounds and a supervised fixed policy. |
| Train a new global weather foundation model on these weeks | Rejected for this data/compute budget. Start with operational guidance and bounded residual models. Existing global graph and physics/learning approaches are prior art; a grand novelty claim supplies no training coverage. |
| Fix losses by shifting the hourly label one hour | Rejected by the alignment audit's 864 training labels, 2,770 listed August contracts and four final paper events. |
| Treat E022 station MAE as proof of hourly-index or daily-extrema profit | Rejected. Source/aggregation, tail probabilities and executable costs differ. I03/I04/I09 explicitly test those bridges. |
| Trade Miami only, add leverage, or choose the best E023 size by endpoint | Rejected as a current promotion. Concentrated known training profit is a station/regime hypothesis, not fresh validation. Whole-contract capacity and shared exposures prevent linear return scaling. |
| Search thousands more nearby thresholds and call the best result a breakthrough | Rejected without a new mechanism. E023's family-wide failure and live book constraints remain; additional trials increase selection risk. |

## Next bounded scout round

Prepare **one source-feasibility table for I04/I05**, without weather-value or
profit evaluation: the exact five Miami index component IDs from primary
methodology; permitted public station metadata within 150 km; temperature,
wind and cloud cadence; successful-receipt versus valid-time semantics;
revision/quality fields; historical coverage claims and free endpoint limits.
Use at most twenty spaced public GETs and no paid Synoptic access. Stop a
provider after systematic denial/unsupported responses and retain failures.
Return a frozen proposed source manifest, estimated transfer/requests, and the
smallest causal upwind baseline that the available schema can actually support.
If primary component access is unavailable, say so and prioritize I01 plus
E025 instead of substituting another target.

That next round would resolve a concrete data bottleneck while E024 accounting
and E025 physical-baseline work proceed independently. It would not activate
the models, consume an evaluation interval or alter any existing strategy.
