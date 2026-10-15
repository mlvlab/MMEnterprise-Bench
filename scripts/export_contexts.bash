#!/usr/bin/env bash
# Export the paper's per-question contexts (k retrieved records in presentation order) as JSONL.
# Usage: scripts/export_contexts.bash --retriever 2b --k 10 --output contexts.jsonl [--limit N] [--include-gold]
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/_common.bash"
exec "$python_bin" -m mmenterprise_bench export-contexts "$@"
