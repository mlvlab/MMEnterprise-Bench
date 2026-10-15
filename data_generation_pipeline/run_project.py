"""Run one project from a source PDF to a validated dataset."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
from pathlib import Path
import sys
import time

from pipeline import config


def load_environment(path: Path):
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {chr(34), chr(39)}:
            value = value[1:-1]
        os.environ.setdefault(name, value)


def read_config(path: Path) -> dict:
    value = json.loads(path.read_text())
    required = {"project_id", "seed_pdf", "output_dir", "models"}
    missing = required - value.keys()
    if missing:
        raise ValueError(f"Missing configuration fields: {', '.join(sorted(missing))}")
    if not isinstance(value["project_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value["project_id"]):
        raise ValueError("project_id must contain only letters, numbers, underscores, and hyphens")
    allowed = required | {"api", "settings", "ingest", "qa", "quality_control"}
    unknown = value.keys() - allowed
    if unknown:
        raise ValueError(f"Unknown configuration fields: {', '.join(sorted(unknown))}")
    if set(value["models"]) != {"generation", "validation"} or not all(isinstance(x, str) and x.strip() for x in value["models"].values()):
        raise ValueError("models must define nonempty generation and validation model IDs")
    for section, defaults in [("settings", config.SETTINGS), ("api", config.API), ("quality_control", config.QC), ("qa", config.QA)]:
        if not isinstance(value.get(section, {}), dict):
            raise ValueError(f"{section} must be a JSON object")
        unknown = value.get(section, {}).keys() - defaults.keys()
        if unknown:
            raise ValueError(f"Unknown {section} fields: {', '.join(sorted(unknown))}")
    if value.get("ingest", "vlm") not in {"vlm", "rules"}:
        raise ValueError("ingest must be 'vlm' or 'rules'")
    qa = {**config.QA, **value.get("qa", {})}
    if not isinstance(qa["families_file"], str) or not qa["families_file"].strip():
        raise ValueError("qa.families_file must be a nonempty path")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for k, v in qa.items() if k not in {"families_file", "families"}):
        raise ValueError("QA limits must be positive integers")
    from pipeline import taxonomy
    families = qa["families"]
    if (not isinstance(families, list) or not families
            or any(not isinstance(name, str) or name not in taxonomy.TASKS for name in families)):
        raise ValueError("qa.families must select from the eight question families")
    if len(families) != len(set(families)):
        raise ValueError("qa.families must not contain duplicate names")
    if families == ["Unanswerability"]:
        raise ValueError("qa.families must include an answerable family to supply Unanswerability perturbations")
    taxonomy.load_families((path.parent / qa["families_file"]).resolve())
    api = {**config.API, **value.get("api", {})}
    if not isinstance(api["key_env"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api["key_env"]):
        raise ValueError("api.key_env must be an environment variable name")
    if not isinstance(api["transport_retries"], int) or api["transport_retries"] < 1:
        raise ValueError("api.transport_retries must be a positive integer")
    if not isinstance(api["timeout_seconds"], (int, float)) or api["timeout_seconds"] <= 0:
        raise ValueError("api.timeout_seconds must be positive")
    from datetime import date
    settings = {**config.SETTINGS, **value.get("settings", {})}
    date.fromisoformat(settings["week_start"])
    if not 7 <= settings["n_people"] <= 9:
        raise ValueError("n_people must be between 7 and 9")
    if settings["n_errors"] != 5:
        raise ValueError("n_errors must be 5: one event for each supported error type")
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for k, v in settings.items() if k != "week_start"):
        raise ValueError("Numeric settings must be nonnegative integers")
    qc = {**config.QC, **value.get("quality_control", {})}
    if any(not isinstance(v, int) or isinstance(v, bool) or v < (0 if k == "max_revisions" else 1) for k, v in qc.items()):
        raise ValueError("Quality-control counts must be positive integers (max_revisions may be zero)")
    return value


def fingerprint(value: dict, seed: Path) -> str:
    payload = dict(value)
    payload["effective_base_url"] = value.get("api", {}).get("base_url") or os.environ.get("OPENAI_BASE_URL")
    payload["seed_sha256"] = hashlib.sha256(seed.read_bytes()).hexdigest()
    payload["qa_families_sha256"] = hashlib.sha256(config.QA_FAMILIES_FILE.read_bytes()).hexdigest()
    source = hashlib.sha256()
    for path in sorted(list((config.PKG / "pipeline").rglob("*.py")) + list(config.PROMPT_DIR.rglob("*.md"))):
        source.update(str(path.relative_to(config.PKG)).encode())
        source.update(path.read_bytes())
    payload["source_sha256"] = source.hexdigest()
    return config.digest(payload)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=config.PKG / "config.json")
    parser.add_argument("--dry-run", action="store_true", help="Validate configuration and print the stage plan without API calls")
    parser.add_argument("--resume", action="store_true", help="Skip completed stages and reuse cached API responses (default)")
    parser.add_argument("--from", dest="from_stage", choices=config.STAGES, help="Regenerate this stage and invalidate downstream stages")
    parser.add_argument("--to", dest="to_stage", choices=config.STAGES, help="Stop after this stage")
    parser.add_argument("--force", action="store_true", help="Regenerate every stage; valid API responses remain cached")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required")
    try:
        load_environment(config.PKG.parent / ".env")
        path = args.config.resolve()
        value = read_config(path)
        seed = (path.parent / value["seed_pdf"]).resolve()
        if not seed.is_file() or seed.suffix.lower() != ".pdf":
            raise ValueError(f"Seed PDF not found: {seed}")
        if not seed.read_bytes().startswith(b"%PDF-"):
            raise ValueError("seed_pdf is not a PDF file")
        config.configure(value, path)
        start = config.STAGES.index(args.from_stage) if args.from_stage else 0
        end = config.STAGES.index(args.to_stage) if args.to_stage else len(config.STAGES) - 1
        if end < start:
            raise ValueError("--to must be at or after --from")
        run_dir = config.RUNS_DIR / value["project_id"]
        stamp = fingerprint(value, seed)
        saved = run_dir / "configuration.json"
        if saved.exists() and json.loads(saved.read_text())["fingerprint"] != stamp and not args.force:
            raise ValueError("Configuration, seed, or pipeline changed. Use a new project_id or --force to regenerate the project")
        stages = config.STAGES[start:end + 1]
        for previous in config.STAGES[:start]:
            if not (run_dir / previous / "_done").is_file():
                raise ValueError(f"Required stage is incomplete: {previous}")
        print(f"Project: {value['project_id']}\nSeed: {seed}\nOutput: {run_dir}")
        for stage in stages:
            skip = (run_dir / stage / "_done").exists() and not (args.force or args.from_stage)
            print(f"  {'skip' if skip else 'run ':4s}  {stage}")
        if args.dry_run:
            return 0
        if any(s != "08_export" and (args.force or args.from_stage or not (run_dir / s / "_done").exists()) for s in stages):
            if not os.environ.get(config.API["key_env"]):
                raise ValueError(f"Set {config.API['key_env']} before starting generation")
        from pipeline.llm import LLM
        run = config.Run(value["project_id"], str(seed))
        saved.write_text(json.dumps({"fingerprint": stamp, "config": value}, indent=2))
        llm = LLM(run)
        for stage in stages:
            if run.done(stage) and not (args.force or args.from_stage):
                continue
            for later in config.STAGES[config.STAGES.index(stage):]:
                (run.dir / later / "_done").unlink(missing_ok=True)
            module_name = "s00_ingest_vlm" if stage == "00_ingest" and value.get("ingest", "vlm") == "vlm" else f"s{stage}"
            module = importlib.import_module(f"pipeline.stages.{module_name}")
            started = time.monotonic()
            print(f"[run] {stage}", flush=True)
            module.run(run, llm)
            if not run.done(stage):
                raise RuntimeError(f"Stage {stage} did not create its completion marker")
            print(f"[done] {stage}: {time.monotonic() - started:.0f}s", flush=True)
        print(f"Outputs: {run.dir}")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
