#!/usr/bin/env bash
# Score a predictions file written by your own agent.
# Usage: scripts/score.bash predictions.jsonl [--output DIR] [--judge-model M --judge-base-url URL] [--pre-judged] ...
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/_common.bash"
if [ $# -lt 1 ]; then
  echo "usage: scripts/score.bash PREDICTIONS.jsonl [mmeb score options]" >&2
  exit 2
fi
exec "$python_bin" -m mmenterprise_bench score "$@"
