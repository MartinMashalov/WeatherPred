# Explaining the project in an interview

## A concise description

“WeatherPred is my quantitative research and paper-execution project for weather
prediction markets. It connects archived weather forecasts, exact settlement
rules and market prices to interpretable probability models and trading policies.
I built a reproducible experiment runner and a simulator that accounts for fees,
delays, queue position, partial fills and locked cash. The first1,836policy/cost
comparisons did not establish a profitable strategy, and the repository explains
why. The engineering contribution is making those conclusions auditable.”

## Suggested résumé bullets

- Developed a Python weather-market research pipeline covering11,610historical
  contracts and1,935events, with versioned raw-source provenance and chronological
  information-availability checks.
- Implemented Gaussian/empirical forecast calibration, constrained logarithmic
  pooling and a resumable trading-policy search spanning1,836cost comparisons;
  applied day-block resampling and earlier-period selection to expose overfitting.
- Built a forward paper-execution ledger for latency, depth, partial maker/taker
  fills, fees and cash reservations; independently replayed253,227hypothetical
  trade calculations across alternative strategies from raw market records.

These counts describe the dated evidence snapshot. They do not imply production
trading, that many unique trades, a profitable fund, or independent observations.
The project was developed with AI coding assistance; implementation claims should
be defended by the equations, tests and raw-data audits, not by claims about how
many lines were written manually.

## Questions worth being ready to answer

**Why can a better Brier score still lose money?** A forecast averages errors over
many contracts. A trade must beat the particular executable ask plus fees and
slippage where it is selected. E007/E008 demonstrate the distinction.

**Why not just pick the best backtest?** Trying hundreds of policies creates
lucky winners. The best costed E013result is+$3.33, while its training result is
negative and the separately timed monthly selector loses$4.45. Selection and
evaluation must be separated.

**What is difficult about a maker fill?** Touching a limit price is insufficient.
Other orders can be ahead, a public print can be late or duplicated, and fills
can be adversely selected. The simulator requires fresh qualifying trade-through
volume after the displayed queue ahead and limits participation.

**Why not treat an observed daily high as a hard bound?** Preliminary reports can
be corrected or disagree with final settlement. E014 finds a79°F preliminary
report against a77°F final exchange value. The apparently cheap NO loses.

**What can someone reproduce from GitHub alone?** Unit tests and the compact
evidence reports. Full empirical replay requires the separately stored raw
archive. The reproduction guide makes that distinction explicit.

**What would justify progressing to real trading?** An untouched chronological
test, enough independent forward days, realistic fills/costs, stable parameters,
source reconciliation and credible drawdown/capital estimates. Those gates
remain unmet. See the exact [validation protocol](../config/validation.json).
