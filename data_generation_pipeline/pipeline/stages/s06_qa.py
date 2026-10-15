"""Generate grounded questions from complete workspace records, one family at a time."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from PIL import Image

from .. import archetype, config, taxonomy
from ..llm import LLM
from ..prompts import render_prompt
from ..qa_models import CandidateBatch, UnanswerableBatch
from ..records import Atoms, G, full_project
from ..rule_questions import candidates as rule_candidates


def question_key(question: str) -> str:
    return re.sub(r"\s+", " ", question).strip().casefold().rstrip("?.!")


def validate_batch(batch: CandidateBatch | UnanswerableBatch, *, family: str, expected_ids: list[str],
                   record_ids: set[str], existing_questions: list[str], max_chars: int,
                   source_candidates: list[dict] | None = None) -> list[str]:
    errors = []
    if family not in taxonomy.TASKS:
        return [f"Unknown family: {family}"]
    unanswerable = family == "Unanswerability"
    if unanswerable != isinstance(batch, UnanswerableBatch):
        return ["The batch answerability must match the supplied family"]
    sources = {item["id"]: item for item in source_candidates or []}
    actual_ids = [item.id for item in batch.items] + [item.id for item in batch.omitted]
    if len(actual_ids) != len(set(actual_ids)):
        errors.append("Each allocated ID must occur once across items and omitted")
    if set(actual_ids) != set(expected_ids):
        errors.append(f"Account for exactly these IDs in items or omitted: {expected_ids}")
    seen_questions = {question_key(q) for q in existing_questions}
    for item in batch.items:
        prefix = f"{item.id}: "
        if len(item.q.strip()) > max_chars:
            errors.append(prefix + f"Question exceeds {max_chars} characters")
        key = question_key(item.q)
        if key in seen_questions:
            errors.append(prefix + "Question duplicates an existing or newly generated question")
        seen_questions.add(key)
        paths = [frozenset(path) for path in item.evidence_paths]
        if len(paths) != len(set(paths)):
            errors.append(prefix + "Evidence paths must be distinct sets, regardless of record order")
        if not unanswerable and item.evidence != item.evidence_paths[0]:
            errors.append(prefix + "Representative evidence must equal evidence_paths[0], including record order")
        if unanswerable:
            change = item.perturbation
            source = sources.get(change.source_id)
            if source is None:
                errors.append(prefix + "Perturbation must refer to a supplied answerable question")
            elif (source["q"].count(change.original) != 1 or change.original == change.replacement
                  or source["q"].replace(change.original, change.replacement, 1) != item.q.strip()):
                errors.append(prefix + "Change exactly one unique original substring in the source question")
        references = [item.evidence, *item.evidence_paths, *(step.record_ids for step in item.solution)]
        for references_in_field in references:
            if len(references_in_field) != len(set(references_in_field)):
                errors.append(prefix + "A record ID must not repeat within an evidence list or solution step")
            unknown = set(references_in_field) - record_ids
            if unknown:
                errors.append(prefix + f"Unknown record IDs: {sorted(unknown)}")
    return list(dict.fromkeys(errors))


def _write_json(path: Path, data: dict):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        temporary = Path(handle.name)
    temporary.replace(path)


def _image_hashes(paths: list[str]) -> list[str]:
    hashes = []
    for path in paths:
        image_path = Path(path)
        if not image_path.is_file():
            raise ValueError(f"Complete QA input is missing an image: {image_path}")
        try:
            with Image.open(image_path) as image:
                image.verify()
        except (OSError, ValueError) as exc:
            raise ValueError(f"QA input image cannot be decoded: {image_path}") from exc
        hashes.append(hashlib.sha256(image_path.read_bytes()).hexdigest())
    return hashes


def _workspace_context(g: G, atoms: Atoms) -> dict:
    timestamps = [timestamp for aid in atoms.order for timestamp, _ in atoms.items[aid].get("releases", [])]
    return {"company": g.world.get("company_name", ""),
            "system": g.world.get("internal_model_name", ""),
            "people": g.world.get("people", []), "channels": g.world.get("channels", []),
            "record_count": len(atoms.order), "scope": "Complete project, omniscient reader",
            "earliest_record": min(timestamps, default=""), "latest_record": max(timestamps, default="")}


def _check_input_size(variables: dict, vocabulary: dict, prompt="06_qa/generate", schema=CandidateBatch) -> tuple[str, str]:
    system, user = render_prompt(prompt, {**vocabulary, **variables})
    user += "\n\nReturn ONLY a JSON object matching this JSON schema:\n"
    user += json.dumps(schema.model_json_schema(), ensure_ascii=False)
    size = len(system) + len(user)
    if size > config.QA["max_input_chars"]:
        raise ValueError(f"Complete QA input has {size} text characters; increase qa.max_input_chars "
                         f"(currently {config.QA['max_input_chars']}). No records were omitted.")
    return system, user


def run(run: config.Run, llm: LLM):
    families = config.QA["families"]
    if (not isinstance(families, list) or not families
            or any(not isinstance(family, str) or family not in taxonomy.TASKS for family in families)
            or len(families) != len(set(families))):
        raise ValueError("qa.families must list distinct configured family names")
    # Perturbations need their answerable sources even when a custom family order puts UNS first.
    families = [family for family in families if family != "Unanswerability"] + (["Unanswerability"] if "Unanswerability" in families else [])
    g, stage = G(run), run.stage_dir("06_qa")
    atoms = Atoms(g)
    records, images = full_project(atoms)
    if not atoms.order:
        raise ValueError("QA generation requires nonempty workspace records")
    if len(images) > config.QA["max_images"]:
        raise ValueError(f"Complete QA input needs {len(images)} images; increase qa.max_images "
                         f"(currently {config.QA['max_images']}). No images were omitted.")
    image_hashes = _image_hashes(images)
    context = _workspace_context(g, atoms)
    vocabulary = archetype.prompt_vars(archetype.load(run))
    source_digest = config.digest({"stage": Path(__file__).read_text(),
                                   "rules": (config.PKG / "pipeline/rule_questions.py").read_text(),
                                   "schema": CandidateBatch.model_json_schema(),
                                   "unanswerable_schema": UnanswerableBatch.model_json_schema()})
    quotas = taxonomy.quotas(config.SETTINGS["qa_target"], families)
    if set(quotas) != set(families):
        raise ValueError("QA quotas must contain exactly the active families")
    if any(type(count) is not int or count < 0 for count in quotas.values()):
        raise ValueError("QA family quotas must be nonnegative integers")
    accepted, omitted = [], []
    rules = rule_candidates(g, atoms)
    checkpoints = stage / "batches"
    checkpoints.mkdir(exist_ok=True)
    for family_index, family in enumerate(families, 1):
        definition, examples = taxonomy.SPEC[family]
        rule = next((item for item in rules if item["family"] == family
                     and len(item["q"]) <= config.QA["question_max_chars"]), None) if quotas[family] else None
        if rule:
            accepted.append({**rule, "primary_task": family, "task": family,
                             "task_group": taxonomy.GROUP_OF[family], "category": taxonomy.GROUP_OF[family],
                             "evidence_ids": list(rule["evidence"])})
        schema = UnanswerableBatch if family == "Unanswerability" else CandidateBatch
        prompt = "06_qa/unanswerable" if family == "Unanswerability" else "06_qa/generate"
        source_candidates = [item for item in accepted if item["answer_status"] == "answerable"]
        for offset in range(int(rule is not None), quotas[family], config.QA["batch_size"]):
            count = min(config.QA["batch_size"], quotas[family] - offset)
            id_prefix = f"Q{family_index:02d}_{offset:03d}"
            expected_ids = [f"{id_prefix}_{number:02d}" for number in range(1, count + 1)]
            existing = [item["q"] for item in accepted]
            variables = {"workspace_context": context, "family": family, "definition": definition,
                         "examples": examples, "existing_questions": existing, "records": records,
                         "source_questions": source_candidates,
                         "n": count, "id_prefix": id_prefix, "question_max_chars": config.QA["question_max_chars"]}
            rendered = _check_input_size(variables, vocabulary, prompt, schema)
            fingerprint = config.digest({"images": image_hashes, "prompt": rendered,
                                         "source": source_digest, "model": llm.model,
                                         "api": config.API, "base_url": config.API["base_url"] or os.environ.get("OPENAI_BASE_URL"),
                                         "qa": config.QA})
            checkpoint = checkpoints / f"{family_index:02d}_{offset:03d}.json"

            def validate(batch):
                return validate_batch(batch, family=family, expected_ids=expected_ids,
                                      record_ids=set(atoms.order), existing_questions=existing,
                                      max_chars=config.QA["question_max_chars"], source_candidates=source_candidates)

            batch = None
            if checkpoint.exists():
                saved = json.loads(checkpoint.read_text())
                if saved.get("fingerprint") == fingerprint:
                    batch = schema.model_validate(saved["response"])
                    errors = validate(batch)
                    if errors:
                        raise ValueError(f"Invalid QA checkpoint {checkpoint.name}: {errors}")
            if batch is None:
                batch = llm.json(prompt, variables, schema, images=images,
                                 tag=f"qa_family_{family_index:02d}_{offset:03d}", effort="medium",
                                 max_tokens=config.QA["max_output_tokens"], validator=validate)
                errors = validate(batch)
                if errors:
                    raise ValueError(f"Invalid QA batch: {errors}")
                _write_json(checkpoint, {"fingerprint": fingerprint, "family": family,
                                         "response": batch.model_dump()})
            for item in batch.items:
                accepted.append({**item.model_dump(), "q": item.q.strip(), "family": family, "primary_task": family,
                                 "task": family, "task_group": taxonomy.GROUP_OF[family],
                                 "category": taxonomy.GROUP_OF[family], "evidence_ids": list(item.evidence),
                                 "generation_method": "perturbation" if family == "Unanswerability" else "llm"})
            omitted.extend({**item.model_dump(), "family": family} for item in batch.omitted)
            _write_json(stage / "qa.json", {"schema_version": "qa_project_v1", "requested": sum(quotas.values()),
                        "items": accepted, "omitted": omitted,
                        "family_counts": dict(Counter(item["family"] for item in accepted))})
    _write_json(stage / "qa.json", {"schema_version": "qa_project_v1", "requested": sum(quotas.values()),
                "items": accepted, "omitted": omitted,
                "family_counts": dict(Counter(item["family"] for item in accepted))})
    if not accepted:
        raise RuntimeError("No QA candidates were generated; inspect 06_qa/qa.json and batch omissions")
    run.mark_done("06_qa")
