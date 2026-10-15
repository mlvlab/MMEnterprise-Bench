#!/usr/bin/env python3
"""Upload the MMEnterprise-Bench dataset to the Hugging Face Hub.

The repo layout after upload:

    <raw release layout>   qa/, corpus/, retrieval/, media/, projects.json, seed_documents.json,
                           manifest.json, SHA256SUMS  (byte-for-byte the local data directory)
    parquet/<config>/      built by scripts/hf/build_parquet.py
    README.md              scripts/hf/dataset_card.md

The repo is created PRIVATE. Making it public is a deliberate, manual step in
the repo Settings on the Hub (or ``--public``, which is off by default).
The token is read from the ``HF_TOKEN`` environment variable only.

Usage:
    export HF_TOKEN=...   # never put the token in a file inside this repository
    python scripts/hf/upload_dataset.py \
        --data-root data/MMEnterprise-Bench \
        --parquet build/parquet \
        --card scripts/hf/dataset_card.md
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from huggingface_hub import HfApi

DEFAULT_REPO = "MMEnterprise-Bench/MMEnterprise-Bench"
RAW_PATTERNS = ["qa/*", "corpus/*", "retrieval/*", "media/*", "projects.json",
                "seed_documents.json", "manifest.json", "SHA256SUMS"]
MAX_ATTEMPTS = 5


def token_from_env() -> str:
    token = os.environ.get("HF_TOKEN")
    if not token:
        sys.exit("HF_TOKEN is not set. Export a Hugging Face write token and retry.")
    return token


def with_retries(label: str, call):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return call()
        except Exception as error:  # transient network / Hub errors
            if attempt == MAX_ATTEMPTS:
                raise
            wait = 15 * attempt
            print(f"{label}: attempt {attempt} failed ({type(error).__name__}: {error}); retrying in {wait}s")
            time.sleep(wait)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-id", default=DEFAULT_REPO)
    parser.add_argument("--data-root", type=Path, default=Path("data/MMEnterprise-Bench"),
                        help="Raw release directory (uploaded to the repo top level)")
    parser.add_argument("--parquet", type=Path, default=Path("build/parquet"),
                        help="Output of build_parquet.py (uploaded under parquet/)")
    parser.add_argument("--card", type=Path, default=Path("scripts/hf/dataset_card.md"),
                        help="Dataset card (uploaded as README.md)")
    parser.add_argument("--skip-raw", action="store_true", help="Do not upload the raw layout")
    parser.add_argument("--skip-parquet", action="store_true", help="Do not upload parquet/")
    parser.add_argument("--skip-card", action="store_true", help="Do not upload README.md")
    parser.add_argument("--public", action="store_true",
                        help="Create the repo as public. OFF by default: the maintainer flips visibility "
                             "by hand in the Hub Settings after review.")
    args = parser.parse_args()

    api = HfApi(token=token_from_env())
    url = api.create_repo(args.repo_id, repo_type="dataset", private=not args.public, exist_ok=True)
    info = api.dataset_info(args.repo_id)
    print(f"repo: {url}  (private={info.private})")

    if not args.skip_raw:
        if not (args.data_root / "SHA256SUMS").is_file():
            sys.exit(f"{args.data_root} does not look like the release directory (no SHA256SUMS)")
        print(f"uploading raw layout from {args.data_root} ...")
        with_retries("raw upload", lambda: api.upload_folder(
            repo_id=args.repo_id, repo_type="dataset", folder_path=str(args.data_root), path_in_repo=".",
            allow_patterns=RAW_PATTERNS,
            commit_message="Add MMEnterprise-Bench v1.0 raw release (questions, corpus, rankings, media)",
        ))

    if not args.skip_parquet:
        if not (args.parquet / "questions").is_dir():
            sys.exit(f"{args.parquet} does not contain the parquet configs; run build_parquet.py first")
        print(f"uploading parquet configs from {args.parquet} ...")
        with_retries("parquet upload", lambda: api.upload_folder(
            repo_id=args.repo_id, repo_type="dataset", folder_path=str(args.parquet), path_in_repo="parquet",
            allow_patterns=["*.parquet"],
            commit_message="Add parquet configs for datasets.load_dataset",
        ))

    if not args.skip_card:
        print(f"uploading dataset card from {args.card} ...")
        with_retries("card upload", lambda: api.upload_file(
            repo_id=args.repo_id, repo_type="dataset", path_or_fileobj=str(args.card), path_in_repo="README.md",
            commit_message="Update dataset card",
        ))

    info = api.dataset_info(args.repo_id, files_metadata=True)
    total = sum((s.size or 0) for s in info.siblings)
    print(f"done: {len(info.siblings)} files, {total / 1e6:.1f} MB, private={info.private}")
    print(f"https://huggingface.co/datasets/{args.repo_id}")
    if info.private:
        print("The repo is PRIVATE. Make it public in Settings on the Hub when the release is approved.")


if __name__ == "__main__":
    main()
