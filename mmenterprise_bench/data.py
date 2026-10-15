"""Dataset access, the frozen context protocol, and evidence recall."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

DEFAULT_DATASET_DIR = "data/MMEnterprise-Bench"
DATASET_ENV = "MMEB_DATA_DIR"

TASKS = ("Extraction", "Attribution", "Computation", "Comparison", "Interpretation",
         "Cross-source", "Temporal", "Unanswerability")
TASK_MAP = {"Fact": "Extraction", "Selection": "Comparison", "Discrepancy": "Cross-source",
            "Resolution": "Cross-source", "State": "Temporal", "Change": "Temporal",
            "Abstention": "Unanswerability"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def task_of(row):
    task = row.get("task", row.get("primary_task"))
    return TASK_MAP.get(task, task)


class Dataset:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.rows = read_json(self.root / "qa/rows.json")
        unit_list = read_json(self.root / "corpus/units.json")
        self.units = {unit["id"]: unit for unit in unit_list}
        if len(self.units) != len(unit_list):
            raise ValueError("Duplicate retrieval unit ID")
        if len({row["id"] for row in self.rows}) != len(self.rows):
            raise ValueError("Duplicate question ID")
        self.ranks = {}
        self.query_hashes = {}
        self._image_hashes = {}
        for size in ("2b", "8b"):
            path = self.root / f"retrieval/ranks_{size}.json"
            if path.exists():
                ranking = read_json(path)
                self.ranks[size] = ranking["hits"]
                self.query_hashes[size] = ranking.get("query_sha256", {})
        for row in self.rows:
            if not isinstance(row.get("q"), str) or not isinstance(row.get("gold", {}).get("answer"), str):
                raise ValueError("Question or reference answer is missing")
            if task_of(row) not in TASKS:
                raise ValueError("Unknown task: " + str(task_of(row)))
            gold = row.get("frozen_representative_retrieval_units", [])
            if not set(gold) <= self.units.keys():
                raise ValueError("Gold evidence contains an unknown retrieval unit")
            if row.get("answer_status") == "answerable" and not gold:
                raise ValueError("Answerable question has no representative evidence")

    def image_path(self, unit):
        value = Path(unit["image"])
        if value.is_absolute():
            raise ValueError("Image paths must be dataset-relative")
        path = (self.root / value).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Image path escapes the dataset directory")
        if not path.is_file():
            raise FileNotFoundError("Missing image for unit " + unit["id"])
        return path

    def image_sha256(self, unit):
        path = self.image_path(unit)
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key not in self._image_hashes:
            hasher = hashlib.sha256()
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    hasher.update(block)
            self._image_hashes[key] = hasher.hexdigest()
        value = self._image_hashes[key]
        expected = unit.get("image_sha256")
        if expected and expected != value:
            raise ValueError("Image checksum mismatch for unit " + unit["id"])
        return value

    def selected(self, row, retriever, k):
        if retriever not in self.ranks:
            raise ValueError("Missing retrieval ranking: " + retriever)
        expected = self.query_hashes[retriever].get(row["id"])
        actual = hashlib.sha256(json.dumps(row["q"], ensure_ascii=False).encode()).hexdigest()
        if expected and expected != actual:
            raise ValueError("Retrieval query checksum mismatch for question " + row["id"])
        hits = self.ranks[retriever].get(row["id"], [])
        ids = [hit["id"] for hit in hits[:k]]
        if len(ids) != k or len(set(ids)) != k or not set(ids) <= self.units.keys():
            raise ValueError("Invalid top-k ranking for question " + row["id"])
        return sorted((self.units[uid] for uid in ids),
                      key=lambda unit: (unit.get("timestamp") or "", unit["order"], unit["id"]))

    def validate(self, retrievers=("2b", "8b"), k_values=(10, 50, 100), check_images=True):
        for row in self.rows:
            for retriever in retrievers:
                for k in k_values:
                    self.selected(row, retriever, k)
        if check_images:
            for unit in self.units.values():
                if unit.get("image"):
                    self.image_sha256(unit)
        return {"questions": len(self.rows), "retrieval_records": len(self.units),
                "visual_records": sum(bool(u.get("image")) for u in self.units.values()),
                "projects": len({r["project"] for r in self.rows}),
                "task_counts": {task: sum(task_of(row) == task for row in self.rows) for task in TASKS}}

    def input_identity(self, row, selected):
        return {"question": row["q"], "records": [
            {"text": unit.get("text") or "", "image_sha256": self.image_sha256(unit) if unit.get("image") else None}
            for unit in selected]}

    def recall(self, rows, retriever, k):
        fractions, complete, recovered, total = [], 0, 0, 0
        for row in rows:
            gold = set(row.get("frozen_representative_retrieval_units", []))
            if row.get("answer_status") == "unanswerable" or not gold:
                continue
            selected = {unit["id"] for unit in self.selected(row, retriever, k)}
            found = len(gold & selected)
            fractions.append(found / len(gold))
            complete += int(gold <= selected)
            recovered += found
            total += len(gold)
        return {"answerable_questions": len(fractions),
                "evidence_recall": 100 * sum(fractions) / len(fractions) if fractions else None,
                "complete_evidence_questions": complete,
                "micro_evidence_recall": 100 * recovered / total if total else None}


def default_dataset_dir():
    """Dataset directory: ``$MMEB_DATA_DIR`` if set, else ``data/MMEnterprise-Bench`` under the CWD."""
    return Path(os.environ.get(DATASET_ENV) or DEFAULT_DATASET_DIR)


def dataset_present(root):
    root = Path(root)
    return (root / "qa/rows.json").is_file() and (root / "corpus/units.json").is_file()


def load_dataset(root=None, download=True, revision=None):
    """Return the benchmark as a :class:`Dataset`, downloading it from the Hub when it is missing.

    ``root`` defaults to ``$MMEB_DATA_DIR`` or ``data/MMEnterprise-Bench`` relative to the current
    directory. With ``download=False`` a missing dataset raises ``FileNotFoundError`` instead.
    """
    root = Path(root) if root is not None else default_dataset_dir()
    if not dataset_present(root):
        if not download:
            raise FileNotFoundError(f"Dataset not found at {root}; run `mmeb download` first")
        from .hub import download_dataset
        download_dataset(root, revision=revision)
    return Dataset(root)


def build_context(dataset, row, retriever="2b", k=10):
    """The paper's reader input for one question, as a list of plain dicts.

    ``row`` is a question dict from ``dataset.rows`` (or a question ID). The records are the first ``k``
    hits of the released ``retriever`` ranking, sorted by (timestamp, corpus order, unit ID), exactly as
    ``Dataset.selected`` returns them. Each dict has ``unit_id``, ``record_id``, ``timestamp``, ``text``,
    ``image_path`` (absolute path or ``None``) and ``image_sha256``.
    """
    if isinstance(row, str):
        matches = [candidate for candidate in dataset.rows if candidate["id"] == row]
        if not matches:
            raise KeyError("Unknown question ID: " + row)
        row = matches[0]
    context = []
    for unit in dataset.selected(row, retriever, k):
        has_image = bool(unit.get("image"))
        context.append({"unit_id": unit["id"], "record_id": unit["record_id"],
                        "timestamp": unit.get("timestamp"), "text": unit.get("text") or "",
                        "image_path": str(dataset.image_path(unit)) if has_image else None,
                        "image_sha256": dataset.image_sha256(unit) if has_image else None})
    return context
