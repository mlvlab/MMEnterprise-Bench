"""``mmeb`` command line: download, validate, export-contexts, score, run-baseline, metrics, serve."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

from . import __version__
from .data import DATASET_ENV, DEFAULT_DATASET_DIR, Dataset, dataset_present, default_dataset_dir
from .hub import HF_DATASET_REPO, DatasetUnavailable, download_dataset
from .judge import DEFAULT_JUDGE_API_KEY_ENV, DEFAULT_JUDGE_BASE_URL, DEFAULT_JUDGE_MODEL, Judge
from .providers import ProviderError

RETRIEVERS = ("2b", "8b")
K_VALUES = (10, 50, 100)


def load_environment(path):
    """Read ``NAME=value`` lines from a .env file into the environment (existing variables win)."""
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("Invalid .env assignment: " + line.split("=")[0])
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {chr(34), chr(39)}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def add_dataset_argument(parser):
    parser.add_argument("--dataset", type=Path, default=None,
                        help=f"Dataset directory (default: ${DATASET_ENV} or {DEFAULT_DATASET_DIR})")


def open_dataset(args):
    root = args.dataset or default_dataset_dir()
    if not dataset_present(root):
        raise FileNotFoundError(f"Dataset not found at {root}. Run `mmeb download` (or set {DATASET_ENV}).")
    return Dataset(root)


def build_judge(args):
    return Judge(model=args.judge_model, base_url=args.judge_base_url, api_key_env=args.judge_api_key_env,
                 concurrency=args.judge_concurrency, timeout=args.judge_timeout)


# -- subcommands --------------------------------------------------------------------------------
def cmd_download(args):
    root = args.dataset or default_dataset_dir()
    download_dataset(root, revision=args.revision, verify=not args.skip_checksums)
    print(f"Dataset ready at {root}")
    return 0


def cmd_validate(args):
    from .validate import run
    return run(args.dataset or default_dataset_dir(), args.skip_checksums)


def cmd_export_contexts(args):
    from .contexts import export_contexts
    dataset = open_dataset(args)
    count = export_contexts(dataset, args.retriever, args.k, args.output, args.limit, args.question_ids, args.include_gold)
    print(f"Wrote {count} contexts to {args.output} (image_path values are relative to {dataset.root})")
    return 0


def cmd_score(args):
    from .score import score_predictions
    dataset = open_dataset(args)
    judge = None if args.pre_judged else build_judge(args)
    output = args.output or Path("outputs/score") / Path(args.predictions).stem
    metrics = score_predictions(dataset, args.predictions, output_dir=output, judge=judge, pre_judged=args.pre_judged,
                                retriever=args.retriever, k=args.k, limit=args.limit, question_ids=args.question_ids)
    return 0 if metrics.get("complete", True) else 2


def _pipeline(args, stage):
    from .pipeline import DEFAULT_CONFIG, Pipeline, load_config
    overrides = {key: getattr(args, key) for key in ("models", "retrievers", "k", "limit", "question_ids", "output",
                                                     "concurrency", "judge_concurrency") if getattr(args, key, None) is not None}
    if args.dataset is not None:
        overrides["dataset"] = str(args.dataset)
    config = load_config(args.config or DEFAULT_CONFIG, overrides)
    return config, Pipeline(config)


def cmd_run_baseline(args):
    config, pipeline = _pipeline(args, args.stage)
    if args.validate:
        print(json.dumps(pipeline.dataset.validate(config["retrievers"], config["k"]), indent=2))
        return 0
    if args.dry_run:
        print(json.dumps(pipeline.plan(), indent=2))
        return 0
    summaries, pending = pipeline.run(args.stage)
    for summary in summaries:
        accuracy = "pending" if summary["accuracy"] is None else f"{summary['accuracy']:.2f}%"
        print(f"{summary['model']} / {summary['retriever']} / k={summary['k']}: {accuracy}; {summary['pending']} remaining")
    return 2 if pending else 0


def cmd_metrics(args):
    _, pipeline = _pipeline(args, "metrics")
    print(json.dumps(pipeline.metrics(), indent=2))
    return 0


def cmd_serve(args):
    from .serve import run
    return run(args)


# -- parser -------------------------------------------------------------------------------------
def add_baseline_arguments(parser):
    add_dataset_argument(parser)
    parser.add_argument("--config", type=Path, help="Run configuration JSON (default: the package's configs/default.json)")
    parser.add_argument("--models", nargs="+", metavar="ALIAS", help="Reader aliases from configs/models.json (sol luna gemini38flash gemini31pro qwen27b qwen9b qwen4b qwen2b internvl8b kimivl16b)")
    parser.add_argument("--retrievers", nargs="+", choices=RETRIEVERS)
    parser.add_argument("--k", nargs="+", type=int, choices=K_VALUES)
    parser.add_argument("--limit", type=int, help="Evaluate the first N questions in release order")
    parser.add_argument("--question-ids", nargs="+", metavar="ID")
    parser.add_argument("--output", type=str, help="Output directory (default: outputs/eval, from the config)")
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--judge-concurrency", type=int)


def build_parser():
    parser = argparse.ArgumentParser(prog="mmeb", description="MMEnterprise-Bench evaluation toolkit.")
    parser.add_argument("--version", action="version", version=f"mmenterprise-bench {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p = sub.add_parser("download", help=f"Download the dataset from huggingface.co/datasets/{HF_DATASET_REPO}",
                       description=f"Download the raw dataset layout from {HF_DATASET_REPO} and verify SHA256SUMS. Honours $HF_TOKEN.")
    add_dataset_argument(p)
    p.add_argument("--revision", help="Hub revision (branch, tag or commit); default: main")
    p.add_argument("--skip-checksums", action="store_true", help="Do not verify SHA256SUMS after downloading")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("validate", help="Validate the local dataset (checksums, references, rankings)",
                       description="Validate the local dataset: checksums, media references, IDs, gold evidence, task labels and both rankings.")
    add_dataset_argument(p)
    p.add_argument("--skip-checksums", action="store_true", help="Validate structure without the checksum manifest")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("export-contexts", help="Write the paper's per-question contexts to a JSONL file",
                       description="Export one JSON object per question with the k retrieved records in presentation order, for agents written in any language.")
    add_dataset_argument(p)
    p.add_argument("--retriever", choices=RETRIEVERS, default="2b")
    p.add_argument("--k", type=int, choices=K_VALUES, default=10)
    p.add_argument("--output", type=Path, default=Path("contexts.jsonl"))
    p.add_argument("--limit", type=int, help="Only the first N questions in release order")
    p.add_argument("--question-ids", nargs="+", metavar="ID")
    p.add_argument("--include-gold", action="store_true", help="Add reference_answer, answer_status, task, category and gold_unit_ids (debugging only)")
    p.set_defaults(func=cmd_export_contexts)

    p = sub.add_parser("score", help="Score a predictions.jsonl file written by your own agent",
                       description="Judge each prediction against the reference answer and write metrics.json, metrics.md and graded.jsonl. "
                                   "Missing questions count as incorrect. Judgments are cached under <output>/cache/judgments.")
    p.add_argument("predictions", help="JSONL with one {\"id\": ..., \"answer\": ...} object per line")
    add_dataset_argument(p)
    p.add_argument("--output", type=Path, help="Output directory (default: outputs/score/<predictions stem>)")
    p.add_argument("--retriever", choices=RETRIEVERS, default="2b", help="Released ranking reported as the Evidence Recall@k reference")
    p.add_argument("--k", type=int, choices=K_VALUES, default=10)
    p.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    p.add_argument("--judge-base-url", default=DEFAULT_JUDGE_BASE_URL, help="Any OpenAI-compatible chat-completions endpoint (vLLM, etc.)")
    p.add_argument("--judge-api-key-env", default=DEFAULT_JUDGE_API_KEY_ENV, help="Environment variable holding the judge API key")
    p.add_argument("--judge-concurrency", type=int, default=8)
    p.add_argument("--judge-timeout", type=int, default=180, help="Seconds per judge request")
    p.add_argument("--pre-judged", action="store_true", help="Use the 'correct' field in the file instead of calling a judge")
    p.add_argument("--limit", type=int, help="Score only the first N questions in release order")
    p.add_argument("--question-ids", nargs="+", metavar="ID")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("run-baseline", help="Reproduce the paper's RAG baselines (reader + judge)",
                       description="Generate answers with a registered reader over the frozen contexts, judge them and write metrics (resumable).")
    add_baseline_arguments(p)
    p.add_argument("--stage", choices=("run", "answer", "judge"), default="run", help="run = answer + judge; or one stage only")
    p.add_argument("--dry-run", action="store_true", help="Validate the plan without API calls")
    p.add_argument("--validate", action="store_true", help="Validate rankings and image checksums for the selected conditions, then exit")
    p.set_defaults(func=cmd_run_baseline)

    p = sub.add_parser("metrics", help="Re-aggregate metrics of a run-baseline output directory")
    add_baseline_arguments(p)
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("serve", help="Launch a local reader with vLLM (needs the [local] extra)")
    from .serve import add_arguments
    add_arguments(p)
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        load_environment(Path.cwd() / ".env")
        return args.func(args)
    except DatasetUnavailable as exc:
        print(f"Download error: {exc}", file=sys.stderr)
        return 1
    except ProviderError as exc:
        print(f"Judge/provider error: {exc.code}" + (f" (HTTP {exc.status})" if exc.status else ""), file=sys.stderr)
        return 1
    except (ValueError, FileNotFoundError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
