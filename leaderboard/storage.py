"""Storage backends for the leaderboard: a submission queue plus the approved results list.

Two implementations share one interface:

* ``LocalStorage(root)``  — plain directories, used by the tests and for running the app locally.
* ``HfStorage(queue_repo, results_repo, token)`` — two Hugging Face dataset repos (the queue repo stays private).

Layout (identical in both):

    <queue>/submissions/<utc-timestamp>_<slug>/meta.json      status: pending | approved | rejected | hidden
    <queue>/submissions/<utc-timestamp>_<slug>/metrics.json
    <queue>/submissions/<utc-timestamp>_<slug>/predictions.jsonl   (optional)
    <results>/results.jsonl                                      one approved row per line (``hidden`` rows kept, flagged)

Select the backend with ``get_storage()``: env ``STORAGE=hf`` or ``STORAGE=local`` (default local, root ``LOCAL_STORAGE_DIR``).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

SUBMISSIONS_PREFIX = "submissions"
RESULTS_FILE = "results.jsonl"
STATUSES = ("pending", "approved", "rejected", "hidden")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (slug[:limit].rstrip("-") or "entry")


def make_submission_id(model: str, when: datetime | None = None) -> str:
    when = when or utc_now()
    return f"{when.strftime('%Y%m%dT%H%M%SZ')}_{slugify(model)}"


def submission_timestamp(submission_id: str) -> datetime | None:
    try:
        return datetime.strptime(submission_id.split("_", 1)[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def results_to_jsonl(rows: Iterable[dict]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def results_from_jsonl(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class Storage:
    """Interface. ``meta`` dicts carry the submission form fields and ``status`` / ``admin_notes``."""

    # --- queue -----------------------------------------------------------------------------------
    def create_submission(self, meta: dict, metrics_text: str, predictions_path: str | None) -> str:
        raise NotImplementedError

    def list_submission_ids(self) -> list[str]:
        raise NotImplementedError

    def get_meta(self, submission_id: str) -> dict:
        raise NotImplementedError

    def get_metrics(self, submission_id: str) -> dict:
        raise NotImplementedError

    def update_meta(self, submission_id: str, changes: dict) -> dict:
        raise NotImplementedError

    # --- results ---------------------------------------------------------------------------------
    def read_results(self) -> list[dict]:
        raise NotImplementedError

    def write_results(self, rows: list[dict]) -> None:
        raise NotImplementedError

    # --- shared helpers --------------------------------------------------------------------------
    def list_submissions(self, status: str | None = None) -> list[dict]:
        metas = []
        for sid in sorted(self.list_submission_ids(), reverse=True):
            try:
                meta = self.get_meta(sid)
            except Exception:  # a half-written folder must not break the admin tab
                continue
            if status is None or meta.get("status") == status:
                metas.append(meta)
        return metas

    def submissions_since(self, since: datetime) -> list[dict]:
        metas = []
        for sid in self.list_submission_ids():
            when = submission_timestamp(sid)
            if when is not None and when >= since:
                try:
                    metas.append(self.get_meta(sid))
                except Exception:
                    continue
        return metas

    def count_recent_by_contact(self, contact_email: str, hours: int = 24) -> int:
        needle = (contact_email or "").strip().lower()
        since = utc_now() - timedelta(hours=hours)
        return sum(1 for meta in self.submissions_since(since)
                   if (meta.get("contact_email") or "").strip().lower() == needle)

    def append_result(self, row: dict) -> None:
        rows = [r for r in self.read_results() if r.get("id") != row.get("id")]
        rows.append(row)
        self.write_results(rows)

    def set_result_hidden(self, row_id: str, hidden: bool) -> bool:
        rows = self.read_results()
        found = False
        for row in rows:
            if row.get("id") == row_id:
                row["hidden"] = hidden
                found = True
        if found:
            self.write_results(rows)
        return found

    def visible_results(self) -> list[dict]:
        return [row for row in self.read_results() if not row.get("hidden")]


class LocalStorage(Storage):
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.queue_dir = self.root / "queue" / SUBMISSIONS_PREFIX
        self.results_path = self.root / "results" / RESULTS_FILE
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.results_path.parent.mkdir(parents=True, exist_ok=True)

    def create_submission(self, meta, metrics_text, predictions_path):
        sid = meta.get("id") or make_submission_id(meta.get("model", ""))
        folder = self.queue_dir / sid
        suffix = 1
        while folder.exists():
            suffix += 1
            folder = self.queue_dir / f"{sid}-{suffix}"
        sid = folder.name
        meta = {**meta, "id": sid}
        folder.mkdir(parents=True)
        (folder / "metrics.json").write_text(metrics_text, encoding="utf-8")
        if predictions_path:
            shutil.copyfile(predictions_path, folder / "predictions.jsonl")
        (folder / "meta.json").write_text(dumps(meta), encoding="utf-8")
        return sid

    def list_submission_ids(self):
        return [p.name for p in self.queue_dir.iterdir() if p.is_dir() and (p / "meta.json").exists()]

    def get_meta(self, submission_id):
        return json.loads((self.queue_dir / submission_id / "meta.json").read_text(encoding="utf-8"))

    def get_metrics(self, submission_id):
        return json.loads((self.queue_dir / submission_id / "metrics.json").read_text(encoding="utf-8"))

    def update_meta(self, submission_id, changes):
        meta = {**self.get_meta(submission_id), **changes}
        (self.queue_dir / submission_id / "meta.json").write_text(dumps(meta), encoding="utf-8")
        return meta

    def read_results(self):
        if not self.results_path.exists():
            return []
        return results_from_jsonl(self.results_path.read_text(encoding="utf-8"))

    def write_results(self, rows):
        tmp = self.results_path.with_suffix(".tmp")
        tmp.write_text(results_to_jsonl(rows), encoding="utf-8")
        tmp.replace(self.results_path)


class HfStorage(Storage):
    """Hugging Face Hub backend. Every write is one commit; reads go through the Hub file cache."""

    def __init__(self, queue_repo: str, results_repo: str, token: str | None = None):
        from huggingface_hub import HfApi

        self.queue_repo = queue_repo
        self.results_repo = results_repo
        self.token = token
        self.api = HfApi(token=token)

    # helpers
    def _download(self, repo_id: str, filename: str) -> str:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(repo_id=repo_id, filename=filename, repo_type="dataset", token=self.token)
        return Path(path).read_text(encoding="utf-8")

    def _exists(self, repo_id: str, filename: str) -> bool:
        return self.api.file_exists(repo_id=repo_id, filename=filename, repo_type="dataset")

    # queue
    def create_submission(self, meta, metrics_text, predictions_path):
        from huggingface_hub import CommitOperationAdd

        sid = meta.get("id") or make_submission_id(meta.get("model", ""))
        existing = set(self.list_submission_ids())
        base, suffix = sid, 1
        while sid in existing:
            suffix += 1
            sid = f"{base}-{suffix}"
        meta = {**meta, "id": sid}
        folder = f"{SUBMISSIONS_PREFIX}/{sid}"
        ops = [
            CommitOperationAdd(path_in_repo=f"{folder}/meta.json", path_or_fileobj=dumps(meta).encode("utf-8")),
            CommitOperationAdd(path_in_repo=f"{folder}/metrics.json", path_or_fileobj=metrics_text.encode("utf-8")),
        ]
        if predictions_path:
            ops.append(CommitOperationAdd(path_in_repo=f"{folder}/predictions.jsonl", path_or_fileobj=predictions_path))
        self.api.create_commit(repo_id=self.queue_repo, repo_type="dataset", operations=ops,
                               commit_message=f"submission {sid}")
        return sid

    def list_submission_ids(self):
        files = self.api.list_repo_files(repo_id=self.queue_repo, repo_type="dataset")
        ids = set()
        for f in files:
            parts = f.split("/")
            if len(parts) == 3 and parts[0] == SUBMISSIONS_PREFIX and parts[2] == "meta.json":
                ids.add(parts[1])
        return sorted(ids)

    def get_meta(self, submission_id):
        return json.loads(self._download(self.queue_repo, f"{SUBMISSIONS_PREFIX}/{submission_id}/meta.json"))

    def get_metrics(self, submission_id):
        return json.loads(self._download(self.queue_repo, f"{SUBMISSIONS_PREFIX}/{submission_id}/metrics.json"))

    def update_meta(self, submission_id, changes):
        meta = {**self.get_meta(submission_id), **changes}
        self.api.upload_file(path_or_fileobj=dumps(meta).encode("utf-8"),
                             path_in_repo=f"{SUBMISSIONS_PREFIX}/{submission_id}/meta.json",
                             repo_id=self.queue_repo, repo_type="dataset",
                             commit_message=f"{submission_id}: {changes.get('status', 'update')}")
        return meta

    # results
    def read_results(self):
        if not self._exists(self.results_repo, RESULTS_FILE):
            return []
        return results_from_jsonl(self._download(self.results_repo, RESULTS_FILE))

    def write_results(self, rows):
        self.api.upload_file(path_or_fileobj=results_to_jsonl(rows).encode("utf-8"), path_in_repo=RESULTS_FILE,
                             repo_id=self.results_repo, repo_type="dataset",
                             commit_message=f"results: {len(rows)} rows")


def get_storage() -> Storage:
    backend = os.environ.get("STORAGE", "local").lower()
    if backend == "hf":
        queue = os.environ.get("QUEUE_REPO", "MMEnterprise-Bench/leaderboard-queue")
        results = os.environ.get("RESULTS_REPO", "MMEnterprise-Bench/leaderboard-results")
        return HfStorage(queue, results, token=os.environ.get("HF_TOKEN"))
    if backend != "local":
        raise ValueError(f"Unknown STORAGE backend {backend!r}; use 'hf' or 'local'.")
    root = os.environ.get("LOCAL_STORAGE_DIR") or os.path.join(tempfile.gettempdir(), "mmeb_leaderboard_local")
    return LocalStorage(root)
