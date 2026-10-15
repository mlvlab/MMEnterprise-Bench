"""Derive candidate questions from released, structured project records."""
from __future__ import annotations

from collections import Counter


def candidates(g, atoms):
    corpus = getattr(g, "corpus", {})
    rows = []

    def add(family, question, answer, aid):
        if aid not in atoms.items:
            return
        rows.append({"id": f"RULE_{len(rows) + 1:03d}", "q": question,
                     "family": family, "answer_status": "answerable", "gold": {"answer": answer},
                     "evidence": [aid], "evidence_paths": [[aid]],
                     "solution": [{"step": "Read the specified record and its metadata.", "record_ids": [aid]},
                                  {"step": f"The recorded answer is {answer}.", "record_ids": [aid]}],
                     "generation_method": "rule"})

    emails = corpus.get("emails", [])
    subjects = Counter((mail["subject"], mail["t"][:10]) for mail in emails)
    for mail in emails:
        if subjects[mail["subject"], mail["t"][:10]] != 1:
            continue
        sender = g.pinfo[mail["from_id"]]["name"] if mail["from_id"] else mail["from"]
        add("Attribution", f"Who sent the email titled '{mail['subject']}' on {mail['t'][:10]}?", sender, mail["id"])

    metadata = {"run", "dataset", "config", "source", "status", "owner"}
    for row in corpus.get("tracker", []):
        history = sorted(row["history"], key=lambda version: version["t"])
        if not history:
            continue
        current = history[-1]
        fields = [(key, str(value)) for key, value in current["v"].items()
                  if key not in metadata and value not in (None, "") and any(char.isdigit() for char in str(value))]
        for field, value in fields:
            previous = next((version for version in reversed(history[:-1])
                             if version["v"].get(field) not in (None, "")
                             and str(version["v"][field]) != value), None)
            if previous is None:
                add("Extraction", f"What {field} does the tracker list for {row['run']}?",
                    value, f"T:{row['run']}@{current['t']}")
            else:
                add("Temporal", f"What was the tracker value of {field} for {row['run']} at {previous['t']}?",
                    str(previous["v"][field]), f"T:{row['run']}@{previous['t']}")
    return rows
