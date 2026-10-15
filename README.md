# MMEnterprise-Bench

**Evaluating Enterprise Agents over Evolving Multimodal Records**

[Project page](https://mlvlab.github.io/MMEnterprise-Bench/) · Paper (arXiv, coming soon) · [🤗 Dataset](https://huggingface.co/datasets/MMEnterprise-Bench/MMEnterprise-Bench) · [🤗 Baselines](https://huggingface.co/datasets/MMEnterprise-Bench/baselines)

<img src="docs/static/figures/fig1_teaser.png" alt="MMEnterprise-Bench teaser" width="100%">

MMEnterprise-Bench asks whether an agent can answer questions over a real-looking company workspace: Slack threads, emails, Notion pages, meeting notes, slide decks, PDF pages and charts that are produced, revised and contradicted over time. Each of the 30 simulated projects is grown from a public seed document, and every question comes with a reference answer, the sufficient evidence records, and a reasoning trace. Answering requires finding the right records in a shared 7,140-record corpus, reading text and images together, and resolving conflicts between sources and between versions. The benchmark ships with frozen retrieval rankings from two retrievers, so a run is reproducible without any embedding model, and with a judge protocol that scores any agent from a one-line-per-question predictions file.

**What's inside:** 1,613 questions (1,526 answerable + 87 unanswerable) · 30 projects · 7,140 retrieval records (5,725 text + 1,415 visual) · 287 record versions · 8 tasks in 3 categories · frozen top-100 rankings for a 2B and an 8B retriever.

## Quick start

```bash
git clone https://github.com/mlvlab/MMEnterprise-Bench.git
cd MMEnterprise-Bench
python -m pip install -e .                 # Python >= 3.10; only huggingface_hub is required
mmeb download                              # -> data/MMEnterprise-Bench (230 MB, SHA-256 verified)
mmeb score examples/predictions_sample.jsonl --output outputs/sample --limit 20   # needs OPENAI_API_KEY
```

`mmeb score` judges each predicted answer against the reference with an LLM judge (GPT-5.6 Luna by default; any OpenAI-compatible server works, see below) and writes `metrics.json`, `metrics.md` and `graded.jsonl`. The sample file contains 20 synthetic answers, so the command above scores just those 20 questions. Without an API key you can still try the whole flow with the mock judge used by the tests:

```bash
python tests/mock_judge.py --port 8765 &
mmeb score examples/predictions_sample.jsonl --output outputs/sample \
    --judge-base-url http://127.0.0.1:8765/v1 --judge-model mock
```

Every command is also available as `python -m mmenterprise_bench ...` and as a wrapper in `scripts/` (`scripts/download_data.bash`, `scripts/score.bash`, ...). Set `MMEB_DATA_DIR` or pass `--dataset DIR` to keep the dataset somewhere other than `data/MMEnterprise-Bench`.

## Evaluate your own agent

Your agent can be anything: an API model, a local VLM, a multi-step tool-using agent, in any language. The only contract is a predictions file, one JSON object per question:

```json
{"id": "p01::MU260919_03", "answer": "Not recorded. The 07:30 comparison only reports R@1."}
```

Optional keys: `"retrieved_unit_ids": ["u00123", ...]` (the corpus units your retriever returned; enables Evidence Recall of *your* retrieval), and, for re-aggregating already-judged outputs, `"correct": true|false` + `"judge_reason"` with `--pre-judged`. Other keys are ignored and preserved in `graded.jsonl`.

**Input.** Either use the paper's frozen contexts (first *k* hits of a released retriever, sorted chronologically) or do your own retrieval over the corpus:

```bash
# Option A: export the paper's contexts (text + image paths) for an agent written in any language
mmeb export-contexts --retriever 2b --k 10 --output contexts.jsonl
```

```python
# Option B: Python
from mmenterprise_bench import load_dataset, build_context

ds = load_dataset()                                   # auto-downloads if missing
for row in ds.rows:                                   # question dicts: id, project, q, t, ...
    context = build_context(ds, row, retriever="2b", k=10)   # [{unit_id, record_id, timestamp, text, image_path, image_sha256}, ...]
    answer = my_agent(row["q"], context)              # or search ds.units yourself
```

`examples/your_own_agent.py` is a fill-in-the-blank template that writes `predictions.jsonl`; `examples/openai_compatible_agent.py` is a complete working agent (the paper's RAG reader) for any OpenAI-compatible endpoint.

**Scoring.**

```bash
export OPENAI_API_KEY=...                      # or put it in .env (see .env.example)
mmeb score predictions.jsonl --output outputs/my_agent
```

* Accuracy = 100 × correct / **all 1,613 questions**. Questions without a prediction count as incorrect and are reported as `missing`; `observed_accuracy` is over the submitted ones. Use `--limit N` / `--question-ids` only for smoke tests.
* The judge sees only `{question, reference_answer, candidate_answer}`; its prompt is `mmenterprise_bench/prompts/judge_system.txt`. Judgments are cached under `<output>/cache/judgments/` keyed by their content, so re-runs are free.
* Breakdowns: per category (Evidence Reasoning / Conflict Resolution / Unanswerability), per task (8), answerable vs unanswerable, evidence modality (text / image / multi) and evidence-record count (1 / 2 / 3+), plus Evidence Recall when `retrieved_unit_ids` is present.
* Open-source judge: point `--judge-base-url` at any OpenAI-compatible server (for example vLLM) and set `--judge-model`; `--judge-api-key-env` names the variable holding its key (none needed for local servers). Results judged by a different model are not directly comparable with the paper's numbers.
* `mmeb score predictions.jsonl --pre-judged` re-aggregates files that already carry `correct`, such as the released baselines.

See [EVALUATION.md](EVALUATION.md) for the full protocol, options and output formats.

## Reproduce the paper baselines

`mmeb run-baseline` runs a registered reader over the frozen contexts, judges the answers and writes metrics. It is resumable (answers and judgments are cached by content) and supports all 60 paper conditions (10 readers × 2 retrievers × k ∈ {10, 50, 100}).

```bash
mmeb run-baseline --dry-run                                   # plan only, no API calls
mmeb run-baseline --models luna --retrievers 2b --k 10        # default condition (OPENAI_API_KEY)
mmeb run-baseline --models sol luna --retrievers 2b 8b --k 10 50 100
mmeb run-baseline --models gemini38flash --limit 20 --output outputs/smoke   # GEMINI_API_KEY (+ OPENAI_API_KEY for the judge)
```

| Alias | Reader | Provider / key | Alias | Reader | Provider / key |
| --- | --- | --- | --- | --- | --- |
| `sol` | GPT-5.6 Sol | OpenAI · `OPENAI_API_KEY` | `qwen27b` | Qwen3.5-27B | local vLLM (2 GPUs) |
| `luna` | GPT-5.6 Luna | OpenAI · `OPENAI_API_KEY` | `qwen9b` | Qwen3.5-9B | local vLLM |
| `gemini38flash` | Gemini 3.8 Flash | Gemini · `GEMINI_API_KEY` | `qwen4b` | Qwen3.5-4B | local vLLM |
| `gemini31pro` | Gemini 3.1 Pro | Gemini · `GEMINI_API_KEY` | `qwen2b` | Qwen3.5-2B | local vLLM |
| | | | `internvl8b` | InternVL3.5-8B | local vLLM |
| | | | `kimivl16b` | Kimi-VL-16B-A3B-Instruct | local vLLM |

Open-weight readers are served with vLLM 0.24.0 at the pinned checkpoint revisions in `mmenterprise_bench/configs/models.json`:

```bash
python -m pip install -e ".[local]"                 # in a separate environment with CUDA
mmeb serve --model qwen4b --gpus 0 --port 8000      # or: mmeb serve ... --print-command
QWEN4B_BASE_URL=http://127.0.0.1:8000/v1 mmeb run-baseline --models qwen4b --retrievers 2b 8b --k 10 50 100
```

The full grid is `mmeb run-baseline --config mmenterprise_bench/configs/full_grid.json`; re-running the same command resumes. `mmeb metrics` re-aggregates an output directory. The paper's per-question outputs for all 60 conditions are released at [🤗 MMEnterprise-Bench/baselines](https://huggingface.co/datasets/MMEnterprise-Bench/baselines) in the predictions format, so you can re-aggregate them without any API key (`mmeb score <file> --pre-judged`).

## Results

Accuracy (%) on all 1,613 questions with the Qwen3-VL-Embedding-2B retriever (Table 5 of the paper). Extr. = Extraction, Attr. = Attribution, Comp. = Computation, Cmpr. = Comparison, Interp. = Interpretation, Cross = Cross-source, Temp. = Temporal, Unans. = Unanswerability.

**k = 10 retrieved records**

| Model | Extr. | Attr. | Comp. | Cmpr. | Interp. | Cross | Temp. | Unans. | **Overall** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
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

**k = 50 retrieved records**

| Model | Extr. | Attr. | Comp. | Cmpr. | Interp. | Cross | Temp. | Unans. | **Overall** |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
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

Question counts per column: 268 / 80 / 310 / 212 / 232 / 212 / 212 / 87 / 1,613. Evidence Recall@k of the released retrievers (macro over the 1,526 answerable questions): 2B 41.3 / 57.0 / 64.8 and 8B 43.0 / 59.6 / 67.3 at k = 10 / 50 / 100. Results for k = 100, the 8B retriever and the evidence-modality breakdown are on the [project page](https://mlvlab.github.io/MMEnterprise-Bench/).

## Dataset

The dataset lives at [🤗 MMEnterprise-Bench/MMEnterprise-Bench](https://huggingface.co/datasets/MMEnterprise-Bench/MMEnterprise-Bench) (CC BY 4.0 for the annotations and simulated records; embedded figures and page images keep the rights of their source documents, listed in `seed_documents.json`). `mmeb download` fetches the raw layout the evaluator uses:

```text
data/MMEnterprise-Bench/
├── qa/rows.json               # 1,613 questions: id, project, q, gold.answer, task, category, evidence, solution, ...
├── corpus/units.json          # 7,140 retrieval records: id, record_id, text, image, timestamp, order, ...
├── retrieval/                 # config.json + ranks_2b.json / ranks_8b.json (top-100 hits per question)
├── media/                     # 1,415 original images (sha256-named PNG/JPG)
├── projects.json              # 30 projects: people, clients, channels
├── seed_documents.json        # the 30 public seed documents and their rights
├── manifest.json · SHA256SUMS
```

The same data is also available as parquet configs for `datasets`:

```python
from datasets import load_dataset
questions = load_dataset("MMEnterprise-Bench/MMEnterprise-Bench", "questions", split="test")   # 1,613 rows
corpus    = load_dataset("MMEnterprise-Bench/MMEnterprise-Bench", "corpus", split="test")      # 7,140 rows, `image` column
ranks_2b  = load_dataset("MMEnterprise-Bench/MMEnterprise-Bench", "retrieval_2b", split="test")
```

Field definitions are in [data/SCHEMA.md](data/SCHEMA.md); counts, taxonomy and sources in [data/README.md](data/README.md). `mmeb validate` checks checksums, references, labels and both rankings of a local copy.

## Generate new projects

The generation pipeline turns any seed PDF into a new simulated project (workspace, timeline, rendered records, validated questions). It needs the `[generate]` extra and an OpenAI-compatible model:

```bash
python -m pip install -e ".[generate]"
./run_project.bash --dry-run                 # stage plan for the bundled NIST CSF 2.0 example seed
./run_project.bash                           # OPENAI_API_KEY; resumable; outputs under data_generation_pipeline/outputs/
```

Configuration (`data_generation_pipeline/config.json`), stages, QA families and outputs are described in [data_generation_pipeline/README.md](data_generation_pipeline/README.md). Generated projects are new simulations; the released benchmark is the fixed snapshot on the Hub.

## Repository layout

```text
mmenterprise_bench/          # the `mmeb` package: data.py (dataset + frozen contexts), hub.py (download/verify),
                             # judge.py, score.py (bring-your-own-agent scorer), contexts.py, pipeline.py (baselines),
                             # providers.py (HTTP), serve.py (vLLM), validate.py, cli.py, configs/, prompts/
examples/                    # your_own_agent.py, openai_compatible_agent.py, predictions_sample.jsonl
scripts/                     # bash wrappers, check.bash (all offline checks), check_release.py, package_release.py, hf/
tests/                       # unit tests + mock_judge.py (OpenAI-compatible mock judge)
data/                        # README.md, SCHEMA.md, validate.py; MMEnterprise-Bench/ is downloaded (git-ignored)
data_generation_pipeline/    # seed PDF -> simulated project -> validated questions (+ README.md)
docs/                        # project page (GitHub Pages) and figures
EVALUATION.md                # detailed evaluation guide
```

## Checks and tests

```bash
bash scripts/check.bash          # release scan, dataset validation, generation dry-run, generation tests, evaluation tests
python -m unittest discover -s tests -v
```

No API key or network access is needed; the dataset checks are skipped (with a notice) when the dataset has not been downloaded. The same script runs in CI (`.github/workflows/check.yml`).

## License

Code is released under the [MIT License](LICENSE). The benchmark annotations and simulated records are released under CC BY 4.0; figures and page images embedded in the records keep the rights of their source documents (CC BY 4.0, US public domain, UK Open Government Licence 3.0, or used with the authors' permission), as listed per project in `seed_documents.json`.

## Citation

The paper is an arXiv preprint; a BibTeX entry will be added once it is listed.

## Acknowledgements

The 30 projects are grown from public seed documents (research papers, government reports, standards and guidance). Their titles, sources, identifiers and rights are listed in `seed_documents.json` and summarised in [data/README.md](data/README.md). The bundled example seed for the generation pipeline is *The NIST Cybersecurity Framework (CSF) 2.0*, a U.S. government publication.
