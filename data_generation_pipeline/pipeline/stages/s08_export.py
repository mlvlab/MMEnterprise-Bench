"""Export project questions, retrieval units, and original image files."""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil

from .. import config, taxonomy
from ..records import G, Atoms
from .s07_validate import project_candidate



def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def run(run: config.Run, llm=None):
    g = G(run)
    atoms = Atoms(g)
    out = run.stage_dir("08_export")
    media = out / "media"
    media.mkdir(exist_ok=True)
    units, by_record = [], defaultdict(list)
    for aid in atoms.order:
        atom = atoms.items[aid]
        record_id = f"{run.name}::{aid}"
        timestamp = min((t for t, _ in atom["releases"]), default="")
        paths = atom.get("paths") or ([atom["path"]] if atom.get("path") else [None])
        for page, source_path in enumerate(paths, 1):
            image = image_sha = None
            if source_path:
                source = Path(source_path)
                image_sha = hashlib.sha256(source.read_bytes()).hexdigest()
                image = f"media/{image_sha}{source.suffix.lower()}"
                shutil.copyfile(source, out / image)
                if aid.startswith("IMG:"):
                    name = next(x["name"] for x in g.assets["images"] if x["id"] == aid[4:])
                else:
                    name = next(x["name"] for x in g.assets["docs"] if x["id"] == atom["doc"])
                    name += f" · page {atom.get('page', page)}"
                    if len(paths) > 1:
                        name += f" · part {page} of {len(paths)}"
                text = f"### Attachment · first available {timestamp}\n{name}"
            else:
                text = f"### {atom.get('where', atom['kind'])} · recorded {timestamp}\n{atom['text']}"
            unit = {"id": f"{run.name}_u{len(units) + 1:05d}", "record_id": record_id,
                    "order": len(units) + 1, "timestamp": timestamp, "text": text,
                    "image": image, "image_sha256": image_sha}
            unit["unit_content_sha256"] = config.digest(unit)
            units.append(unit)
            by_record[aid].append(unit)
    questions = []
    latest = max((u["timestamp"] for u in units), default="")
    for raw_item in run.load("07_validate", "qa.json")["items"]:
        item = project_candidate(raw_item, g, atoms)
        ids = item["evidence_ids"] if item["answer_status"] == "answerable" else []
        if any(aid not in by_record for aid in ids):
            raise ValueError(f"Unresolved evidence: {item['id']}")
        evidence = [f"{run.name}::{aid}" for aid in ids]
        representative = [u for aid in ids for u in by_record[aid]]
        visual_count = sum(bool(u["image"]) for u in representative)
        modality = None if not representative else "image" if visual_count == len(representative) else "text" if not visual_count else "multi"
        task = item["task"]
        category = taxonomy.GROUP_OF[task]
        question = {"id": f"{run.name}::{item['id']}", "project": run.name,
                    "role": "omni", "t": latest, "q": item["q"], "gold": item["gold"],
                    "answer_status": item["answer_status"], "primary_task": task, "task_group": taxonomy.GROUP_OF[task],
                    "category": category, "task": task, "family": task, "evidence": evidence,
                    "evidence_paths": [[f"{run.name}::{aid}" for aid in path] for path in item["evidence_paths"]],
                    "frozen_representative_retrieval_units": [u["id"] for u in representative],
                    "evidence_record_count": len(evidence), "evidence_unit_count": len(representative),
                    "evidence_image_count": visual_count, "evidence_modality": modality,
                    "solution": [{"step": step.get("step", ""), "record_ids": [f"{run.name}::{aid}" for aid in step.get("record_ids", [])]}
                                 for step in item.get("solution", [])]}
        questions.append(question)
    write_json(out / "qa/rows.json", questions)
    write_json(out / "corpus/units.json", units)
    write_json(out / "projects.json", [{"id": run.name, "label": g.world["company_name"], "run": run.name,
                                        "as_of": latest, "questions": len(questions), "people": g.world["people"], "channels": g.world["channels"]}])
    write_json(out / "manifest.json", {"name": "MMEnterprise-Bench generated project", "project_id": run.name,
        "questions": len(questions), "retrieval_units": len(units), "tasks": dict(Counter(q["task"] for q in questions)),
        "models": {"generation": config.MODEL, "validation": config.CRITIC_MODEL},
        "files": {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in [out / "qa/rows.json", out / "corpus/units.json", out / "projects.json"]}})
    run.mark_done("08_export")
