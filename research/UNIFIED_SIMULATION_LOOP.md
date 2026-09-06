# A reusable historical strategy research environment

Implementation proposal, 6 September 2026. This review inspected source code,
configuration and existing protocol documents only. It did not open new market
prices, protected Q4 2025 outcomes, E031 outputs, or run a model, strategy or
performance analysis. The latest user request is a reusable environment for
many statistical trading experiments; the account in this proposal starts at
$200, following the user's later bankroll instruction.

The shortest useful implementation is to connect the existing trial registry,
chronological data gates and cash simulator through a small candidate interface.
The simulator should be a fixed referee. New statistical models can then compete
by producing predictions and decisions without supplying their own fills,
settlement payouts or reported returns.

## What already exists, and the precise gaps

| Existing component | Reuse | Missing connection |
| --- | --- | --- |
| [TrialLedger](probes/autoresearch_protocol.py) | Source fingerprints, hash chain, registered candidate identity, one attempt, baseline-first execution and resource accounting. | Its evidence validator assumes actual feature receipts before decisions, nonempty training labels and complete evaluation labels. It cannot honestly represent the retrospective E024 tape, cash baselines or pending positions without a new validator/schema. `prior_comparisons` is an input number, not a global campaign audit. |
| [Supervised candidate/evaluator commands](probes/autoresearch_run.py) | Separate processes, no shell, shared deadline, logs and failed attempts; candidate-created `result.json` cannot become the referee's score. | Both processes receive the same manifest and filesystem. There is no data sandbox or memory limit. The command validator accepts an absolute script in argv[1], whereas repository imports often require pinned `python -m ...` with a fixed checkout. |
| [E024 market normalizer](probes/bankroll_dataset.py) | Complete 1,736-event census, original failed receipts plus explicit amendment 116002, exact quote strings and source fingerprints. | The returned dictionaries include future prices and terminal outcomes. They are suitable evaluator inputs, not candidate inputs. The source is specifically bound to seven daily-high series and 248 dates; it is not a generic multi-year feed. |
| [E024 decision generator](experiments/e024_annual_replay.py) | Its timestamp-before-value checks, original decision hash and retained failed/missing entries are useful reference behavior. | `generate_intents` combines `select_trade` with later fill/exit/settlement resolution. Separate these responsibilities in new code before plugging in arbitrary fitted candidates. Never hand candidates its completed order dictionaries. |
| [Continuous scheduled account](../weatherpred/scheduled_bankroll.py) | One cash account, integer contracts, reservations, fees, exposure limits, delayed cash release, pending holdings and persistent drawdown across choices. | It consumes precompiled intents and endpoints; it does not offer a stateful `step()` interface. It permits the four registered risk fractions and single entry/terminal events. It does not implement a full passive-order exchange or arbitrary rebalancing. |
| [Account auditor](experiments/e024_account_audit.py), [run auditor](experiments/e024_run_audit.py) | Independent money arithmetic and checks for schedules, missing cash flows, full calendars, selection and artifact ordering. | Wrap these in the new evaluator and bind the same input artifacts; do not let candidate code call a different accounting path. |
| [Growth bounds](../weatherpred/bankroll_selection.py) | Shared day-block draws across all candidates and nondegenerate simultaneous bounds. | A campaign must provide the full candidate/scenario family and distinguish repeatedly used development data from an untouched test. The helper alone does not correct an open-ended adaptive search. |
| [Paper execution](../weatherpred/paper.py) | Event reducer, partial taker slices and conservative maker queue/trade-through logic. | A replay driver must provide original received books/prints and deterministic order clocks. Daily candles do not contain those inputs. This belongs behind a separate execution adapter, after the initial integration. |

Keep all registered sources frozen. Reference their hashes in the new protocol;
implement the following adapters in new files. No accounting rewrite is needed
for the first useful version.

## One fixed boundary between candidates and the referee

Proposed new package: `research/simulation/`. These are proposed interfaces,
not commands or classes that already exist.

```python
@dataclass(frozen=True)
class FoldSpec:
    train_start: int
    fit_cutoff: int
    calibration_end: int | None
    decision_start: int
    decision_end: int             # exclusive
    measurement_end: int          # exclusive; may leave inventory unresolved
    embargo_seconds: int

@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str             # digest of recipe, parameters, sources, seed
    family_id: str
    parent_candidate_id: str | None
    source_hashes: dict[str, str]
    parameters: dict
    seed: int
    requires_fit: bool

class Candidate:
    def fit(self, training_view, fit_context) -> ModelArtifact: ...
    def on_snapshot(self, snapshot, model: ModelArtifact) -> CandidateOutput: ...

# CandidateOutput contains forecasts and/or decisions, including explicit abstentions.
# It never contains future entry prices, fills, terminal labels or realized P&L.
```

`CandidateOutput` must identify the original opportunity, contract/event, decision
time, candidate and fitted artifact. A forecast, when supplied, carries its
settlement variable, horizon and calibrated distribution/probability. A decision
carries side, limit, entry timing instruction, exit rule and a reason for entry
or abstention. The evaluator determines integer quantity from the fixed risk
fraction and account state. If a later experiment searches a sizing algorithm,
that algorithm becomes part of candidate identity and trial counting; it cannot
silently replace the referee's exposure limits.

An opportunity is a predeclared `(event, contract or event-selection scope,
decision_time)` key. Create the union of permitted decision clocks before a
batch starts. Every candidate retains a response or an explicit abstention for
every relevant key. Different holding horizons and selected strikes may lead to
different trades; candidates must not define their evaluation sample by emitting
only orders that eventually fill. Compare account paths over the same calendar
and acquisition universe, not just their mutually profitable trades.

A price-only rule or cash baseline can declare `requires_fit=false`. Do not
invent training rows to satisfy the old standalone registry. Model normalization,
feature selection, calibration, checkpoint choice and policy fitting all belong
to the registered candidate recipe. They receive the same causal restrictions
as the main model fit.

The first version supports decisions whose signals do not depend on prior
simulated fills. Portfolio sizing, cash constraints and the drawdown stop still
react to earlier account events inside the existing engine. A policy that needs
live inventory as an input must declare that requirement and be rejected by
this version. A later explicit, parity-tested `step(event)` fork can expose
account snapshots; silently replaying it against a reset or hypothetical state
would be wrong.

## Materialize only the information each phase may use

`views.py` should be the only component that opens the full evaluator tape.
It first checks the frozen acquisition census and all source pins, then creates
training views and sends decision snapshots in time order. The candidate's
model state may persist between snapshots. Do not provide a single unrestricted
file containing every future evaluation snapshot to a general candidate: its
reader could select later rows before producing an earlier prediction.

Keep four clocks distinct in the record schema:

- Observation/valid time: when the measurement or forecast target applies.
- Issue time and conditional publication time: when the forecast was issued and
  when the historical proxy says it could have been available.
- Actual local receipt time: when this archive really received the bytes.
- Final-label release/receipt time: when a trainable settlement label became
  available under the selected evidence mode.

A numerical weather forecast may legitimately have valid time after the decision;
its issue and permitted availability times must precede the decision. The old
`feature_observed_at <= feature_received_at <= decision` schema does not express
this. Do not relabel future valid time as an observation or replace September
receipts with historical candle endpoints.

Use two explicit evidence modes: `receipt_verified` and
`conditional_historical`. The former requires genuine receipts at or before
use; the latter preserves the real late receipts and separately records the
registered historical-availability assumption. An unverified field cannot be
upgraded by changing mode names. With the current E024 dataset, the conditional
research path is usable and the verified path must retain refusals. Fee evidence,
contract metadata editions and forecast model release dates require their own
as-of checks too.

The candidate process should receive only authorized views, no raw archive path,
labels for evaluation, evaluator output path or inherited API credentials.
Separate directories and RPC payloads reduce accidental leakage but do not
isolate code from the filesystem. For automatically generated, unreviewed code,
run with an actual sandbox/container exposing only those inputs, a writable
output directory and no network. Verify that facility is available before
claiming enforcement. Until then, label execution as trusted reviewed code with
logical data gates, as the current supervisor does.

## Chronological fitting, purging and evaluation

Implement `make_fold_views(manifest, FoldSpec, evidence_mode)` once, with the
following gates before any label values are decoded:

1. Training target dates lie in the registered training interval, and each
   final label is available **strictly before** `fit_cutoff`. A label for an old
   event received later is not trainable. Missing or provisional labels remain
   in the availability census and do not become fabricated targets.
2. Purge overlapping target/return intervals and duplicate event labels across
   the fit/calibration/evaluation boundary. Use the actual label interval and
   release timestamp, not just the row's nominal date. A return label can extend
   through its later sale or settlement. A fixed five-day gap alone does not
   prove that it was released.
3. Apply a registered embargo, meaning an excluded decision interval near a
   selection boundary, in addition to release checks. Shared past feature
   history is not automatically leakage: a 168-hour context does not itself
   justify discarding every neighboring observation. Declare the target/return
   overlap rule and any extra dependence gap separately.
4. Fit calibrators and any hyperparameter selector on earlier permitted folds
   only. Archive fitted artifacts and selected recipes before exposing the next
   evaluation fold. Evaluation snapshots and predictions precede the evaluator's
   label/output release; every output identifies its fit cutoff and source view.
5. Keep the calendar intact. Known inactive days have zero cash flow; missing
   source coverage has an explicit status. Never concatenate September and
   January as consecutive days when Q4 is sealed. A later change must reproduce
   every earlier account prefix exactly.

For the first integration, retain E024's existing July 1, August 1 and September 1
selection clocks, five-day decision buffers and contiguous 2026 windows as a
regression target. A new many-model batch on these already used periods is
**development**, even if the candidate code is new. Do not rerun the restricted
annual chart and describe it as a newly available full-year opportunity test.
A longer statistical study needs a separately acquired and audited historical
manifest; no environment abstraction can manufacture missing history or depth.

October–December 2025 remains protected by the existing release law. Candidate
source, feature pipeline, selection algorithm, costs and success criteria must
be frozen before any separately authorized final test. If its results are used
to improve the next candidate, that period is consumed; a new forward test is
required. Repeated development ranking is allowed and recorded, but does not
produce independent validation or a profitability claim.

## Referee API and execution modes

```python
def compile_orders(decisions, evaluator_tape, scenario, measurement_end):
    # Join future execution events here only, after decision artifacts are archived.
    # Preserve failed entries and unresolved terminal events.
    ...

def evaluate_candidate(decision_artifact, tape, fold, scenario, risk_fraction):
    orders = compile_orders(decision_artifact, tape, scenario, fold.measurement_end)
    account = replay_scheduled(orders, fixed_account_config, fixed_schedule,
                               fold.decision_start, fold.measurement_end)
    audit_account(account, orders, fixed_account_config, fixed_schedule,
                  start_ts=fold.decision_start, end_ts=fold.measurement_end)
    return account
```

`compile_orders` takes the timestamp-gated resolution portion of E024's generator
as its parity reference. The new candidate wrapper takes the signal-selection
portion. Future entry failures never delete an earlier decision. Later prefixes
may add newly released information to an older order without changing its side,
limit, planned exit, decision hash or already reserved quantity.

The selected walk-forward strategy uses one $200 account for the requested
continuous interval. Training alternatives can each start with $200 to compare
recipes; their cash is never transferred into the selected account. A monthly
cash choice blocks new entries but preserves orders, holdings, release times,
fees and drawdown. Retain `assert_prefix` plus the independent account audit.

Use explicit execution adapters with a shared account/report vocabulary:

| Adapter | Evidence and behavior |
| --- | --- |
| Historical candle sensitivity | Exact later bid/ask endpoints only; fixed assumed delay/slippage/depth ceiling and fee scenario. Retain rejected limits and absent endpoints. Useful for screening statistical signals; not demonstrated fills. |
| Receipt-verified books/prints | Original received depth and prints, realistic latency, stale-quote rejection, partial taker slices, conservative maker queue/trade-through, cancellation and aggregate fill fees. Adapt existing paper mechanics under a new replay protocol. |
| Diagnostic frictionless path | Optional preregistered diagnostic only; never the selection or promotion score. Do not add its profit to another scenario. |

Do not claim the current scheduled kernel already has recurring maker partial
fills or partial terminal liquidation. It makes one entry allocation, retains
unfilled quantity, and refuses an insufficient-depth terminal sale. A richer
execution adapter needs its own parity and ledger tests; no candle low/high or
volume proxy supplies missing queue evidence. Fee schedules must be indexed by
their actual effective dates when known. The existing 0.07 coefficient and
100-contract ceiling remain assumptions when used with this tape.

The fixed score panel should include daily net log equity growth, cash P&L,
fees, drawdown, reserved cash, open principal, available liquidation value,
utilization, integer intended/filled quantities, fill/rejection reasons and
pending inventory. State explicitly when growth uses cost-basis equity rather
than executable liquidation value. Do not turn unresolved principal into cash
at the evaluation boundary. Forecast losses and calibration can diagnose a
model; the research ranking must use the common costed trading objective and
retain stress results, rather than ranking by forecast MAE alone.

## A bounded loop that can keep accepting new experiments

Add `campaign.py` as a thin archive-backed coordinator. Reuse the immutable
archive and E024's start/completed/failed unit pattern, and the standalone
ledger's trial identities and budget accounting. The necessary new evidence
validator is trading-specific; do not loosen the old registered validator or
feed it fictitious receipts. Pin the coordinator, both execution commands,
data manifest, evaluators, model runtime and all candidate sources.

A campaign contains finite registered batches. Each batch fixes candidate
recipes, seeds, folds, execution scenarios, sizing choices, tie-breaks, metrics
and total/per-candidate resource limits before those trials run. After a batch,
the research agent can inspect the development diagnostics, implement a new
statistical model and register a new batch with explicit parent lineage. The
proposer is allowed to learn from development outcomes. The evaluator and
protected test stay fixed. A shell loop over registered trials is execution
automation; it is not by itself an autonomous hypothesis generator.

Record separate counts for model fits, forecast tasks, unique model/policy/size
recipes, candidate-fold evaluations, cost scenarios, sensitivity variants and
bootstrap families. A rerun is linked to its original attempt and cannot erase
it or become a fresh sample. Every newly inspected variant consumes research
history, including crashes, invalid outputs, timeouts, no-trade candidates and
cash. Maintain a campaign-wide catalog of semantic recipe hashes and prior
batch IDs; derive counts from it instead of trusting `prior_comparisons`.

Within a batch, resample the same contiguous UTC-day indices across the entire
candidate/scenario family using the existing simultaneous-growth helper. Keep
1/7/14-day sensitivities and registered finite seeds. Cross-city/strike dependence
is retained within a day. The campaign must also report cumulative search and
apply the existing multiple-testing/independent-test requirements before any
promotion; per-batch bounds do not erase adaptive selection across batches.

Successful completed units can resume by exact artifact hash within the original
budget. Interrupted/failed fits cannot restart silently. Common tape/evaluator
or baseline failures stop the batch; candidate-specific failures retain their
slot and the fixed batch can continue. A campaign-wide stop file, exclusive
lock, wall deadline and disk/memory ceilings are separate from the account's
financial drawdown stop.

## Concrete implementation order and checks

1. **`contracts.py` and `views.py`:** add immutable candidate/output/fold schemas,
   full opportunity census, dual-clock evidence mode and bounded snapshot feed.
   Test future-field dictionaries that raise on access, late labels, model
   release dates, provisional labels, forecast valid times, sealed overlap,
   missing rows and return-interval purging.
2. **`e024_adapter.py` and `evaluator.py`:** wrap one existing policy chosen by a
   fixed ID, plus cash, as integration baselines. Split decision creation from
   endpoint resolution. On synthetic tapes, require exact old/new decisions and
   cash journals for flat schedules; test failed entries, pending release across
   a policy switch, same-timestamp ordering, no reset and original-prefix
   identity. Then register a finite parity replay before any real-data use.
3. **`campaign.py` and `cli.py`:** implement the new trading report validator,
   global recipe catalog and fixed candidate/referee process boundary. Reuse
   existing log/kill/resource patterns. Test candidate-written scores, source
   mutation, duplicate recipes, all-failed batches, evaluator failure, process
   timeout, completed-unit resume and forbidden silent retries.
4. **Candidate plugins:** expose existing price rules and subsequently registered
   fitted statistical models through the same interface. Start fitted models
   only when a source-audited, settlement-aligned training view exists. Price
   models can use the current tape; weather-to-trade models need the explicit
   feature/contract join. E022/E029 hourly station-temperature errors cannot be
   substituted for daily-high settlement probabilities.
5. **Receipt replay adapter:** connect captured books/prints to paper mechanics
   for execution research once the preceding common interface is stable. Audit
   orders with actual partial-fill paths before treating them as equivalent to
   the candle sensitivity environment.

Proposed CLI, to implement rather than run now:

```sh
python -m research.simulation.cli prepare --config config/search_campaign.json
python -m research.simulation.cli register --manifest reports/search_manifest.json
python -m research.simulation.cli run --registration-id ID --all
python -m research.simulation.cli audit --registration-id ID
python -m research.simulation.cli report --campaign-id CAMPAIGN
```

The registered runtime must expand `python` to its absolute interpreter, pin the
module's actual source plus transitive dependencies, and fix the checkout. Each
command prints the immutable registration/artifact IDs needed to reproduce the
result. Commands for one candidate or completed-unit resume should be available
without changing the original plan. There is no live-order route in this API.
