# From a station forecast gain to one prospective contract study

**6 September 2026. The smallest credible bridge is one-hour Austin station
contracts, with a mandatory settlement-mapping stage before conditional paper
execution.** Five current hourly families name individual TWC stations already
in E022. They offer a closer target match than the Miami index or daily maxima.
The remaining source-definition ambiguity must stay visible: no verified
mapping or executable edge is claimed here.

The reported roughly **1.60°F development MAE** is useful evidence about the
frozen model's average station-temperature error. It is not a probability of
winning, a calibrated standard deviation, a trading return, or a prediction
of a daily maximum. This round inspected existing rules, source code and
whitelisted live-universe metadata; it read no quotes, outcomes, new weather
observations or new model scores, and made no forecasts or orders.

## A real hourly station opportunity, with exact limits

The examined E027 v2 panel is **156694**, received at
`2026-09-06T20:18:44.436886Z`, under registration **122996**. Its complete
temperature metadata panel had 706 selected contracts across 69 series.
The following hourly groups each had one open event with ten contracts:

| Series | Listed authoritative target | E022 station overlap | Conclusion |
| --- | --- | --- | --- |
| KXTEMPAUSH | The Weather Company, KAUS | Yes | Selected first by alphabetic station order among the five matched families, not by errors or quotes. |
| KXTEMPCHIH | The Weather Company, KORD | Yes | Chicago **O'Hare**, not the KMDW daily-high mapping. Retained alternative, not another candidate in this pilot. |
| KXTEMPDCH | The Weather Company, KDCA | Yes | Same station identifier; detailed hourly-source mapping still requires verification. |
| KXTEMPLAXH | The Weather Company, KLAX | Yes | Same limitation. |
| KXTEMPNYCH | The Weather Company, KNYC | Yes | Central Park, not a New York airport or the Miami index. |
| KXTEMPMIAH | Synoptic/Kalshi five-component index | No single-station equivalence | Excluded from this particular bridge. |

KXTEMPBOSH exists in the catalog but had no selected open contract in this
panel. The catalog is source **155283**; hourly market metadata pages are
**156595/156609/156613/156619/156625/156665**, in the table's order.
[Metadata-only projection and receipt hashes](../evidence/forecast_to_trade_metadata.json).
This is an observed panel, not a promise that future events or liquidity exist.

All six examined events opened at **20:00 UTC** and closed at **21:00 UTC**.
The title specifies **5 PM EDT**, even for Austin and Los Angeles. For Austin,
21:00 UTC is **16:00 CDT**, not 17:00 station-local time. The suffix `17` must
not be parsed as an Austin local hour. Bind the rule's explicit timezone,
`close_time`, UTC model target and the TWC station-local row independently.
The [Miami alignment audit](HOURLY_ALIGNMENT_AUDIT.md) does not establish the
semantics of these different TWC-based contracts.

These contracts opened **one hour before their target**. That accommodates
E022's 1h decision horizon; the 3h and 6h forecasts cannot enter these particular
event books at their original decision times. Do not invent prelisting quotes.
Future listing times must be checked for each case, not assumed from this sample.

## The unresolved settlement gate

Austin's primary rule identifies temperature recorded for a specific hour,
The Weather Company and **KAUS**. However, the linked **NHIGHD** rule PDF is
titled LOCALTEMPERATURE and contains generic maximum/minimum/average and
time-period placeholders. The market's secondary boilerplate also refers to
maximum/minimum temperature. It does not explicitly establish whether E022's
`temperature_f` hourly row is the designated value, an hourly aggregate or a
different provider product. Same station and clock time alone are insufficient.
[Official linked terms](https://assets.kalshi.com/contract_terms/NHIGHD.pdf).

Archived PDF **1743**, SHA256
`2b515c2cf521ffffa895f306bad589b026a7f916fa375c7d097448c660e02590`, was
checked as text and visually on page 1. It requires full source precision and
permits qualifying corrections before expiration; later revisions are ignored.
It defines “above” as strict greater-than. Thus a strike such as **96.99°F**
must be evaluated as `Y > 96.99`, not rounded into a different condition merely
because a subtitle says “97° or above.” This is contract metadata, not a price.

The gate has two parts. First reconcile the listing and designated provider
endpoint's actual measurement definition, target interval, first publication,
allowed corrections and final precision from authoritative information.
Second collect a fixed prospective set of matching provider rows and exchange
expiration values/results. Every observed contract predicate must agree at
full precision. If the measurement definition remains unresolved, numerical
agreement on a few examples does not authorize pretending the rule is settled.
Keep the study at the mapping stage and report that limitation.

Do not use METAR as the label or infer a rounding rule by whichever conversion
makes a discrepancy disappear. Preserve pending/settled transitions, exact
numeric source lexemes, revision receipts and the exchange's eventual outcome.
If the exchange supplies only binary outcomes, those reveal an interval rather
than an exact temperature: report the weaker evidence, not an invented value.

## One finite prospective study

Propose **14 UTC days, KAUS only, targets at 00/06/12/18 UTC, 1h horizon**:
56 scheduled targets. Freeze the first UTC midnight **at least two hours
after** the new registration and input-readiness checks as `T0`, so the first
one-hour decision is still in the future, then keep every scheduled target. Root must
register the exact calendar, model hashes, sources, selector and budgets before
collecting or evaluating this study. This document does not start it.

The first two days' eight scheduled targets are a mapping/data preflight with
**no hypothetical orders**. The remaining twelve days' 48 targets form the
conditional paper stage, only if the source-definition and all eight mapping
checks pass. Otherwise the remaining calendar still records missing/blocked
cases; it is not shifted to a more favorable period or changed to another city.
Any later mapping discrepancy stops new hypothetical entries and retains all
earlier forecasts, positions and losses for audit.

Use the frozen **50/50 pretrained Chronos + NBH** model and its original
calibration. This selection follows the already observed E029 development
results and must be declared as such. Keep raw NBH, pretrained Chronos and the
other two frozen blends as diagnostic references; do not refit weights,
recalibrate on the first two days or select a successful subgroup afterward.
The pilot can test implementation and whether the previous gain persists.
Fourteen dependent weather days cannot satisfy the program's financial
validation or seasonal requirements.

Required inputs at decision `D=S−1h` are:

- **Same-product station context:** 168 hourly TWC slots on the original grid
  ending D−1h, at least 120 finite, latest finite at most 120 minutes old.
  Every version used must already have been received locally by D. A startup
  download can provide older history after its actual receipt, but never
  creates earlier prospective forecasts. Preserve missing slots and exact
  source versions; do not substitute E027 METAR temperatures for TWC rows.
- **Actual live physical guidance:** the original NBH cycle D−2h, the exact
  KAUS target column at lead 3, and its raw object/card hash. Request and archive
  that issued object before D, retaining request time, HTTP receipt, object
  edition and source clock. S3 `Last-Modified` alone is not proof of local
  availability. If the fixed cycle is late or missing, record failure; no
  later-cycle substitution or invented zero guidance.
- **Frozen computation:** station identity, seven-step Chronos output, target
  step 2, original 13 quantiles and stored h=1 calibration. Inputs stop at D;
  publish the forecast and its manifest by D+30s. A late forecast is a missed
  decision, not a backdated one. Do not adapt to weather arriving after D.
- **Contract metadata:** exact hourly listing, threshold, greater-than rule,
  open/close times, settlement source and current price/quantity increments.
  Unknown mapping or absent event remains a scheduled missing case.

E027 presently captures public METARs, detailed Miami components and books;
it **does not capture this same-product live TWC context or NBH guidance**.
Its frozen 24h window also cannot supply a 14-day study. Any narrow successor
collector needs its own registration. Reuse already captured receipts only
when they were genuinely available before the corresponding decision.

Keep a finite acquisition plan: initial at most two weekly TWC snapshots to
cover the context, then the same-product endpoint on a fixed cadence; four
fixed NBH cycles per day; current event metadata/books only around the four
decision windows; and bounded outcome polling. The precise source cadence,
response-size limits and request ceiling must be pinned before acquisition,
after metadata-only endpoint feasibility is confirmed. If that product bridge
is unavailable, a different model is needed; another raw METAR feed is not an
equivalent replacement.

## Probability improvement must survive executable prices

For a threshold K, the quantity of interest is **p = P(Y > K)** for the exact
settlement variable. MAE does not identify this probability. Use the frozen
calibrated quantiles and a predeclared monotone CDF conversion, keeping ties
and strict inequality explicit. Do not assume a Gaussian error with standard
deviation 1.60°F.

With adjacent forecast quantiles `Q[a] <= K < Q[b]`, the quantile grid implies
a **model-resolution interval** for YES probability of approximately
`[1−b, 1−a]`. This is not a statistical confidence interval or guaranteed
probability bound. A fixed interpolation can provide the point probability
for scoring, but do not let interpolation manufacture a large trade edge.
Retain tail strikes in scoring; this pilot places no hypothetical orders
outside the 1%–99% quantile range or at unresolved tied/atomic boundaries.

Score every listed eligible threshold at the original decision using Brier
loss `(p−y)²`, first averaging thresholds within an event, then events within
UTC day. Ten nested strikes are not ten independent weather outcomes. Compare
with the contemporaneous two-sided book midpoint and report the bid/ask
interval and missing-sided books. Midpoint is a pricing reference, not a
buyable probability or proof of the market's actual belief.

For YES purchase at ask `a`, unit expected net gain is

`p − a − entry_fee − modeled_execution_cost`.

NO uses `1−p` and its own ask. A lower Brier loss may coexist with negative
tradable edge when the quote already anticipates the forecast, spread is wide,
fees are large or probability errors concentrate at the chosen strikes. The
pilot's one frozen entry rule considers all event strikes but chooses at most
one side/strike: highest conservative model-resolution edge, deterministic
ticker/side tie-break, only if it exceeds **5¢ per requested contract** after
the declared fees and 1¢ price stress. No threshold search is permitted.
This buffer is a practical preregistered screen, not an estimated confidence
bound on profitability.

## Fills, cancellation and a $200 paper account

Use **one requested contract, maximum two unfinished event positions**, and
reserve actual worst-case limit cost plus fees. Cap each reservation at 1% of
current settled cash, combined reservations at 2%; available cash must remain
nonnegative. Fractional partial fills remain exact quantities if returned by
the modeled venue rules. Do not multiply previous $100 results or assume
small-bankroll sizing scales continuously. Retain every rejected order,
unfilled remainder, fee and capital-cap exclusion.

For this first bridge choose a **marketable limit IOC** instruction—fill what
is available on arrival and cancel the remainder. It avoids inventing a maker
queue position. Freeze the limit using a book received after the forecast,
then apply the intent only to a distinct, strictly later complete book with a
fixed minimum 60s arrival delay, before the contract closes. Reject incomplete,
stale or clock-inconsistent books under frozen freshness gates. Walk displayed opposite-side depth at or
better than the limit; never fill on the signal's own quote. Preserve a
half-depth/1¢ adverse-price stress and a zero-fill scenario, with no removal of
failed opportunities. These are paper execution scenarios, not proved fills.

The public book exposes YES and NO **bids**. A YES ask is one minus a NO bid;
use that level's quantity and exact decimal strings. Do not confuse volume,
open interest or yesterday's candle with current executable depth.
[Official order-book semantics](https://docs.kalshi.com/getting_started/orderbook_responses).
Sixty-second snapshots miss cancellations, intervening trades and adverse
selection. A limit resting at a price or a later trade through it does not
establish that our order would have filled. Meaningful maker experiments
require a separately declared queue/order-flow model and better event capture.

IOC remainder cancellation still has operational state: a request, transport
timeout, acknowledgment and actual partial fills are different records. A
later implementation must reconcile terminal state and idempotent order IDs
before releasing reserved cash. Do not assume a timed-out request placed no
order. The current [V2 order documentation](https://docs.kalshi.com/api-reference/orders/create-order-v2)
lists IOC and cancellation-on-pause options; no private endpoint was called.
Its schema must be pinned anew before any deployment rather than copied from
an older intent format. This proposal itself submits no orders.

Charge the current series/event fee schedule and exact per-fill balance
rounding. The [fee documentation](https://docs.kalshi.com/getting_started/fee_rounding)
separates model fee, balance-alignment rounding and accumulated rebates.
Many small partial fills can therefore cost differently from one aggregate
fill. The account must independently reproduce every cash movement.

Hold the small paper position to settlement; no exit-timing optimization in
this pilot. The examined metadata shows a **3,600s settlement timer**, while
expected expiration is close+5min and the maximum expiration can be days
later. Cash is reusable only after the declared finalized settlement receipt,
not after the weather is observed or the result is determined.
[Official market lifecycle](https://docs.kalshi.com/getting_started/market_lifecycle).
Poll outcomes for at most 24h after the last target; unresolved positions stay
locked and explicitly incomplete, never force-valued as winners.

## What this experiment could establish

The useful result is a transparent conversion record:

`56 scheduled targets -> verified mappings -> eligible forecasts -> priced
signals -> order intents -> partial/full fills -> finalized cash`.

Report every count and exclusion, all daily Brier losses, trade-time spread,
forecast age, quoted edge, fill fraction, delay, fees, maximum reserved cash,
worst loss and final/locked balances. A profitable handful of fills is a lead
for longer validation, not proof of the edge or a path from $200 to $10,000.
The [objective's](../docs/OBJECTIVE.md) chronological, seasonal and forward
requirements remain binding. A failure at any conversion step identifies the
next problem to solve without changing this frozen cohort.

Only **one** prospective mechanism is proposed: a verified station-hour
forecast gain may improve probability estimates at the opening of its matching
hourly contract. The preregistered data/mapping and execution gates can reject
it. Miami joint-component distributions and daily-extrema paths remain separate
research problems; they are not shortcuts around a missing target match.

## Evidence and bounded source access

The seven projected catalog/market sources retain only named contract metadata;
quote, volume, open-interest and outcome fields were skipped without decoding.
No live market price was inspected. The metadata artifact retains all ten
contracts in every observed hourly event, not only attractive strikes.

There were six documentation lookups: three through web retrieval (order-book
200, old order URL404, lifecycle200), followed by three separately declared
archived GETs: **164308** current documentation index, **164451** V2 order
documentation and **164460** fee rounding, all200. The old404 is retained in
the lookup manifest. No measurement/history/weight endpoint or private API was
requested. [Documentation manifest](../evidence/forecast_to_trade_documentation.json).
The existing source PDF was rendered locally; no new PDF download was needed.
