#!/usr/bin/env python3
"""Validate the MMEnterprise-Bench dataset (thin wrapper around ``mmenterprise_bench.validate``).

Usage from the repository root: ``python data/validate.py [--data-root DIR] [--skip-checksums]``.
Equivalent to ``mmeb validate``.
"""
from pathlib import Path
import sys

try:
    from mmenterprise_bench import validate as _validate
except ImportError:  # running from a checkout without `pip install -e .`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mmenterprise_bench import validate as _validate

validate = _validate.validate

if __name__ == '__main__':
    raise SystemExit(_validate.main())
