"""Question families, definitions, and candidate allocation."""
from __future__ import annotations

import json
from pathlib import Path


GROUPS = {
    "Evidence Reasoning": ["Extraction", "Attribution", "Computation", "Comparison", "Interpretation"],
    "Conflict Resolution": ["Cross-source", "Temporal"],
    "Unanswerability": ["Unanswerability"],
}
TASKS = [task for tasks in GROUPS.values() for task in tasks]
ANSWERABLE_TASKS = [task for task in TASKS if task != "Unanswerability"]
GROUP_OF = {task: group for group, tasks in GROUPS.items() for task in tasks}
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "configs" / "qa_families.json"


def load_families(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"families"} or not isinstance(value["families"], list):
        raise ValueError("QA family configuration must contain a families list")
    families = value["families"]
    names = []
    for family in families:
        if not isinstance(family, dict) or set(family) != {"name", "category", "definition", "examples"}:
            raise ValueError("Each QA family needs name, category, definition, and examples")
        name = family["name"]
        if not isinstance(name, str) or name not in GROUP_OF:
            raise ValueError(f"Unknown QA family: {name}")
        if family["category"] != GROUP_OF[name]:
            raise ValueError(f"Incorrect category for QA family: {name}")
        if not isinstance(family["definition"], str) or not family["definition"].strip():
            raise ValueError(f"Missing definition for QA family: {name}")
        examples = family["examples"]
        if not isinstance(examples, list) or not examples or any(not isinstance(x, str) or not x.strip() for x in examples):
            raise ValueError(f"QA family examples must be a nonempty list of strings: {name}")
        names.append(name)
    if len(names) != len(TASKS) or set(names) != set(TASKS):
        raise ValueError("QA families must contain each of the eight task names exactly once")
    by_name = {family["name"]: family for family in families}
    return [by_name[name] for name in TASKS]


def spec(path: Path | None = None) -> dict[str, tuple[str, str]]:
    return {f["name"]: (f["definition"], "\n".join(f["examples"]))
            for f in load_families(path or DEFAULT_PATH)}


SPEC = spec()


def configure(path: Path):
    global SPEC
    SPEC = spec(path)


def quotas(target: int, families: list[str] | None = None) -> dict[str, int]:
    """Split a requested total evenly, assigning remainders in the selected order."""
    if not isinstance(target, int) or isinstance(target, bool) or target < 0:
        raise ValueError("qa_target must be a nonnegative integer")
    selected = TASKS if families is None else families
    if not isinstance(selected, list) or not selected or any(not isinstance(task, str) or task not in TASKS for task in selected):
        raise ValueError("Candidate allocation requires a nonempty list of known families")
    if len(selected) != len(set(selected)):
        raise ValueError("Candidate allocation families must be unique")
    each, remainder = divmod(target, len(selected))
    return {task: each + (index < remainder) for index, task in enumerate(selected)}


def modality_of(ids: list[str], records: dict) -> str:
    if not ids:
        return "none"
    visual = sum(bool(records[aid].get("path") or records[aid].get("paths")) for aid in ids)
    return "image" if visual == len(ids) else "text" if not visual else "multi"
