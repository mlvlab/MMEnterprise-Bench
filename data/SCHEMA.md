# Data schema

All JSON files use UTF-8. Paths are relative to the dataset directory (`data/MMEnterprise-Bench/` after `mmeb download`). `manifest.json` records the dataset version and component counts. The parquet configs on the Hub (`questions`, `corpus`, `retrieval_2b`, `retrieval_8b`, `projects`, `seed_documents`) carry the same fields; differences are noted per section.

## Questions: `qa/rows.json` (config `questions`)

A JSON array with one object per question (1,613).

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Unique question identifier, prefixed by the project (`p01::...`). |
| `project` | string | Project identifier in `projects.json`. |
| `q` | string | Question text. |
| `gold.answer` | string | Reference answer (nested object `gold` with key `answer`). |
| `answer_status` | string | `answerable` or `unanswerable`. |
| `category` | string | `Evidence Reasoning`, `Conflict Resolution` or `Unanswerability`. |
| `task` | string | One of the eight evaluation tasks. |
| `primary_task`, `task_group` | string | Original annotation labels; see the mapping below. |
| `role` | string | Question perspective; `omni` denotes access to all workspace records. |
| `t` | string | Question timestamp (ISO 8601, minute precision): the moment the question is asked. |
| `evidence` | string array | Native record IDs of one representative sufficient evidence set. |
| `evidence_paths` | array of string arrays | Annotated sufficient evidence sets as native record IDs; the first equals `evidence`. |
| `frozen_representative_retrieval_units` | string array | Retrieval-unit IDs of the representative set (use these for Evidence Recall). |
| `evidence_record_count` | integer | Distinct native records in the representative set. |
| `evidence_unit_count` | integer | Retrieval units in the representative set. |
| `evidence_image_count` | integer | Representative units that have an image. |
| `evidence_modality` | string or null | `text`, `image` or `multi`; `null` for unanswerable questions. |
| `solution` | object array | Reference reasoning steps: `{"step": str, "record_ids": [str]}`. |

Evidence counts and modality are derived from `frozen_representative_retrieval_units` and the corpus. `image` means every representative unit has an image; `multi` means the set mixes textual and visual units (visual units also carry a short text header). Unanswerable questions have empty evidence sets and zero counts. `evidence_paths` lists other annotated sufficient sets; it is not a claim that every sufficient set has been enumerated.

| Original `primary_task` | `task` | `category` |
| --- | --- | --- |
| Fact | Extraction | Evidence Reasoning |
| Attribution | Attribution | Evidence Reasoning |
| Computation | Computation | Evidence Reasoning |
| Selection | Comparison | Evidence Reasoning |
| Interpretation | Interpretation | Evidence Reasoning |
| Discrepancy, Resolution | Cross-source | Conflict Resolution |
| State, Change | Temporal | Conflict Resolution |
| Abstention | Unanswerability | Unanswerability |

## Corpus: `corpus/units.json` (config `corpus`)

A JSON array with one object per retrieval record (7,140).

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Unique retrieval-unit ID (`u00000`...), used by rankings and gold evidence. |
| `record_id` | string | Native record ID, prefixed by the owning project (`p11::T:AR-00@2026-08-26T07:00`). Several units may share one native record (for example the pages of a document). |
| `text` | string | Record text with a source header (`### Slack · ...`, `### Notion tracker · ...`), or a visual record's short header. |
| `image` | string or null | Path of the original image relative to the dataset directory (`media/<sha256>.png|jpg`). |
| `timestamp` | string | Record timestamp (ISO 8601). |
| `order` | integer | Stable corpus order used for chronological presentation. |
| `image_sha256` | string or null | SHA-256 of the image file. |
| `unit_content_sha256` | string | Content fingerprint retained with the unit. |

In the parquet `corpus` config the column `image` is a `datasets.Image` feature with the embedded bytes (`None` for text units) and `image_path` keeps the original relative path string.

Retrieval ranks determine which records enter a context; corpus order determines their presentation order within it.

## Rankings: `retrieval/ranks_2b.json`, `retrieval/ranks_8b.json` (configs `retrieval_2b`, `retrieval_8b`)

Each file contains:

- `retriever`: the embedding model identifier (`Qwen/Qwen3-VL-Embedding-2B` or `-8B`).
- `top_k`: the maximum retrieval depth, 100.
- `hits`: question ID → 100 ordered hits, each `{"id": unit_id, "rank": 1..100, "score": cosine similarity}`.
- `query_sha256`: question ID → SHA-256 of the UTF-8 encoding of `json.dumps(question, ensure_ascii=False)`.

In parquet, one row per question: `id`, `retriever`, `query_sha256`, `hits` (list of `{id, rank, score}`).

Rank, not score, determines the selected records; top-10 and top-50 are prefixes of top-100. Retrieval is global across the corpus with no project filter. `retrieval/config.json` specifies both checkpoint revisions and embedding dimensions, the query and document instructions, visual preprocessing, sequence length and ranking settings (exact cosine similarity of L2-normalised embeddings, ties broken by corpus order). These embedding settings are separate from any reader's image processing.

## Projects: `projects.json` (config `projects`)

Each project has `id`, `label`, `as_of`, `questions`, `retrieval_records`, `people` (`id`, `name`, `role`), `clients` (`id`, `name`, `email`, `company_name`) and `channels` (`id`, `name`). Counts are computed from the released questions and corpus.

## Source documents: `seed_documents.json` (config `seed_documents`)

One entry per project: `project_id`, `title`, `domain`, `document_type`, `document_subtype`, `publisher_family`, `pages`, `publication_date`, `identifier`, `source_url`, `pdf_url`, `pdf_sha256`, `source_rights`. Unavailable metadata is `null`. Source PDFs are identified by their hashes; the media directory contains only the record images used for evaluation.

## Integrity: `manifest.json`, `SHA256SUMS`

`manifest.json` holds the name, version and the counts the validator checks (questions, records, media files and bytes, per-task, per-category and per-modality totals, supported retrievers and k). `SHA256SUMS` lists `<sha256>  <relative path>` for every file except itself; `mmeb download` verifies it after downloading and `mmeb validate` re-checks it.
