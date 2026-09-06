# Do newly received weather reports precede usable price changes?

The current sample contains later price movement, but it does not establish a
trading signal or a speed advantage. For all eight weather reports with matching
daily-contract coverage, buying at the first eligible later ask and comparing
with the nominal one-minute bid gives a negative gross difference on both YES
and NO. Some five- and fifteen-minute movements exceed the spread, but choosing
their profitable side afterward is not a trading strategy.

This diagnostic was declared at **17:46:58 UTC on 6 September 2026**, before
computing its price-change statistics. It freezes archive record **89260** as
the final permitted input. It is a retrospective analysis of receipts collected
prospectively by E017, not a new untouched validation period. The declaration is
`reports/observation_reaction_declaration.json`; detailed results are in
`reports/observation_reaction.json`.

## Coverage and source handling

The read-only analysis verified 728 archived source records and 130 original
before-weather-after acquisition frames. All frames had their original receipts
in the expected sequence and none reported an acquisition error.

| Archived first-seen versions | Count |
|---|---:|
| New raw METAR text or temperature | 20 |
| Initial or old-provider backfill | 17 |
| Same weather report with only metadata changes | 16 |

All 53 versions remain in the report, including the exclusions. Changing a
provider receipt timestamp does not create another weather event. A new report
with an unchanged temperature remains included because it can contain new cloud,
wind, or other information; no temperature direction is invented for it.

The eight stations produced 20 new reports: Austin 3, Denver 2, Houston Hobby 3,
Los Angeles 3, Chicago Midway 2, Miami 3, New York Central Park 2, and Philadelphia
2. Only Miami, Midway, and Los Angeles have matching daily-contract books in
E017's fixed panel. Their eight reports provide 24 complete windows across the
three declared horizons. The remaining twelve reports are explicitly outside
the exact-station quote panel, rather than assumed to have shown no reaction.

Chicago daily-high contracts use **Midway/KMDW**. O'Hare/KORD is not substituted.
The fourth frozen contract is the Miami hourly **Synoptic index**; KMIA METAR is
treated only as a weather reference for that index. Its one eligible one-minute
window had a negative gross difference on both sides. Its remaining windows are
excluded because the contract had closed, with no fabricated post-close quote.

We examined 260 original E017 book batches and 152 book batches from the existing
paper-price observer. The observer was still alive as PID 20841 when inspected
at approximately 17:40 UTC. Its last book receipt was 16:56:35, its last scheduling
record was 17:00:35, and there was no terminal record for its active protocol
30355. It is not safe to treat its quiet period as a stopped process or to launch
a duplicate. E017's separate daily books supply the later windows here.

## Fixed price measurements

For each report, the baseline is the latest completed book strictly before our
weather receipt, at most 75 seconds old. The later book must have been requested
at or after the chosen horizon and completed within 75 seconds of it. The first
eligible snapshot is used even if it lacks liquidity; an empty or shallow book
causes an explicit abstention instead of a search for a better later price.

Prices are obtained by walking enough displayed depth for one contract. The
following numbers are **mean changes in cents in the YES bid / YES ask** from
that pre-receipt baseline. They are signed price changes, not profits.

| Daily contract | Reports | Nominal 1 minute | Nominal 5 minutes | Nominal 15 minutes |
|---|---:|---:|---:|---:|
| Miami maximum 92–93°F | 3 | +1.33 / +2.03 | +0.67 / +1.03 | +11.67 / +9.70 |
| Chicago maximum 77–78°F | 2 | −2.50 / −2.50 | −6.00 / −6.00 | −21.00 / −20.00 |
| Los Angeles maximum 78–79°F | 3 | −0.33 / −2.00 | −1.33 / −3.33 | −5.00 / −7.67 |

Daily temperature brackets are not monotone: warming can move a forecast into
a bracket or above it. Consequently, this analysis does not label warming as a
YES signal for these contracts. Only the greater-than hourly reference contract
receives a separate temperature-aligned diagnostic.

The first hypothetical arrival snapshot must be requested at least one second
after our receipt. Because the collector samples each minute, the daily arrival
books were actually received **59.46–59.76 seconds later**. Nominal one-minute
comparison books arrived **60.15–119.69 seconds** after the weather receipt.
Exact request and completion timestamps are retained for every window. These
observations cannot establish what could have been executed one second after
the weather appeared.

## What survives crossing the spread?

The gross quote difference is `later bid − arrival ask`, computed separately for
YES and NO. It requires a fresh later request and an arrival before the nominal
horizon. It excludes fees and does not assume any order was submitted or filled.

| Horizon | Daily report windows | Windows with a positive difference on either side |
|---|---:|---:|
| 1 minute | 8 | 0 |
| 5 minutes | 8 | 4 |
| 15 minutes | 8 | 6 |

For example, after the first Midway report, the later arrival NO ask was 51¢ and
the fifteen-minute NO bid was 69¢. That is an 18¢ gross quote difference. But
another Midway report with **zero temperature change** preceded a 15¢ difference
in the same direction. This illustrates why a price move following a report
does not by itself identify new temperature information as the cause.

The declared side comparison is descriptive. It does not select YES or NO before
the movement, apply fees, submit an order, or measure a realized return. Across
these horizons the same eight reports and three contracts are reused, all on
one day. No p-values or independent-event claims are reported. Provider-to-own
receipt gaps range from 2.14 to 114.08 seconds; provider receipt is not proven to
be the first time the public or other traders could obtain the observation.

## Next experiment supported by this evidence

The missing ingredient is a forecast surprise: how different the new observation
is from what a model and the market already expected. Hour-to-hour warming alone
usually includes predictable daytime heating. A next prospective protocol can
freeze a forecast of the observation, record its error when the report arrives,
and map that error into the complete daily temperature distribution. It should
compare simple forecast updates with the proposed small model using the same
weather receipts and market books.

That experiment needs original books collected at explicit second-scale delays
after each new report, all required contract sides, and a station panel declared
in advance. Comparable predeclared times without new weather reports would help
separate ordinary market movement from report-related movement. Existing frozen
collectors were not edited or restarted for this diagnostic.

## Reproduction and checks

```sh
uv run python research/probes/observation_reaction.py --register
uv run python research/probes/observation_reaction.py
uv run pytest -q tests/test_observation_reaction.py
```

Registration creates the declaration exclusively and refuses to overwrite it.
For the existing checkpoint, run the second command using the saved declaration;
do not register a new sample or relabel this sample as independent evidence.

Actual checks in this session:

```text
All checks passed!
4 passed in 0.02s
```

Tests cover weather-versus-metadata novelty, future observations not changing
earlier classifications, actual one-contract depth, stale and prefetched books,
market closure, retaining an unusable first snapshot, and the difference between
monotone thresholds and temperature brackets. The study used zero network
requests, zero archive writes, and zero simulated or real fills.
