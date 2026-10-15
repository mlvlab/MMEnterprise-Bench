# MMEnterprise-Bench data

The released dataset is hosted at [🤗 MMEnterprise-Bench/MMEnterprise-Bench](https://huggingface.co/datasets/MMEnterprise-Bench/MMEnterprise-Bench). `MMEnterprise-Bench/` in this directory is where `mmeb download` places the local copy (git-ignored, about 230 MB).

| Component | Count |
| --- | ---: |
| Projects | 30 |
| Questions | 1,613 |
| Answerable questions | 1,526 |
| Unanswerable questions | 87 |
| Retrieval records | 7,140 |
| Text records | 5,725 |
| Visual records (with an image) | 1,415 |
| Record versions | 287 |

Records by source type: Slack messages 4,231 · Notion entries 1,171 · PDF pages 658 · standalone images 385 · slides 372 · email 210 · meeting notes 90 · TXT/LOG 23.

## Getting the data

```bash
mmeb download                                   # raw layout -> data/MMEnterprise-Bench, SHA-256 verified
mmeb download --dataset /somewhere/else         # or set MMEB_DATA_DIR
mmeb validate                                   # checksums, references, labels, both rankings
```

Or with `datasets` (parquet configs; the `corpus` config embeds the images):

```python
from datasets import load_dataset
repo = "MMEnterprise-Bench/MMEnterprise-Bench"
questions = load_dataset(repo, "questions", split="test")       # 1,613 rows
corpus = load_dataset(repo, "corpus", split="test")             # 7,140 rows; `image` (PIL) + `image_path`
ranks_2b = load_dataset(repo, "retrieval_2b", split="test")     # one row per question: hits = [{id, rank, score}]
ranks_8b = load_dataset(repo, "retrieval_8b", split="test")
projects = load_dataset(repo, "projects", split="test")
seeds = load_dataset(repo, "seed_documents", split="test")
```

The evaluator (`mmeb`) uses the raw layout, which is byte-identical to the parquet contents:

```text
MMEnterprise-Bench/
├── qa/rows.json
├── corpus/units.json
├── projects.json
├── retrieval/
│   ├── config.json
│   ├── ranks_2b.json
│   └── ranks_8b.json
├── media/                 # 1,415 PNG/JPG files named by SHA-256
├── seed_documents.json
├── manifest.json
└── SHA256SUMS
```

The rankings contain 100 retrieval records per question; the first 10, 50 or 100 hits give the corresponding retrieval depth. `retrieval/config.json` records the embedding model revisions, instructions, preprocessing and ranking settings. Images are addressed relative to the dataset directory, and every referenced image is included.

See [SCHEMA.md](SCHEMA.md) for field definitions and [../EVALUATION.md](../EVALUATION.md) for the evaluation protocol.

## Task taxonomy

| Category | Task | Questions | Definition (paper, Table 1) |
| --- | --- | ---: | --- |
| Evidence Reasoning | Extraction | 268 | Extract entities or values explicitly stated in text, tables, or figures |
| Evidence Reasoning | Attribution | 80 | Identify the source, requester, author, or communicator of information |
| Evidence Reasoning | Computation | 310 | Apply question-specified calculations to recorded values |
| Evidence Reasoning | Comparison | 212 | Compare results or properties to identify differences, rankings, or choices |
| Evidence Reasoning | Interpretation | 232 | Draw conclusions or assess claims using recorded evidence |
| Conflict Resolution | Cross-source | 212 | Identify conflicting information across records or determine which value applies |
| Conflict Resolution | Temporal | 212 | Identify information at a specified time or trace its revisions |
| Unanswerability | Unanswerability | 87 | Identify missing information or unsupported premises |

`category` and `task` are the evaluation labels. `task_group` and `primary_task` retain the original annotation labels (`Fact`, `Selection`, `Discrepancy`, `Resolution`, `State`, `Change`, `Abstention`, ...). Question, project, native record and retrieval-unit identifiers are stable; their numeric suffixes are not consecutive.

Representative evidence of the 1,526 answerable questions: 749 need one record, 495 two, 282 three or more; 693 are text-only, 563 image-only, 270 mixed.

## Source documents and rights

`seed_documents.json` lists the 30 public seed documents (title, domain, type, publisher, identifier, URLs, PDF hash) and their rights: 13 US public domain, 11 CC BY 4.0, 5 UK Open Government Licence 3.0, 1 used with the authors' permission. The workplace organisations, people, conversations and document revisions are simulated; figures and page images embedded in the records keep the rights of their source document. A missing URL is `null`. The document list does not grant additional rights to third-party material.

The benchmark annotations and simulated records are released under CC BY 4.0.

## Validation

```bash
mmeb validate                       # or: python data/validate.py [--data-root DIR] [--skip-checksums]
```

The validator checks file checksums, media references, identifiers, gold evidence, task labels, derived counts and both retrieval rankings using the Python standard library. The `.cache/` directory that `huggingface_hub` keeps inside a downloaded folder is ignored.
