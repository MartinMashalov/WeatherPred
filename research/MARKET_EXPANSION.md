# Additional markets and data: measured findings

As of September 6, 2026, approximately 17:08–17:18 UTC, the expansion screen found **no new executable breakthrough**. It did find a useful public hourly data source covering 37 stations and two additional structural mechanisms worth monitoring. These are discovery results; no private account endpoint or real order was used.

## What was actually checked

The existing catalog contained 367 climate/weather series. Before inspecting new quotes, the screen selected all originally open minimum-temperature, monthly-rain and weekly heat-streak series, three annual Atlantic storm-count series, and **all 34 snowfall series**, including those previously inactive. The selection contains 82 series. It is not a new exhaustive census of the entire exchange.

Public GET requests were spaced at least 0.6 seconds apart within this process. Each response was retained in the append-only local archive. All cursors were exhausted. Current series fees and event fee changes were fetched. Order-book batches had to contain every requested market and pass request-duration/server-date/cache-age checks. Crossed books would have been rejected.

| Market family | Open contracts examined | Books with both a bid and ask | Median spread among those books |
|---|---:|---:|---:|
| Minimum temperature (288 daily, 1 monthly) | 289 | 174 | 6¢ |
| Monthly rainfall | 82 | 63 | 3¢ |
| Weekly heat streaks | 50 | 3 | 11¢ |
| Annual Atlantic storm counts | 25 | 18 | 2¢ |
| Snowfall | 0 | 0 | Not applicable |

There were **48 active series, 72 events, 446 contracts and 446 valid book responses**, with zero acquisition errors. Only three of the 50 weekly contracts had two-sided markets: displayed activity or cumulative volume would have substantially overstated usable liquidity. The minimum-temperature count includes 288 daily contracts across 24 cities and two trading dates, plus one monthly San Jose minimum-temperature contract.

Evidence: report record **78957**, `reports/market_expansion.json`; raw source IDs are individually listed there. Book batches are **78942, 78948, 78954, 78955 and 78956**. The acquisition ran from `2026-09-06T17:08:41.616851Z` to `17:11:29.720855Z`. Its prices are observations from those times, not current trading instructions.

## Ranked mechanisms

### 1. Rain-calendar equivalence remains the strongest lead

The separately registered E019 paper experiment is the immediate priority. If Saturday is confirmed dry under compatible source rules, a contract for rain on either Saturday or Sunday and a contract for rain on Sunday have the same weather outcome. Buying YES on one and NO on the other can create a conditional fixed payout when their combined purchase price and fees are below $1.

The binding questions are execution of both legs, capital tied up across different series, available size, partial-fill exposure and exceptional settlement. The E019 journal and audit are authoritative for its current fills and outcome; this expansion study does not add hypothetical profits to that journal.

### 2. Weekly heat streaks: derive the remaining state before forecasting

The `KXAVGTK…` series names are misleading if read casually: these markets are about **consecutive days**, not whether the week's average temperature exceeds a threshold. The current contracts ask for at least two through six consecutive days whose rounded daily average exceeds 90°F.

The rules define a day's temperature as the arithmetic mean of the hourly values published by The Weather Company (TWC), from 00:00 through 23:59 local time, rounded to a whole degree. Fewer than 18 hourly values breaks a streak. They use the data version available at contractual expiration and disregard later revisions. A source row marked `settled` therefore does **not** remove the risk of changes before expiration.

For hour values \(T_{d,h}\), define

\[
q_d=\mathbf{1}\left\{n_d\ge18,\;\operatorname{round}\left(\frac{1}{n_d}\sum_hT_{d,h}\right)>90\right\}.
\]

Let \(L\) be the longest consecutive run of \(q_d=1\) within the contractual week. Contract \(k\) pays \(\mathbf{1}\{L\ge k\}\). Completed days narrow the possible values of \(L\); unresolved days are enumerated as both true and false. This gives a **conditional payout range**, without needing a sophisticated temperature forecast.

The new source is [TWC's public weekly hourly table](https://weather.com/kalshi/api/metar?primary=true&weekStart=2026-08-31). Archive record **78077**, received on September 6, contains **5,443 hourly observations across 37 stations**, with local hour/date, Fahrenheit temperature, observation time and pending/settled status. It also supplies daily averages and hour counts. The full result is in `reports/market_expansion_weekly_weather.json`.

The replay independently calculated completed-day means from hourly records. It kept the current day and insufficient-hour days unknown, rejected duplicate local hours, and checked that the source arrived before the book. It evaluated all 50 contracts under 0°F, ±0.5°F and ±1°F perturbations of completed-day means. These perturbations test sensitivity; they are not statistical confidence bounds on revisions.

Under the received data, nine cities cannot reach two consecutive qualifying days. Phoenix already has four consecutive qualifying days and can finish with four or five. At zero perturbation, 49 of 50 contracts have a conditional known outcome. At ±1°F, 47 do.

That knowledge is largely already reflected in the books:

- Houston's five-day contract offered 12.20 contracts of NO at 99¢. One contract costs **$0.990700 including the modeled current fee**, leaving **$0.009300** if its conditional outcome holds. Ten contracts leave **$0.093000**. One cent of adverse price movement removes the opportunity.
- Phoenix's six-day NO offered the same 0.93¢ conditional margin at the snapshot, but its conclusion does not survive the ±0.5°F mean perturbation because one earlier day is close to the rounding boundary.
- The already supported Phoenix two-, three- and four-day YES contracts had **no available YES ask**. A favorable probability does not create a purchase opportunity.
- **Zero weekly candidates survive the half-depth plus 1¢-slippage scenario**, including the variants with ±1°F mean perturbation.

Evidence: original discovery replay **79338**; the current deterministic replay after validation is **80070**, `reports/weekly_streak_bounds.json`. These are repeated calculations from the same observations, not independent experiments.

**Next experiment:** collect weekly state and book receipts prospectively from Monday onward, and register a passive-order policy before testing it. Compare a rule-state-only policy with a policy forecasting only the remaining unresolved days. Record the opportunity-to-fill conversion rate; do not turn nonexistent asks into fills. This is lower priority than E019 today because current executable returns are tiny and fragile.

### 3. Monthly rainfall: observed accumulation plus a forecast of what remains

For compatible daily amounts \(R_d\), a monthly total can be written

\[
R_{\text{month}}=A_t+R_{\text{remaining}},\qquad A_t=\sum_{d<t}R_d.
\]

If amounts remain nonnegative and the published past reports remain valid, any threshold already below \(A_t\) is exceeded. For unresolved thresholds, model the distribution of **additional** rain: an explicit probability of no rain plus a positive-amount distribution, with common storms linking nearby days and cities. Binary daily rain contracts disclose occurrence, not amount, so they cannot simply be added to monthly threshold contracts.

New [TWC daily source](https://weather.com/kalshi/api/climate/primary?date=2026-09-05) receipts for September 1–6 are **79581, 79582, 79583, 79617, 79618 and 79619**. September 6 lacked daily source reports at this retrieval; missing current-day data was not treated as zero. All ten monthly stations had numeric official values for the preceding five dates.

| Station | Published September 1–5 accumulation |
|---|---:|
| Houston Hobby / CLIHOU | 2.43 inches |
| Miami / CLIMIA | 3.00 inches |
| New York Central Park / CLINYC | 0.89 inches |
| Seattle / CLISEA | 0.36 inches |
| San Francisco / CLISFO | 0.01 inches |
| Austin, Chicago O'Hare, Dallas, Denver, Los Angeles | 0.00 inches each |

The Houston and Miami one- and two-inch thresholds were already crossed by these published amounts. **All four had no executable YES ask** in a fresh book batch obtained after the weather sources. Miami's exactly 3.00 inches does not satisfy a strictly-greater-than-3 threshold. This is an efficient-market result, not an arbitrage.

Evidence: source collection **79620**, bounds report **80450**, fresh book batch **80449**, `reports/monthly_precipitation_bounds.json`. This additional acquisition checks displayed absence of asks; it does not model fills or establish that monthly and daily source revisions can never diverge.

**Next experiment:** use archived ensemble precipitation forecasts and radar/observations to forecast remaining rainfall, and test calibration at the listed thresholds. Monthly spreads are narrower than minimum-temperature spreads in this snapshot, but a September position may tie up funds until October, with some expiration limits in mid-October. Capacity and growth must include that holding period.

### 4. Nested thresholds and annual storm counts: attractive structure, no current discount

If event \(A\) implies event \(B\), then

\[
\mathbf{1}_{\neg A}+\mathbf{1}_B\ge1.
\]

Buying NO(A) and YES(B) therefore has a conditional minimum $1 payout. For the same event, a higher rainfall/streak threshold implies a lower one only when the station, interval, threshold semantics and source conventions match. Across annual Atlantic counts, major hurricanes are a subset of hurricanes, and hurricanes are a subset of storms reaching 39 mph, provided the dates and NHC source definitions match.

The reusable screen checked exact normalized primary rules and matching secondary rules for within-event implications. It checked explicit category/wind-speed definitions, dates and event source agencies for cross-series count implications. The 25 current count definitions passed the stricter semantic parser.

There were **525 within-event comparisons plus 16 cross-series count comparisons: 541 total**. None had a positive quoted payout floor after current fees, even before the 1¢ and 2¢ slippage stresses. These many correlated comparisons are not 541 independent observations of profitability. Cross-batch comparisons are asynchronous and would still need a joint execution test if a future opportunity appears.

**Next experiment:** monitor a graph of exact contract implications, score complete portfolios by their worst permitted payout, and test only discounts that remain after fees, conservative fill sizes and the cost of unmatched legs. This combines established logical-arbitrage and execution methods. No claim that it has never been done before is warranted.

### 5. Daily minima and broader station data: forecast selectively

The fresh minimum-temperature sample has a median 6¢ spread among its two-sided books. That is a substantial cost before fees or model uncertainty. A useful model would need to identify particular conditions—overnight cloud changes, dew-point constraints, cold-front arrival, wind shifts—where probability improvements exceed this cost.

Minimum temperature can only fall as genuine observations accumulate, but provisional-source conversion, station mismatches and later corrections can invalidate a naive observed bound. The existing E014 result already demonstrates why a small arbitrary correction buffer is not enough to call such trades certain. This study therefore does not label current temperature extrema an arbitrage.

The 37-station hourly source is useful for learning and testing local temporal patterns, subject to verifying the contractual target. Its weekly averages are not automatically interchangeable with the proprietary Miami hourly index. Prior data retrieved today also lacks historical publication timestamps; future receipts are needed before claiming a latency advantage. Houston's weekly series uses **KIAH**, while the rainfall series uses **KHOU/CLIHOU**. Chicago rain uses **KORD/CLIORD**; the daily-high experiment uses **KMDW**. Pooling these station names would silently change the target.

## Reproduction and validation

The bounded live discovery command is:

```sh
uv run python research/probes/market_expansion.py
```

It makes public GET requests and appends evidence. It does not submit orders. The weekly calculation uses only the already archived source and books:

```sh
uv run python research/probes/weekly_streak_bounds.py --source-id 78077
```

The source ID is explicit so a later weather revision cannot silently change the replay. Tests cover consecutive-day dependence, unknown/future/pending observations, insufficient hours, changed source conventions and changed hurricane count definitions. Actual checks in this session:

```text
All checks passed!
....                                                                     [100%]
4 passed in 0.03s
```

The untouched October–December 2025 holdout was not read. No sources pinned by E009, E015, E016, E017, E018 or E019 were modified. Profitability remains unproven; the strongest result of this expansion is a richer settlement-aligned data source and clear evidence about which apparently attractive trades have no usable price or margin.
