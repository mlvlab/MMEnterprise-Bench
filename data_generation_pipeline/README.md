# Data generation pipeline

Generate a multimodal workplace project from a seed PDF, validate its questions, and export a dataset in the MMEnterprise-Bench layout. This is the pipeline that produced the 30 released projects; run it to create new projects from your own documents.

## Quick start

Python 3.10 or newer, from the repository root:

```bash
python -m pip install -e ".[generate]"       # openai, pydantic, PyMuPDF, Pillow, matplotlib, fonttools
cp .env.example .env                         # set OPENAI_API_KEY (or export it)
./run_project.bash --dry-run                 # validate the configuration and print the stage plan
./run_project.bash                           # generate the bundled example project
```

The default configuration (`config.json`) uses the included *NIST Cybersecurity Framework 2.0* PDF (`examples/nist_csf_2_0.pdf`, a U.S. government publication, https://doi.org/10.6028/NIST.CSWP.29). To use another seed, copy `config.json`, set `seed_pdf` and `project_id`, and pass it:

```bash
./run_project.bash --config path/to/project.json
```

Paths in a configuration are relative to that configuration file. Environment variables already set in the shell take precedence over `.env`. `PYTHON` selects the interpreter (`PYTHON=.venv/bin/python ./run_project.bash`). The pipeline calls an OpenAI-compatible chat API (`api.base_url` optional) with the generation and validation models named in the configuration; API responses are cached in the project directory so an interrupted run resumes.

## Configuration

| Field | Purpose |
| --- | --- |
| `project_id` | Output directory name and record-ID prefix (letters, digits, `_`, `-`) |
| `seed_pdf` | Source document (PDF) |
| `output_dir` | Parent directory for generated projects (default `outputs`, git-ignored) |
| `models.generation` | Model used for ingestion, world building, rendering and QA drafting |
| `models.validation` | Model used for final QA validation (solver, comparison, audit) |
| `api.key_env` | Environment variable holding the API key (default `OPENAI_API_KEY`) |
| `api.base_url` | Optional OpenAI-compatible endpoint (`null` = OpenAI; `OPENAI_BASE_URL` also works) |
| `api.timeout_seconds`, `api.transport_retries` | Per-request timeout and retries |
| `ingest` | `vlm` for page-level visual extraction (default); `rules` for layout-based extraction |
| `qa.families_file` | QA family definitions and example questions (`configs/qa_families.json`) |
| `qa.families` | Which of the eight families to generate (default: all; must include an answerable family) |
| `qa.batch_size` | Candidate questions drafted per request |
| `qa.question_max_chars` | Maximum question length |
| `qa.max_images`, `qa.max_input_chars`, `qa.max_output_tokens` | Limits of the QA-drafting requests |
| `settings` | `week_start`, cast size (`n_people`, 7–9), event counts (`n_errors` = 5, `n_decisions`, `n_emails`), extraction limits (`max_figures`, `max_tables`), `chatter_per_day`, candidate target (`qa_target`), `llm_retries` |
| `quality_control` | `max_revisions`, cited-image limit (`max_images`), project-size limits (`max_project_images`, `max_project_chars`), `solver_max_tokens`, `audit_max_tokens` |

QA generation is **family-based**: `configs/qa_families.json` defines the eight families (name, category, definition, example questions) that make up the benchmark taxonomy, and `qa.families` selects which ones to draft. Unanswerable questions are perturbations of answerable candidates, so `Unanswerability` cannot be the only selected family. `qa_target` controls the number of candidates; the accepted count after validation is usually smaller.

Prompt variables such as `company`, `voices`, `events` and `records` are filled from the preceding stage's outputs; missing variables stop prompt rendering. Workspace vocabulary is selected from the source document. The simulation spans nine calendar days beginning at `settings.week_start`.

## Stages

| Stage | Output |
| --- | --- |
| `00_ingest` | Page text, figure crops, extracted tables, visual facts |
| `01_facts` | Grounded facts and table roles |
| `02_world` | Company, collaborators, voices, channels, access rules |
| `03_timeline` | Events, revisions, errors, decisions, document versions |
| `04_assets` | Images, tables, charts, reports, slides |
| `05_render` | Slack messages, email, Notion records, meeting notes |
| `06_qa` | Candidate questions per family, reference answers, evidence, verification reports |
| `07_validate` | Validated questions and audit history |
| `08_export` | Dataset JSON and original image files |

Generation uses schema checks and accumulated rejection feedback. Workspace rendering also checks event constraints and reviews busy Slack conversations for naturalness. Final validation runs an evidence-only solver, a candidate–reference comparison and an audit over the complete project text and cited images; suggested changes to questions, answers or evidence are checked again. Candidates that exceed the cited-image limit are rejected rather than silently trimmed. Prompts are plain Markdown files under `prompts/<stage>/`.

## Resume and inspect

```bash
./run_project.bash --dry-run
./run_project.bash --resume
./run_project.bash --to 05_render
./run_project.bash --from 06_qa
./run_project.bash --force
```

Completed stages are skipped by default. `--from` regenerates a stage and invalidates downstream completion markers; `--to` stops after a stage. API responses are cached, including the separate requests of validation retries. If the configuration, seed, QA families or pipeline code change, choose a new `project_id` or use `--force`. Failed requests never create completion markers.

## Export

The generated dataset is written to `<output_dir>/<project_id>/08_export/`:

```text
qa/rows.json
corpus/units.json
projects.json
media/
manifest.json
```

Question rows contain `q`, `gold.answer`, `answer_status`, the task labels (`category`, `task`, `primary_task`), evidence record IDs and representative retrieval-unit IDs. Visual retrieval units contain attachment metadata and an image path; curator descriptions and extracted visual facts are excluded. Media files preserve their original bytes.

New projects have their own questions and records. The released benchmark is the fixed snapshot on the Hub; to evaluate a new project with the retrieval protocol, build rankings for it first (`retrieval/config.json` in the released data documents the embedding settings).

## Offline checks

```bash
python -m unittest discover -s data_generation_pipeline/tests -v
```

These tests cover configuration, resumption, stage invalidation, prompt rendering, font rendering, family-based QA drafting, validation gates and export integrity without calling a model API. `bash scripts/check.bash` runs them together with the evaluation tests.
