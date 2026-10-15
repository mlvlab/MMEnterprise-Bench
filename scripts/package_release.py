"""Create a portable ZIP archive with deterministic file order and timestamps."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import zipfile

from check_release import check, release_files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.is_relative_to(root):
        parser.error("Write the archive outside the repository.")
    issues = check(root)
    if issues:
        raise SystemExit("Release checks failed:\n" + "\n".join(issues))
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in release_files(root):
            if path.suffix in {".zip", ".log", ".pyc"}:
                continue
            info = zipfile.ZipInfo("MMEnterprise-Bench/" + path.relative_to(root).as_posix(), (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100755 if path.suffix == ".bash" else 0o100644) << 16
            archive.writestr(info, path.read_bytes())
            count += 1
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{digest}  {output.name}\n")
    print(f"Created {output.name}: {count} files, {output.stat().st_size / 1_000_000:.1f} MB")


if __name__ == "__main__":
    main()
