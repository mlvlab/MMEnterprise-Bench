"""Stage 02 — the company, its people and its access graph.

LLM: one call producing the World (company, product, customer, milestones, 7-9 people, channels incl. private
rooms and DMs, a late joiner). Deterministic validation enforces the cast and the access structure the benchmark
needs (private rooms, DMs, an exec outside the research channels, a newcomer who joins on the last day).
Output: 02_world/world.json"""
from __future__ import annotations
import re
from datetime import datetime, timedelta
from .. import config
from ..llm import LLM
from ..schemas import World
from .. import archetype
from pydantic import BaseModel
from typing import Literal
VOCAB = archetype.ARCHETYPES[archetype.DEFAULT]


def week_days():
    d0 = datetime.strptime(config.SETTINGS["week_start"], "%Y-%m-%d")
    return [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(9)]


def validate(w: World):
    errs = []
    ids = [p.id for p in w.people]
    if len(ids) != len(set(ids)):
        errs.append("person ids must be unique")
    for p in w.people:
        if not re.fullmatch(r"[a-z]{2,12}", p.id):
            errs.append(f"person id '{p.id}' must be a lowercase ascii first name")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", p.color):
            errs.append(f"person {p.id}: color must be a hex like #7c3aed")
    n = len(w.people)
    if not 7 <= n <= 9:
        errs.append("there must be 7 to 9 people")
    levels = [p.level for p in w.people]
    if levels.count("exec") != 1: errs.append("exactly one exec (the CEO)")
    if levels.count("lead") < 1: errs.append("at least one lead")
    if levels.count("junior") < 2: errs.append("at least two juniors (one of them is the late joiner)")
    if levels.count("intern") < 1: errs.append("at least one intern")
    if levels.count("senior") < 2: errs.append("at least two seniors")
    if w.late_joiner not in ids: errs.append("late_joiner must be a person id")
    else:
        lj = next(p for p in w.people if p.id == w.late_joiner)
        if lj.level != "junior": errs.append("the late joiner must be a junior")
    roles = " ".join(p.role.lower() for p in w.people)
    owns = " ".join(p.owns.lower() for p in w.people)
    if "product" not in roles and "product" not in owns: errs.append("one person must have a product role (product manager)")
    if not any(k in roles or k in owns for k in VOCAB["infra_keywords"]): errs.append(f"one person must have an infrastructure/tooling role ({' / '.join(VOCAB['infra_keywords'])})")
    if not any(("experiments" in c.id) or ("analysis" in c.id) for c in w.channels): errs.append("one public channel id must contain 'experiments' or 'analysis' (where results/findings are posted)")
    cids = [c.id for c in w.channels]
    if len(cids) != len(set(cids)): errs.append("channel ids must be unique")
    if "general" not in cids: errs.append("a channel with id 'general' containing everyone is required")
    dms = [c for c in w.channels if c.id.startswith("dm-")]
    pub = [c for c in w.channels if not c.private and not c.id.startswith("dm-")]
    priv = [c for c in w.channels if c.private and not c.id.startswith("dm-")]
    if len(dms) != 4: errs.append("exactly 4 DM channels with ids 'dm-<a>-<b>' (2 members each, private=true)")
    for c in dms:
        if len(c.members) != 2 or not c.private: errs.append(f"{c.id}: a DM has exactly 2 members and private=true")
    if len(pub) < 5: errs.append("at least 5 public channels (general, project, experiments, infra, product ...)")
    if len(priv) < 2: errs.append("at least 2 private non-DM channels (e.g. leadership, a small core-team room)")
    for c in priv:
        if not 2 <= len(c.members) <= 4: errs.append(f"{c.id}: private rooms have 2-4 members")
    exec_id = next((p.id for p in w.people if p.level == "exec"), None)
    for c in w.channels:
        for m in c.members:
            if m not in ids: errs.append(f"channel {c.id}: unknown member {m}")
        if c.id == "general" and set(c.members) != set(ids): errs.append("general must contain everyone")
    for p in w.people:
        k = sum(p.id in c.members for c in w.channels if not c.id.startswith("dm-"))
        if k < 2: errs.append(f"{p.id} must be in at least 2 non-DM channels")
    lead = next((p.id for p in w.people if p.level == "lead"), None)
    if lead and not any(lead in c.members for c in priv): errs.append("the lead must be in a private room (leadership)")
    if exec_id and not any(exec_id in c.members for c in priv): errs.append("the exec must be in the private leadership room")
    for bad in ["paper", "arxiv", "conference", "university", "lab"]:
        blob = (w.project_sentence + " " + w.product + " " + w.tagline).lower()
        if re.search(rf"\b{bad}\b", blob): errs.append(f"do not mention '{bad}' in the company description")  # whole word: 'labor' is not 'lab'
    return errs


def _repair_access(world):
    """Deterministic fixes the benchmark relies on, applied after the LLM's draft: the exec sees only general, the
    product channel and the leadership room; the late joiner is in no private room or DM; the experiments channel
    has no exec and no PM."""
    exec_id = next(p["id"] for p in world["people"] if p["level"] == "exec")
    pm = next((p["id"] for p in world["people"] if "product" in p["role"].lower()), None)
    lj = world["late_joiner"]
    for c in world["channels"]:
        keep_exec = c["id"] == "general" or "product" in c["id"] or (c["private"] and "lead" in c["id"]) or (c["private"] and not c["id"].startswith("dm-") and pm in c["members"] and len(c["members"]) <= 4)
        if not keep_exec and exec_id in c["members"]:
            c["members"] = [m for m in c["members"] if m != exec_id]
        if c["private"] and not c["id"].startswith("dm-") and lj in c["members"]:
            c["members"] = [m for m in c["members"] if m != lj]  # a DM with the newcomer stays intact: nothing is posted there before they join
        if "experiment" in c["id"] or "analysis" in c["id"] or "finding" in c["id"]:
            c["members"] = [m for m in c["members"] if m not in (exec_id, pm)]
    lead = next(p["id"] for p in world["people"] if p["level"] == "lead")
    leadership = next((c for c in world["channels"] if c["private"] and not c["id"].startswith("dm-") and exec_id in c["members"]), None)
    if leadership is None:
        leadership = next((c for c in world["channels"] if c["private"] and not c["id"].startswith("dm-") and lead in c["members"]), None)
        if leadership and exec_id not in leadership["members"]:
            leadership["members"].append(exec_id)


class Archetype(BaseModel):
    archetype: Literal["research_lab", "analysis_firm"]
    reason: str


def _used_company_names(run: config.Run) -> list[str]:
    """Company names of every other run on disk (so worlds do not share a name or a root)."""
    import json
    names = []
    for w in sorted(run.dir.parent.glob("*/02_world/world.json")):
        if w.parent.parent == run.dir: continue
        try: n = json.load(open(w, encoding="utf-8")).get("company_name")
        except Exception: continue
        if n and n not in names: names.append(n)
    return names


def run(run: config.Run, llm: LLM):
    facts = run.load("01_facts", "facts.json")
    # archetype first: it sets the vocabulary of every later prompt (research runs vs. verified findings)
    tables_md = "\n".join(f"- {t['id']}: {t['title'][:80]} | " + "; ".join(f"{r['label']}: {', '.join(r['cells'][:4])}" for r in t["rows"][:3]) for t in facts["tables"][:10]) or "(no tables)"
    arch = llm.json("02_world/archetype", {"title": facts["title"], "one_sentence": facts["one_sentence"], "task_summary": facts["task_summary"], "tables": tables_md,
                                          "components": ", ".join(facts["components"][:20])}, Archetype, tag="world_archetype", effort="low")
    run.save("02_world", "archetype.json", arch.model_dump())
    llm._vocab = None  # reload the vocabulary for this archetype
    global VOCAB
    VOCAB = archetype.load(run)
    used = _used_company_names(run)
    def validate_names(w):
        errs = validate(w) if validate else []
        name = (w.company_name or "").strip().lower()
        for u in used:
            if name == u.lower() or (len(name) >= 5 and name[:5] == u.lower()[:5]):
                errs.append(f"company_name '{w.company_name}' collides with an existing company '{u}' (same name or first syllable); invent a name with a different root"); break
        return errs
    w = llm.json("02_world/world", {"method_name": facts["method_name"], "one_sentence": facts["one_sentence"],
                                    "task_summary": facts["task_summary"], "components": ", ".join(facts["components"][:20]),
                                    "pipeline_steps": "\n".join("- " + s for s in facts["pipeline_steps"]),
                                    "avoid_names": ", ".join(used) or "(none)"},
                 World, tag="world", validator=validate_names, effort="medium")
    world = w.model_dump()
    _repair_access(world)
    days = week_days()
    for p in world["people"]:
        p["joined"] = days[8] if p["id"] == world["late_joiner"] else "2025-01-15"
    world["days"] = days
    world["freeze_day"] = days[6]; world["review_day"] = days[7]
    run.save("02_world", "world.json", world)
    run.save("02_world", "verify_report.json", {"people": len(world["people"]), "channels": len(world["channels"]),
                                                "private": [c["id"] for c in world["channels"] if c["private"]]})
    run.mark_done("02_world")
    return world
