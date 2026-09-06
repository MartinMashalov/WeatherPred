# Evidence available for a $200 rolling-year simulation

The requested calendar is provisionally September 6, 2025 through September 5,
2026, inclusive: 365 days. A full-year executable simulation is not currently
supported. The local inventory reads SQLite record metadata and previously
produced aggregate coverage files. It opens **zero archived response bodies**,
including zero sealed October–December 2025 outcomes or prices. It computes no
new strategy scores or returns.

## Coverage already present

| Period / dataset | Evidence available | Practical limitation |
| --- | --- | --- |
| September 6–30, 2025, seven daily maximum-temperature series | 175 events, 1,050 contracts, 23,535 hourly candles; all 1,050 requests have at least one candle | 25 event dates, only 23-hour request windows; no historical depth, queue position or verified public receipt time |
| October–December 2025 | Existing aggregate inventory records 644 daily-high events across these seven cities; 133 daily-low events also present | 92 days remain sealed. Presence is not authorization to inspect outcomes or prices |
| January 1–September 5, 2026, daily maximum temperature | Fresh metadata-only census confirms every one of 248 dates in all seven series: 1,736 events | Prices, complete contract metadata and source histories still require acquisition; a few old canaries do not fill this gap |
| September 1–5, 2026, Miami hourly index | 118 one-hour candle requests, ending September 1 00:00 UTC through September 5 23:00 UTC | Only 1,219 of 3,510 contract-horizon pairs have usable two-sided quotes; 2,291 are absent, stale or one-sided |
| Small 2026 candle canaries | Five unique contracts, ten requests including duplicates; dates July 1, August 1, September 1 and September 5 | Selected examples, not a complete market universe |
| Executable depth / prospective paper fills | First local order-book receipt September 6, 2026 at 10:21:57 UTC | All receipts fall after the requested year; **0 of 365 days** have the depth evidence needed for complete executable replay |

The seven daily series are `KXHIGHAUS`, `KXHIGHCHI`, `KXHIGHDEN`,
`KXHIGHLAX`, `KXHIGHMIA`, `KXHIGHNY`, and `KXHIGHPHIL`. This universe comes
from the actual earlier development dataset, not from cities selected by later
profitability. The 25-day overlap is 6.85% of the requested year; 92 days are sealed
and the other 248 days need the broader daily dataset. Those categories describe
the daily study, not proof that every quote endpoint in the first segment can fill.

Detailed source IDs and counts are in `reports/bankroll_data_audit.json`.
`reports/E002_acquisition.json` supplies candle counts without opening price
bodies. `reports/E002_coverage.json` supplies sealed-quarter aggregate presence.
The 1,050 daily candle blobs occupy 7,706,130 uncompressed bytes, with a 7,350-byte
median, measured using filesystem metadata only.

Weather inputs also need date and contract alignment. Existing archived NBM
forecast work covers January–September 2025, including the 25-day overlap, with
273 original daily cycles and 546 additional cycles. It does not supply a 2026
forecast history. Newly collected TWC station snapshots contain 50,771 retained
hourly observations at 31 stations, June 1–August 30, 2026, acquired today; May
was empty. These observations are retrospective current versions, not evidence
that a strategy could have received them historically. The earlier Miami index
experiment similarly has current retrospective data and missing receipt metadata.

## What the earlier experiments actually calculate

E013 tests 576 finite policies under three execution-cost assumptions. Its inputs
are January–September 2025. E014 tests 36 observation-bound policies under three
cost assumptions using the same development quote period. Neither is a complete
rolling-year simulation, and neither establishes profitable fills.

The E013/E014 portfolio starts with $100 and buys one conditional contract per
eligible event. It enforces event exposure of 5%, a common city-cluster exposure
of 10%, and total exposure of 25%; the common 10% limit is the binding combined
limit. Cash stays locked until the specified sale or actual settlement timestamp.
Unreleased positions remain at their purchase cost. Consequently the reported
cost-basis equity and realized drawdown are not a liquidation value or a complete
mark-to-market drawdown. Starting at $200 changes eligibility and capital
constraints; multiplying old $100 profits by two is not a valid replay.

Entry and quoted exits use an explicitly assumed taker fee of
`0.07 × price × (1 − price)` per contract, with each separate unit order's cash
debit rounded to cents. That is an assumption, not verified historical fee terms.
The costed case includes one-cent slippage; the stress case includes two cents
and a longer entry delay. Exact later candle endpoints must satisfy the original
order limit. Missing exit quotes fall back to actual settlement, and settlement
does not incur a sale fee. No candle midpoint, high/low or traded volume proves a
fill. Candle endpoints lack available size, quote age and maker queue history.

E013's monthly selector uses only positions released before each selection date,
requires 30 prior trade days, penalizes mean daily log growth by two estimated
standard errors, and requires positive stress-case performance. The best strategy
chosen using the completed year would instead be a hindsight optimum. Any new
bankroll report must identify these separately, preserve every failed candidate,
and freeze the selector, sizing, fees, costs, no-trade rules and calendar before
scoring new evaluation data. Cash is a legitimate frozen decision.

## Public acquisition route

Kalshi retains old event metadata on the ordinary event endpoint, while markets
and candles move to the historical tier according to settlement time. A fresh
public cutoff request, source record **92386**, returned **July 8, 2026 00:00 UTC**.
[Historical data guide](https://docs.kalshi.com/getting_started/historical_data),
[cutoff API](https://docs.kalshi.com/api-reference/historical/get-historical-cutoff-timestamps).

The metadata census was registered as **92385** before collection and archived as
**92494**. It made 14 non-nested event requests and one cutoff request, with zero
failures. Each series returned 251 metadata events; filtering their event dates
kept exactly 248. No nested market objects, outcomes or prices were acquired.
The manifest is `reports/bankroll_event_metadata.json`.
[Event API](https://docs.kalshi.com/api-reference/events/get-events).

Historical market listing documents exact `event_ticker` and `tickers` filters,
but no date filter. Therefore request only an event already accepted by the
January–September 2026 manifest; do not request an entire historical series and
then filter its outcome-containing body. Historical single-market candles are
available at one-, 60- or 1,440-minute intervals. Recent batch candles allow up to
100 contracts and 10,000 aggregate rows.
[Historical markets](https://docs.kalshi.com/api-reference/historical/get-historical-markets),
[historical candles](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks),
[batch candles](https://docs.kalshi.com/api-reference/market/batch-get-market-candlesticks).

Before full acquisition, two fixed NYC event canaries test January 1 on the old
tier and September 1 on the recent tier. The documented event-level candle API
could reduce requests if it also returned archived markets, but that support must
be observed rather than assumed.
[Event candles](https://docs.kalshi.com/api-reference/events/get-event-candlesticks).

The intended bulk scope is every contract in the 1,736-event manifest, hourly
quotes over its available open-to-close interval, strictly bounded by January 1
00:00 UTC and September 6 00:00 UTC. Any excluded boundary interval is retained
as a coverage limitation. Post-year settlement timestamps may remain in metadata
so cash cannot be released before the year ends by mistake; post-year prices are
excluded. Source code and the event manifest must be pinned before price
acquisition, with a file lock, resumable receipts, a stop file, bounded requests,
and all errors retained. Acquisition does not authorize scoring the new data.

Public trade history can supplement candle diagnostics, but it cannot reconstruct
an unobserved order book or demonstrate where a hypothetical limit order sat in
the queue. Historical public receipt and fee/source revisions remain separate
acquisition gaps even after quote collection.
[Historical trades](https://docs.kalshi.com/api-reference/historical/get-historical-trades).

## Acquisition launched September 6, 2026

Route probe protocol **92869**, report **92875**, retained all five responses:
January 1 NYC metadata **92870** contained six contracts; the event candle route
**92871** returned HTTP 200 with zero contracts/candles; the historical single
contract route **92872** returned 29 hourly candles. September 1 metadata
**92873** contained six contracts and recent batch **92874** returned all six,
with 182 candles. Therefore the old tier uses one request per contract; a
successful-but-empty event response cannot establish absent historical trading.

The canary also revealed that the old endpoint includes a candle ending exactly
at `start_ts`. Its January 1 00:00 candle could summarize the prior December 31
hour in the January 1 contract. It is retained in the raw canary and excluded
from evaluation; this was not a sealed fourth-quarter event. The bulk request
starts one second after `max(open_time, January 1 00:00 UTC)` so every returned
hourly endpoint lies strictly after that lower bound. The requested upper bound
remains inclusive September 6 00:00 UTC, covering the final September 5 hour.

**Protocol 99015** freezes the complete event manifest, this scope, request
interval of at least 0.5 seconds, maximum two transient retries, and the collector
plus HTTP/archive/time source hashes. No experiment scores are authorized.
Collector: `research/probes/bankroll_acquisition.py`. Its initial bounded run,
archived as **99031**, acquired one event, six contracts and 170 candles with
seven GET operations and zero failures; checkpoint **99030** was reused on
resume. This is an acquisition test, not a performance result.

The full run started at **18:08:25 UTC**, PID **67831**, execution session
**64640**. At **18:08:50 UTC** it had eight complete events, 48 contracts,
1,411 candles and zero failed events. Progress is written atomically after each
event to `reports/bankroll_acquisition_progress.json`; the process log is
`reports/bankroll_acquisition_run_output.txt`. The append-only archive retains
raw responses and per-event receipts. These counts are a timestamped checkpoint,
not a completion claim. The expected request volume is roughly ten thousand;
historical single-contract calls dominate its runtime.

Do not edit the collector or its pinned dependencies while the run is active.
Creating `data/STOP_BANKROLL_ACQUISITION` stops before the next request. Resume
the identical source with `uv run python research/probes/bankroll_acquisition.py`;
it rejects changed pins and reuses successful receipts. Failures remain recorded,
and empty candles remain empty. A completed download will still lack historical
depth, complete quote age and contemporaneous fee/source revision proof.

Checks run in this session:

```text
17 passed in 0.04s
All checks passed!
5 files already formatted
```

These checks cover the station parser and acquisition boundaries, including
sealed-quarter/date rejection, the start bucket, the final year bucket and
duplicate candle endpoints. The initial live acquisition and resumed checkpoint
also ran against the public API. No $200 balance or profit was calculated.
