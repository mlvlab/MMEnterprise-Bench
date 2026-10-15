#!/usr/bin/env python3
"""Bring-your-own-agent template.

Fill in ``my_agent`` and run from the repository root::

    python examples/your_own_agent.py --output predictions.jsonl            # all 1,613 questions
    python examples/your_own_agent.py --output predictions.jsonl --limit 20 # a quick smoke test
    mmeb score predictions.jsonl --output outputs/my_agent                  # judge + metrics

Your agent receives the question and (optionally) the paper's frozen context: the first k records of the
released retriever ranking in chronological presentation order, each with its text and image path. You may
ignore that context and run your own retrieval over ``ds.units`` (the whole corpus); if you do, also return
the IDs of the units you retrieved so ``mmeb score`` can report Evidence Recall of your retriever.

The only contract is the output file: one JSON object per line with ``id`` and ``answer`` (plain text).
Optional: ``retrieved_unit_ids`` (list of corpus unit IDs). Any other keys are preserved in graded.jsonl.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mmenterprise_bench import build_context, load_dataset


def my_agent(question: str, context: list[dict], ds) -> dict:
    """Return {"answer": str, "retrieved_unit_ids": [...] (optional)}.

    ``context`` is a list of records, each {"unit_id", "record_id", "timestamp", "text", "image_path", "image_sha256"}.
    ``image_path`` is an absolute path (None for text records); open it with PIL or send it base64-encoded to a VLM.
    ``ds.units`` maps every corpus unit ID to its record if you want to do your own retrieval.
    """
    # --- replace this placeholder with your model call -------------------------------------
    # Example: a trivial "reader" that only looks at how many records mention a number.
    answer = "Not established in the supplied records."
    # ---------------------------------------------------------------------------------------
    return {"answer": answer, "retrieved_unit_ids": [record["unit_id"] for record in context]}


def main():
    parser = argparse.ArgumentParser(description="Run your agent over MMEnterprise-Bench and write predictions.jsonl")
    parser.add_argument("--output", type=Path, default=Path("predictions.jsonl"))
    parser.add_argument("--retriever", choices=("2b", "8b"), default="2b", help="Released ranking used for the context")
    parser.add_argument("--k", type=int, choices=(10, 50, 100), default=10, help="Records per question")
    parser.add_argument("--limit", type=int, help="Only the first N questions (release order)")
    parser.add_argument("--dataset", help="Dataset directory (default: $MMEB_DATA_DIR or data/MMEnterprise-Bench; auto-downloads)")
    args = parser.parse_args()

    ds = load_dataset(args.dataset)                      # downloads from the Hub on first use
    rows = ds.rows[: args.limit] if args.limit else ds.rows
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as out:
        for index, row in enumerate(rows, 1):
            context = build_context(ds, row, retriever=args.retriever, k=args.k)
            result = my_agent(row["q"], context, ds)
            out.write(json.dumps({"id": row["id"], **result}, ensure_ascii=False) + "\n")
            if index % 100 == 0 or index == len(rows):
                print(f"{index}/{len(rows)} questions answered", flush=True)
    print(f"Wrote {args.output}. Next: mmeb score {args.output} --output outputs/my_agent")


if __name__ == "__main__":
    main()
