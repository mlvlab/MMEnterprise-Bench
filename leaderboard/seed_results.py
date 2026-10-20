"""Turn the paper's per-condition baseline metrics into leaderboard rows (``results.jsonl``).

Usage:
    python seed_results.py --baselines baselines/metrics.json --output results.jsonl [--date YYYY-MM-DD]

The input is the ``metrics.json`` of the ``MMEnterprise-Bench/baselines`` release: ``{"judge", "tasks",
"categories", "conditions": [{"model", "label", "retriever", "k", "correct", "accuracy", "per_task",
"per_category", "per_answer_status"}, ...]}``.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rows import PAPER_JUDGE, PAPER_LINK, PAPER_ORG, build_row, setting_for, today
from storage import results_to_jsonl, slugify
from validate import CATEGORIES, CATEGORY_SHORT, TASKS


def _acc(group: dict, key: str):
    entry = (group or {}).get(key) or {}
    value = entry.get("accuracy")
    return float(value) if isinstance(value, (int, float)) else None


def seed_rows(baselines: dict, date: str | None = None) -> list[dict]:
    date = date or today()
    judge = baselines.get("judge") or PAPER_JUDGE
    rows = []
    for cond in baselines["conditions"]:
        retriever, k = str(cond["retriever"]), int(cond["k"])
        n = int(cond.get("n", 1613))
        graded = int(cond.get("graded", n))
        summary = {
            "overall": float(cond["accuracy"]),
            "correct": int(cond["correct"]),
            "missing": n - graded,
            "categories": {CATEGORY_SHORT[c]: _acc(cond.get("per_category"), c) for c in CATEGORIES},
            "tasks": {t: _acc(cond.get("per_task"), t) for t in TASKS},
            "answerability": {s: _acc(cond.get("per_answer_status"), s) for s in ("answerable", "unanswerable")},
            "evidence_modality": None,
            "evidence_count": None,
            "retrieval_recall": None,
        }
        rows.append(build_row(
            row_id=f"paper_{slugify(cond['model'])}_{retriever}_k{k}",
            model=cond["label"],
            organization=PAPER_ORG,
            link=PAPER_LINK,
            setting=setting_for(retriever, k),
            judge=judge,
            date=date,
            source="paper",
            summary=summary,
            notes=f"Paper baseline ({cond.get('model_id', cond['model'])}); released {retriever} retriever, k={k}.",
        ))
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baselines", required=True, help="baselines metrics.json (per-condition metrics)")
    parser.add_argument("--output", required=True, help="results.jsonl to write")
    parser.add_argument("--date", default=None, help="date stored on the rows (default: today, UTC)")
    args = parser.parse_args(argv)
    baselines = json.loads(Path(args.baselines).read_text(encoding="utf-8"))
    rows = seed_rows(baselines, args.date)
    Path(args.output).write_text(results_to_jsonl(rows), encoding="utf-8")
    print(f"wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
