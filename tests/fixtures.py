"""A tiny synthetic dataset in the release layout, shared by the evaluation tests."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")

ROWS = [
    {"id": "p01::Q1", "project": "p01", "q": "What is the value?", "gold": {"answer": "42"}, "t": "2026-01-02T00:00",
     "category": "Evidence Reasoning", "task": "Extraction", "primary_task": "Fact", "answer_status": "answerable",
     "evidence_modality": "multi", "evidence_unit_count": 2, "frozen_representative_retrieval_units": ["u000", "u050"]},
    {"id": "p01::Q2", "project": "p01", "q": "Who has the unavailable ID?", "gold": {"answer": "Not established."},
     "t": "2026-01-02T00:00", "category": "Unanswerability", "task": "Unanswerability", "primary_task": "Abstention",
     "answer_status": "unanswerable", "evidence_modality": None, "evidence_unit_count": 0,
     "frozen_representative_retrieval_units": []},
    {"id": "p01::Q3", "project": "p01", "q": "Which run came last?", "gold": {"answer": "Run 7"}, "t": "2026-01-02T00:00",
     "category": "Conflict Resolution", "task": "Temporal", "primary_task": "State", "answer_status": "answerable",
     "evidence_modality": "text", "evidence_unit_count": 1, "frozen_representative_retrieval_units": ["u007"]},
    {"id": "p02::Q4", "project": "p02", "q": "How many items?", "gold": {"answer": "Three"}, "t": "2026-01-02T00:00",
     "category": "Evidence Reasoning", "task": "Computation", "primary_task": "Computation", "answer_status": "answerable",
     "evidence_modality": "text", "evidence_unit_count": 3, "frozen_representative_retrieval_units": ["u001", "u002", "u003"]},
]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")


def make_dataset(root, with_checksums=False):
    """Write the fixture under ``root`` and return it. Unit u000 has an image; ranks are u000..u099."""
    root = Path(root)
    (root / "media").mkdir(parents=True, exist_ok=True)
    (root / "media/image.png").write_bytes(PNG)
    units = [{"id": f"u{i:03}", "record_id": f"p01::R{i}", "order": i, "timestamp": f"2026-01-01T00:{i % 60:02}",
              "text": f"Record {i}", "image": None, "image_sha256": None} for i in range(100)]
    units[0]["image"] = "media/image.png"
    units[0]["image_sha256"] = hashlib.sha256(PNG).hexdigest()
    write_json(root / "corpus/units.json", units)
    write_json(root / "qa/rows.json", ROWS)
    for size in ("2b", "8b"):
        # 8b ranks start at u050 so the two retrievers differ
        offset = 0 if size == "2b" else 50
        hits = {row["id"]: [{"id": units[(i + offset) % 100]["id"], "rank": i + 1} for i in range(100)] for row in ROWS}
        write_json(root / f"retrieval/ranks_{size}.json", {"retriever": size, "top_k": 100, "hits": hits, "query_sha256": {}})
    write_json(root / "projects.json", [{"id": "p01"}, {"id": "p02"}])
    write_json(root / "seed_documents.json", [])
    write_json(root / "manifest.json", {"name": "fixture", "version": "test"})
    if with_checksums:
        lines = []
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != "SHA256SUMS":
                lines.append(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.relative_to(root).as_posix())
        (root / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root
