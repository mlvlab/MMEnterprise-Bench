"""MMEnterprise-Bench: evaluation toolkit for enterprise agents over evolving multimodal records.

Quick start::

    from mmenterprise_bench import load_dataset, build_context, score_predictions

    ds = load_dataset()                       # downloads to ./data/MMEnterprise-Bench if missing
    ctx = build_context(ds, ds.rows[0], retriever="2b", k=10)
    metrics = score_predictions(ds, "predictions.jsonl", output_dir="outputs/my_agent")
"""
from .data import Dataset, build_context, default_dataset_dir, load_dataset
from .hub import HF_BASELINES_REPO, HF_DATASET_REPO, RAW_PATTERNS, download_dataset
from .judge import Judge
from .score import score_predictions
from .contexts import export_contexts

__version__ = "1.0.0"

__all__ = ["__version__", "Dataset", "Judge", "HF_BASELINES_REPO", "HF_DATASET_REPO", "RAW_PATTERNS",
           "build_context", "default_dataset_dir", "download_dataset", "export_contexts", "load_dataset",
           "score_predictions"]
