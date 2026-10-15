#!/usr/bin/env bash
set -euo pipefail
pipeline_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$pipeline_dir/run_project.py" "$@"
