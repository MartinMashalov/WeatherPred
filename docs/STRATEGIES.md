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

## What is still a research idea

Faster observation-reaction strategies, broader cross-market relative value,
and snowfall strategies have not been validated by these experiments. E019 now
tests one precipitation calendar relationship with prospective paper orders.
E015 now tests two-sided maker spread capture; its nine simulated accounts do not
establish a profitable market maker.
No neural model, HRRR ensemble strategy, live brokerage integration or production
fund management is claimed.

## What this project demonstrates

The contribution is an auditable quantitative research process: acquire original
sources, preserve when data became available, implement interpretable models,
test trading rules with costs, account for alternative experiments, simulate
orders prospectively, and reproduce results from raw records. Negative results
are evidence about the tested mechanisms, not proof that all weather markets
are efficient.
