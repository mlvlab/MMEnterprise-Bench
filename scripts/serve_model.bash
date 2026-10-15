#!/usr/bin/env bash
# Serve an open-weight reader locally with vLLM 0.24.0 (install with: pip install -e ".[local]").
# Usage: scripts/serve_model.bash --model qwen4b --gpus 0 --port 8000 [--print-command]
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/_common.bash"
exec "$python_bin" -m mmenterprise_bench serve "$@"
