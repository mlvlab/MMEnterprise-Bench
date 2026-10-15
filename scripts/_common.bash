# Sourced by the wrapper scripts: resolve the repository root and the Python interpreter.
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
python_bin="${PYTHON:-python3}"
export PYTHONDONTWRITEBYTECODE=1
