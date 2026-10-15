"""Validate QA candidates against sufficient evidence and the complete project."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Literal

from pydantic import BaseModel, Field

from .. import config, taxonomy
from ..llm import LLM
from ..records import G, Atoms, record_text, record_block, full_project


class Solution(BaseModel):
    answer: str
    knowable: bool


class Grade(BaseModel):
    asked_object: str
    verdict: Literal["match", "partial", "mismatch"]
    cause: str
    reason: str


class FinalValue(BaseModel):
    ok: bool
    superseding_records: list[str]
    note: str


class CompetingAnswer(BaseModel):
    answer: str | None
    records: list[str]
    equally_valid: bool


class Numbers(BaseModel):
    wrong_or_unverified: list[str]
    note: str


class Status(BaseModel):
    ok: bool
    note: str


class ReasoningStep(BaseModel):
    step: str
    record_ids: list[str]


class Fix(BaseModel):
    q: str | None = None
    gold: str | None = None
    evidence_add: list[str] = Field(default_factory=list)
    evidence: list[str] | None = None
    evidence_paths: list[list[str]] | None = None
    solution: list[ReasoningStep] | None = None


class Audit(BaseModel):
    final_value: FinalValue
    competing_answer: CompetingAnswer
    numbers: Numbers
    status: Status
    evidence_sufficient: bool
    verdict: Literal["pass", "fix", "drop"]
    category: str
    fix: Fix | None
    note: str


def _ids(value, atoms: Atoms, field: str, *, allow_empty=True) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(aid, str) or not aid for aid in value):
        raise ValueError(f"{field} must be a list of record IDs")
    if not allow_empty and not value:
        raise ValueError(f"{field} cannot be empty")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains duplicate record IDs")
    unknown = [aid for aid in value if aid not in atoms.items]
    if unknown:
        raise ValueError(f"{field} cites unknown records: {', '.join(unknown)}")
    return list(value)


def project_candidate(item: dict, g: G | None, atoms: Atoms) -> dict:
    """Validate a direct LLM candidate and normalize its evidence aliases."""
    candidate = deepcopy(item)
    for field in ("id", "q"):
        if not isinstance(candidate.get(field), str) or not candidate[field].strip():
            raise ValueError(f"Candidate {field} is missing")
    if len(candidate["q"]) > config.QA["question_max_chars"]:
        raise ValueError(f"Candidate question exceeds qa.question_max_chars ({config.QA['question_max_chars']})")
    if not isinstance(candidate.get("gold"), dict) or not isinstance(candidate["gold"].get("answer"), str) or not candidate["gold"]["answer"].strip():
        raise ValueError("Candidate has no gold answer")
    status = candidate.get("answer_status")
    if status not in {"answerable", "unanswerable"}:
        raise ValueError("Candidate answer_status must be answerable or unanswerable")
    labels = [candidate[field] for field in ("family", "task", "primary_task") if candidate.get(field)]
    if not labels or len(set(labels)) != 1 or labels[0] not in taxonomy.TASKS:
        raise ValueError("Candidate must have one consistent, configured task family")
    task = labels[0]
    if (task == "Unanswerability") != (status == "unanswerable"):
        raise ValueError("Candidate task family and answer_status disagree")
    category = taxonomy.GROUP_OF[task]
    if candidate.get("category", category) != category:
        raise ValueError("Candidate category and task family disagree")
    candidate.update(family=task, task=task, primary_task=task, category=category)
    evidence = _ids(candidate.get("evidence", candidate.get("evidence_ids", [])), atoms, "evidence")
    if "evidence_ids" in candidate and candidate["evidence_ids"] != evidence:
        raise ValueError("evidence and evidence_ids disagree")
    raw_paths = candidate.get("evidence_paths", [evidence] if evidence else [])
    if not isinstance(raw_paths, list):
        raise ValueError("evidence_paths must be a list of sufficient evidence sets")
    paths = [_ids(path, atoms, "evidence_paths", allow_empty=False) for path in raw_paths]
    keys = [frozenset(path) for path in paths]
    if len(keys) != len(set(keys)):
        raise ValueError("evidence_paths contains duplicate evidence sets")
    if status == "answerable":
        if not evidence or not paths or evidence != paths[0]:
            raise ValueError("Answerable evidence must equal the first sufficient path in the same order")
    elif evidence or paths:
        raise ValueError("Unanswerable candidates must have empty gold evidence and evidence_paths")
    candidate["evidence"] = candidate["evidence_ids"] = evidence
    candidate["evidence_paths"] = paths
    if not isinstance(candidate.get("solution"), list) or not 2 <= len(candidate["solution"]) <= 5:
        raise ValueError("Candidate solution must contain 2–5 reasoning steps")
    for step in candidate.get("solution", []):
        if not isinstance(step, dict) or not isinstance(step.get("step"), str) or not step["step"].strip():
            raise ValueError("Candidate solution contains an invalid step")
        _ids(step.get("record_ids", []), atoms, "solution")
    return candidate


def project_context(atoms: Atoms) -> tuple[str, list[str]]:
    records, images = full_project(atoms)
    max_images = config.QC.get("max_project_images", 128)
    max_chars = config.QC.get("max_project_chars", 1_000_000)
    if len(images) > max_images:
        raise ValueError(f"Complete-project QC requires {len(images)} images; increase quality_control.max_project_images ({max_images})")
    if len(records) > max_chars:
        raise ValueError(f"Complete-project QC requires {len(records)} characters; increase quality_control.max_project_chars ({max_chars})")
    return records, images


def check(candidate: dict, atoms: Atoms, project_records: str, llm: LLM) -> dict:
    candidate = project_candidate(candidate, None, atoms)
    project_records, project_images = project_context(atoms)
    ids = candidate["evidence_ids"]
    records, images = record_block(ids, atoms)
    if len(images) > config.QC["max_images"]:
        raise ValueError(f"Cited evidence needs {len(images)} images; increase quality_control.max_images")
    common = {"question": candidate["q"], "gold": candidate["gold"]["answer"], "answer_status": candidate["answer_status"],
              "family": candidate["family"], "family_definition": taxonomy.SPEC[candidate["family"]][0]}
    solution = llm.json("07_validate/solve", {"question": candidate["q"], "records": records or "(no cited records)"}, Solution, images=images,
                        effort="low", max_tokens=config.QC["solver_max_tokens"], tag=f"qc_solve_{candidate['id']}")
    grade = llm.json("07_validate/grade", {**common, "candidate": solution.answer, "knowable": solution.knowable}, Grade,
                     effort="low", max_tokens=config.QC["solver_max_tokens"], tag=f"qc_grade_{candidate['id']}")
    solver_result = json.dumps({"solution": solution.model_dump(), "grade": grade.model_dump()}, ensure_ascii=False)
    # Image positions in cited text are local to the solver; the auditor uses
    # the single complete-project attachment manifest below.
    cited_text = "\n\n".join(record_text(atoms.items[aid]) for aid in ids)
    audit = llm.json("07_validate/audit", {**common, "records": cited_text or "(none cited)", "evidence_ids": ids,
                      "evidence_paths": candidate.get("evidence_paths", [ids] if ids else []),
                      "solution": candidate.get("solution", []), "solver_result": solver_result, "project_records": project_records}, Audit,
                     images=project_images, effort="high", max_tokens=config.QC["audit_max_tokens"], tag=f"qc_audit_{candidate['id']}")
    _ids(audit.final_value.superseding_records, atoms, "audit.superseding_records")
    _ids(audit.competing_answer.records, atoms, "audit.competing_answer.records")
    passed = (audit.verdict == "pass" and audit.category == "none" and audit.final_value.ok and not audit.competing_answer.equally_valid
              and not audit.numbers.wrong_or_unverified and audit.status.ok
              and (candidate["answer_status"] == "unanswerable" or audit.evidence_sufficient))
    return {"solver": solution.model_dump() if solution else None, "grade": grade.model_dump() if grade else None,
            "audit": audit.model_dump(), "passed": passed}


def apply_fix(candidate: dict, fix: dict, atoms: Atoms) -> dict:
    updated = deepcopy(candidate)
    if (candidate.get("perturbation") and fix.get("q") is not None
            and fix["q"] != candidate["q"]):
        raise ValueError("Cannot revise a perturbed question without invalidating its source provenance")
    content_changed = False
    for field in ("q", "gold"):
        if fix.get(field) is not None:
            if not isinstance(fix[field], str) or not fix[field].strip():
                raise ValueError(f"Fix {field} cannot be empty")
            previous = updated["gold"]["answer"] if field == "gold" else updated[field]
            content_changed |= previous != fix[field]
            updated[field] = {"answer": fix[field]} if field == "gold" else fix[field]
    evidence = list(updated.get("evidence", updated.get("evidence_ids", [])))
    if fix.get("evidence") is not None:
        evidence = _ids(fix["evidence"], atoms, "fix.evidence")
    additions = _ids(fix.get("evidence_add", []), atoms, "fix.evidence_add")
    for aid in additions:
        if aid not in evidence:
            evidence.append(aid)
    evidence_changed = evidence != updated.get("evidence", updated.get("evidence_ids", []))
    updated["evidence"] = updated["evidence_ids"] = evidence
    if fix.get("evidence_paths") is not None:
        updated["evidence_paths"] = fix["evidence_paths"]
    elif content_changed or evidence_changed:
        updated["evidence_paths"] = [evidence] if evidence else []
    if fix.get("solution") is not None:
        updated["solution"] = fix["solution"]
    elif content_changed or evidence_changed:
        raise ValueError("Fixes to the question, gold answer, or evidence require corrected solution steps")
    return project_candidate(updated, None, atoms)


def run(run: config.Run, llm: LLM):
    validator = LLM(run, model=config.CRITIC_MODEL)
    g = G(run)
    atoms = Atoms(g)
    project_records, _ = project_context(atoms)
    original = run.load("06_qa", "qa.json")["items"]
    accepted, reports = [], []
    for item in original:
        report = {"id": item["id"], "history": [], "accepted": False}
        try:
            candidate = project_candidate(item, g, atoms)
            for revision in range(config.QC["max_revisions"] + 1):
                result = check(candidate, atoms, project_records, validator)
                report["history"].append({"revision": revision, "candidate": candidate, **result})
                if result["passed"]:
                    accepted.append(candidate)
                    report["accepted"] = True
                    break
                audit = result["audit"]
                if audit["verdict"] != "fix" or not audit.get("fix") or revision == config.QC["max_revisions"]:
                    break
                candidate = apply_fix(candidate, audit["fix"], atoms)
        except ValueError as exc:
            report["error"] = str(exc)
        reports.append(report)
        run.save("07_validate", "report.json", {"candidates": len(original), "accepted": len(accepted), "items": reports})
    if not accepted:
        raise RuntimeError("No candidate passed validation; inspect 07_validate/report.json")
    run.save("07_validate", "qa.json", {"items": accepted})
    run.mark_done("07_validate")
