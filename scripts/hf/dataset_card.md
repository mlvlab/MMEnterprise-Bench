---
pretty_name: MMEnterprise-Bench
license: cc-by-4.0
language:
- en
task_categories:
- question-answering
tags:
- multimodal
- enterprise
- retrieval-augmented-generation
- temporal-reasoning
- conflict-resolution
- benchmark
size_categories:
- 1K<n<10K
configs:
- config_name: questions
  default: true
  data_files:
  - split: test
    path: parquet/questions/test-*.parquet
- config_name: corpus
  data_files:
  - split: test
    path: parquet/corpus/test-*.parquet
- config_name: retrieval_2b
  data_files:
  - split: test
    path: parquet/retrieval_2b/test-*.parquet
- config_name: retrieval_8b
  data_files:
  - split: test
    path: parquet/retrieval_8b/test-*.parquet
- config_name: projects
  data_files:
  - split: test
    path: parquet/projects/test-*.parquet
- config_name: seed_documents
  data_files:
  - split: test
    path: parquet/seed_documents/test-*.parquet
---

# MMEnterprise-Bench

**Evaluating Enterprise Agents over Evolving Multimodal Records**

MMEnterprise-Bench asks whether an agent can answer questions over a simulated organization's working records: Slack threads, Notion pages, emails, meeting notes, slides, PDF pages, and standalone images that are revised over time and sometimes contradict each other. Each of the 30 projects is seeded from one public document and grows into a multimodal workspace; together the 30 workspaces hold 7,140 retrieval records, and 1,613 questions test evidence reasoning, conflict resolution across sources and time, and knowing when the records do not contain an answer.

![MMEnterprise-Bench construction pipeline](https://raw.githubusercontent.com/mlvlab/MMEnterprise-Bench/main/docs/static/figures/fig2_pipeline.png)

- Code and evaluation toolkit: https://github.com/mlvlab/MMEnterprise-Bench
- Project page: https://mlvlab.github.io/MMEnterprise-Bench/
- Baseline predictions and judgments: https://huggingface.co/datasets/MMEnterprise-Bench/baselines
- Paper: arXiv preprint (link to follow)

## At a glance

| Component | Count |
| --- | ---: |
| Projects (simulated organizations) | 30 |
| Questions | 1,613 |
| Answerable / unanswerable questions | 1,526 / 87 |
| Retrieval records | 7,140 |
| Textual / visual records | 5,725 / 1,415 |
| Record versions (document-version IDs) | 287 |
| Tasks (categories) | 8 (3) |
| Frozen retrieval rankings | Qwen3-VL-Embedding-2B and -8B, top-100 per question |

Records by source: Slack messages 4,231 · Notion entries 1,171 · PDF pages 658 · Standalone images 385 · Slides 372 · Email 210 · Meeting notes 90 · TXT/LOG 23.

Gold evidence (1,526 answerable questions): 49.1% need one record, 32.4% need two, 18.5% need three or more; 45.4% are text-only, 36.9% visual-only, and 17.7% mix text and images.

## Task taxonomy

| Category | Task | Questions | What it tests |
| --- | --- | ---: | --- |
| Evidence Reasoning | Extraction | 268 | Read a value or statement from the right record |
| Evidence Reasoning | Attribution | 80 | Who said, decided, or owned something |
| Evidence Reasoning | Computation | 310 | Arithmetic over values spread across records |
| Evidence Reasoning | Comparison | 212 | Select or rank options against a stated criterion |
| Evidence Reasoning | Interpretation | 232 | Explain what a figure, table, or message implies |
| Conflict Resolution | Cross-source | 212 | Reconcile records that disagree with each other |
| Conflict Resolution | Temporal | 212 | Track the state of something as records are revised |
| Unanswerability | Unanswerability | 87 | Recognize that the records do not support an answer |

Categories: Evidence Reasoning 1,102 · Conflict Resolution 424 · Unanswerability 87.

## Quick start

### Load with `datasets`

Every config is a Parquet table with typed nested fields; images in `corpus` are embedded, so nothing else needs to be downloaded.

```python
from datasets import load_dataset

REPO = "MMEnterprise-Bench/MMEnterprise-Bench"

questions = load_dataset(REPO, "questions", split="test")        # 1,613 rows
corpus = load_dataset(REPO, "corpus", split="test")              # 7,140 rows, `image` is a PIL image or None
ranks_2b = load_dataset(REPO, "retrieval_2b", split="test")      # 1,613 rows, 100 ordered hits each
ranks_8b = load_dataset(REPO, "retrieval_8b", split="test")      # 1,613 rows
projects = load_dataset(REPO, "projects", split="test")          # 30 rows
seed_documents = load_dataset(REPO, "seed_documents", split="test")  # 30 rows

q = questions[0]
print(q["q"], "->", q["gold"]["answer"])
units = {u["id"]: u for u in corpus}
top10 = [units[h["id"]] for h in ranks_2b[0]["hits"][:10]]
```

### Score an agent with the toolkit

The toolkit downloads the raw layout from this repo, exports per-question contexts, and scores a `predictions.jsonl` with the paper's judge protocol.

```bash
pip install git+https://github.com/mlvlab/MMEnterprise-Bench
mmeb download                                   # -> data/MMEnterprise-Bench (checksums verified)
mmeb export-contexts --retriever 2b --k 10 --output contexts.jsonl
# run your agent: one {"id": ..., "answer": ...} JSON line per question -> predictions.jsonl
export OPENAI_API_KEY=...                        # or point --judge-base-url at any OpenAI-compatible server
mmeb score predictions.jsonl --output outputs/my_agent
```

`predictions.jsonl` needs only `id` and `answer`; add `retrieved_unit_ids` to also get Evidence Recall@k for your own retriever. See the GitHub README for the full bring-your-own-agent format.

### Raw files

The top level of this repo is the exact release layout used by the toolkit and by `data/validate.py` (checksums in `SHA256SUMS`):

```text
qa/rows.json              1,613 questions
corpus/units.json         7,140 retrieval records (text + relative image path)
retrieval/config.json     embedding models, revisions, instructions, ranking settings
retrieval/ranks_2b.json   top-100 hits per question, Qwen3-VL-Embedding-2B
retrieval/ranks_8b.json   top-100 hits per question, Qwen3-VL-Embedding-8B
media/*.png|jpg           1,415 record images (named by SHA-256)
projects.json             30 projects: label, as-of time, people, clients, channels
seed_documents.json       the 30 public source documents and their rights
manifest.json, SHA256SUMS
parquet/                  the load_dataset configs above
```

```python
from huggingface_hub import snapshot_download

snapshot_download(
    "MMEnterprise-Bench/MMEnterprise-Bench", repo_type="dataset", local_dir="data/MMEnterprise-Bench",
    allow_patterns=["qa/*", "corpus/*", "retrieval/*", "media/*", "projects.json",
                    "seed_documents.json", "manifest.json", "SHA256SUMS"],
)
```

## Schema summary

Full field definitions: [data/SCHEMA.md](https://github.com/mlvlab/MMEnterprise-Bench/blob/main/data/SCHEMA.md).

**`questions`** (one row per question; identical to `qa/rows.json`)

| Field | Type | Meaning |
| --- | --- | --- |
| `id`, `project` | string | Question ID (`<project>::<local id>`) and project ID |
| `q` | string | Question text |
| `gold.answer` | string | Reference answer |
| `answer_status` | string | `answerable` or `unanswerable` |
| `category`, `task` | string | Evaluation labels (3 categories, 8 tasks) |
| `primary_task`, `task_group`, `role` | string | Original annotation labels; `role` is `omni` (full workspace access) |
| `t` | string | Question timestamp (ISO 8601); the workspace state at this time is what the question is about |
| `evidence` | list[string] | Native record IDs of the representative sufficient evidence set |
| `evidence_paths` | list[list[string]] | All annotated sufficient evidence sets (the first equals `evidence`) |
| `frozen_representative_retrieval_units` | list[string] | Retrieval-unit IDs of the representative set; the gold for Evidence Recall@k |
| `evidence_record_count`, `evidence_unit_count`, `evidence_image_count` | int | Sizes of the representative set |
| `evidence_modality` | string or null | `text`, `image`, `multi`; null for unanswerable questions |
| `solution` | list[{`step`, `record_ids`}] | Reference reasoning steps with supporting records |

**`corpus`** (one row per retrieval record)

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | string | Retrieval-unit ID (`u00003`), referenced by rankings and gold evidence |
| `record_id` | string | Native record ID; a multi-page document yields several units |
| `text` | string | Record text with a source header (channel, author, time), or the text header of a visual record |
| `image` | Image or null | The record image, embedded; null for the 5,725 text records |
| `image_path` | string or null | Original relative path (`media/<sha256>.png`) in the raw layout |
| `image_sha256` | string or null | SHA-256 of the image file; null for text records |
| `unit_content_sha256` | string | Fingerprint of the record content |
| `timestamp` | string | Record time (ISO 8601) |
| `order` | int | Stable corpus order used to interleave records chronologically |

**`retrieval_2b` / `retrieval_8b`** (one row per question): `id`, `retriever` (model ID), `query_sha256`, and `hits` = 100 ordered `{id, rank, score}`; `score` is the cosine similarity and is null where the released ranking does not carry a score (rank order is what matters; top-10 and top-50 are prefixes of top-100).

**`projects`**: `id`, `label`, `as_of`, `questions`, `retrieval_records`, `people[{id,name,role}]`, `clients[{id,name,email,company_name}]`, `channels[{id,name}]`.

**`seed_documents`**: `project_id`, `title`, `domain`, `document_type`, `document_subtype`, `publisher_family`, `pages`, `publication_date`, `identifier`, `source_url`, `pdf_url`, `pdf_sha256`, `source_rights` (null where the metadata is unavailable).

## Evaluation protocol

1. **Retrieval.** For a question, take the first *k* hits (k = 10, 50, or 100) of the chosen frozen ranking (`retrieval_2b` or `retrieval_8b`). Retrieval is global over all 7,140 records with no project filter. You may also run your own retriever over `corpus`.
2. **Context.** Sort the selected records by (`timestamp`, `order`, `id`); present each record's `text` followed by its image, then `Question: <q>`. The reader answers in free text.
3. **Judging.** An LLM judge sees only `{question, reference_answer, candidate_answer}` and returns `correct: true|false`. The paper uses GPT-5.6 Luna; the toolkit lets you swap in any OpenAI-compatible model.
4. **Metrics.** Accuracy = 100 × correct / N over all 1,613 questions (missing predictions count as incorrect), reported overall, per task, per category, by evidence modality, and by evidence count. Evidence Recall@k is macro-averaged over the 1,526 answerable questions against `frozen_representative_retrieval_units`.

## Baselines

Accuracy (%) on all 1,613 questions, Qwen3-VL-Embedding-2B retriever. Per-question predictions and judge decisions for all 10 readers × 2 retrievers × 3 depths are released in [MMEnterprise-Bench/baselines](https://huggingface.co/datasets/MMEnterprise-Bench/baselines).

**k = 10**

| Model | Extraction | Attribution | Computation | Comparison | Interpretation | Cross-source | Temporal | Unanswerability | Overall |
|---|---|---|---|---|---|---|---|---|---|
| GPT-5.6 Sol | 67.5 | 61.3 | 58.1 | 47.2 | 58.6 | 36.3 | 32.5 | 65.5 | **52.6** |
| GPT-5.6 Luna | 66.4 | 57.5 | 55.5 | 44.8 | 58.2 | 35.4 | 33.5 | 63.2 | 51.3 |
| Gemini 3.8 Flash | 67.9 | 52.5 | 54.2 | 44.3 | 34.5 | 28.8 | 30.2 | 87.4 | 47.6 |
| Gemini 3.1 Pro | 66.8 | 58.8 | 54.8 | 42.5 | 30.6 | 27.4 | 28.8 | 74.7 | 45.9 |
| Qwen3.5-27B | 63.1 | 52.5 | 37.7 | 33.5 | 28.9 | 22.6 | 20.3 | 75.9 | 38.6 |
| Qwen3.5-9B | 60.1 | 40.0 | 36.5 | 25.9 | 27.2 | 14.2 | 21.2 | 73.6 | 34.9 |
| Qwen3.5-4B | 62.7 | 40.0 | 37.4 | 31.1 | 28.4 | 19.8 | 19.8 | 55.2 | 36.0 |
| Qwen3.5-2B | 58.6 | 20.0 | 21.9 | 17.9 | 12.9 | 12.7 | 13.2 | 19.5 | 23.6 |
| InternVL3.5-8B | 63.4 | 51.3 | 31.9 | 23.1 | 27.2 | 15.6 | 19.3 | 36.8 | 32.7 |
| Kimi-VL-16B-A3B-Instruct | 56.3 | 40.0 | 14.5 | 16.0 | 16.4 | 11.8 | 14.2 | 10.3 | 22.6 |

**k = 50**

| Model | Extraction | Attribution | Computation | Comparison | Interpretation | Cross-source | Temporal | Unanswerability | Overall |
|---|---|---|---|---|---|---|---|---|---|
| GPT-5.6 Sol | 77.6 | 68.8 | 71.6 | 67.5 | 73.3 | 55.2 | 56.1 | 72.4 | **68.0** |
| GPT-5.6 Luna | 76.5 | 65.0 | 69.4 | 63.7 | 72.4 | 52.8 | 54.2 | 67.8 | 65.8 |
| Gemini 3.8 Flash | 78.0 | 71.3 | 71.6 | 65.6 | 53.4 | 46.7 | 54.2 | 74.7 | 63.9 |
| Gemini 3.1 Pro | 76.9 | 72.5 | 69.0 | 62.7 | 49.6 | 49.1 | 50.9 | 80.5 | 62.5 |
| Qwen3.5-27B | 73.9 | 56.3 | 50.3 | 43.9 | 42.7 | 35.8 | 38.7 | 71.3 | 50.3 |
| Qwen3.5-9B | 71.3 | 38.8 | 43.9 | 37.7 | 32.8 | 26.4 | 33.0 | 63.2 | 43.1 |
| Qwen3.5-4B | 69.8 | 43.8 | 48.7 | 42.9 | 35.3 | 26.4 | 30.2 | 47.1 | 43.8 |
| Qwen3.5-2B | 60.8 | 25.0 | 16.5 | 18.4 | 18.1 | 19.3 | 16.0 | 14.9 | 25.0 |
| InternVL3.5-8B | 66.4 | 53.8 | 35.5 | 28.8 | 31.0 | 24.1 | 23.1 | 29.9 | 36.6 |
| Kimi-VL-16B-A3B-Instruct | 57.5 | 35.0 | 12.6 | 19.3 | 19.4 | 15.6 | 9.9 | 13.8 | 23.1 |

Evidence Recall@k of the frozen rankings (macro over answerable questions): 2B 41.3 / 57.0 / 64.8 and 8B 43.0 / 59.6 / 67.3 at k = 10 / 50 / 100.

## Source documents and rights

Every project is seeded from one public document listed in `seed_documents.json` (12 research papers and 18 government publications): 11 under CC BY 4.0, 13 US public domain, 5 under the UK Open Government Licence v3.0, and 1 used with the authors' permission. The organizations, people, conversations, and document revisions in the workspaces are simulated; no real company data is included.

**License.** The benchmark annotations (questions, reference answers, evidence labels, solutions) and the simulated records are released under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Figures and page images embedded in `media/` (and in the `corpus` config) are derived from the seed documents and keep the rights of their source documents as listed in `seed_documents.json`; the dataset does not grant additional rights to that third-party material.

## Citation

MMEnterprise-Bench is an arXiv preprint; a BibTeX entry will be added once the arXiv ID is available. Until then please cite it as:

> Seohyun Lee\*, Jongha Kim\*, Dohwan Ko, Seoung Choi, Ji Soo Lee, Hyunwoo J. Kim. *MMEnterprise-Bench: Evaluating Enterprise Agents over Evolving Multimodal Records.* arXiv preprint, 2026.

## Links

- GitHub (toolkit, generation pipeline, docs): https://github.com/mlvlab/MMEnterprise-Bench
- Project page: https://mlvlab.github.io/MMEnterprise-Bench/
- Baselines: https://huggingface.co/datasets/MMEnterprise-Bench/baselines
- MLV Lab, KAIST: https://mlv.kaist.ac.kr/
