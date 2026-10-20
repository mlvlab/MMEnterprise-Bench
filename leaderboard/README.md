---
title: MMEnterprise-Bench Leaderboard
emoji: 🏆
colorFrom: blue
colorTo: indigo
sdk: gradio
sdk_version: 5.50.0
python_version: "3.10"
app_file: app.py
pinned: false
license: mit
short_description: Moderated, self-reported leaderboard for MMEnterprise-Bench
---

# MMEnterprise-Bench Leaderboard

Moderated leaderboard for [MMEnterprise-Bench](https://github.com/mlvlab/MMEnterprise-Bench), a QA benchmark over
simulated company workspaces whose records are revised and contradicted over time (1,613 questions, 8 tasks in
3 categories: Evidence Reasoning, Conflict Resolution, Unanswerability).

Scores are **self-reported**: you run the official scorer locally with your own judge API key and upload the
`metrics.json` it writes. The Space itself never calls an LLM. Every upload lands in a private review queue and is
listed only after a maintainer approves it.

## How to submit

1. Install the benchmark package and download the data:

   ```bash
   pip install git+https://github.com/mlvlab/MMEnterprise-Bench
   mmeb download
   ```

2. Run your agent and write `predictions.jsonl` (`{"id": ..., "answer": ...}` per line, one per question;
   `retrieved_unit_ids` is optional and enables Evidence Recall of your own retrieval).

3. Score it with the official protocol (needs `OPENAI_API_KEY`, default judge `gpt-5.6-luna`):

   ```bash
   mmeb score predictions.jsonl --output outputs/my_agent
   ```

4. Open the **Submit** tab, fill in model name, organization, link, contact e-mail (kept private), the retrieval
   setting and judge model, upload `outputs/my_agent/metrics.json` (and, recommended, `predictions.jsonl` for
   audit), tick the consent box and submit. You get a receipt id; the entry shows up once reviewed.

## What is checked

The upload must be an unmodified `mmeb score` output over the full benchmark: 1,613 questions, the fixed task and
category sizes, internally consistent counts (`sum(tasks.correct) == correct`, `accuracy == 100 * correct / 1613`),
and a fresh judge run (`judge.pre_judged` must be false; re-aggregated files are refused). Missing predictions count
as incorrect. Limits: `metrics.json` up to 1 MB, `predictions.jsonl` up to 25 MB, 5 submissions per contact per day.

## Settings

| Setting | Meaning |
|---|---|
| Released retrieval 2B / 8B · k=10, 50, 100 | the frozen top-k records of the released retriever (`mmeb export-contexts --retriever 2b --k 10`) |
| Own retrieval | your own retriever over the workspace records |
| Other / full workspace | anything else (describe it in the notes) |

## For maintainers

The Space reads `HF_TOKEN`, `ADMIN_PASSWORD`, `QUEUE_REPO`, `RESULTS_REPO` and `STORAGE=hf` from its secrets and
variables. Submissions are stored in the private queue dataset as `submissions/<utc-timestamp>_<slug>/{meta.json,
metrics.json, predictions.jsonl}`; approved rows are appended to `results.jsonl` in the results dataset. The
**Admin** tab (password) lists pending entries with the parsed headline numbers and lets you approve, reject (with a
reason) or hide rows. Source code and tests: `leaderboard/` in the GitHub repository; `python deploy.py` recreates
the repos, seeds the paper baselines and uploads the app.
