# Reproducing the engineering checks and research

## A fresh clone: no credentials or research download required

```sh
git clone https://github.com/MartinMashalov/WeatherPred.git
cd WeatherPred
uv sync --frozen
uv run pytest -q
uv run ruff check weatherpred tests research/experiments
uv run ruff format --check weatherpred tests research/experiments
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
