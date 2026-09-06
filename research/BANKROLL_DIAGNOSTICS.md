# Why the largest training bankrolls appeared

The largest costed training account grew from **$200 to $341.87**, but its profit
depends strongly on Miami, the months of May and June, and the particular
allocation produced by 5% order sizing. Its simultaneous uncertainty bound is
negative. The strongest stress account reached **$280.13**, while the identical
policy and size in the ordinary costed scenario ended at **$164.77**. These are
conditional, retrospectively selected training results, not validated profits or
a recommendation to trade.

Declaration **100153** fixed two policy IDs, four risk fractions and two scenarios
before this drilldown: exactly 16 archived accounts, all retained. Source accounts
are **98620–98639** (the exact 16 IDs and hashes are in the declaration). Parent
protocol **93872** uses decisions from January 1 through August 31, 2025, and cash
released before September 6, 2025. The five September buffer days can include
settlements from August orders. No 2026 observations, prices or strategy outcomes
were analyzed, no new policies were searched, and the sealed quarter was not
opened. Results are archived as **101221**, exported to
`reports/bankroll_diagnostics.json` by
[`bankroll_diagnostics.py`](probes/bankroll_diagnostics.py).

## The actual trading mechanism

Both policies inspect an event 24 hours before its defined source-period end.
They retain contracts whose YES bid/ask midpoint is at most 0.30 and whose spread
is at most 0.08. Among those, they choose the **highest** midpoint, breaking ties
by ticker, and buy its **NO** side. Thus `longshot_no` means betting against a
relatively unlikely YES bracket; it does not mean buying a cheap lottery ticket.
The midpoint selects a contract, while the purchase price comes from the NO ask,
which equals one minus the YES bid. No forecast model is used by these policies.

Policy **A**, `13ed692131c09392`, holds until settlement. Policy **B**,
`77643c1015d10d39`, schedules a sale three hours after entry; if a usable scheduled
exit quote is unavailable or the market is closed, the inherited policy holds to
settlement. This fallback matters: B's best stress account had **207 settlements
and only 30 scheduled sales**. Calling all 237 positions three-hour trades would
misdescribe the account. Definitions are in
[`trading_research.py`](../weatherpred/trading_research.py) and
[`e013_autoresearch.json`](../config/e013_autoresearch.json).

The costed case delays entry one hour and adds one cent of slippage. The stress
case delays entry two hours and adds two cents. Both require the later entry
price to satisfy the limit frozen at the signal. Both assume aggregate taker
fees with coefficient 0.07 and cent-rounded account cash movements. These fees
and fills have not been verified against contemporaneous depth and fee history.

## Every frozen size and scenario

Each cell below is an ending **training cash** balance, including the listed
fees. There are no unresolved positions at the training cutoff in these accounts.
An asterisk means the 20% drawdown rule stopped subsequent orders at some point;
it does not mean losses were capped at exactly 20%.

| Policy | Order risk fraction | Costed ending cash | Costed fees | Stress ending cash | Stress fees |
| --- | ---: | ---: | ---: | ---: | ---: |
| A: settlement | 0.5% | $198.16 | $13.21 | $196.25 | $9.06 |
| A: settlement | 1% | $201.68 | $20.91 | $196.03 | $14.38 |
| A: settlement | 2.5% | $232.15* | $31.32 | $215.57* | $23.98 |
| A: settlement | 5% | **$341.87** | $33.24 | $278.56* | $28.01 |
| B: scheduled 3h | 0.5% | $184.68 | $15.06 | $190.95 | $10.77 |
| B: scheduled 3h | 1% | $167.80 | $24.53 | $185.19 | $17.57 |
| B: scheduled 3h | 2.5% | $161.76* | $12.45 | $205.01* | $26.47 |
| B: scheduled 3h | 5% | $164.77* | $9.67 | **$280.13*** | $31.92 |

The risk fraction is a maximum order budget, not a promised return or estimated
loss probability. Integer quantities, order reservations, the 10% shared weather
exposure cap, earlier cash releases and the drawdown stop all affect which later
orders fit. All cities share this exposure cap. The account processes releases,
then decisions ordered by trade ID, then entries at a common timestamp. That
deterministic ordering can favor some cities when capital is scarce.

Consequently changing the risk fraction changes the selected **portfolio**, not
merely its scale. A's costed 0.5%, 1%, 2.5% and 5% accounts filled **852, 854, 524
and 318** orders respectively. Their release-day counts were **240, 240, 227 and
205**. At 5%, 1,020 potential orders were rejected by the cluster cap; 336 of the
607 reservations were themselves sized by that cap. At 0.5%, all 1,617
reservations were limited by the per-order risk budget and ten attempts could
not afford one integer contract. Small balances also make cent rounding material.
Scaling a historical $100 profit or assuming that doubling risk doubles return
would omit these changes.

Stress is also **not** a fee-only deduction from a fixed list of trades. Its
one-hour later entry tests a different quote, changes which limits fill, moves
the scheduled exit time, and interacts with the same capital constraints. For B
at 5%, the costed path stopped on February 20 after 70 trades across 40 release
days; the stress path continued until August 18 and completed 237 trades across
169 release days. The stress path's larger ending cash does not show that higher
trading costs are beneficial. It shows substantial path dependence and the need
to validate execution assumptions.

## Profit concentration and asymmetric losses

The following tables include every training month and city at the 5% size, for
both policies and both scenarios. All 16 accounts' complete daily, monthly and
city contributions, including zeros, are retained in the JSON report. A row is
assigned to its actual UTC cash-release date, not its signal date. Fees attached
to a released trade are included in that trade's contribution; the report also
retains daily fees charged and daily cost-equity changes separately.

| Release month | A costed | A stress | B costed | B stress |
| --- | ---: | ---: | ---: | ---: |
| January | $6.55 | -$0.13 | -$15.01 | -$0.13 |
| February | $14.13 | $21.28 | -$20.22 | $21.28 |
| March | -$0.76 | $18.80 | $0.00 | $17.12 |
| April | $11.78 | $25.61 | $0.00 | $22.55 |
| May | $50.22 | $46.17 | $0.00 | $44.21 |
| June | $41.22 | $23.68 | $0.00 | $30.58 |
| July | $27.58 | -$14.44 | $0.00 | -$14.17 |
| August | $7.78 | -$42.41 | $0.00 | -$41.31 |
| September settlement buffer | -$16.63 | $0.00 | $0.00 | $0.00 |
| **Total** | **$141.87** | **$78.56** | **-$35.23** | **$80.13** |

| City / series | A costed | A stress | B costed | B stress |
| --- | ---: | ---: | ---: | ---: |
| Austin / KXHIGHAUS | $0.67 | $1.91 | -$0.81 | $1.94 |
| Chicago / KXHIGHCHI | $0.33 | -$1.46 | -$0.74 | -$0.77 |
| Denver / KXHIGHDEN | $9.59 | $0.00 | -$20.17 | $0.00 |
| Legacy Houston / KXHIGHHOU | $0.00 | $0.00 | $0.00 | $0.00 |
| Los Angeles / KXHIGHLAX | $14.56 | $10.58 | $4.07 | -$7.15 |
| Miami / KXHIGHMIA | $106.94 | $128.43 | -$9.54 | $144.31 |
| New York / KXHIGHNY | $7.58 | -$60.15 | -$6.96 | -$57.40 |
| Philadelphia / KXHIGHPHIL | $2.20 | -$0.75 | -$1.08 | -$0.80 |

The earlier training universe included legacy Houston even though it is absent
from the seven-city continuation manifest. It is explicitly included here;
these four accounts happened to allocate it zero trades.

Miami contributed **75.4%** of A's costed net profit. May and June contributed
**64.5%**, counted across all cities; these overlaps must not be added together.
For B's stress account, Miami earned $144.31 while the other cities together lost
$64.18. This suggests a station or weather-regime hypothesis to test on new data.
It does **not** justify selecting Miami after seeing these results and reporting
that selection as an independently validated strategy.

A's costed account had 318 distinct events on 205 release days, with 3,271
contracts. There were 270 profitable trades, 46 losing trades and two flat trades:
an 84.9% trade win rate. The average winner earned **$1.73**, while the average
loser lost **$7.09**. Total winning-trade profit was $468.16 against $326.29 of
losing-trade losses. A high hit rate alone is insufficient when each loss costs
about four average wins.

For this settlement-only account the arithmetic is particularly transparent:

```text
Total net profit = sum(quantity × settlement payout)
                   − sum(quantity × entry price) − all fees
                 = $2,856.00 − $2,680.89 − $33.24
                 = $141.87
```

The quantity-weighted observed settlement success fraction was 87.31%, against
an average acquisition cost including fees of 82.98 cents per contract. These
are weighted statistics of the already observed sample, not an estimated future
win probability. The fees consumed **19.0%** of gross pre-fee profit for A
costed and **28.5%** for B stress. Reducing fees could matter economically, but
the existing comparison does not demonstrate any achievable fee reduction.

The best five release days contributed $48.23, or 34.0% of A costed's final net
profit; the best ten contributed $86.46, or 60.9%. For B stress those figures were
$50.29 (62.8%) and $83.28 (103.9%). The latter exceeds 100% because losses on
other days offset gains. These are contribution summaries, not counterfactual
replays with favorable or unfavorable days removed.

## Drawdowns, stops and reserved capital

| At 5% risk | A costed | A stress | B costed | B stress |
| --- | ---: | ---: | ---: | ---: |
| Closed trades / release days | 318 / 205 | 250 / 178 | 70 / 40 | 237 / 169 |
| Maximum cost-basis drawdown | 16.78% | 22.13% | 24.07% | 20.69% |
| Maximum drawdown marking all held contracts zero | 24.09% | 26.55% | 27.51% | 25.80% |
| Worst release day | -$17.86, Jul 31 | -$31.31, Aug 21 | -$9.93, Jan 29 | -$16.03, Jul 27 |
| Worst consecutive seven release-calendar days | -$45.00, Aug 21–27 | -$54.32, Aug 21–27 | -$29.11, Jan 24–30 | -$42.59, Jul 12–18 |
| Maximum reserved cash | $38.74 | $34.25 | $21.61 | $34.25 |
| Maximum held purchase principal | $39.34 | $33.41 | $21.24 | $33.41 |
| Maximum simultaneous held positions | 4 | 5 | 4 | 4 |
| Reserved orders | 607 | 608 | 152 | 602 |
| Reservations sized by order risk / cluster cap | 271 / 336 | 325 / 283 | 89 / 63 | 337 / 265 |
| Rejected by cluster cap | 1,020 | 992 | 194 | 936 |
| Rejected at entry limit | 255 | 214 | 76 | 218 |
| Missing entry quote after reservation | 34 | 144 | 6 | 147 |

No reserved order in any of the 16 accounts reached the assumed 100-contract
capacity ceiling, and none of their filled quantities was reduced after
reservation. That does not prove those smaller orders would have filled: all
historical depth remains unknown. The binding capital restriction was usually
the per-order risk budget or shared cluster cap, not the arbitrary quantity cap.

Cost-basis equity counts held positions at purchase principal and expenses fees
when charged. It can hide changes in their liquidation value. Marking every held
contract zero is a deliberately conservative lower mark, not an observed
liquidation price. Neither measure should be presented as a complete historical
mark-to-market account.

The 20% stop cancels pending orders and prevents later orders. It does not undo
already held contracts or force an assumed immediate sale. A discrete settlement
loss can jump across 20%, and existing holdings can then add losses. All seven
triggered cases are retained below; times are UTC.

| Policy / scenario / size | First trigger | Drawdown at trigger | Later-held trades' full net P&L | Maximum eventual cost drawdown |
| --- | --- | ---: | ---: | ---: |
| A costed 2.5% | Aug 27 12:02:00.697426 | 21.09% | -$3.68 across 2 trades | 22.27% |
| A stress 2.5% | Aug 26 12:01:30.706876 | 21.05% | -$3.22 across 3 trades | 22.71% |
| A stress 5% | Aug 27 12:02:00.697426 | 22.13% | +$3.48 across 1 trade | 22.13% |
| B costed 2.5% | Mar 2 16:21:57.688393 | 20.13% | +$0.92 across 3 trades | 20.13% |
| B costed 5% | Feb 20 15:44:49.089845 | 23.74% | -$0.73 across 1 trade | 24.07% |
| B stress 2.5% | Aug 18 10:00:00 | 20.21% | -$0.14 across 1 trade | 20.25% |
| B stress 5% | Aug 18 10:00:00 | 20.05% | -$2.52 across 1 trade | 20.69% |

The last row is a concrete example. A Los Angeles entry fee crossed the threshold:
cost equity became $282.41 against a $353.22 peak, with $13.50 of principal still
held. Its later sale completed the trade at a $2.52 net loss. The account's final
cash was $280.13. The change from trigger equity differs from the full trade loss
because its entry fee was already expensed when the trigger occurred.

## Why the uncertainty gate still selects cash

E023 compared 576 policies × four sizes × two scenarios: **4,608** daily return
streams, or 2,304 policy-size pairs. It resampled shared seven-day blocks over
248 calendar days, preserving simultaneous movements across candidates, with
10,000 resamples. Its observed family critical value was **3.969145**. This is
larger than a single-strategy uncertainty multiplier because many alternatives
were examined. The historical training account bodies and their exact arithmetic
can be correct while the evidence for a repeatable edge remains weak.

For each candidate, the existing method calculates:

```text
lower mean daily log growth
    = observed mean − family critical value × bootstrap standard error
```

For A costed at 5%, this is
`0.002161747 − 3.969145 × 0.001116442 = −0.002269574`.
For B stress at 5%, it is
`0.001358615 − 3.969145 × 0.000984762 = −0.002550048`.
Both lower bounds are negative despite positive ending cash. These figures were
read from the frozen parent result; this drilldown did not resample or change the
candidate family. **All 2,304 costed pairs failed the simultaneous-bound gate**.
The selected action remains cash. Parent method:
[`bankroll_selection.py`](../weatherpred/bankroll_selection.py); portfolio and
selection rules: [`e023_bankroll_replay.json`](../config/e023_bankroll_replay.json).

These training winners merit a narrowly registered replication that retains all
cities and reports Miami/regime concentration, verifies receipts and depth, and
uses the unchanged capital and execution rules. The current evidence supports
testing that explanation on new data. It does not support increasing the size,
loosening the exposure cap, cherry-picking Miami, assuming maker fills, or
extrapolating a year-end balance or ruin probability from these results.

## Verification

The diagnostic independently reconciled cash from every reservation, entry and
release in all 16 immutable account ledgers. Final cash, closed-trade P&L,
entry/exit fees, daily/monthly/city sums and every first drawdown trigger matched
exactly. It did not rerun or modify the frozen strategy or account code.

```text
{"report_record_id": 101221, "accounts": 16,
 "checks": "All16 exact ledger cash, closed PnL, entry/exit fees, month/city/day sums and kill timestamps reconciled"}

2 passed in 0.02s
All checks passed!
2 files already formatted
```

The regression tests cover an entry fee crossing the stop threshold followed by
a held-position loss, and rejection of a one-cent cash discrepancy. Complete
daily and rolling-week rows are in the JSON export, not only the favorable cases.
