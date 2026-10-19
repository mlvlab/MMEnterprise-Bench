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

Question answering over simulated company workspaces (Slack, email, Notion, meeting notes, slides, PDF pages, charts) whose records are revised and contradicted over time. 1,613 questions · 30 projects · 7,140 retrieval records (5,725 text + 1,415 visual) · 8 tasks in 3 categories (Evidence Reasoning, Conflict Resolution, Unanswerability).

Code and evaluation toolkit: [github.com/mlvlab/MMEnterprise-Bench](https://github.com/mlvlab/MMEnterprise-Bench) · Project page: [mlvlab.github.io/MMEnterprise-Bench](https://mlvlab.github.io/MMEnterprise-Bench/)

## Use

```python
from datasets import load_dataset

questions = load_dataset("MMEnterprise-Bench/MMEnterprise-Bench", "questions", split="test")   # 1,613 rows
corpus    = load_dataset("MMEnterprise-Bench/MMEnterprise-Bench", "corpus", split="test")      # 7,140 records, `image` column
ranks     = load_dataset("MMEnterprise-Bench/MMEnterprise-Bench", "retrieval_2b", split="test") # top-100 hits per question

q = questions[0]
q["id"], q["q"], q["gold"]["answer"], q["task"], q["frozen_representative_retrieval_units"]
```

| config | rows | key columns |
| --- | ---: | --- |
| `questions` | 1,613 | `id`, `project`, `q`, `gold.answer`, `answer_status`, `category`, `task`, `t`, `frozen_representative_retrieval_units` (gold evidence), `solution` |
| `corpus` | 7,140 | `id`, `record_id`, `text`, `image` (PNG/JPG or null), `timestamp`, `order` |
| `retrieval_2b` / `retrieval_8b` | 1,613 | `id`, `hits` = 100 × `{id, rank, score}` from Qwen3-VL-Embedding-2B / 8B |
| `projects` / `seed_documents` | 30 | people, channels; source documents and rights |

The same data is also stored as raw files at the top level of this repo (`qa/rows.json`, `corpus/units.json`, `retrieval/`, `media/`, `SHA256SUMS`), which is what the toolkit downloads:

```bash
pip install git+https://github.com/mlvlab/MMEnterprise-Bench
mmeb download        # -> data/MMEnterprise-Bench (230 MB, checksums verified)
```

## Evaluate your agent

```bash
mmeb export-contexts --retriever 2b --k 10 --output contexts.jsonl   # the paper's inputs: top-k records per question (text + image paths)
# run your agent -> predictions.jsonl, one line per question: {"id": "p01::MU260919_03", "answer": "..."}
mmeb score predictions.jsonl --output outputs/my_agent                 # OPENAI_API_KEY; LLM judge (GPT-5.6 Luna, paper prompt)
```

* Accuracy = correct / 1,613 (the judge compares your answer with the reference; equivalent abstention counts as correct for unanswerable questions). `metrics.md` breaks it down by category, task and evidence modality.
* Your agent may use the released rankings or its own retrieval over `corpus`; add `"retrieved_unit_ids"` to each line to get Evidence Recall.
* Any OpenAI-compatible judge works: `--judge-base-url`, `--judge-model`. Details: [EVALUATION.md](https://github.com/mlvlab/MMEnterprise-Bench/blob/main/EVALUATION.md).

## License

Annotations and simulated records: CC BY 4.0. Embedded figures and page images keep the rights of their source documents, listed in `seed_documents.json`. The paper is an arXiv preprint; a BibTeX entry will follow.
