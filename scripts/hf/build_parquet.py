#!/usr/bin/env python3
"""Build the Parquet configs of the MMEnterprise-Bench Hugging Face dataset.

The Hugging Face repo keeps the raw release layout at the top level (so
``snapshot_download`` reproduces ``data/MMEnterprise-Bench/`` byte-for-byte) and
adds a ``parquet/`` directory for ``datasets.load_dataset``. This script reads
the raw release directory and writes one sub-directory per config:

    parquet/questions/test-00000-of-00001.parquet      1,613 rows
    parquet/corpus/test-0000N-of-0000M.parquet         7,140 rows, images embedded
    parquet/retrieval_2b/test-00000-of-00001.parquet   1,613 rows
    parquet/retrieval_8b/test-00000-of-00001.parquet   1,613 rows
    parquet/projects/test-00000-of-00001.parquet          30 rows
    parquet/seed_documents/test-00000-of-00001.parquet    30 rows

Every config has an explicit ``datasets.Features`` definition so nested fields
are typed and nullable fields (``evidence_modality``, ``image``, ``score``,
``publication_date``, ...) stay nullable. The corpus config embeds the image
bytes in an ``Image`` column and keeps the original relative path in
``image_path``; it is sharded so each file stays well below 300 MB.

Usage:
    python scripts/hf/build_parquet.py --data-root data/MMEnterprise-Bench --output build/parquet
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from datasets import Dataset, Features, Image, Sequence, Value

SPLIT = "test"
CONFIGS = ("questions", "corpus", "retrieval_2b", "retrieval_8b", "projects", "seed_documents")
CORPUS_SHARD_BYTES = 120 * 1024 * 1024  # target raw media bytes per corpus shard (< 300 MB per file)

QUESTION_FEATURES = Features({
    "id": Value("string"),
    "project": Value("string"),
    "q": Value("string"),
    "gold": {"answer": Value("string")},
    "answer_status": Value("string"),
    "role": Value("string"),
    "t": Value("string"),
    "primary_task": Value("string"),
    "task_group": Value("string"),
    "evidence": Sequence(Value("string")),
    "evidence_paths": Sequence(Sequence(Value("string"))),
    "frozen_representative_retrieval_units": Sequence(Value("string")),
    "solution": [{"record_ids": Sequence(Value("string")), "step": Value("string")}],
    "category": Value("string"),
    "task": Value("string"),
    "evidence_record_count": Value("int32"),
    "evidence_unit_count": Value("int32"),
    "evidence_image_count": Value("int32"),
    "evidence_modality": Value("string"),
})

CORPUS_FEATURES = Features({
    "id": Value("string"),
    "record_id": Value("string"),
    "text": Value("string"),
    "image": Image(),
    "image_path": Value("string"),
    "image_sha256": Value("string"),
    "timestamp": Value("string"),
    "order": Value("int32"),
    "unit_content_sha256": Value("string"),
})

RETRIEVAL_FEATURES = Features({
    "id": Value("string"),
    "retriever": Value("string"),
    "query_sha256": Value("string"),
    "hits": [{"id": Value("string"), "rank": Value("int32"), "score": Value("float64")}],
})

PROJECT_FEATURES = Features({
    "id": Value("string"),
    "label": Value("string"),
    "as_of": Value("string"),
    "questions": Value("int32"),
    "retrieval_records": Value("int32"),
    "people": [{"id": Value("string"), "name": Value("string"), "role": Value("string")}],
    "clients": [{"id": Value("string"), "name": Value("string"), "email": Value("string"),
                 "company_name": Value("string")}],
    "channels": [{"id": Value("string"), "name": Value("string")}],
})

SEED_DOCUMENT_FEATURES = Features({
    "project_id": Value("string"),
    "title": Value("string"),
    "domain": Value("string"),
    "document_type": Value("string"),
    "document_subtype": Value("string"),
    "publisher_family": Value("string"),
    "pages": Value("int32"),
    "publication_date": Value("string"),
    "identifier": Value("string"),
    "source_url": Value("string"),
    "pdf_url": Value("string"),
    "pdf_sha256": Value("string"),
    "source_rights": Value("string"),
})


def load_json(root: Path, name: str):
    return json.loads((root / name).read_text(encoding="utf-8"))


def question_rows(root: Path) -> list[dict]:
    rows = load_json(root, "qa/rows.json")
    expected = set(QUESTION_FEATURES)
    for row in rows:
        if set(row) != expected:
            raise ValueError(f"Unexpected question fields for {row.get('id')}: {sorted(set(row) ^ expected)}")
    return rows


def corpus_rows(root: Path) -> list[dict]:
    rows = []
    for unit in load_json(root, "corpus/units.json"):
        image_path = unit["image"]
        rows.append({
            "id": unit["id"],
            "record_id": unit["record_id"],
            "text": unit["text"],
            "image": {"bytes": (root / image_path).read_bytes(), "path": image_path} if image_path else None,
            "image_path": image_path,
            "image_sha256": unit["image_sha256"],
            "timestamp": unit["timestamp"],
            "order": unit["order"],
            "unit_content_sha256": unit["unit_content_sha256"],
        })
    return rows


def retrieval_rows(root: Path, size: str, question_ids: list[str]) -> list[dict]:
    ranks = load_json(root, f"retrieval/ranks_{size}.json")
    rows = []
    for qid in question_ids:  # release order of qa/rows.json
        rows.append({
            "id": qid,
            "retriever": ranks["retriever"],
            "query_sha256": ranks["query_sha256"][qid],
            "hits": [{"id": hit["id"], "rank": hit["rank"], "score": hit.get("score")} for hit in ranks["hits"][qid]],
        })
    if len(rows) != len(ranks["hits"]):
        raise ValueError(f"Ranking {size} covers {len(ranks['hits'])} questions, expected {len(rows)}")
    return rows


def write_config(name: str, rows: list[dict], features: Features, output: Path, num_shards: int = 1) -> list[Path]:
    dataset = Dataset.from_list(rows, features=features)
    config_dir = output / name
    config_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(num_shards):
        shard = dataset.shard(num_shards=num_shards, index=index, contiguous=True) if num_shards > 1 else dataset
        path = config_dir / f"{SPLIT}-{index:05d}-of-{num_shards:05d}.parquet"
        shard.to_parquet(str(path))  # image bytes were read into the rows, so they are embedded
        paths.append(path)
    return paths


def corpus_shards(root: Path, rows: list[dict]) -> int:
    media_bytes = sum((root / row["image_path"]).stat().st_size for row in rows if row["image_path"])
    return max(1, -(-media_bytes // CORPUS_SHARD_BYTES))


def build(root: Path, output: Path) -> dict:
    root = root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    questions = question_rows(root)
    question_ids = [row["id"] for row in questions]
    corpus = corpus_rows(root)
    plan = {
        "questions": (questions, QUESTION_FEATURES, 1),
        "corpus": (corpus, CORPUS_FEATURES, corpus_shards(root, corpus)),
        "retrieval_2b": (retrieval_rows(root, "2b", question_ids), RETRIEVAL_FEATURES, 1),
        "retrieval_8b": (retrieval_rows(root, "8b", question_ids), RETRIEVAL_FEATURES, 1),
        "projects": (load_json(root, "projects.json"), PROJECT_FEATURES, 1),
        "seed_documents": (load_json(root, "seed_documents.json"), SEED_DOCUMENT_FEATURES, 1),
    }
    report = {}
    for name in CONFIGS:
        rows, features, shards = plan[name]
        paths = write_config(name, rows, features, output, shards)
        report[name] = {
            "rows": len(rows),
            "files": [str(path.relative_to(output)) for path in paths],
            "bytes": sum(path.stat().st_size for path in paths),
        }
        print(f"{name}: {len(rows)} rows -> {len(paths)} file(s), {report[name]['bytes'] / 1e6:.1f} MB")
    (output / "build_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-root", type=Path, default=Path("data/MMEnterprise-Bench"),
                        help="Raw release directory (default: data/MMEnterprise-Bench)")
    parser.add_argument("--output", type=Path, default=Path("build/parquet"),
                        help="Directory that receives one sub-directory per config")
    args = parser.parse_args()
    build(args.data_root, args.output)


if __name__ == "__main__":
    main()
