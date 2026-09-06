# A $200 account: measured results and missing annual evidence

E023 tested 2,304 policy-and-size pairs under two execution-cost assumptions:
**4,608 simulated training accounts**. Its predeclared selector chose cash.
Every costed candidate failed the simultaneous lower-bound criterion. This is
not proof that cash is globally optimal; it is the decision supported by this
finite selection rule and the evidence available to it.

The requested year is September 6, 2025 through September 5, 2026, with UTC
account boundaries. The complete annual **trading** balance is not computed.
The existing comparable daily-market panel supplies only September 6–30, 2025:
25 of 365 event dates. The selected cash account remains $200 over that panel.
An actual 365-day replay of the cash-only benchmark also ends at $200, assuming
zero interest, account costs and external cash flows. It is a benchmark, not
an estimate of what an optimal trading strategy could have earned.

## Experiment and accounting

Registration **93872** precedes all new account scores. Report **98830** retains
the complete results. The 576 price-based policies come from E013; four risk
budgets—0.5%, 1%, 2.5% and 5%—are evaluated with 1¢/one-hour and 2¢/two-hour
slippage/delay scenarios. These are hourly candle sensitivities, not measured
realistic exchange delays. There are 4,608 alternative accounts, not 4,608
independent histories or a combined investment portfolio.

Each account starts with $200. Orders reserve their limit-price debit before
later quotes are observed. Missing quotes and unfilled limits release that
reservation at entry time. Integer fills cannot exceed the decided quantity,
cash, remaining event/cluster exposure or the explicit assumed 100-contract
capacity ceiling. Entry and sale fees are recalculated on aggregate order
quantity; the previous one-contract P&L is never multiplied by two.

All cities share a 10% weather exposure limit and each event a 5% limit. Entry
fees reduce equity immediately. Cash remains unavailable until the recorded
sale/settlement and the supplied proceeds-availability time. Missing terminal
endpoints keep positions open. The report distinguishes cash, reserved cash,
held purchase principal and a conservative value that marks every open holding
at zero. Purchase-cost equity does not measure liquidation value.

A 20% cost-based drawdown stops new orders and cancels unfilled reservations.
Previously held positions can still lose afterward: the lowest training account
ends at $145.37. A drawdown stop is not a guaranteed maximum-loss boundary.

## Selection and result

Training orders must be decided before September 1, 2025. Only cash flows
released before September 6 affect selection; the five-day gap allows normal
settlements to mature. All candidates share the same 248 UTC accounting days.
The earlier data was already used in development research, so this selection
exercise does not establish independent statistical validity.

A shared seven-day circular bootstrap uses 10,000 resamples across all 4,608
streams. The simultaneous critical value is **3.9691445666**; 4,140 streams have
nondegenerate variability and 468 cannot establish a positive bound. Selection
requires a positive costed lower bound, positive stressed profit, at least 60
release days and no unresolved training cash flows. The choice is archived
before running the selected account on the requested-period subset.

| Training diagnostic | Costed | Stressed |
| --- | ---: | ---: |
| Positive ending balances above $200 | 218 / 2,304 | 196 / 2,304 |
| Highest ending cash, selected after looking at training | $341.87 | $280.13 |
| Lowest ending cash | $145.37 | $148.06 |
| Highest simultaneous lower daily log-growth bound | −0.0000015721 | −0.0000017733 |

The two largest balances belong to different policies. The $341.87 training
account buys NO on a contract whose midpoint is at most 30%, signals 24 hours
before the source day ends, allows an 8¢ spread and holds to settlement, with a
5% order budget. Its apparent profit is a research lead, not the requested year's
return. No winner is selected from September 2025 evaluation profit.

A separately declared drilldown finds that Miami supplies 75.4% of that
account's net profit, while average losses cost roughly four average wins.
Changing size changes its city allocation, rather than merely scaling the same
trades. The policy with the largest stressed balance ends at only $164.77 in
the ordinary cost scenario because the different entry delay, fill decisions
and drawdown stop produce different portfolios. See
[all 16 declared diagnostic accounts](BANKROLL_DIAGNOSTICS.md) and
[machine-readable breakdown](../evidence/E023_diagnostics.json).

All 2,304 costed bounds fail. Separately, 2,108 pairs fail positive stressed
profit and 1,944 lack 60 release days; rejection categories overlap. No trading
policy qualifies. Both cost scenarios and both availability modes leave the
selected 25-day account at $200 with zero orders.

## What remains unknown

- Historical candles provide neither order-book depth nor verified historical
  receipt times. Hypothetical mode labels its assumptions; verified mode refuses
  unverified inputs and requires depth for entries and quoted exits.
- The assumed quadratic fee coefficient is 0.07, with aggregate account rounding
  to cents. Complete contemporaneous fee evidence for the requested year is
  missing. Current fee documentation cannot silently establish old fees.
- The sealed October–December 2025 quarter is untouched. A separate registered
  collector is downloading all 1,736 daily events across seven cities for
  January 1–September 5, 2026. It preserves errors and actual September receipts.
- Missing months are not imputed as zero-return days. Annual trading balance
  stays null until a complete, properly frozen study can evaluate the year.
- No annual probability of ruin, 100× return probability or optimal-growth claim
  follows from these unvalidated, dependent historical alternatives.

The source and validation requirements in the main goal remain unchanged.
See [coverage audit](BANKROLL_DATA_AUDIT.md),
[independent protocol review](BANKROLL_REPLAY_PROTOCOL_REVIEW.md),
[strategy guide](../docs/STRATEGIES.md#18-starting-with-200-capital-and-strategy-selection)
and [mathematics](../docs/MATHEMATICS.md#25-integer-bankroll-sizing-and-selection-before-trading).

## Reproduction

With the pinned original archive and unchanged registered sources:

```bash
uv run python -m research.experiments.e023_bankroll --run-record-id 93872
```

The command resumes the exact stored accounts. Source/config changes cause a
failure instead of silently replacing the experiment. Full account journals are
gzip-compressed, append-only archive records; a second implementation audits
them independently. The complete local report is `reports/E023_bankroll.json`.
The [independent audit](../evidence/E023_audit.json) reconstructs all 4,608
accounts, 198,778 entries and releases, and 2,145,475 journal records. Its
accounting result does not verify historical liquidity or annual performance.
