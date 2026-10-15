#!/usr/bin/env bash
# All offline checks: release scan, dataset validation, generation dry-run, generation tests, evaluation tests.
# No API key or network access is needed. Dataset checks are skipped (with a notice) when the dataset is absent.
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/_common.bash"
dataset_dir="${MMEB_DATA_DIR:-data/MMEnterprise-Bench}"

echo "== release scan"
"$python_bin" scripts/check_release.py

if [ -f "$dataset_dir/qa/rows.json" ]; then
  echo "== dataset validation ($dataset_dir)"
  "$python_bin" -m mmenterprise_bench validate --dataset "$dataset_dir"
  echo "== frozen rankings and image checksums for every retriever/k"
  "$python_bin" -m mmenterprise_bench run-baseline --validate --config mmenterprise_bench/configs/full_grid.json --dataset "$dataset_dir"
else
  echo "== dataset validation skipped: $dataset_dir not found (run scripts/download_data.bash)"
fi

if "$python_bin" -c "import fitz, PIL, pydantic, openai, matplotlib, fontTools" 2>/dev/null; then
  echo "== generation pipeline dry-run"
  "$python_bin" data_generation_pipeline/run_project.py --dry-run
  echo "== generation pipeline tests"
  "$python_bin" -m unittest discover -s data_generation_pipeline/tests
else
  echo "== generation checks skipped: install the [generate] extra (pip install -e '.[generate]')"
fi

echo "== evaluation package tests"
"$python_bin" -m unittest discover -s tests
echo "All checks passed."
