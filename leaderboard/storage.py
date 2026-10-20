"""Storage backends for the leaderboard: a submission queue plus the approved results list.

Two implementations share one interface:

* ``LocalStorage(root)``  — plain directories, used by the tests and for running the app locally.
* ``HfStorage(queue_repo, results_repo, token)`` — two Hugging Face dataset repos (the queue repo stays private).

Layout (identical in both):

    <queue>/submissions/<utc-timestamp>_<slug>/meta.json      status: pending | approved | rejected | hidden
    <queue>/submissions/<utc-timestamp>_<slug>/metrics.json
    <queue>/submissions/<utc-timestamp>_<slug>/predictions.jsonl   (optional)
    <results>/results.jsonl                                      one approved row per line (``hidden`` rows kept, flagged)

Select the backend with ``get_storage()``: env ``STORAGE=hf`` or ``STORAGE=local`` (default: ``hf`` inside a Space,
i.e. when ``SPACE_ID`` is set, otherwise ``local`` with root ``LOCAL_STORAGE_DIR``).

Concurrency: every read-modify-write of ``results.jsonl`` runs under a process-wide lock, and the Hub backend
additionally commits with ``parent_commit`` (retrying on a 412 conflict) so concurrent writers never lose a row.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

SUBMISSIONS_PREFIX = "submissions"
RESULTS_FILE = "results.jsonl"
STATUSES = ("pending", "approved", "rejected", "hidden")
SUBMISSION_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z_[a-z0-9-]+$")
ROW_ID_RE = re.compile(r"^(paper_[a-z0-9_-]+|[0-9]{8}T[0-9]{6}Z_[a-z0-9-]+)$")
WRITE_RETRIES = 5

# One lock per process: the Gradio app runs handlers on several threads, and results.jsonl is rewritten whole.
_RESULTS_LOCK = threading.RLock()


class ConflictError(RuntimeError):
    """The results file changed between read and write (Hub backend); the caller re-reads and retries."""


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


def normalize_contact(email: str) -> str:
    """Lower-case, drop a ``+tag`` and dots in the local part, so ``A.b+x@Example.org`` == ``ab@example.org``."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return email
    local, _, domain = email.rpartition("@")
    local = local.split("+", 1)[0].replace(".", "")
    return f"{local}@{domain}"


def is_valid_submission_id(submission_id: str) -> bool:
    return bool(SUBMISSION_ID_RE.match(submission_id or ""))


def is_valid_row_id(row_id: str) -> bool:
    return bool(ROW_ID_RE.match(row_id or ""))


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def results_to_jsonl(rows: Iterable[dict]) -> str:
    """Strict JSON lines (``allow_nan=False``: a NaN would make the file unreadable for non-Python consumers)."""
    return "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows)


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
        """Replace results.jsonl. Backends may raise ConflictError when the file changed since read_results()."""
        raise NotImplementedError

    def modify_results(self, mutate) -> list[dict]:
        """Serialized read -> ``mutate(rows)`` -> write; retries on a Hub conflict. Returns the rows written."""
        with _RESULTS_LOCK:
            for attempt in range(WRITE_RETRIES):
                rows = mutate(self.read_results())
                try:
                    self.write_results(rows)
                    return rows
                except ConflictError:
                    if attempt == WRITE_RETRIES - 1:
                        raise
                    time.sleep(0.5 * (attempt + 1))
        raise RuntimeError("unreachable")

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
        """Submissions in the window from the same contact, comparing normalized addresses (+tags, dots ignored)."""
        needle = normalize_contact(contact_email)
        since = utc_now() - timedelta(hours=hours)
        return sum(1 for meta in self.submissions_since(since)
                   if normalize_contact(meta.get("contact_email")) == needle)

    def append_result(self, row: dict) -> None:
        def mutate(rows):
            rows = [r for r in rows if r.get("id") != row.get("id")]
            rows.append(row)
            return rows
        self.modify_results(mutate)

    def set_result_hidden(self, row_id: str, hidden: bool) -> bool:
        found = []

        def mutate(rows):
            found.clear()
            for row in rows:
                if row.get("id") == row_id:
                    row["hidden"] = hidden
                    found.append(row)
            return rows

        with _RESULTS_LOCK:
            if not any(r.get("id") == row_id for r in self.read_results()):
                return False
            self.modify_results(mutate)
        return bool(found)

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
        with _RESULTS_LOCK:
            handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.results_path.parent,
                                                 prefix=".results-", suffix=".tmp", delete=False)
            try:
                with handle:
                    handle.write(results_to_jsonl(rows))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(handle.name, self.results_path)
            except BaseException:
                try:
                    os.unlink(handle.name)
                except OSError:
                    pass
                raise


class HfStorage(Storage):
    """Hugging Face Hub backend. Every write is one commit; reads go through the Hub file cache."""

    def __init__(self, queue_repo: str, results_repo: str, token: str | None = None):
        from huggingface_hub import HfApi

        self.queue_repo = queue_repo
        self.results_repo = results_repo
        self.token = token
        self.api = HfApi(token=token)
        # commit the last read_results() came from, per thread: a public page load on another thread must not
        # replace the parent commit of an admin's read -> write inside modify_results()
        self._local = threading.local()

    @property
    def _results_sha(self) -> str | None:
        return getattr(self._local, "results_sha", None)

    @_results_sha.setter
    def _results_sha(self, value: str | None) -> None:
        self._local.results_sha = value

    # helpers
    def _download(self, repo_id: str, filename: str, revision: str | None = None) -> str:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(repo_id=repo_id, filename=filename, repo_type="dataset", token=self.token,
                               revision=revision)
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
        info = self.api.repo_info(self.results_repo, repo_type="dataset")
        sha = info.sha
        if not any(getattr(s, "rfilename", None) == RESULTS_FILE for s in (info.siblings or [])):
            self._results_sha = sha
            return []
        rows = results_from_jsonl(self._download(self.results_repo, RESULTS_FILE, revision=sha))
        self._results_sha = sha
        return rows

    def write_results(self, rows):
        from huggingface_hub.errors import HfHubHTTPError

        try:
            self.api.upload_file(path_or_fileobj=results_to_jsonl(rows).encode("utf-8"), path_in_repo=RESULTS_FILE,
                                 repo_id=self.results_repo, repo_type="dataset",
                                 commit_message=f"results: {len(rows)} rows", parent_commit=self._results_sha)
        except HfHubHTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 412:
                raise ConflictError(f"results.jsonl changed since it was read (parent {self._results_sha})") from exc
            raise


def get_storage() -> Storage:
    default = "hf" if os.environ.get("SPACE_ID") else "local"   # inside a Space, local /tmp storage would be lost
    backend = os.environ.get("STORAGE", default).lower()
    if backend == "hf":
        queue = os.environ.get("QUEUE_REPO", "MMEnterprise-Bench/leaderboard-queue")
        results = os.environ.get("RESULTS_REPO", "MMEnterprise-Bench/leaderboard-results")
        return HfStorage(queue, results, token=os.environ.get("HF_TOKEN"))
    if backend != "local":
        raise ValueError(f"Unknown STORAGE backend {backend!r}; use 'hf' or 'local'.")
    root = os.environ.get("LOCAL_STORAGE_DIR") or os.path.join(tempfile.gettempdir(), "mmeb_leaderboard_local")
    return LocalStorage(root)
