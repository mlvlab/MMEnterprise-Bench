# Hugging Face release tooling

Scripts for publishing the dataset repo [`MMEnterprise-Bench/MMEnterprise-Bench`](https://huggingface.co/datasets/MMEnterprise-Bench/MMEnterprise-Bench).

| File | Purpose |
| --- | --- |
| `build_parquet.py` | Converts the raw release directory into the `load_dataset` configs (`questions`, `corpus`, `retrieval_2b`, `retrieval_8b`, `projects`, `seed_documents`) with explicit typed features; images are embedded in `corpus`. |
| `dataset_card.md` | The dataset card, uploaded as the repo `README.md`. The YAML header declares every config with explicit `data_files`. |
| `upload_dataset.py` | Creates the repo (private) and uploads the raw layout, `parquet/`, and the card. |

The repo layout is the raw release at the top level plus `parquet/`:

```text
qa/ corpus/ retrieval/ media/ projects.json seed_documents.json manifest.json SHA256SUMS
parquet/{questions,corpus,retrieval_2b,retrieval_8b,projects,seed_documents}/test-*.parquet
README.md
```

Keeping the raw layout at the top level means `mmeb download` (`snapshot_download` with the
`RAW_PATTERNS` list in `mmenterprise_bench/hub.py`) reproduces `data/MMEnterprise-Bench/`
byte-for-byte, and `data/validate.py` verifies it against `SHA256SUMS`.

## Release procedure

Run from the repository root with the release data in `data/MMEnterprise-Bench/`
(`python data/validate.py` must print `"valid": true` first).

```bash
pip install "datasets>=2.19" pyarrow huggingface_hub

# 1. Build the parquet configs (about 210 MB; the corpus is split into shards < 300 MB each)
python scripts/hf/build_parquet.py --data-root data/MMEnterprise-Bench --output build/parquet

# 2. Check the configs locally
python -c "
from datasets import load_dataset
for cfg in ['questions','corpus','retrieval_2b','retrieval_8b','projects','seed_documents']:
    print(cfg, load_dataset('parquet', data_files=f'build/parquet/{cfg}/*.parquet', split='train').num_rows)
"

# 3. Upload (creates the repo PRIVATE if it does not exist). Token from the environment only.
export HF_TOKEN="<write token>"
python scripts/hf/upload_dataset.py --data-root data/MMEnterprise-Bench --parquet build/parquet --card scripts/hf/dataset_card.md
```

Re-running the upload is idempotent: unchanged files are skipped by the Hub and only the card
or changed parquet files create a new commit. Use `--skip-raw` / `--skip-parquet` to push only the card.

## Verify from the Hub

```bash
python - <<'EOF2'
import os
from datasets import load_dataset
from huggingface_hub import snapshot_download
repo = "MMEnterprise-Bench/MMEnterprise-Bench"
token = os.environ.get("HF_TOKEN")  # needed only while the repo is private
for cfg, n in [("questions", 1613), ("corpus", 7140), ("retrieval_2b", 1613),
               ("retrieval_8b", 1613), ("projects", 30), ("seed_documents", 30)]:
    ds = load_dataset(repo, cfg, split="test", token=token)
    assert ds.num_rows == n, (cfg, ds.num_rows)
snapshot_download(repo, repo_type="dataset", local_dir="/tmp/mmeb_raw_check", token=token,
                  allow_patterns=["qa/*", "corpus/*", "retrieval/*", "media/*", "projects.json",
                                  "seed_documents.json", "manifest.json", "SHA256SUMS"])
EOF2
rm -rf /tmp/mmeb_raw_check/.cache        # snapshot_download metadata, not part of the release
python data/validate.py --data-root /tmp/mmeb_raw_check
```

## Final step: make the repo public

The scripts never change visibility. When the release is approved, open
https://huggingface.co/datasets/MMEnterprise-Bench/MMEnterprise-Bench/settings and change
**Visibility** to *Public*. (The `--public` flag of `upload_dataset.py` only affects repo creation and
is intentionally off by default.) Do the same for `MMEnterprise-Bench/baselines`.
