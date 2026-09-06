# Reproducing the engineering checks and research

## A fresh clone: no credentials or research download required

```sh
git clone https://github.com/MartinMashalov/WeatherPred.git
cd WeatherPred
uv sync --frozen
uv run pytest -q
uv run ruff check weatherpred tests research/experiments research/probes
uv run ruff format --check weatherpred tests research/experiments research/probes
```

The test suite checks fees, partial fills, source-time restrictions, contract
predicates, archive integrity, numerical gradients, bankroll accounting and
research input isolation. External HTTP fixtures test the client boundary;
the execution and accounting units themselves are exercised directly.

Browse [summary.json](../evidence/summary.json),
[all compact strategy results](../evidence/strategy-results.json), and the
[interactive explorer](https://martinmashalov.github.io/weatherpred-results.html)
without acquiring the original archive. The published verification log records
the dated local research check. CI tests source correctness on a clean checkout;
it does not claim to rerun years of market data.

## What is and is not included

Included: implementation, tests, fixed experiment configurations, experiment
history, model equations, compact results and independent-audit summaries.
Not bundled: the large `data/` SQLite/blob archive, generated `reports/` files,
local operator notes or credentials. Source record identifiers in reports refer
to the original local append-only archive, not globally resolvable URLs.

Consequently, a fresh clone can reproduce the tests and inspect the findings,
but **cannot immediately replay the complete historical experiments**. The
research scripts require their original archived inputs and registration records.
Publishing the summaries is not the same as distributing that full dataset.

## Acquiring a new public-data snapshot

The collector uses public GET endpoints. It cannot submit real exchange orders.

```sh
mkdir -p reports
uv run weatherpred discover
uv run weatherpred markets
uv run weatherpred rules
uv run weatherpred capture --cycles 10 --interval 30
uv run weatherpred verify-archive
```

Newly acquired data has new receipt times and source IDs. It must not be presented
as the original snapshot. Acquisition may take time and depends on provider
availability and rate limits. Historical scripts have separate date gates,
resource bounds and stop files; read their configuration before running them.

## Replaying the original research archive

With the original `data/` and corresponding generated reports restored:

```sh
bash scripts/verify.sh
uv run python research/experiments/e003_replay.py
uv run python research/experiments/e007_replay.py
uv run python research/experiments/e010_replay.py
uv run python research/experiments/e013_audit.py
uv run python research/experiments/e014_source_diagnostics.py
```

The complete `scripts/verify.sh` also verifies the archive hash chain and replays
the basket experiment. On an empty archive, those checks cannot establish the
published empirical results. Do not replace the original evidence with an empty
or newly downloaded archive and call it a reproduction.

The recorded E013 batch can resume using `--run-record-id 35387` **only with its
original registration/archive and matching source hashes**. It freezes input
queries at registration, writes every candidate and abstention, and stops when
`data/STOP_AUTORESEARCH` exists. Repeating that batch is a replay, not additional
independent statistical evidence. E009 similarly pins its source files and
protects the active paper run with an operating-system lock.

E015 is a separate forward experiment, registered as 42731. With its original
archive, `uv run python research/experiments/e015_audit.py` checks recorded quote,
queue, fill and cash evidence without networking. Its runner resumes with
`--run-record-id 42731` only if source hashes still match. The process lock is
`data/market_making.lock`; `data/STOP_MARKET_MAKING` cancels pending orders and
stops it. A new run needs new future dates and registration before decisions.
Its initial panel and short observation window cannot establish profitability.

E016 registration 45913 adds same-contract netting with a separate journal. Use
`uv run python research/experiments/e015_audit.py --run-record-id 45913` for its
raw-source audit, or `uv run python research/experiments/e016_netted_maker.py
--project-locked-run 42731` for an identical-fill cash projection of E015. The
projection places no orders. The E016 runner resumes with `--run-record-id 45913`,
uses `data/netted_maker.lock`, and stops on `data/STOP_NETTED_MAKER`. Do not edit
its pinned code while the registered cohort is running.

E018 registration 59488 and model 59490 select a conditional hourly distribution
using August data only, then create a separate netted paper cohort. With the
original archive, run `uv run python research/experiments/e018_diagnostics.py`
to rebuild its training rows and selection from raw sources. Run
`uv run python research/experiments/e009_audit.py --run-record-id 59488` for the
recorded execution audit. Resume the runner with both `--run-record-id 59488`
and `--model-record-id 59490`; it uses `data/e018.lock` and `data/STOP_E018`.
Missing a registered slot produces an explicit skip, never a backdated forecast.

## Optional model and bounded-search research

The [small-model report](../research/model_candidates.md) documents the separate
optional PyTorch/Chronos environment, pinned checkpoint, August inputs and one
fixed supervised fit. It is not installed by the core `uv sync`. Its public
[model evidence](../evidence/model_candidates.json) retains the overall failures,
the exploratory five-minute result and the deterministic checkpoint replay.
The original stochastic evaluation and its correction are disclosed. Rerunning
a probe is a new calculation, not additional independent evidence.

The later [station comparison](../research/STATION_FORECASTS.md) freezes eight
models, the full case manifest and one 200-step fit as registration 105314.
Its optional model command is `python -m research.experiments.e022_run`; a fresh
execution needs its own registration and artifacts. Completed attempts must not
be silently retrained. The core environment can run
`uv run python -m research.experiments.e022_audit --self-check` without PyTorch.
The full `--run-record-id 105314` audit needs the saved raw archive and prediction
artifacts; it reconstructs scores without loading or executing neural models.

The [autoresearch adaptation](../research/AUTORESEARCH_ADAPTATION.md) describes
registration of a finite candidate list and a fixed evaluator. The subprocess
runner enforces a shared deadline, retains logs and failures, and does not retry.
Its engineering checks execute real tiny processes using synthetic data. They
do not require the optional model environment or the market archive.

## Evidence and limitations

The [validation protocol](../config/validation.json) requires independent
chronological and forward evidence before promotion. July–September 2025 remains
development data even after many experiments. October–December 2025 has not been
opened. Historical source modification/issue timestamps do not independently
establish publication to traders; paper records use actual receipt timestamps.

The hash chain detects accidental changes but is not externally notarized.
Independent quote/fee audits do not establish actual liquidity or eliminate all
selection bias. Failed strategies and inconclusive checks remain part of the
[experiment history](../EXPERIMENTS.md).

### Frozen manifest used by the public tests

The test session restores `reports/E022_manifest.json` from the compressed
fixture in `tests/fixtures/E022_manifest.json.gz` when absent. It verifies the
original SHA-256 `ccbcbb142cbb7d6954d3ea0d89000427003b27834f9b9f8bb28477d46339b741`
and refuses to overwrite a differing local artifact. The manifest contains
case identities, clocks, eligibility and source references, without numeric
weather observations or forecasts. Frozen test assertions and experiment
source files are unchanged. The first published checkout exposed the missing
fixture (four failures); a fresh checkout with the fixture ran all 543 tests.
[Actual clean-checkout output](../evidence/verification-clean-checkout-2026-09-06.txt).
