# Execution, capacity and bankroll growth — E020

The current bottleneck is finding more independently repeatable opportunities
with usable liquidity. Raising the bet size or selling early does not solve it.
This study measures the existing NYC daily/weekend rain pair using a new book
received on September 6, 2026 at 17:09:39 UTC. It submits no exchange orders and
adds no simulated fills to E019.

## What can actually be executed

The E019 paper experiment publishes two fixed-price order intentions before
receiving two separate later books. Each leg can fill partially or fail. Cash,
fees and unmatched exposure are retained. Its initial three alternative accounts
have conditional settlement gains of $0.4113, $0.2873 and $0.1690 if both contract
outcomes retain their normal equivalence. Those gains are not yet realized.

The new pure `order_intent` function translates these economics into Kalshi's
[current V2 schema](https://docs.kalshi.com/api-reference/orders/create-order-v2):
buying YES is a bid at the YES price; buying NO is an ask at one minus the NO
price. It checks the market's price grid and constructs stable client IDs,
immediate-or-cancel or fill-or-kill instructions, and order safety fields.
The payloads are inspectable examples. Authentication, submission, broker
acknowledgments and recovery after ambiguous submissions are not implemented.

Fill-or-kill applies to one order. A batch or
[order group](https://docs.kalshi.com/getting_started/order_groups) does not
establish an atomic cross-market trade. A real adapter would need to reconcile
actual fills before choosing whether to cancel, hedge or keep an unmatched leg.
An IOC instruction limits how long an order rests; it does not guarantee a fill.

## Scaling the position

Protocol **78157** fixes quantities 1–100 and three depth/slippage scenarios before
the new quotes. Report **78171** retains all 300 screens, including failures.
Within each illustrative cash-cost cap, the table selects the available size
with the largest conditional surplus, requiring at least 3¢ per matched pair.
These cost caps differ from E019's conservative order-reservation caps; this is
a diagnostic and does not change its position limits.

| Maximum purchase cost | Full displayed depth | Half depth, +1¢ per leg | Quarter depth, +2¢ per leg |
|---|---|---|---|
| $5 | 5 pairs; $0.3646 surplus | 5 pairs; $0.2640 | 5 pairs; $0.1636 |
| $10 | 10 pairs; $0.7292 | 10 pairs; $0.5280 | 6 pairs; $0.1892 |
| $25 | 26 pairs; $1.8101 | 13 pairs; $0.6436 | 6 pairs; $0.1892 |
| $50 or $100 | 27 pairs; $1.8368 | 13 pairs; $0.6436 | 6 pairs; $0.1892 |

At the strongest tested stress, more capital stops helping after six pairs.
These are same-snapshot displayed capacities, not later fills. The favorable
full-depth row must not be used as a scalable expected return. Larger positions
also concentrate the loss if the settlement relationship fails.

## Selling early to reuse cash

Liquidation walks actual bids, deducts exit fees and reports any quantity without
a bid as unfilled. Using each account's matching exit stress:

| Existing E019 account | Entry cost | Quoted proceeds after exit fees | Profit if that complete exit executes |
|---|---:|---:|---:|
| Full depth | $4.5887 | $2.4384 | −$2.1503 |
| Half depth, +1¢ | $4.7127 | $2.3419 | −$2.3708 |
| Quarter depth, +2¢ | $3.8310 | $1.7278 | −$2.1032 |

The weekend NO contract's best bid is only 43¢ while its ask is 81¢. That spread
makes early cash recycling expensive. The exit values are observations from this
book, not actual sales or guarantees about a later book.

Same-contract YES/NO offsets can return matched cash in E016. Kalshi's current
[collateral-return rules](https://help.kalshi.com/en/articles/13823816-collateral-return)
also describe eligible groups within an event. They do not establish a cash
offset between this daily event and a separate weekend event. E019 therefore
keeps both legs funded until their actual settlement.

## Trader improvements worth testing

1. **Choose execution by opportunity.** Compare crossing both asks with posting
   one leg and hedging only after an observed fill. Register the hedge limit,
   cancellation deadline and unmatched-risk cap first. A better entry price is
   useful only if the resulting fills remain favorable after adverse selection.
2. **Allocate across independent events.** Optimize expected logarithmic wealth
   with limits for shared stations, storms and settlement sources. Twenty cities
   affected by the same weather system are not twenty independent bets.
3. **Price the holding period.** Rank opportunities using conservative expected
   growth over the actual time cash remains committed. Monthly rain can hold
   capital into October; a high percentage return alone is insufficient.
4. **Value information before speed.** Archive new observations with their real
   receipt times, then test whether the market reacts later. Faster requests do
   not create an information advantage when the observation is already priced.

For a matched quantity q, total cost C and bankroll B, suppose normal settlement
pays q and a source failure pays zero with assumed probability r. Expected profit
is `(1-r)q-C`; expected log growth is
`(1-r) log((B+q-C)/B) + r log((B-C)/B)`.
The study evaluates r = 0%, 1%, 3%, 5% and 10% as sensitivity assumptions, not
estimated probabilities. It does not fit Kelly bets to a single successful-looking
weekend or infer a probability of reaching $10,000.

## Evidence and implementation

- [Capital curves, exits and unsent intents](../evidence/E020_execution_finance.json).
- [Independent replay](../evidence/E020_audit.json) reproduces all 300 capacity
  rows, 15 cap selections and 18 sell legs, recomputing fees without the
  production accumulator. These remain displayed quotes, not actual fills.
- [Pure order and capital functions](../weatherpred/execution_finance.py).
- [Registered diagnostic](experiments/e020_execution_finance.py).
- [Tests of side mapping, partial exits and logarithmic growth](../tests/test_execution_finance.py).
- Source receipts 78716 / 78719 / 78756 retain the official execution documentation.

The conditional relationship remains exposed to source changes and exceptional
settlement. No estimated source-failure rate, profitable live strategy or
increase in authorized paper exposure is claimed.
