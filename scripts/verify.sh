#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check weatherpred tests research/experiments
uv run ruff format --check weatherpred tests research/experiments
uv run pytest -q
uv run weatherpred verify-archive
uv run weatherpred replay-baskets
