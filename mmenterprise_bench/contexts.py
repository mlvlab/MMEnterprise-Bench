"""Export per-question reader contexts so agents in any language can consume the frozen protocol."""
from __future__ import annotations

import json
from pathlib import Path

from .data import task_of


def select_rows(dataset, limit=None, question_ids=None):
    rows = dataset.rows
    if question_ids:
        wanted = set(question_ids)
        unknown = wanted - {row["id"] for row in rows}
        if unknown:
            raise ValueError("Unknown question ID(s): " + ", ".join(sorted(unknown)[:5]))
        rows = [row for row in rows if row["id"] in wanted]
    if limit is not None:
        if limit <= 0:
            raise ValueError("Limit must be a positive integer")
        rows = rows[:limit]
    return rows


def context_record(dataset, row, retriever, k, include_gold=False):
    """One JSON-serialisable context for ``row``: the paper's k records in presentation order."""
    records = []
    for unit in dataset.selected(row, retriever, k):
        records.append({"unit_id": unit["id"], "record_id": unit["record_id"],
                        "timestamp": unit.get("timestamp"), "text": unit.get("text") or "",
                        "image_path": unit.get("image") or None})
    record = {"id": row["id"], "project": row["project"], "question": row["q"], "timestamp": row.get("t"),
              "records": records, "retriever": retriever, "k": k}
    if include_gold:
        record.update(reference_answer=row["gold"]["answer"], answer_status=row.get("answer_status"),
                      task=task_of(row), category=row.get("category"),
                      gold_unit_ids=list(row.get("frozen_representative_retrieval_units", [])))
    return record


def export_contexts(dataset, retriever="2b", k=10, output="contexts.jsonl", limit=None, question_ids=None,
                    include_gold=False):
    """Write one JSON object per line to ``output``; returns the number of questions written.

    ``image_path`` values are relative to the dataset directory (``dataset.root``).
    """
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = select_rows(dataset, limit, question_ids)
    with output.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(context_record(dataset, row, retriever, k, include_gold), ensure_ascii=False) + "\n")
    return len(rows)
