---
title: MMEnterprise-Bench Leaderboard
emoji: 🏆
colorFrom: blue
colorTo: indigo
sdk: static
pinned: false
license: mit
app_file: index.html
short_description: Moderated, self-reported leaderboard for MMEnterprise-Bench
---

# MMEnterprise-Bench Leaderboard

Public leaderboard for [MMEnterprise-Bench](https://github.com/mlvlab/MMEnterprise-Bench), a QA benchmark of
1,613 questions over evolving multimodal workplace records from 30 simulated collaboration projects (8 tasks in
3 categories: Evidence Reasoning, Conflict Resolution, Unanswerability). Scores are self-reported and moderated:
contributors run the official scorer (`mmeb score`) locally and upload its `metrics.json` through the
[submission form](https://huggingface.co/spaces/happy8825/mmenterprise-bench-leaderboard); entries are listed
after maintainer approval. This static page reads the approved rows from the
[leaderboard-results](https://huggingface.co/datasets/MMEnterprise-Bench/leaderboard-results) dataset and falls
back to the bundled `results.snapshot.jsonl` when that file cannot be fetched.

Links: [GitHub](https://github.com/mlvlab/MMEnterprise-Bench) ·
[Dataset](https://huggingface.co/datasets/MMEnterprise-Bench/MMEnterprise-Bench) ·
[Project page](https://mlvlab.github.io/MMEnterprise-Bench/) · Paper: arXiv coming soon
