#!/usr/bin/env bash
# Download the dataset from the Hugging Face Hub into data/MMEnterprise-Bench (or $MMEB_DATA_DIR).
# Usage: scripts/download_data.bash [--revision REV] [--dataset DIR]
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/_common.bash"
exec "$python_bin" -m mmenterprise_bench download "$@"
