"""Check the portable release for private files and machine-specific references."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

IGNORED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache",
                ".ruff_cache", ".github", "outputs", "runs", "results", "result",
                "build", "dist"}
TEXT_SUFFIXES = {".py", ".bash", ".sh", ".json", ".jsonl", ".yaml", ".yml",
                 ".toml", ".md", ".txt", ".csv", ".cff", ".html", ".js", ".tex"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "OpenAI credential": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{30,}"),
    "Hugging Face credential": re.compile(r"\bhf_[A-Za-z0-9]{25,}"),
    "Google credential": re.compile(r"\bAIza[A-Za-z0-9_-]{30,}"),
    "GitHub credential": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,})"),
    "machine-specific path": re.compile(r"/(?:home|Users|hub_data\d+)/[A-Za-z0-9_.-]+/"),
}


def release_files(root: Path):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRS or part.startswith(".venv") or part.endswith(".egg-info")
               for part in relative.parts):
            continue
        if path.is_symlink() or path.is_file():
            yield path


def check(root: Path) -> list[str]:
    issues = []
    for path in release_files(root):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            issues.append(f"{relative}: symbolic link")
            continue
        if path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example"):
            issues.append(f"{relative}: private environment file")
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in {".gitignore", ".gitattributes", ".env.example"}:
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                issues.append(f"{relative}: unreadable text file")
                continue
            for name, pattern in PATTERNS.items():
                if pattern.search(content):
                    issues.append(f"{relative}: {name}")
    return issues


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    issues = check(args.root.resolve())
    for issue in issues:
        print(issue)
    if issues:
        raise SystemExit(1)
    print("Release file checks passed.")


if __name__ == "__main__":
    main()
