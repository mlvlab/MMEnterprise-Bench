#!/usr/bin/env bash
# Reproduce the paper's RAG baselines: reader answers over the frozen contexts, then the LLM judge.
# Usage: scripts/run_baseline.bash --models luna --retrievers 2b --k 10 [--dry-run] [--limit N] [--output DIR]
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/_common.bash"
exec "$python_bin" -m mmenterprise_bench run-baseline "$@"
