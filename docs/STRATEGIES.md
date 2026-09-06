# Strategy guide

WeatherPred is a research and paper-trading system for weather prediction markets.
The strategies below are implemented experiments, not a list of profitable live
investments. No real-money orders were submitted. Results are a dated research
checkpoint, not a continuously updated performance advertisement.

Read the [mathematics guide](MATHEMATICS.md) for equations and worked examples,
the [evidence snapshot](../evidence/summary.json) for machine-readable counts,
and the [experiment ledger](../EXPERIMENTS.md) for failed attempts and amendments.

## The trading problem

A YES contract pays $1 if its condition is satisfied and $0 otherwise. A NO
contract pays the complementary outcome. For example, a daily temperature
contract might resolve YES if a particular station's final maximum is 77–78°F.
The station, date, temperature precision and source report are part of the
contract. Nearby airports and preliminary reports are not interchangeable.

There are two ways a long position can make money: sell it later above its
purchase cost, or receive a settlement payout above that cost. Both require
fees and execution to be included. A generally accurate forecast can still
produce bad trades; a selective trading rule can be useful without improving
average forecast accuracy.

## 1. Complete-bracket consistency — E001

**Idea.** If exactly one of six brackets must win, buying one YES in every
bracket gives a $1 total payout at normal settlement. Buying every NO gives
$5. A basket is interesting only if its guaranteed minimum payout exceeds
the total price and fees across all legs.

**Implementation.** Check exclusivity and coverage from numeric predicates,
walk displayed order-book depth, and compare full, half and quarter retained
depth with zero, one and two cents of slippage. Check current fee metadata,
quote receipt age and complete event membership.

**Finding.** No positive net conditional basket among 149 fully quoted scenarios
covering 48 events. This was a snapshot study. Legs are not executed atomically,
so even an apparent basket surplus would require a separate execution test.

Code: [basket.py](../weatherpred/basket.py), [contracts.py](../weatherpred/contracts.py).

## 2. Market-only probability calibration — E002

**Idea.** Test whether market prices systematically overstate or understate
probabilities, especially near zero and one. Fit a small logistic correction
to the market probability using earlier outcomes.

**Implementation.** Use strict historical dollar-price schemas, chronological
training and equal day weighting. Missing winning-contract strike metadata is
recovered only from explicit primary rules, preventing selective removal of
winners. Midpoints are forecast benchmarks, never assumed purchase prices.

**Finding.** Small development improvements have uncertainty intervals that
include zero. This is a calibration benchmark, not a demonstrated trading edge.

Code: [calibration.py](../weatherpred/calibration.py).

## 3. Daily weather distributions — E003 and E010

**Idea.** Turn NOAA's National Blend of Models (NBM) guidance into a full distribution of final temperature,
then calculate each bracket's probability. A distribution expresses how
uncertain the forecast is instead of betting on a single predicted degree.

Six implementations are compared:

| Model | What it does |
|---|---|
| Native NBM proxy | Uses the model's native extrema mean and spread |
| Global grid correction | Adds the average historical error to the maximum of the day's forecast grid |
| Station grid correction | Learns a separate mean error and uncertainty for each station |
| Station empirical residuals | Uses the observed distribution of earlier forecast errors instead of requiring a bell curve |
| Station extrema correction | Corrects the native extrema proxy at each station |
| Mean/spread regression | Adjusts mean using station and disagreement features; lets uncertainty depend on the model's reported spread |

E010 repeats these methods with later 07:00 and 13:00 UTC model cycles. Each
forecast point uses the latest eligible source; an update cannot replace earlier
missing grid points with observations or forecasts published later.

**Finding.** Later data improves several weather baselines, but all six still
trail the market's probability scores. The best updated Brier score is 0.207857
versus 0.145231 for the market; lower is better. The native extrema product has
an 18-hour window and is explicitly treated as a proxy for the contract's daily
maximum, not as an identical target.

Code: [daily_forecasts.py](../weatherpred/daily_forecasts.py),
[updated_forecasts.py](../weatherpred/updated_forecasts.py).

## 4. Hourly persistence, trend and freshness — E004–E006

**Idea.** Forecast Miami's hourly temperature index from the latest known index
level, or extrapolate its recent slope. Estimate the remaining error from
earlier examples. Test whether fresher inputs help.

There are four base models: persistence with Gaussian errors, persistence with
empirical errors, trend with Gaussian errors, and trend with empirical errors.
The paper cohort compares the same four under five-minute and ten-minute input
limits. Its eight model variants are alternative accounts, not eight independent
weather outcomes.

**Finding.** The four original baselines trail the market at all tested horizons
over five development validation days. E005 finds pending source values can
precede the final canonical index, but that is not evidence of leading traders.
E006 compares freshness prospectively rather than repeatedly testing those
same five days. The sample is too small for promotion.

Code: [forecasts.py](../weatherpred/forecasts.py),
[freshness.py](../weatherpred/freshness.py), [index_reconstruction.py](../weatherpred/index_reconstruction.py).

## 5. Combine weather and market probabilities — E007–E008

**Idea.** Weather information may help only when combined with market prices.
Use a logarithmic probability pool with learnable weights on each source.

**Implementation.** Train the weather component on earlier months and construct
out-of-fit predictions for the combination stage. Regularization favors the
market-only starting point. Probabilities remain coherent across a complete
bracket partition. E008 then selects the largest positive expected edge at the
ask, including an explicit fee and slippage assumption.

**Finding.** The native-weather pool has a small development forecast-score gain,
but its selected unit trades go from +$1.43 gross to −$2.0155 with assumed fees
and one-cent slippage. These are conditional quote calculations, not fills.
This directly demonstrates why better prediction is insufficient for trading.

Code: [forecast_pool.py](../weatherpred/forecast_pool.py),
[quote_screen.py](../weatherpred/quote_screen.py).

## 6. Forward paper execution — E009

**Idea.** A signal is useful only if an order placed before the outcome can
actually interact with later market data at the proposed price and size.

Thirty-two virtual $100 accounts compare eight frozen hourly models across four
execution assumptions:

| Execution case | What the simulator requires |
|---|---|
| Fast taker | New book after a one-second delay; walk full visible depth |
| Reduced-depth taker | Five-second delay, half visible depth, one-cent worse prices |
| Stressed taker | Thirty-second delay, quarter depth, two-cent worse prices |
| Passive maker | Five-second arrival, post-only quote, displayed queue ahead, then only qualifying trades strictly through the quote |

Taker orders cancel any unfilled remainder. Maker orders receive only 25% of
qualifying excess trade volume after the queue ahead is exhausted. A touch,
a cancellation elsewhere, an old trade or a duplicate trade cannot create a
fill. Fees accumulate across partial fills. Orders reserve cash; positions keep
capital locked until a valid exit or finalized settlement.

At the published 14:28 UTC checkpoint on September 6, 2026, there were 88 orders,
64 taker fill records and 22 maker fill records. None had settled. Those fills
share one underlying event and cannot demonstrate profitability. Unfinalized
closed positions are carried at cost, which is not a realizable account value.

**Later result, September 6 at 15:12 UTC.** That first event finalized at 84.56°F.
All 32 alternative accounts lost between $0.48 and $4.16 on it. The audit replayed
57 settled positions. This is one weather outcome, not 32 independent losses;
it is also not enough evidence to infer the performance of reversing the models.
See the separately dated [forward audit update](../evidence/forward-update-2026-09-06.json).

Code: [paper.py](../weatherpred/paper.py),
[paper runner](../research/experiments/e009_paper.py),
[independent audit](../research/experiments/e009_audit.py).

## 7. Trading-policy autoresearch — E013

**Idea.** Search trading behavior directly, without requiring a superior overall
weather forecast. Each candidate specifies when to enter, which side to buy,
its price limit and when to exit.

| Family | Signal and direction |
|---|---|
| Momentum | Buy in the direction of a sufficiently large earlier price move |
| Reversal | Trade against that earlier move |
| Buy favorites | Buy YES where the market already assigns a high probability |
| Fade favorites | Buy NO on those high-probability contracts |
| Buy longshots | Buy YES on low-probability contracts |
| Fade longshots | Buy NO on those low-probability contracts |

The registered grid contains 576 policies: four decision horizons, two spread
limits, three exit rules, and family-specific move/price thresholds and lookbacks.
Each is evaluated under three fee/slippage/delay assumptions: **1,728 comparisons**.
All outcomes, including abstentions, are kept. The runner supports interruption
and resumption with frozen code and source cutoffs; it does not autonomously
invent strategies or modify its success criteria.

Training is January–June 2025; July–September is development validation. A
separate monthly selector can use only returns released before that month.
It requires at least 30 traded days, a positive conservative training criterion
and positive stressed training growth. Otherwise it chooses cash.

**Finding.** Thirty costed policies show a positive development validation P&L,
but none survives the search adjustment. The best-looking reversal returns
+$3.33 over 31 conditional trades while losing $1.89 in training. The earlier-
month selector loses $4.45. Selecting the reversal after seeing validation would
be precisely the selection bias this process is intended to expose.

The audit reconstructs 253,227 hypothetical entries across alternative cases,
100,170 quoted exits and 153,057 settlements. They are repeated policy evaluations,
not that many unique trades or independent samples.

Code: [trading_research.py](../weatherpred/trading_research.py),
[batch runner](../research/experiments/e013_autoresearch.py),
[raw-quote audit](../research/experiments/e013_audit.py).

## 8. Observed-high constraints — E011, E012 and E014

**Idea.** Once a station has observed 79°F, a bracket ending at 77°F appears
unlikely to win a daily-high market. Trade only if its NO price leaves enough
room for fees and an allowance for preliminary-report errors.

Source investigations first distinguish NWS daily climate reports, current
Weather Company domestic tables, international METAR rules and retrospective
observation archives. E014 then uses 3,238 eligible preliminary NWS reports.
Three temperature margins, three publication delays, four price caps and
three cost cases produce **108 comparisons**.

The error allowance is estimated using 180 training days. A day counts as a
failure if any station's preliminary bound exceeds its final exchange value.
Four training days fail with no margin; none fail with one- or two-degree
margins. The margin choices are fixed before validation.

**Finding.** No positive costed result. The alternative-case trades collapse to
one Chicago event: a preliminary 79°F report versus final 77°F settlement. The
cheap NO loses. This two-degree validation discrepancy also shows why covering
all training errors with a one-degree margin does not establish a safe rule.

Code: [intraday_bounds.py](../weatherpred/intraday_bounds.py),
[experiment](../research/experiments/e014_intraday_bounds.py),
[source diagnostics](../research/experiments/e014_source_diagnostics.py).

## 9. Paired passive quotes with inventory control — E015

**Idea.** Offer to buy YES and NO below their combined dollar payout. A matched
pair can earn the spread, but the two orders fill independently. If only the
losing outcome fills, inventory losses can exceed the spread on successful pairs.

The new forward experiment compares joining the best bids, improving them by one
cent, and adjusting prices by one cent per net contract of inventory. Each policy
has three queue/delay assumptions, giving nine alternative $100 accounts. Quotes
are one contract per side. Both legs require subsequent opposite-direction trades
strictly through the quote after the displayed queue ahead has been consumed.
E015 deliberately keeps matched cash committed until settlement as a capital
stress case. That differs from Kalshi's automatic same-contract offsetting,
corrected in E016 below. Total committed cost is capped at 10%, with 5% per event.

**Status.** Registered on September 6 at 15:08 UTC, before its 15:20 first decisions.
The fixed panel contains Miami hourly temperature and Miami, Chicago and LA daily
highs. NYC high and NYC/Boston low contracts failed the preset eligibility filter.
This is a forward feasibility experiment; no profitability or calibrated optimal
inventory policy is claimed. Its outcome does not change the earlier dated
1,836-comparison evidence snapshot.

Code: [quote and inventory logic](../weatherpred/market_making.py),
[registered runner](../research/experiments/e015_market_making.py),
[independent execution audit](../research/experiments/e015_audit.py).

## 10. Same-contract offsetting and capital reuse — E016

**Correction.** Buying NO while already holding YES in the same contract offsets
the position. Kalshi returns the matched dollar payout early. This is different
from optional collateral return across several contracts in an event.

E016 keeps the same four-market panel and nine quoting/scenario alternatives,
but removes matched quantities immediately, returns cash and realizes their
profit or loss. Only the remaining net position reaches final settlement. The
fill and its offset form one journal event, preventing duplicate cash credits.
Conservative opening-order cash and risk caps remain; they can still restrict
some hedges and are strategy choices rather than venue requirements.

**Status.** Registered at 15:28 UTC before new 15:40 forward decisions. A separate
replay of the same first 43 E015 fills finds two alternative LA accounts completing
a one-contract offset, earning 2¢ each. Each also has unmatched inventory that
can lose more. Returning their $1 of cash changes funding timing, not the range
of final profits on those same fills. No additional trades are assumed in this
replay, and no positive long-run return is established.

Sources: [Kalshi netting](https://news.kalshi.com/p/collateral-return),
[current settlement behavior](https://docs.kalshi.com/getting_started/market_settlement).
Code: [atomic netting ledger](../weatherpred/netted_paper.py),
[forward cohort and identical-fill replay](../research/experiments/e016_netted_maker.py).

## 11. Supporting experiment: observation timing — E017

**Question.** When a station report reaches this system, has the market already
reacted? Answering that requires original receipt times and surrounding quotes,
not just a retrospective weather file with an observation timestamp.

The recorder saves METAR reports, which are standardized aviation weather
observations, for eight explicit stations. Each one-minute cycle receives a
market-book batch, the weather batch, and another market-book batch. It retains
the observation time, provider receipt time, original report, every changed
version and the system's own first receipt. Chicago uses Midway (KMDW), not a
substitute from O'Hare (KORD).

**Status.** Registered on September 6 at 15:36 UTC as data acquisition. Old
reports returned in the initial two-hour batch are marked as backfill. Neither
provider receipt time nor the report's nominal time proves the first public
availability. One-minute sampling cannot establish an advantage measured in
seconds. METAR observations also do not replace the contract's official
settlement source. No trading rule or profitable reaction effect is claimed.

Source: [NOAA Aviation Weather Center data API](https://aviationweather.gov/data/api/).
Code: [registered receipt collector](../research/experiments/e017_station_receipts.py).

## 12. Conditional hourly forecasts with heavier tails — E018

**Idea.** Warming and cooling hours need not have the same forecast error. Learn
how the remaining temperature change depends on the recent trend and the time
of day, while limiting how strongly a small training sample can change the model.
Compare ordinary Gaussian errors with a fixed Student t distribution, which
assigns more probability to large surprises.

Twelve candidates combine one or two daily harmonics, three ridge penalties and
two error distributions. A harmonic is a smooth repeating daily pattern; ridge
penalization shrinks unstable coefficients. Four expanding training windows
score the following two days each, all within August 20–31. One candidate is
chosen across the three forecast horizons. September development data and the
sealed final holdout are not used for this selection.

**Development result.** The two-harmonic, strongest-penalty, Student t candidate
is selected. Its earlier-fold temperature RMSE is 0.775°F versus 0.848°F for
persistence and 1.022°F for trend. These eight August days also selected the
candidate, so this comparison is a development diagnostic with selection bias.
It is not independent validation or a profit result.

**Forward test.** Model 59490 was frozen at 16:09 UTC on September 6, before the
16:30, 16:45 and 16:55 decisions. A new cohort compares the chosen model and the
four existing fresh-input baselines under four execution scenarios, in 20
alternative $100 paper accounts. It uses original future books, conservative
queues, fees, quarter-Kelly sizing, exposure caps and same-contract netting.
All three decisions concern one hourly event; substantial forward evidence
remains necessary.

Code: [conditional distributions](../weatherpred/conditional_forecasts.py),
[registered runner](../research/experiments/e018_conditional_hourly.py),
[raw-source model replay](../research/experiments/e018_diagnostics.py).

## 13. Daily-versus-weekend rain pairs — E019

**Idea.** If Saturday had no rain, rain on either day of the weekend means rain
on Sunday. Under matching settlement rules, Sunday's contract and the weekend
contract then have the same outcome. Buying YES in one and NO in the other can
cost less than their combined conditional $1 payout.

The system first checks the exact station, dates, reporting conventions,
finalized Saturday result and an official source report showing numeric zero.
It then checks both pair directions, actual asks, available depth and fees.
All 19 eligible cities are included in the registered September 6 panel.

**Execution matters.** These are separate contracts and separate orders. The
second leg arrives later and may fail or fill only partly. The simulator keeps
that unmatched exposure, cancels unfilled quantities and retains the actual cash
cost. Cross-contract positions do not automatically return cash through netting.

**Current evidence.** One exploratory NYC snapshot costs 92.71 cents per matched
pair including fees, rising to 96.73 cents after the strongest registered depth
and slippage stress. Those are displayed costs, not earned returns. Houston's
apparent gap disappears when using its book. Forty historical binary outcomes
match the calendar relationship, but only twenty pass strict identical-rule
checks, and they cover one weekend. There is too little evidence to estimate
rare source or settlement failures.

The prospective test begins at 17:00 UTC on September 6 with three alternative
$100 accounts. It uses a 5% pair reservation cap, fixed limits, up to five
contracts per leg and at most one attempt per city/account. A fixed one-cent
deduction for source uncertainty is a stress assumption, not an estimated
probability. Reports show losses possible if one leg fails or the settlement
relationship breaks. **No guaranteed arbitrage or validated profit is claimed.**

**First forward checkpoint.** At 17:00, only NYC qualified. Both legs received
simulated fills in all three scenarios, using six separate later book receipts.
After fees, the conditional gains are $0.4113 on five matched pairs, $0.2873 on
five pairs and $0.1690 on four pairs. The latter uses quarter depth and two cents
extra slippage per leg. An independent audit reproduces all eight fill slices
and the cash balances. Realized profit is zero until actual settlement; the
three accounts reuse one underlying weekend and do not prove a durable edge.

Code: [calendar rules and pair costs](../weatherpred/rain_relations.py),
[registered runner](../research/experiments/e019_rain_pairs.py),
[independent execution audit](../research/experiments/e019_audit.py).

## 14. Execution, capacity and capital reuse — E020

**Idea.** Improve the money earned per unit of scarce capital by choosing useful
order types, measuring available size and testing whether early exits release
cash economically. This is a diagnostic of the existing rain trade, not a new
forecast or additional paper position.

One hundred quantities under three depth/slippage scenarios yield 300 retained
screens. At the strongest stress, the new NYC snapshot supports only six pairs,
costing $5.8108 for $0.1892 conditional surplus. A larger bankroll does not create
more quoted size at the required margin. Selling the existing positions at the
available bids would instead lose between $2.10 and $2.37 per alternative
account under its matching exit scenario. Cash recycling is expensive here.

The new order-intent builder maps YES/NO purchases and sales into the current
exchange schema. It produces inspectable payloads and validates limits; it does
not authenticate or submit them. A fill-or-kill instruction applies to one leg,
so it cannot make a two-contract trade atomic. The study also computes expected
logarithmic growth under explicit source-failure assumptions, retaining the
distinction between average profit and bankroll growth.

Code and full results: [execution and capital study](../research/EXECUTION_FINANCE.md).

## 15. Wider contract relationships and weather state

**Idea.** Use completed weather observations to narrow what remains possible,
then buy only when the executable cost is below the conditional payout. Weekly
heat streaks depend on consecutive qualifying days, while monthly rainfall
depends on accumulated amount plus future rain. Exact source, station and
rounding rules matter as much as the forecast.

A fresh screen covers 82 selected series and 446 books. None of 541 nested
threshold/count relationships has a positive quoted floor after fees. Fifty
weekly contracts are tested under three perturbations of past daily means;
none survives one cent of additional slippage. Four monthly rain thresholds
already exceeded by published accumulations have no available YES asks.
All 34 catalog snowfall series currently have no open market.

A useful acquisition result is 5,443 hourly observations across 37 stations.
The data has timestamps and pending/settled status, but its historical arrival
times are unverified. It is a candidate input for future models, not evidence
that earlier traders could have accessed it at our assumed time.

Code, source rules, retained failures and next tests:
[market expansion report](../research/MARKET_EXPANSION.md).

## 16. A small forecasting transformer — E021

**Idea.** Adapt a pretrained time-series model to the local temperature target,
then require it to beat simple forecasts before using it to price contracts.
The selected research candidate is Chronos-2-small, with about 28 million
parameters. It runs locally in a separate optional environment.

Two pretrained variants—index alone and index plus stations/clock—produce 288
forecasts on 48 August hourly events. Both lose to persistence overall. One
fixed supervised experiment then trains for 100 steps on August 20–23, taking
22.55 seconds on CPU. No evaluation outcome enters training and no alternative
checkpoint is selected. The evaluation uses 93 forecasts from eight already
examined development days.

Fine-tuned overall temperature error is 0.8010°F versus 0.7504°F for persistence.
The five-minute subgroup improves from 0.5375°F to 0.4512°F, approximately 16%,
while longer horizons worsen. This is an exploratory specialist hypothesis,
not independent evidence of forecasting skill or profit. The saved weights
reproduce all predictions exactly after correcting an evaluation-mode error;
the rejected initial scores remain in the evidence.

Reinforcement learning has not been trained here. Supervised learning directly
rewards accurate distributions; a later RL policy could choose sequential
execution actions once sufficient realistic fill data supports that experiment.
Neither RL nor a small transformer establishes a world-leading weather model.

Model comparison, licenses, exact training parameters and replay:
[model research](../research/model_candidates.md),
[compact evidence](../evidence/model_candidates.json).

## 17. Bounded automated research

**Purpose.** Make experiments repeatable and keep failed attempts visible.
The existing policy search runs finite registered batches. A new process runner,
inspired by Karpathy's autoresearch, additionally executes a registered candidate
and a fixed evaluator under one wall-time deadline. It preserves logs, stops
child processes and consumes the attempt when a run fails or times out.

The evaluator requires the baseline's same event panel, earlier training labels,
eligible input receipts and sealed-date exclusions. A lower development error
can qualify a candidate for more research; it cannot promote it as profitable.
The new runner's verification uses real subprocesses with synthetic inputs, so
its test count is not a count of market experiments.

Design, commands and limitations:
[autoresearch adaptation](../research/AUTORESEARCH_ADAPTATION.md).

## 18. Starting with $200: capital and strategy selection

**Question.** How much would a small account retain after integer contracts,
transaction costs, failed orders, delayed settlements and correlated weather risk?
E023 is a new account simulation; it does not multiply the older $100 study's
one-contract profit by two.

The same 576 price-based policies are paired with four maximum order budgets:
0.5%, 1%, 2.5% and 5% of current account equity. Each pair is simulated under
the existing costed and stressed execution assumptions. The account reserves
cash when an order is decided, releases unused cash when an order fails or fills
partially, and retains invested cash until sale or settlement. Entry and sale
fees are recalculated for the actual integer order quantity. All weather cities
share a 10% exposure cap; each event has a 5% cap.

The selector uses only orders decided before September 1, 2025 and money
released before September 6. A five-day buffer allows ordinary settlements to
finish. It ranks earlier log growth after a simultaneous uncertainty penalty
across all 4,608 policy, size and cost combinations. A qualifying choice must
also survive stressed costs and have sufficient earlier release days. If none
qualifies, the policy holds cash. The requested year's results cannot select
its winner.

**Accuracy limit.** The requested interval is September 6, 2025 through
September 5, 2026. The existing main daily-market panel covers only 25 of those
365 days. Historical candle prices do not establish order depth or actual
availability, and historical fees remain explicit assumptions. Missing months
cannot become zero-profit observations or be filled by extrapolating a short
period. The annual cash-only benchmark is $200 with no interest or external cash
flows; an annual trading balance requires a complete valid replay.

Code and independently reviewed assumptions:
[account simulator](../weatherpred/bankroll_replay.py),
[registered study specification](../config/e023_bankroll_replay.json),
[replay review](../research/BANKROLL_REPLAY_PROTOCOL_REVIEW.md).

## 19. Station forecasts against strong simple models — E022

**Idea.** A pretrained time-series model may recognize temperature patterns
better than a small regression, while a fixed local adaptation may reduce
remaining bias. This is a forecast component; it has no attached trading rule.

The experiment freezes eight models before execution: latest-temperature
persistence, the previous day's temperature, their equal blend, three penalized
regressions and two versions of Chronos-2-small. All use the same 6,599
development cases across 20 stations and 28 days. Each receives only the eligible
past context. Separate earlier periods provide fitting and quantile calibration.

**Result.** Mean absolute error, with equal weight per UTC day, is 2.1501°F for
the strongest regression, 1.9072°F for pretrained Chronos, and 1.8872°F after one
200-step supervised fit. The adapted model improves 12.23% over regression and
only 1.05% over its pretrained checkpoint. Its distribution loss also improves,
and the independent auditor reproduces all eight models' score arithmetic.
The complete station/horizon diagnostic finds gains versus regression at 19
of 20 stations. The additional gain from adaptation is less broad and its
exploratory uncertainty interval includes no improvement over pretraining.

**Limit.** Historical receipt times and revisions are unverified; the 15-minute
publication lag is an explicit assumption. The target is an individual station
temperature, not the Miami settlement index or a daily high. Twenty stations
share only 28 days, and physical weather forecasts are not yet included in this
same-target comparison. The result supports further research, not a profitable
trading or world-leading forecasting claim. No reinforcement learning was used.

Code and evidence: [full study](../research/STATION_FORECASTS.md),
[all candidate scores](../evidence/E022_station_forecasts.json),
[independent audit](../evidence/E022_audit.json).

## 21. Physical guidance and transformer combinations

**Question.** Does an operational weather forecast contain useful information
that the station-history transformer misses, and does combining them help?

E026 registers a common-case comparison of NOAA's hourly NBH guidance with
all eight existing station candidates. The source run is fixed two hours
before each original decision. Actual September receipts remain visible;
original storage timestamps provide only conditional historical availability.
Missing forecasts cannot silently disappear from the comparison.

E029 separately freezes three combinations with 25%, 50% or 75% weight on the
saved pretrained transformer. Each uses the same earlier quantile calibration.
All twelve comparisons with four baselines remain in the result family. No
weight is selected from development performance and no new fit is authorized.
Acquisition completes all 498 objects and 9,870 cases. NBH has mean absolute
error 1.8345°F versus adapted Chronos 1.8872°F; their descriptive difference
interval includes zero. The three combinations then reach 1.6608°F, 1.6023°F
and 1.6866°F respectively. All twelve paired intervals are below zero, and
calibrated quantile loss also improves. The lowest observed 50/50 error is a
development finding, not a selected trading strategy. No trading signal or
financial validation follows from these scores.

A new prospective collector records 28 stations, the exact Miami index
components and eligible market books with actual receipt times. This supplies
data for later observation-age and wind-transport tests; collection itself
does not simulate a fill. A dedicated idea scout maintains the
[ranked hypotheses](../research/INNOVATION_QUEUE.md) and their failure tests.

Code and design: [physical baseline](../research/PHYSICAL_STATION_BASELINE.md),
[fixed combinations](../evidence/E029_design.json),
[all twelve forecasts](../evidence/E029_fixed_combinations.json),
[receipt capture](../evidence/E027_v2_capture_checkpoint.json).

## What is still a research idea

Faster observation-reaction strategies, broader cross-market relative value,
and snowfall strategies have not been validated by these experiments. E019 now
tests one precipitation calendar relationship with prospective paper orders.
E015 now tests two-sided maker spread capture; its nine simulated accounts do not
establish a profitable market maker.
A neural development pilot is now implemented. A combined HRRR/residual-model
strategy, reinforcement-learning execution policy, live brokerage integration
and production fund management remain unvalidated research or future engineering.

## What this project demonstrates

The contribution is an auditable quantitative research process: acquire original
sources, preserve when data became available, implement interpretable models,
test trading rules with costs, account for alternative experiments, simulate
orders prospectively, and reproduce results from raw records. Negative results
are evidence about the tested mechanisms, not proof that all weather markets
are efficient.
