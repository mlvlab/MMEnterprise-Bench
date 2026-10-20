"""Create (privately) and update the leaderboard Space and its two dataset repos. Idempotent.

    HF_TOKEN=... ADMIN_PASSWORD=... python deploy.py --baselines path/to/baselines_metrics.json

Steps: private dataset repos (queue + results) -> seed results.jsonl from the paper baselines when it does not
exist yet (``--reseed`` replaces the paper rows and keeps community rows) -> private Gradio Space -> upload this folder (without tests) -> secrets
HF_TOKEN / ADMIN_PASSWORD and variables QUEUE_REPO / RESULTS_REPO / STORAGE -> restart -> wait until RUNNING.
Secrets are read from the environment only and never written to disk or printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from huggingface_hub import HfApi

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rows import PAPER_DATE  # noqa: E402
from seed_results import seed_rows  # noqa: E402
from storage import results_from_jsonl, results_to_jsonl  # noqa: E402

IGNORE = ["tests/*", "tests/**", "__pycache__/*", "**/__pycache__/*", "*.pyc", ".pytest_cache/*", ".pytest_cache/**",
          ".ruff_cache/*", ".ruff_cache/**", "*.png", ".*"]

QUEUE_README = """---
license: mit
---
# MMEnterprise-Bench leaderboard queue (private)

Raw submissions from the leaderboard Space. One folder per upload: `submissions/<utc-timestamp>_<slug>/` with
`meta.json` (form fields, `status` pending|approved|rejected|hidden, admin notes, parsed headline numbers),
`metrics.json` (the uploaded `mmeb score` output) and optionally `predictions.jsonl`. Contains contact e-mails:
keep this repository private.
"""

RESULTS_README = """---
license: mit
---
# MMEnterprise-Bench leaderboard results

`results.jsonl`: one approved leaderboard entry per line (paper baselines with `source: "paper"`, community
entries with `source: "community"`). Fields: id, model, organization, link, setting, judge, date, source, overall,
categories{ER,CR,UNS}, tasks{8}, missing, answerability, evidence_modality, evidence_count, retrieval_recall,
notes, hidden. Accuracy is over all 1,613 questions; missing predictions count as incorrect.
"""


def ensure_dataset(api: HfApi, repo_id: str, readme: str) -> None:
    api.create_repo(repo_id, repo_type="dataset", private=True, exist_ok=True)
    info = api.repo_info(repo_id, repo_type="dataset")
    if not info.private:
        print(f"WARNING: {repo_id} is public; this script never changes visibility.", file=sys.stderr)
    if not api.file_exists(repo_id, "README.md", repo_type="dataset"):
        api.upload_file(path_or_fileobj=readme.encode("utf-8"), path_in_repo="README.md", repo_id=repo_id,
                        repo_type="dataset", commit_message="dataset card")
    print(f"dataset ready: https://huggingface.co/datasets/{repo_id} (private={info.private})")


def seed_results(api: HfApi, repo_id: str, baselines: str | None, reseed: bool, date: str = PAPER_DATE) -> None:
    exists = api.file_exists(repo_id, "results.jsonl", repo_type="dataset")
    if exists and not reseed:
        print("results.jsonl already present; not reseeding (use --reseed to overwrite)")
        return
    if not baselines:
        raise SystemExit("results.jsonl is missing: pass --baselines <metrics.json> to seed it")
    rows = seed_rows(json.loads(Path(baselines).read_text(encoding="utf-8")), date)
    kept = []
    if exists:   # reseed: replace the paper rows, keep every community row (approved and hidden alike)
        from huggingface_hub import hf_hub_download

        current = Path(hf_hub_download(repo_id, "results.jsonl", repo_type="dataset", token=api.token,
                                       force_download=True)).read_text(encoding="utf-8")
        paper_ids = {r["id"] for r in rows}
        kept = [r for r in results_from_jsonl(current) if r.get("source") != "paper" and r.get("id") not in paper_ids]
    api.upload_file(path_or_fileobj=results_to_jsonl(rows + kept).encode("utf-8"), path_in_repo="results.jsonl",
                    repo_id=repo_id, repo_type="dataset",
                    commit_message=f"seed {len(rows)} paper baseline rows (+{len(kept)} community rows kept)")
    print(f"seeded {len(rows)} paper rows into {repo_id} ({len(kept)} community rows kept)")


def wait_running(api: HfApi, space: str, timeout: float) -> str:
    start = time.time()
    last = None
    while time.time() - start < timeout:
        runtime = api.get_space_runtime(space)
        stage = str(runtime.stage)
        if stage != last:
            print(f"  stage: {stage} (+{time.time() - start:.0f}s)")
            last = stage
        if stage.endswith("RUNNING"):
            return stage
        if any(bad in stage for bad in ("ERROR", "STOPPED", "PAUSED")):
            raise SystemExit(f"Space ended in stage {stage}; check the logs at https://huggingface.co/spaces/{space}")
        time.sleep(10)
    raise SystemExit(f"Space not RUNNING after {timeout:.0f}s (last stage {last})")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--space", default=os.environ.get("SPACE_REPO", "MMEnterprise-Bench/leaderboard"))
    parser.add_argument("--queue-repo", default=os.environ.get("QUEUE_REPO", "MMEnterprise-Bench/leaderboard-queue"))
    parser.add_argument("--results-repo",
                        default=os.environ.get("RESULTS_REPO", "MMEnterprise-Bench/leaderboard-results"))
    parser.add_argument("--baselines", default=None, help="baselines metrics.json used to seed results.jsonl")
    parser.add_argument("--reseed", action="store_true",
                        help="rewrite the paper rows of results.jsonl from --baselines (community rows are kept)")
    parser.add_argument("--date", default=PAPER_DATE, help="date stored on seeded paper rows")
    parser.add_argument("--no-wait", action="store_true", help="do not wait for the Space to reach RUNNING")
    parser.add_argument("--timeout", type=float, default=900, help="seconds to wait for RUNNING")
    args = parser.parse_args(argv)

    token = os.environ.get("HF_TOKEN")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if not token or not admin_password:
        raise SystemExit("set HF_TOKEN and ADMIN_PASSWORD in the environment (never on the command line)")
    api = HfApi(token=token)

    ensure_dataset(api, args.queue_repo, QUEUE_README)
    ensure_dataset(api, args.results_repo, RESULTS_README)
    seed_results(api, args.results_repo, args.baselines, args.reseed, args.date)

    api.create_repo(args.space, repo_type="space", space_sdk="gradio", private=True, exist_ok=True)
    info = api.repo_info(args.space, repo_type="space")
    if not info.private:
        print(f"WARNING: Space {args.space} is public; this script never changes visibility.", file=sys.stderr)
    api.upload_folder(folder_path=str(HERE), repo_id=args.space, repo_type="space", ignore_patterns=IGNORE,
                      commit_message="deploy leaderboard app")
    api.add_space_secret(args.space, "HF_TOKEN", token, description="write token for the queue/results datasets")
    api.add_space_secret(args.space, "ADMIN_PASSWORD", admin_password, description="Admin tab password")
    api.add_space_variable(args.space, "QUEUE_REPO", args.queue_repo)
    api.add_space_variable(args.space, "RESULTS_REPO", args.results_repo)
    api.add_space_variable(args.space, "STORAGE", "hf")
    try:
        api.restart_space(args.space)   # pick up new secrets/variables; a brand-new Space is already building
    except Exception as exc:
        print(f"restart skipped ({type(exc).__name__}); the Space builds from the upload", file=sys.stderr)
    print(f"space uploaded: https://huggingface.co/spaces/{args.space} (private={info.private})")
    if not args.no_wait:
        stage = wait_running(api, args.space, args.timeout)
        print(f"space is {stage}: https://huggingface.co/spaces/{args.space}")


if __name__ == "__main__":
    main()
