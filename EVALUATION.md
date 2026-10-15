# Evaluation guide

This guide covers the three ways to evaluate on MMEnterprise-Bench:

1. **Bring your own agent** – produce a `predictions.jsonl` with any system and score it with `mmeb score`.
2. **Reproduce the paper baselines** – `mmeb run-baseline` runs a registered reader over the frozen contexts and judges it.
3. **Re-aggregate released outputs** – `mmeb score --pre-judged` on the files from [🤗 MMEnterprise-Bench/baselines](https://huggingface.co/datasets/MMEnterprise-Bench/baselines).

All commands run from the repository root after `python -m pip install -e .` and `mmeb download`. The dataset directory defaults to `data/MMEnterprise-Bench`; override it with `--dataset DIR` or `MMEB_DATA_DIR`. Credentials are read from the environment or from a `.env` file in the current directory (`NAME=value` lines; variables already set in the shell win).

## 1. The protocol

**Questions.** 1,613 questions over 30 projects; 1,526 answerable and 87 unanswerable. Each has a reference answer (`gold.answer`), a task (`task`, one of eight) and a category (`category`, one of three). Answerable questions list a representative sufficient evidence set as corpus unit IDs (`frozen_representative_retrieval_units`).

**Corpus and retrieval.** The corpus is global: 7,140 retrieval units across all projects (5,725 text, 1,415 with an image). The release includes frozen top-100 rankings per question from Qwen3-VL-Embedding-2B and -8B (`retrieval/ranks_2b.json`, `ranks_8b.json`), so no embedding model or GPU is needed. Top-10 and top-50 are prefixes of top-100.

**Context construction (frozen).** For the paper's RAG setting, the reader input for a question is: take the first *k* hits (k ∈ {10, 50, 100}) of the chosen retriever, sort them by `(timestamp, order, id)`, present each unit's stored `text` followed by its image (if any), then append `Question: <q>`. The reader system prompt is `mmenterprise_bench/prompts/answer_system.txt`. No OCR transcript, reference, evidence annotation or task label is given to the reader. `Dataset.selected(row, retriever, k)`, `build_context(...)` and `mmeb export-contexts` all return exactly this ordering.

**Judging.** The judge receives a JSON object with exactly `question`, `reference_answer` and `candidate_answer` under the system prompt `mmenterprise_bench/prompts/judge_system.txt`, and must return `{"correct": true|false, "reason": "..."}`. The paper's judge is GPT-5.6 Luna (`reasoning_effort=low`, `max_completion_tokens=4096`, JSON output). Semantically equivalent answers count; an equivalent abstention is correct when the reference says the information is unavailable; refusing when the reference gives a concrete answer is incorrect.

**Metrics.** Accuracy = 100 × correct / N over **all** questions in the selection (the whole benchmark unless `--limit`/`--question-ids` are used). Missing predictions and inputs a model could not process count as incorrect. Evidence Recall@k is the macro average, over answerable questions, of the fraction of the representative evidence set found in the retrieved units.

## 2. Bring your own agent

### 2.1 Input

Option A – the frozen contexts as JSONL, usable from any language:

```bash
mmeb export-contexts --retriever 2b --k 10 --output contexts.jsonl        # 1,613 lines
mmeb export-contexts --retriever 8b --k 50 --limit 100 --output dev.jsonl  # first 100 questions
mmeb export-contexts ... --include-gold                                     # adds reference_answer, task, gold_unit_ids (debugging only)
```

Each line:

```json
{"id": "p01::MU260919_03", "project": "p01", "question": "...", "timestamp": "2026-09-03T11:20",
 "records": [{"unit_id": "u01208", "record_id": "p01::S019", "timestamp": "2026-08-27T10:00",
              "text": "### #blim-research-experiments · sent 2026-08-27T10:00\nPriya Nwosu: ...",
              "image_path": "media/<sha256>.png"}],
 "retriever": "2b", "k": 10}
```

`image_path` is relative to the dataset directory and `null` for text-only records. `timestamp` on the question is the moment the question is asked (records after it do not exist yet in the simulation).

Option B – Python:

```python
from mmenterprise_bench import load_dataset, build_context

ds = load_dataset()                      # Dataset; ds.rows (questions), ds.units (unit_id -> record)
row = ds.rows[0]
context = build_context(ds, row, retriever="2b", k=10)
# [{"unit_id", "record_id", "timestamp", "text", "image_path" (absolute or None), "image_sha256"}, ...]
```

Option C – your own retrieval. Iterate over `ds.units.values()` (each has `id`, `record_id`, `text`, `image` (relative path or `None`), `timestamp`, `order`) and build your own index. Report the unit IDs you retrieved in `retrieved_unit_ids` so the scorer can compute Evidence Recall of your retriever. Any retrieval depth and any project filtering is allowed, but state it when you report results.

### 2.2 Output

`predictions.jsonl`, one object per question:

| Key | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Question ID from `qa/rows.json` |
| `answer` | yes | Final answer, plain text (what the judge sees) |
| `retrieved_unit_ids` | no | Corpus unit IDs your system retrieved, in rank order |
| `correct`, `judge_reason` | only with `--pre-judged` | Existing judgment to re-aggregate |
| anything else | no | Ignored by the scorer, copied into `graded.jsonl` |

Duplicate IDs are an error. IDs that are not in the dataset are ignored and listed in `metrics.json` (`unknown_prediction_ids`).

### 2.3 Scoring

```bash
export OPENAI_API_KEY=...
mmeb score predictions.jsonl --output outputs/my_agent
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--output DIR` | `outputs/score/<file stem>` | Where metrics and the judgment cache go |
| `--judge-model M` | `gpt-5.6-luna` | Judge model ID |
| `--judge-base-url URL` | `https://api.openai.com/v1` | Any OpenAI-compatible chat-completions endpoint |
| `--judge-api-key-env VAR` | `OPENAI_API_KEY` | Variable holding the judge key (optional for local servers) |
| `--judge-concurrency N` | 8 | Parallel judge requests |
| `--pre-judged` | off | Use the file's `correct` field; no judge calls |
| `--retriever {2b,8b} --k {10,50,100}` | `2b`, 10 | Released ranking reported as a recall reference |
| `--limit N`, `--question-ids ...` | all | Score a subset (smoke tests only; N changes) |

Outputs in `--output`:

* `metrics.json` – overall `accuracy`, `observed_accuracy`, `correct`, `missing`, `submitted`, status counts; breakdowns `categories`, `tasks`, `answerability`, `evidence_modality` (text / image / multi, answerable only), `evidence_count` (1 / 2 / 3+ gold records, answerable only); `retrieval` (your Evidence Recall, when `retrieved_unit_ids` is present); `reference_retrieval` (the released retriever's recall on the same questions); `judge` (model, endpoint, API calls, cache hits).
* `metrics.md` – the same as ready-to-paste Markdown tables (also printed to stdout).
* `graded.jsonl` – one line per question in release order: your prediction's keys plus `status` (`graded` / `missing` / `invalid` / `unsupported` / `error`), `correct`, `judge_reason`, `task`, `category`, `answer_status`, `reference_answer`.
* `cache/judgments/<sha256>.json` – judgments keyed by `{question, reference, candidate, judge model, generation settings, prompt}`. Re-running a score with the same judge makes no API calls; changing the judge model or prompt invalidates the cache.

Transient judge failures (rate limits, timeouts) are retried with backoff; if some judgments still fail, the run exits with status 2, `accuracy` is `null`, and re-running the same command completes the missing judgments from the cache. Authentication and billing errors stop the run immediately.

### 2.4 Using an open-source judge

Any OpenAI-compatible server can judge. For example, with vLLM serving a model as `my-judge` on port 8001:

```bash
mmeb score predictions.jsonl --output outputs/my_agent_oss_judge \
    --judge-base-url http://127.0.0.1:8001/v1 --judge-model my-judge --judge-api-key-env VLLM_API_KEY
```

The judge must support `response_format={"type": "json_object"}` and return the two-key JSON object; invalid responses are retried and then reported as errors. Numbers obtained with a different judge are not directly comparable to the paper's, so report the judge model alongside the accuracy.

For development without any model, `tests/mock_judge.py` is a deterministic OpenAI-compatible judge (correct iff the candidate contains `[MOCK-CORRECT]` or equals the reference):

```bash
python tests/mock_judge.py --port 8765 &
mmeb score examples/predictions_sample.jsonl --judge-base-url http://127.0.0.1:8765/v1 --judge-model mock --output outputs/sample
```

### 2.5 Python API

```python
from mmenterprise_bench import Judge, load_dataset, score_predictions

ds = load_dataset()
judge = Judge(model="gpt-5.6-luna")                       # or Judge(model="my-judge", base_url="http://127.0.0.1:8001/v1")
metrics = score_predictions(ds, "predictions.jsonl", output_dir="outputs/my_agent", judge=judge)
print(metrics["accuracy"], metrics["tasks"]["Temporal"]["accuracy"])
```

`score_predictions` also accepts a `{id: prediction}` dict instead of a path, `pre_judged=True`, and `limit`/`question_ids`.

## 3. Reproducing the paper baselines

```bash
mmeb run-baseline --dry-run                                        # validates the plan, no API calls
mmeb run-baseline                                                  # luna, 2b, k=10 (OPENAI_API_KEY)
mmeb run-baseline --models sol --limit 5 --output outputs/sol_smoke
mmeb run-baseline --models sol luna --retrievers 2b 8b --k 10 50 100
mmeb run-baseline --models gemini38flash gemini31pro --retrievers 2b 8b --k 10 50 100   # GEMINI_API_KEY
mmeb run-baseline --validate --retrievers 2b 8b --k 10 50 100      # offline: rankings + image checksums
```

`OPENAI_API_KEY` is needed for the default judge in every run, including runs whose reader is Gemini or a local model.

### Configuration

`mmeb run-baseline` reads `mmenterprise_bench/configs/default.json` (or `--config FILE`); CLI options override the file.

| Option | Purpose |
| --- | --- |
| `dataset` | Dataset directory containing `qa`, `corpus`, `retrieval`, `media` |
| `output` | Answers, judgments and reports (default `outputs/eval`) |
| `models` | Reader aliases from `mmenterprise_bench/configs/models.json` (table below) |
| `retrievers` | `2b`, `8b`, or both |
| `k` | Any combination of 10, 50, 100 |
| `concurrency`, `judge_concurrency` | Parallel reader / judge requests |
| `timeout_seconds`, `max_attempts` | Per-request timeout and retries |
| `question_ids`, `limit` | Subset selection |
| `model_overrides` | Per-alias overrides of fields in `mmenterprise_bench/configs/models.json` |
| `judge` | Judge model, endpoint, key variable and generation settings |
| `capacity_errors_as_incorrect` | Count confirmed context-length / image-count rejections as incorrect (paper policy) |

Reader aliases and their generation settings (`mmenterprise_bench/configs/models.json`):

| Alias | Model ID | Provider | Output tokens |
| --- | --- | --- | ---: |
| `sol` | `gpt-5.6-sol` | OpenAI | 8,192 |
| `luna` | `gpt-5.6-luna` | OpenAI | 8,192 |
| `gemini38flash` | `gemini-3.8-flash` | Gemini | 8,192 |
| `gemini31pro` | `gemini-3.1-pro-preview` | Gemini | 8,192 |
| `qwen27b` | `Qwen/Qwen3.5-27B` | local (vLLM, 2 GPUs) | 8,192 |
| `qwen9b` | `Qwen/Qwen3.5-9B` | local (vLLM) | 8,192 |
| `qwen4b` | `Qwen/Qwen3.5-4B` | local (vLLM) | 8,192 |
| `qwen2b` | `Qwen/Qwen3.5-2B` | local (vLLM) | 8,192 |
| `internvl8b` | `OpenGVLab/InternVL3_5-8B` | local (vLLM) | 2,048 |
| `kimivl16b` | `moonshotai/Kimi-VL-A3B-Instruct` | local (vLLM) | 2,048 |

OpenAI readers use low reasoning effort without an explicit temperature. Gemini uses temperature 1, low thinking and high media resolution. Local readers use temperature 0; Qwen thinking is disabled. `OPENAI_BASE_URL` / `GEMINI_BASE_URL` override the hosted endpoints.

### Local readers

Install vLLM 0.24.0 in a separate environment and launch a reader at its pinned checkpoint revision:

```bash
python -m pip install -e ".[local]"
mmeb serve --model qwen4b --gpus 0 --port 8000          # --print-command shows the vLLM command instead
```

Then, from another terminal:

```bash
export QWEN4B_BASE_URL="http://127.0.0.1:8000/v1"
mmeb run-baseline --models qwen4b --retrievers 2b 8b --k 10 50 100
```

Each local alias supports `<ALIAS>_BASE_URL` and `<ALIAS>_MODEL_ID` (alias in upper case, for example `INTERNVL8B_BASE_URL`), and `VLLM_API_KEY` supplies an optional server key. The runner checks `/models` before generating. Run local models sequentially on one server, or give each its own endpoint; `mmenterprise_bench/configs/full_grid.json` lists all ten readers for environments where every endpoint is available (10 × 2 × 3 × 1,613 = 96,780 answer cells).

### Outputs and resuming

```text
outputs/eval/
├── cache/answers/       # content-addressed reader responses
├── cache/judgments/     # content-addressed judgments
├── cells/               # one result per question and condition
├── runs/                # the configuration and question IDs of each invocation
├── results.jsonl        # selected cells
├── metrics.json         # overall, task, answerability and evidence-recall metrics per condition
└── metrics.csv          # flat model / retriever / k table
```

Answer identities bind the ordered record text, image checksums, question, model settings and reader prompt; judgment identities additionally bind the reference, candidate, judge settings and judge prompt. Rate limits, transport errors and malformed judgments stay pending and are completed by re-running; authentication and billing errors stop scheduling. A confirmed context-length or image-count rejection is recorded as `unsupported` and counted as incorrect (two InternVL3.5-8B conditions in the paper are affected). Accuracy is reported only when every selected question is graded or unsupported; otherwise `observed_accuracy` and `coverage` describe the partial run. Stages can be run separately with `--stage answer`, `--stage judge`, and `mmeb metrics` re-aggregates without API calls.

## 4. Released baseline outputs

[🤗 MMEnterprise-Bench/baselines](https://huggingface.co/datasets/MMEnterprise-Bench/baselines) contains `predictions/<alias>/<retriever>_k<k>.jsonl` for all 60 conditions in the predictions format, with the paper's judgments (`correct`, `judge_reason`, `status`). Re-aggregate any of them without an API key:

```bash
hf download MMEnterprise-Bench/baselines --repo-type dataset --local-dir baselines
mmeb score baselines/predictions/sol/2b_k10.jsonl --pre-judged --output outputs/sol_2b_k10
```

## 5. Tests

```bash
python -m unittest discover -s tests -v     # ~35 offline tests, no API key
bash scripts/check.bash                     # plus release scan, dataset validation and generation checks
```

`tests/test_pipeline.py` covers the baseline runner (resume, cache identities, capacity errors), `tests/test_providers_http.py` the HTTP client, `tests/test_score.py` the scorer (missing predictions, pre-judged mode, recall, outputs, mock HTTP judge), `tests/test_contexts.py` the context export, and `tests/test_cli_hub.py` the CLI and the Hub download logic.
