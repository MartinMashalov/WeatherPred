# Review of the proposed $200 annual trading replay

Reviewed 6 September 2026 against the full research objective, E013/E014 source
and configuration, and current official Kalshi API documentation. This review
opened no October–December 2025 price or outcome data and ran no optimization.

**A defensible result requires a new chronological account simulation.** E013
is a fixed one-contract screen using $100, assumed historical fees, and
January–September 2025 development data. Doubling its P&L does not simulate a
$200 account with integer sizing, compounding, capacity, and locked cash.

## Required gates before calculating the headline balance

1. **Freeze the actual year and accounting endpoint.** Unless the user specifies
   calendar 2025, use the trailing completed UTC days
   `[2025-09-06, 2026-09-06)`. Record this interpretation explicitly. That interval
   intersects the sealed October–December 2025 period. The existing January–
   September dataset is neither a complete calendar year nor this trailing year.
   Keep the sealed prices and labels unopened while preparing the protocol.
   Any later one-time holdout evaluation needs a frozen strategy and must be
   recorded as consuming that holdout; subsequent changes require new forward
   data. An annual retrospective replay cannot automatically become a fresh test.

2. **Freeze the objective, candidates, and selection rule.** Start with exactly
   $200 already credited to the account, no deposits, borrowing, or retrospective
   capital injections. Maximize ending wealth subject to the goal's execution,
   drawdown, correlated-exposure, and ruin constraints. For a positive fixed
   starting balance, ranking a single path by ending wealth is equivalent to
   ranking by its total log growth; across uncertain paths, preserve expected
   logarithmic growth rather than chasing the best realized historical path.
   Register every policy, sizing rule, cost scenario, seed, tie-break, and stop
   rule. The year's ex-post winning policy cannot be the headline strategy.

3. **Select chronologically and include cash as an action.** At each monthly
   boundary, fit and rank policies only from earlier observations and positions
   whose outcomes or exit proceeds were already released. Use an inner earlier
   time split for parameter and sizing selection; freeze the resulting policy
   for the next month. Preserve all warm-up and abstention days. Carry the one
   actual simulated account forward between months, including old positions;
   do not reset it to $200. E013's expanding monthly selector is a useful pattern,
   but its existing evaluation dates have already influenced research and remain
   development evidence. Count the new policy × sizing comparisons and all
   failed attempts; repeated evaluations are not independent confirmations.

4. **Establish the historical universe before examining returns.** Enumerate all
   eligible contemporaneous markets, including losing, closed, and subsequently
   discontinued series. Preserve creation/open/close times, exact station,
   reporting period, source, rounding, and contract predicates as they applied
   then. Do not create historical trades in products launched later. Include a
   day/series coverage matrix and every exclusion reason. Missing prices or
   metadata mean an unscorable opportunity, not a zero price or a losing/winning
   trade. Do not silently replace a missing city with one that backtests better.

5. **Prove information availability at each decision.** Forecast initialization
   and valid time are insufficient: require publication/receipt before the
   decision. Preserve preliminary observations and later corrections separately.
   A historical file downloaded today can contain revised values. E014 explicitly
   assumes issue time plus a publication delay; that is a sensitivity assumption,
   not verified historical receipt. A model trained on August 2026 outcomes cannot
   supply a causal 2025 prediction. Check pretrained checkpoint release dates and
   training-data cutoffs too; later checkpoints cannot be presented as technology
   actually available earlier. A modern-model research backcast must be labeled
   separately from a historically available strategy.

6. **Size whole contracts from actual available cash.** For each decision,
   enumerate affordable integer quantity using that quantity's full order cost,
   fees, depth, and caps. Do not multiply a unit-order fee or unit P&L by an
   arbitrarily large size. Under an explicitly assumed cent-rounded quadratic
   schedule, ten contracts at 50¢ incur an 18¢ aggregate fee, whereas ten separate
   one-contract orders incur 20¢. This example illustrates order-size arithmetic,
   not an assertion that that schedule applied throughout the requested year.
   Fractional fills require proof that the historical venue and market supported
   them. Limit shared day/weather-system exposure; multiple strikes on one
   outcome do not diversify the underlying risk.

7. **Replay orders, cash, and settlement as timed events.** Reserve cash on
   submission; release unused reservations only on observed cancellation or
   expiry. Record partial fills, both-side transaction fees, inventory, and
   pending orders. Credit settlement when it became available, not at the
   weather observation or market close. Resolve identical timestamps
   conservatively unless the receipt/transaction ordering proves funds were
   available before the next decision. Same-contract offsets may release cash
   only under the applicable rules; related daily/weekend contracts do not net
   merely because a payout identity is expected. Reconcile cash and quantities
   independently after every event.

8. **Use the strongest execution claim supported by the data.** Candle closes
   can support a later-quote conditional screen, but do not establish quote age,
   depth, queue position, or fills. OHLC extremes are not selectable execution
   prices. Volume cannot be assigned to our order without trade direction,
   timing, queue and competing-liquidity evidence. No historical maker fill is
   justified by a price touch. If depth is absent, even a one-contract trade is
   conditional; scaling it to hundreds of contracts is less defensible. Show
   optimistic, costed, and pessimistic cases under fixed assumptions rather than
   calling any one missing-data assumption accurate execution.

## Historical API and fee evidence

Kalshi partitions live and historical data using `/historical/cutoff`; market
candles are routed according to market settlement time. Query both required
tiers and complete their pagination. Older markets and candles do not appear
in the corresponding live endpoints. Historical orders and fills are
account-specific records, not a public reconstruction of our hypothetical
orders. [Official historical-data guide](https://docs.kalshi.com/getting_started/historical_data).

`/historical/markets/{ticker}/candlesticks` supports 1-, 60-, and 1,440-minute
intervals. Its documented schema supplies bid/ask OHLC, trade-price summaries,
volume, and open interest; it supplies no historical book-depth or queue history.
Normalize legacy and current fixed-point fields explicitly and reject ambiguous
units. [Historical candlestick API](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks).

Retrieve `/series/fee_changes` with `show_historical=true`; preserve effective
times and the initial schedule for each series, plus applicable overrides.
Read-only checks during this review returned HTTP 200 and empty change arrays
for KXHIGHMIA and KXHIGHCHI. An empty change list alone does not establish the
initial fee schedule or rounding rules.
[Official fee-change endpoint](https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes).

The PDF currently served at the common fee-schedule URL is effective **7 July
2026**. Search indexing also returned an October 2025 version at that same URL;
that mutable URL and cached snippet are not an immutable historical fee record.
Archive dated source bytes before claiming contemporaneous fees. The current
rounding documentation distinguishes direct and non-direct member precision
and carries rounding across an order's fills; do not apply those mechanics
retroactively without effective-date evidence.
[Current fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf),
[fee-rounding mechanics](https://docs.kalshi.com/getting_started/fee_rounding).

## Honest output when the archive is insufficient

Report the requested window, actual covered window, coverage by series/day,
historical-fee coverage, source-receipt coverage, and depth/trade coverage first.
If a full execution-quality year cannot be reconstructed, state that limitation
and produce the supported partial-window or candle-conditional replay with its
exact assumptions. Do not annualize a short period into a supposedly observed
ending balance, fill missing months with manufactured trades, or conceal them as
ordinary cash decisions. Keep unavailable periods distinct from intentional
abstention; full-year ending wealth remains unverified.

At the endpoint show **cash, reserved cash, open-position cost, realized P&L,
and executable liquidation equity where available**. Unresolved positions carried
at cost are not recovered money. If terminal quotes are missing, provide explicit
inventory payout bounds and mark liquidation equity unavailable. A separate
runoff result may settle positions after year-end, but its later settlement date
must be shown; it is not the year-end spendable balance.

The final report should also show gross and net results, maximum drawdown,
turnover, capacity, utilization, skipped opportunities, day/event counts, and
uncertainty clustered across related outcomes and dates. Ruin probabilities and
time-to-target distributions are model-dependent estimates requiring a validated
edge; one winning annual path cannot prove them. An independent event-ledger
replay and future paper execution remain necessary before promoting the strategy.

Local sources reviewed:
[E013 configuration](../config/e013_autoresearch.json),
[E013 runner](experiments/e013_autoresearch.py),
[trading/account screen](../weatherpred/trading_research.py),
[E014 configuration](../config/e014_intraday_bounds.json), and
[E014 runner](experiments/e014_intraday_bounds.py).
