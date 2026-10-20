"""Build the results snapshot and upload the static leaderboard page to the org Space.

    HF_TOKEN=... python deploy_static.py                # snapshot + upload to MMEnterprise-Bench/leaderboard
    HF_TOKEN=... python deploy_static.py --local-only   # only write static/results.snapshot.jsonl

Steps: download ``results.jsonl`` from the results dataset -> write ``static/results.snapshot.jsonl`` (hidden rows
dropped, contact fields stripped, bad lines skipped) -> upload ``index.html`` + ``README.md`` +
``results.snapshot.jsonl`` to the static Space in one commit. The Space's visibility is never changed. The token is
read from the environment only and never printed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATIC = HERE / "static"
SNAPSHOT = STATIC / "results.snapshot.jsonl"
UPLOADS = ["index.html", "README.md", "results.snapshot.jsonl"]
RESULTS_FILE = "results.jsonl"
# files the Hub drops into a fresh static Space; unreferenced by index.html, so they are deleted on deploy
PLACEHOLDERS = {"style.css"}


def _is_contact_key(key: str) -> bool:
    k = key.lower()
    return "contact" in k or "email" in k or "e-mail" in k


def strip_contact(obj):
    """Drop every key that looks like a contact field, recursively (rows come from community submissions)."""
    if isinstance(obj, dict):
        return {k: strip_contact(v) for k, v in obj.items() if not _is_contact_key(str(k))}
    if isinstance(obj, list):
        return [strip_contact(v) for v in obj]
    return obj


HIDDEN_STRINGS = {"true", "1", "yes"}


def is_hidden(value) -> bool:
    """``hidden`` as written by storage.py is a bool, but be tolerant of 1 / "true" / "yes" (mirrors the page)."""
    if value is True:
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in HIDDEN_STRINGS
    return False


def build_snapshot(text: str) -> tuple[list[dict], int, int]:
    """Parse results.jsonl robustly. Returns (public rows, hidden rows dropped, bad lines skipped)."""
    rows, hidden, bad = [], 0, 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(row, dict):
            bad += 1
            continue
        if is_hidden(row.get("hidden")):
            hidden += 1
            continue
        rows.append(strip_contact(row))
    return rows, hidden, bad


def write_snapshot(rows: list[dict], path: Path = SNAPSHOT) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, allow_nan=False) + "\n" for r in rows),
                    encoding="utf-8")


def download_results(api, results_repo: str) -> str:
    from huggingface_hub import hf_hub_download

    with tempfile.TemporaryDirectory(prefix="mmeb-static-") as tmp:
        local = hf_hub_download(results_repo, RESULTS_FILE, repo_type="dataset", token=api.token,
                                cache_dir=tmp, force_download=True)
        return Path(local).read_text(encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--space", default=os.environ.get("STATIC_SPACE", "MMEnterprise-Bench/leaderboard"))
    parser.add_argument("--results-repo",
                        default=os.environ.get("RESULTS_REPO", "MMEnterprise-Bench/leaderboard-results"))
    parser.add_argument("--local-only", action="store_true", help="only build static/results.snapshot.jsonl")
    parser.add_argument("--results-file", default=None,
                        help="use this local results.jsonl instead of downloading (offline snapshot build)")
    args = parser.parse_args(argv)

    for name in ("index.html", "README.md"):
        if not (STATIC / name).is_file():
            raise SystemExit(f"missing {STATIC / name}")

    if args.results_file:
        text = Path(args.results_file).read_text(encoding="utf-8")
        api = None
    else:
        token = os.environ.get("HF_TOKEN")
        if not token:
            raise SystemExit("set HF_TOKEN in the environment (never on the command line)")
        from huggingface_hub import HfApi

        api = HfApi(token=token)
        text = download_results(api, args.results_repo)

    rows, hidden, bad = build_snapshot(text)
    write_snapshot(rows)
    print(f"snapshot: {len(rows)} public rows -> {SNAPSHOT} ({hidden} hidden dropped, {bad} bad lines skipped)")
    if args.local_only:
        return
    if api is None:
        token = os.environ.get("HF_TOKEN")
        if not token:
            raise SystemExit("set HF_TOKEN in the environment to upload")
        from huggingface_hub import HfApi

        api = HfApi(token=token)

    from huggingface_hub import CommitOperationAdd, CommitOperationDelete

    info = api.repo_info(args.space, repo_type="space")
    if getattr(info, "sdk", None) not in (None, "static"):
        raise SystemExit(f"{args.space} is a {info.sdk} Space, not a static one; refusing to upload")
    if not info.private:
        print(f"WARNING: Space {args.space} is public; this script never changes visibility.", file=sys.stderr)
    existing = set(api.list_repo_files(args.space, repo_type="space"))
    ops = [CommitOperationAdd(path_in_repo=name, path_or_fileobj=str(STATIC / name)) for name in UPLOADS]
    stale = sorted(existing & PLACEHOLDERS)
    ops += [CommitOperationDelete(path_in_repo=name) for name in stale]
    api.create_commit(repo_id=args.space, repo_type="space", operations=ops,
                      commit_message=f"deploy static leaderboard ({len(rows)} rows)")
    if stale:
        print(f"removed HF placeholder files: {', '.join(stale)}")
    files = api.list_repo_files(args.space, repo_type="space")
    print(f"uploaded {', '.join(UPLOADS)}; Space files: {', '.join(sorted(files))}")
    print(f"space: https://huggingface.co/spaces/{args.space} (private={info.private})")


if __name__ == "__main__":
    main()
