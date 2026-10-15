"""Generation configuration and stage outputs."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
PROMPT_DIR = PKG / "prompts"
RUNS_DIR = PKG / "outputs"
MODEL = "gpt-5.6-luna"
CRITIC_MODEL = MODEL
QA = {"families_file": "configs/qa_families.json",
      "families": ["Extraction", "Attribution", "Computation", "Comparison", "Interpretation", "Cross-source", "Temporal", "Unanswerability"],
      "batch_size": 6, "question_max_chars": 220,
      "max_images": 128, "max_input_chars": 1_000_000, "max_output_tokens": 12000}
QA_FAMILIES_FILE = PKG / QA["families_file"]
API = {"key_env": "OPENAI_API_KEY", "base_url": None, "timeout_seconds": 600, "transport_retries": 4}
QC = {"max_revisions": 2, "max_images": 4, "max_project_images": 128, "max_project_chars": 1_000_000,
      "solver_max_tokens": 3000, "audit_max_tokens": 12000}
SETTINGS = {
    "week_start": "2026-08-26", "n_people": 8, "n_errors": 5,
    "n_decisions": 6, "n_emails": 7, "max_figures": 8, "max_tables": 6,
    "chatter_per_day": 10, "qa_target": 90,
    "llm_retries": 5,
}
STAGES = ["00_ingest", "01_facts", "02_world", "03_timeline", "04_assets", "05_render", "06_qa", "07_validate", "08_export"]


def configure(value: dict, config_path: Path):
    global RUNS_DIR, MODEL, CRITIC_MODEL, QA_FAMILIES_FILE
    base = config_path.parent
    RUNS_DIR = (base / value["output_dir"]).resolve()
    MODEL = value["models"]["generation"]
    CRITIC_MODEL = value["models"]["validation"]
    QA.update(value.get("qa", {}))
    QA_FAMILIES_FILE = (base / QA["families_file"]).resolve()
    from . import taxonomy
    taxonomy.configure(QA_FAMILIES_FILE)
    SETTINGS.update(value.get("settings", {}))
    API.update(value.get("api", {}))
    QC.update(value.get("quality_control", {}))


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class Run:
    def __init__(self, name: str, seed_pdf: str | None = None):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", name):
            raise ValueError("project_id must contain only letters, numbers, underscores, and hyphens")
        self.name = name
        self.dir = RUNS_DIR / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cache = self.dir / "cache"
        self.cache.mkdir(exist_ok=True)
        meta = self.dir / "run.json"
        if seed_pdf:
            self.seed_pdf = str(Path(seed_pdf).resolve())
            if meta.exists():
                saved = json.loads(meta.read_text())
                if Path(saved["seed_pdf"]).resolve() != Path(self.seed_pdf):
                    raise ValueError("This project_id already belongs to another seed PDF")
            else:
                meta.write_text(json.dumps({"name": name, "seed_pdf": self.seed_pdf, "model": MODEL}, indent=2))
        else:
            self.seed_pdf = json.loads(meta.read_text())["seed_pdf"]

    def stage_dir(self, stage: str) -> Path:
        directory = self.dir / stage
        directory.mkdir(exist_ok=True)
        return directory

    def path(self, stage: str, name: str) -> Path:
        return self.stage_dir(stage) / name

    def load(self, stage: str, name: str):
        return json.loads(self.path(stage, name).read_text())

    def save(self, stage: str, name: str, obj) -> Path:
        path = self.path(stage, name)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
        return path

    def done(self, stage: str) -> bool:
        return (self.dir / stage / "_done").is_file()

    def mark_done(self, stage: str):
        self.path(stage, "_done").write_text("ok\n")
