# Project brief and interview guide

## The project in one minute

“WeatherPred is my quantitative research and paper-execution project for weather
prediction markets. It connects archived weather forecasts, exact settlement
rules and market prices to interpretable probability models and trading policies.
I built a reproducible experiment runner and a simulator that accounts for fees,
delays, queue position, partial fills and capital availability. The first 1,836 policy/cost
comparisons did not establish a profitable strategy, and the repository explains
why. The engineering contribution is making those conclusions auditable.”

The research connects two questions: **how likely is the weather outcome, and
could an order actually earn money at the available price?** Better forecasts
help only when their advantage survives the spread, fees, uncertainty and the
risk of receiving an unfavorable fill.

Read the [strategy guide](STRATEGIES.md) for every implemented approach and the
[mathematics guide](MATHEMATICS.md) for derivations and numerical examples.

## Suggested résumé bullets

- Developed a Python weather-market research pipeline covering 11,610 historical
  contracts and 1,935 events, with versioned raw sources and chronological
  information-availability checks.
- Implemented Gaussian/empirical forecast calibration, constrained logarithmic
  pooling and a resumable trading-policy search spanning 1,836 cost comparisons;
  used whole-day resampling and earlier-period selection to evaluate selection bias.
- Built a forward paper-execution ledger for latency, depth, partial maker/taker
  fills, fees, cash reservations and same-contract netting; independently replayed
  253,227 historical hypothetical trade calculations from raw market records.
- Implemented integer bankroll allocation and an earlier-data selection rule;
  evaluated 4,608 alternative $200 accounts and independently reconciled 198,778
  hypothetical entries/releases, preserving missing annual data and failed candidates.

These counts describe the dated evidence snapshot. They do not imply production
trading, that many unique trades, a profitable fund, or independent observations.
The project was developed with AI coding assistance; implementation claims should
be defended by the equations, tests and raw-data audits, not by claims about how
many lines were written manually.

## How the strategies fit together

| Approach | Practical question | What was learned |
|---|---|---|
| Forecast calibration and pooling | Can earlier weather errors or a weather/market combination improve probabilities? | Some small development improvements; none establishes a costed trading edge. |
| Momentum, reversal, favorites and longshots | Can earlier prices predict useful later price changes or settlement returns? | Positive-looking candidates disappear under search correction or chronological selection. |
| Bracket baskets and observed-high constraints | Can settlement logic rule out expensive or impossible combinations? | No positive costed basket; preliminary observations can disagree with final settlement. |
| Paired passive quotes | Can spread capture pay for inventory risk and waiting behind other orders? | Separate forward experiments model independent fills, unmatched inventory and same-contract offsets; validation is ongoing. |
| Observation receipt recording | Does a newly received station report arrive before a useful market reaction? | Exact station versions and adjacent books are being recorded; an information-speed advantage is not established. |
| Conditional rain pairs and capacity | Can compatible daily/weekend contracts produce a discounted combined payout? | Initial later-book simulated fills leave conditional gains, but capacity is small and early sale is expensive; settlement and further validation remain pending. |
| Small transformer adaptation | Does pretrained sequence modeling improve the local weather target? | One 100-step fit completes locally; a five-minute development subgroup improves, but aggregate error is worse and no model promotion is justified. |
| Wider weather state | Can completed days constrain weekly streaks or monthly rain? | Rules narrow outcomes, but no new tested opportunity survives usable asks, fees and the slippage screen. |
| Integer bankroll allocation | Can position size increase growth without inventing fills or reusing locked cash? | The best training account reaches $341.87, but every candidate fails the simultaneous selection bound; the rule holds cash. Full-year trading performance remains uncomputed. |

## What to emphasize for a role

**Quantitative research:** explain the hypothesis, chronological split, cost
model and why a failed experiment changes the next question. The strongest
example is the small forecast-score gain that becomes a trading loss after costs.

**Machine learning:** explain probability distributions, station bias correction,
regularization and the weather/market combination. Show how an earlier model fit
produces the training inputs for the next stage without using its own future labels.
The transformer pilot adds a concrete deployment lesson: saved-checkpoint replay
caught stochastic training-mode evaluation. The same weights were rescored in
evaluation mode, both score versions were retained, and no training retry was
used to improve the result. Its approximately 16% five-minute error reduction
is explicitly a selected development subgroup, not a general forecasting claim.

**Software or data engineering:** follow one decision through original source
versions, receipt-time checks, reserved cash, a later partial fill and an audit.
Explain how a frozen registration makes a resumed run use the same rules, and
how an atomic fill-and-offset update prevents double cash credits.

## Questions worth being ready to answer

**Why can a better Brier score still lose money?** Brier score is the average
squared error of a probability forecast; lower is better. A trade must beat the
particular executable ask plus fees and worse execution prices where it is
selected. E007/E008 demonstrate the distinction.

**Why not just pick the best backtest?** Trying hundreds of policies creates
lucky winners. The best costed E013 development result is +$3.33, while its
training result is negative and the separately timed monthly selector loses
$4.45. Selection and evaluation must be separated. A holdout is data set aside
for evaluation; once inspected to guide changes, it becomes development data.

**What is difficult about a maker fill?** Touching a limit price is insufficient.
Other orders can be ahead, a public print can be late or duplicated, and fills
can be adversely selected. The simulator requires fresh qualifying trade-through
volume after the displayed queue ahead and limits participation.

**Why not double a $100 backtest to simulate \$200?** Integer quantities,
aggregate fee rounding, pending reservations and shared exposure limits change
which trades fit. In E023, changing a policy's risk budget also changes its city
allocation and drawdown-stop path. The account must be replayed from the new
starting cash. Its largest training gain is concentrated in Miami and does not
justify choosing that city after seeing the results.

**Can a known winning outcome be spent immediately?** Only when its cash is
released under the account's rules. The hourly audit separates the target minute,
publication, exchange determination and final settlement. A 60-minute settlement
timer explains the delayed cash; it is not an hour-later weather target.

**Why not treat an observed daily high as a hard bound?** Preliminary reports can
be corrected or disagree with final settlement. E014 finds a 79°F preliminary
report against a 77°F final exchange value. The apparently cheap NO loses.

**Does a matched YES/NO pair prove the market maker earns money?** It proves only
the result of that matched quantity. Other positions may be unmatched and lose
more. Same-contract netting returns matched cash early, but it does not improve
the eventual profit of those same fills. New trades using returned cash require
new execution evidence.

**What does “autoresearch” mean here?** A reproducible runner evaluates a
registered grid, records failures and abstentions, checks chronological results
and resumes safely. The new process supervisor also pins the evaluator and input
manifest, enforces a shared candidate/evaluation deadline, and retains logs on
crash or timeout. It does not invent a profitable strategy, repeatedly tune
against the final holdout, or place real-money orders.

**Would RL make the forecast better?** It could be researched, but it adds no
weather information by itself. Supervised quantile learning already rewards
useful forecast distributions. Sequential order placement, cancellation and
inventory reduction are a more natural next RL question, after sufficient
realistic fill data and simple execution baselines exist.

**What can someone reproduce from GitHub alone?** Unit tests and the compact
evidence reports. Full empirical replay requires the separately stored raw
archive. The reproduction guide makes that distinction explicit.

**What would justify progressing to real trading?** An untouched chronological
test, enough independent forward days, realistic fills/costs, stable parameters,
source reconciliation and credible drawdown/capital estimates. Those gates
remain unmet. See the exact [validation protocol](../config/validation.json).

## A short glossary

| Term | Meaning in this project |
|---|---|
| NBM | NOAA's National Blend of Models, a source of combined weather guidance. |
| Residual | The observed temperature minus its earlier forecast. |
| Calibration | Agreement between forecast probabilities and observed frequencies. |
| Regularization | A penalty that discourages a model from fitting unstable patterns. |
| Maker / taker | A maker posts an order for others to trade against; a taker trades against an existing order. |
| Spread / depth | The gap between bid and ask / the displayed quantity available at each price. |
| Slippage | A worse execution price than the decision assumed. |
| Adverse selection | The possibility that an order fills because the market has moved against it. |
| Bootstrap | Repeatedly resampling observations to measure uncertainty; here related days/contracts stay grouped. |
| Kelly sizing | Choosing an allocation by expected logarithmic bankroll growth; its usefulness depends on the probability estimate. |
| Netting | Offsetting opposite positions in the same contract and returning matched cash. |

For a job application, link the [case study](https://martinmashalov.github.io/weatherpred.html)
and [repository](https://github.com/MartinMashalov/WeatherPred). Describe the
implemented research and its measured results; profitable trading and the
$100 to $10,000 target remain unproven.
