# Adapting autoresearch to weather trading

Checked on 6 September 2026. This is a tested extension to research bookkeeping
and bounded process execution; it is not a new profitable strategy or an
unattended training service.

Karpathy's official repository uses a small, fixed evaluation harness and a
five-minute training budget. An agent changes the training code, records the
result, then keeps or discards the change. Its current upstream implementation
requires an NVIDIA GPU. We inspected commit
[`228791fb499afffb54b46200aca536f79142f117`](https://github.com/karpathy/autoresearch/commit/228791fb499afffb54b46200aca536f79142f117).
The source documents are the
[README](https://github.com/karpathy/autoresearch/blob/228791fb499afffb54b46200aca536f79142f117/README.md)
and [agent experiment instructions](https://github.com/karpathy/autoresearch/blob/228791fb499afffb54b46200aca536f79142f117/program.md).

The useful idea here is a short, repeatable experiment with a fixed referee.
WeatherPred already has finite registered research batches: E013 tested 576
trading policies under three cost assumptions, and E014 tested 108 observation
constraint variants including their cost assumptions. E018 separately selected
among 12 forecasting models on expanding August time splits. Those are different
kinds of comparisons; model-selection comparisons should not silently be added
to a headline count of trading-policy comparisons. None becomes an independent
validation sample by being rerun or renamed.

## What the new component does

[`autoresearch_protocol.py`](probes/autoresearch_protocol.py) provides a finite
trial registry. Register the candidate list, their source files, fixed evaluator,
input manifest, time splits, score, and resource budget before running candidates.
The registry pins its own source as well. A hash is a fingerprint of file bytes;
changing any registered source or input manifest prevents a new trial from
starting under that registration.

The first trial establishes the fixed baseline. Each later candidate gets one
attempt and a deadline. A started attempt reserves its full allowed duration;
the measured elapsed time remains charged after success or failure. Crashes,
invalid results, and overruns retain their slots. The supervised runner records
ordinary interruptions after stopping its process group. If it is killed before
recording a terminal result, the ledger still reports the trial as running.
A caller must inspect the actual process before recovery; a stale state file is
not proof that the process ended.

Each scored artifact must identify the same evaluation events and decision
times as the baseline. The evidence checks require:

- Weather observations and their receipt times precede the trading decision.
- Training outcomes were received before the fitting cutoff.
- Evaluation decisions follow the cutoff and lie in the registered time window.
- A prospective prediction was committed after registration and by its decision.
- No registered training or evaluation interval overlaps a sealed period.
- Evaluation includes the registered minimum number of distinct UTC days.

The last condition prevents 100 contracts on one day from masquerading as 100
days of evidence. Different days can still be dependent; this check does not
establish independence or replace the existing block-based uncertainty analysis.
Reports with an altered event panel, future labels, missing lineage, or a
nonfinite score remain recorded as invalid.

The ranking returns `keep_for_research` when a candidate improves on the baseline
by the registered amount. It always returns `promotion_eligible: false`.
Choosing the best result from many trials creates selection bias even when
the time splits are correctly implemented. Independent future testing remains
necessary.

## Using it

Build a plan using the complete schema in
[`test_autoresearch_protocol.py`](../tests/test_autoresearch_protocol.py).
`source_hashes()` creates the required source fingerprints. The input manifest
should enumerate the already authorized research data and its archived lineage;
do not point it at the sealed holdout. The evaluator's result JSON supplies
`training_rows`, `evaluation_rows`, the same input-manifest fingerprint, and
`metrics` keyed by the registered metric name.

For supervised execution, each candidate additionally registers `command`, an
argument list beginning with an absolute interpreter path and an absolute pinned
script path. The plan's `execution` contains an absolute `cwd` and a fixed
`evaluator_command` using the same argument-list format. Include
[`autoresearch_run.py`](probes/autoresearch_run.py) in `evaluator_sources`.
The `process_setup` test fixture demonstrates all these fields with real tiny
subprocesses.

```sh
uv run python research/probes/autoresearch_protocol.py tmp/search/trials.jsonl register tmp/search/plan.json
uv run python research/probes/autoresearch_run.py tmp/search/trials.jsonl baseline
uv run python research/probes/autoresearch_protocol.py tmp/search/trials.jsonl ranking
```

The runner starts the candidate, then the fixed evaluator, with `shell=False`.
Both share the same total deadline, including startup. It terminates their
process groups on timeout, interruption, or completion, so a remaining child
cannot continue consuming resources in that group. Standard output, standard
error, process identifiers, exit codes, and timing are retained under the
ledger's adjacent `.artifacts` directory and fingerprinted in its terminal
record. Failures consume their trial slot and are never silently retried.

The evaluator receives `WEATHERPRED_TRIAL_DIR`, `WEATHERPRED_CANDIDATE_ID`,
`WEATHERPRED_CANDIDATE_JSON`, and `WEATHERPRED_INPUT_MANIFEST` in its environment.
It must write `result.json` in the trial directory. Any candidate-written file
with that name is preserved separately before evaluation; it cannot substitute
for the fixed evaluator's output.

The runner executes only registered, reviewed local commands. It does not
allocate GPUs or infer training commands. It is not a filesystem or network
sandbox and does not limit memory; a child deliberately creating a new session
could escape its process group. The registry checks reported lineage and pinned
manifests, while independent raw-data audits must verify that the reported
information was actually used. Absolute local source paths make a registration
specific to its checkout. An uncatchable kill of the runner leaves its ledger
attempt running until the original process state is inspected and reconciled.

## Applying the loop to the next model experiments

Use short candidate budgets to compare a station forecast against persistence,
the existing conditional model, and available numerical weather forecasts.
Improve a calibrated forecast with supervised learning first. A later policy
model can choose entry, size, or abstention using simulated fills and locked
capital. Its research score should include fees, adverse price movement,
unfilled orders, and settlement delays. Optimizing only forecast error or a
frictionless profit score does not meet the trading objective.

Keep training, candidate selection, final evaluation, and prospective paper
execution as distinct stages. Reinforcement learning must not learn directly
from the final evaluation outcomes or exploit an unrealistic execution simulator.
Any claimed gain still needs comparison against simple rules using the same
future observations and execution assumptions. No claim that this combination
is unprecedented follows from the implementation.

Verification in this session:

```text
All checks passed!
...................                                                      [100%]
19 passed in 2.43s
```

These nineteen checks cover source and manifest mutation, ledger tampering,
interrupted trial recovery, finite resource accounting, retained failures,
late receipts, sealed-period overlap, event-panel substitution, and counting
evaluation days. Real subprocess tests also cover candidate/evaluator failures,
literal shell arguments, shared deadlines, termination of a stubborn grandchild,
and interruption of the runner itself. They use synthetic protocol fixtures
and do not count as nineteen market experiments.
