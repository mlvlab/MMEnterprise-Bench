"""Download and verify the released dataset from the Hugging Face Hub."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath

HF_DATASET_REPO = "MMEnterprise-Bench/MMEnterprise-Bench"
HF_BASELINES_REPO = "MMEnterprise-Bench/baselines"

# The raw release layout inside the dataset repo. ``parquet/`` (the ``load_dataset`` configs) is not
# needed by the evaluator and is skipped; the files below reproduce ``data/MMEnterprise-Bench/``.
RAW_PATTERNS = ["qa/*", "corpus/*", "retrieval/*", "media/*",
                "projects.json", "seed_documents.json", "manifest.json", "SHA256SUMS"]

# Metadata that huggingface_hub keeps next to a ``local_dir`` download; never part of the release.
IGNORED_DIRS = {".cache"}


class DatasetUnavailable(Exception):
    """The dataset repo could not be reached or is not (yet) accessible with the given token."""


def _token(token=None):
    return token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN") or None


def download_dataset(local_dir, revision=None, token=None, verify=True, repo_id=HF_DATASET_REPO, quiet=False):
    """Fetch the raw layout of ``repo_id`` into ``local_dir`` and (optionally) verify ``SHA256SUMS``.

    The Hub token is taken from ``token``, else ``$HF_TOKEN``. Returns the local directory as a Path.
    """
    local_dir = Path(local_dir)
    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub import errors as hub_errors
    except ImportError as exc:  # pragma: no cover - dependency is declared in pyproject
        raise DatasetUnavailable("Install huggingface_hub (`pip install huggingface_hub`) to download the dataset") from exc
    local_dir.mkdir(parents=True, exist_ok=True)
    if not quiet:
        print(f"Downloading {repo_id}" + (f"@{revision}" if revision else "") + f" -> {local_dir}", flush=True)
    try:
        snapshot_download(repo_id=repo_id, repo_type="dataset", revision=revision, local_dir=str(local_dir),
                          allow_patterns=RAW_PATTERNS, token=_token(token))
    except (hub_errors.RepositoryNotFoundError, hub_errors.GatedRepoError, hub_errors.RevisionNotFoundError) as exc:
        raise DatasetUnavailable(
            f"Cannot access https://huggingface.co/datasets/{repo_id}" + (f" (revision {revision})" if revision else "")
            + ". The repository may be private or not published yet; if you were granted access, "
              "export HF_TOKEN=<your token> and retry.") from exc
    except hub_errors.HfHubHTTPError as exc:
        raise DatasetUnavailable(f"Hugging Face Hub request failed for {repo_id}: {exc}") from exc
    except OSError as exc:
        raise DatasetUnavailable(f"Network or file-system error while downloading {repo_id}: {exc}") from exc
    if verify:
        count = verify_checksums(local_dir)
        if not quiet:
            print(f"Verified {count} files against SHA256SUMS", flush=True)
    return local_dir


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_files(root):
    """Relative POSIX paths of every file in ``root`` except hub metadata."""
    root = Path(root)
    return {path.relative_to(root).as_posix() for path in root.rglob("*")
            if path.is_file() and not (set(path.relative_to(root).parts) & IGNORED_DIRS)}


def parse_checksums(text):
    expected = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        digest, _, relative = line.partition("  ")
        if len(digest) != 64 or not relative or PurePosixPath(relative).is_absolute() or ".." in PurePosixPath(relative).parts:
            raise ValueError("Invalid SHA256SUMS entry: " + line)
        if relative in expected:
            raise ValueError("Duplicate SHA256SUMS entry: " + relative)
        expected[relative] = digest
    return expected


def verify_checksums(root, require_complete=True):
    """Check every entry in ``SHA256SUMS``; return the number of verified files.

    Raises ``FileNotFoundError`` for a missing file and ``ValueError`` for a mismatch or (when
    ``require_complete``) for files that the manifest does not cover.
    """
    root = Path(root)
    manifest = root / "SHA256SUMS"
    if not manifest.is_file():
        raise FileNotFoundError(f"Missing SHA256SUMS in {root}")
    expected = parse_checksums(manifest.read_text(encoding="utf-8"))
    for relative, digest in expected.items():
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(f"Missing dataset file: {relative}")
        if file_sha256(path) != digest:
            raise ValueError(f"Checksum mismatch: {relative}")
    if require_complete:
        extra = release_files(root) - set(expected) - {"SHA256SUMS"}
        if extra:
            raise ValueError("Files not covered by SHA256SUMS: " + ", ".join(sorted(extra)[:5]))
    return len(expected)
