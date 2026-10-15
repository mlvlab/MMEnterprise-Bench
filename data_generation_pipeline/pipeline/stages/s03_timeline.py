"""Stage 03 — the world clock: which fact becomes known when, to whom, and which mistakes happen.

Three small LLM plans (runs+invented facts → errors+decisions → emails), each validated against the facts and the
access graph, then a deterministic expansion into an event script. A fourth LLM pass adds reactive chatter per day.
Nothing here is rendered as prose yet; every event carries `requires` (value strings the rendered text must contain)
and `refs` (fact/run/error/decision/doc ids) so later stages and the gold compiler can trace it.
Output: 03_timeline/plan.json, 03_timeline/events.json"""
from __future__ import annotations
import re
from .. import config, util, archetype
V = archetype.ARCHETYPES[archetype.DEFAULT]
from ..llm import LLM
from ..schemas import RunsPlan, ErrorsDecisions, EmailsPlan, DayChatter, EvolvingFacts, DocArcs
from .s01_facts import table_markdown

DOC_SCHEDULE = {  # fixed by design: report draft the night before the freeze day, both v2s on the freeze day
    "report_v1": (5, "22:10"), "report_v2": (6, "17:30"), "deck_v1": (6, "14:30"), "deck_v2": (6, "17:05"),
}
MEETINGS = [  # (day_offset, time, title, kind)
    (5, "10:30", "Monday standup", "public"), (6, "15:00", "Leadership sync", "private"), (7, "11:00", "Post-freeze retro", "public"),
]


FIG_SLOTS = [(1, "16:05"), (2, "17:30"), (3, "20:15"), (5, "11:30"), (6, "09:30"), (6, "16:10"), (4, "18:00"), (5, "16:40")]  # when the intern shares figure i


def ts(days, off, hhmm):
    return f"{days[off]}T{hhmm}"


def _people_digest(world):
    return "\n".join(f"- {p['id']}: {p['name']}, {p['role']} ({p['level']}) — owns: {p['owns']}" for p in world["people"])


def _channels_digest(world):
    return "\n".join(f"- {c['id']}{' (private)' if c['private'] else ''}: members {', '.join(c['members'])} — {c['purpose']}" for c in world["channels"])


def _days_digest(days):
    return "\n".join(f"- day {i}: {d} ({util.dayname(d + 'T00:00')})" + (" — internal milestone (freeze)" if i == 6 else " — external milestone (customer review)" if i == 7 else " — newcomer's first day" if i == 8 else "") for i, d in enumerate(days))


def _fact_lookup(facts):
    look = {f["id"]: f for f in facts["metric_facts"]}
    for c in facts["config_facts"]:
        look[c["id"]] = {"id": c["id"], "kind": "config", "value": c["value"], "name": c["name"]}
    for fig in facts["figures"]:
        for v in fig["image_only_facts"]:
            look[v["id"]] = {"id": v["id"], "kind": "visual", "value": v["value"], "figure": fig["id"], "statement": v["statement"]}
    return look


def fact_schedule(runs: list[dict], row_facts: dict) -> tuple[dict, dict]:
    """Keep the first source time and one stable run for scripted record errors."""
    canonical, first, priority = {}, {}, {}
    for run in runs:
        when = (run["day_offset"], run["time"])
        rank = (run["kind"] not in {"progression", "release"}, when, run["run_id"])
        for cell in row_facts[run["run_id"]]:
            fid = cell["fact_id"] if isinstance(cell, dict) else cell[2]
            first[fid] = min(first.get(fid, when), when)
            if fid not in priority or rank < priority[fid]:
                canonical[fid], priority[fid] = run["run_id"], rank
    return canonical, first


def first_draft_times(runs: list[dict], row_facts: dict) -> dict:
    """Earliest posted cumulative image that actually contains each fact's row."""
    first = {}
    for posted in sorted(runs, key=lambda run: (run["day_offset"], run["time"])):
        if posted["kind"] not in {"progression", "release"}:
            continue
        when = (posted["day_offset"], posted["time"])
        for row in runs:
            if (row["table_id"] != posted["table_id"] or row["kind"] not in {"baseline", "progression", "release"}
                    or (row["day_offset"], row["time"]) > when):
                continue
            for cell in row_facts[row["run_id"]]:
                fid = cell["fact_id"] if isinstance(cell, dict) else cell[2]
                first.setdefault(fid, when)
    return first


def validate_error_events(events: list[dict], errors: list[dict]):
    """A correction must refer to a wrong tracker value or image actually released earlier."""
    for error in errors:
        kind, fid, wrong = error["kind"], error["fact_id"], error["wrong_value"]
        related = [event for event in events if event.get("refs", {}).get("error") == error["id"]]
        if kind == "tracker_typo":
            introduced = [event for event in related if event["kind"] == "tracker_update"
                          and event.get("values", {}).get(fid) == wrong]
            fixed = [event for event in related if event["kind"] == "tracker_fix"]
        elif kind == "image_cell":
            introduced = [event for event in related
                          if event.get("image", {}).get("wrong") == {"fact_id": fid, "value": wrong}]
            fixed = [event for event in related if event["kind"] == "correction" and event.get("image")
                     and event["image"].get("wrong") is None]
        else:
            continue
        caught = [event for event in related if event["kind"] == "error_caught"]
        if not introduced or not caught or not fixed:
            raise ValueError(f"{error['id']}: scripted {kind} requires an actual wrong record, detection, and correction")
        if not min(event["t"] for event in introduced) <= min(event["t"] for event in caught) <= min(event["t"] for event in fixed):
            raise ValueError(f"{error['id']}: scripted error records are out of order")



def summary_views(tables: list) -> set:
    """Table ids that are summary views of another table. Stage 01 marks overlaps with `overlaps_with`; when two tables
    point at each other (the same ablation printed twice), keep the more detailed one and treat the other as the view."""
    by = {t["id"]: t for t in tables}
    seen, out = set(), set()
    for t in tables:
        if t["id"] in seen or not t.get("overlaps_with"): continue
        cluster, stack = set(), [t["id"]]
        while stack:
            x = stack.pop()
            if x in cluster or x not in by: continue
            cluster.add(x)
            for y in by:
                if by[y].get("overlaps_with") == x or (y == by[x].get("overlaps_with")): stack.append(y)
        seen |= cluster
        marked = [x for x in cluster if by[x].get("overlaps_with")]
        mains = [x for x in cluster if by[x].get("role") == "main_results"]
        if len(cluster) > 1 and mains: out |= cluster - {mains[0]}  # the headline table is never the view: its release row must stay schedulable
        elif len(cluster) > 1 and len(marked) == 1: out |= set(marked)  # one-way mark: that table is the view
        elif len(cluster) > 1:
            def size(x): return (len(by[x].get("rows") or []), sum(len(r.get("cells") or []) for r in by[x].get("rows") or []), -int(re.sub(r"\D", "", x) or 0))
            keep = max(cluster, key=size); out |= cluster - {keep}
    return out


def scheduled_progressions(tables: list, views: set, budget: int = 11) -> list:
    """The ablation_progression tables the week can actually schedule, in source order: not a summary view, row labels
    that name something (a row called '✓ · ✓' cannot become a finding), and cumulative rows within the run budget
    (14 runs minus baseline / release / main-results rows)."""
    out, used = [], 0
    for t in tables:
        if t["role"] != "ablation_progression" or t["id"] in views: continue
        labs = t.get("progression_order") or []
        if not all(re.search(r"[A-Za-z0-9]", lab or "") for lab in labs) or len(set(labs)) != len(labs): continue  # unnamed (symbol-only) or repeated rows cannot become distinct findings
        n = len(t.get("progression_order") or [])
        if used + n > budget: continue
        out.append(t); used += n
    return out

def run(run: config.Run, llm: LLM):
    global V
    V = archetype.load(run)
    facts = run.load("01_facts", "facts.json"); world = run.load("02_world", "world.json")
    days = world["days"]
    people = {p["id"]: p for p in world["people"]}
    channels = {c["id"]: c for c in world["channels"]}
    research = [p["id"] for p in world["people"] if p["level"] in ("lead", "senior", "junior", "intern") and p["id"] != world["late_joiner"]
                and "product" not in p["role"].lower()]
    pm = next((p["id"] for p in world["people"] if "product" in p["role"].lower()), research[0])
    lead = next(p["id"] for p in world["people"] if p["level"] == "lead")
    exec_ = next(p["id"] for p in world["people"] if p["level"] == "exec")
    intern = next((p["id"] for p in world["people"] if p["level"] == "intern"), research[-1])
    infra = next((p["id"] for p in world["people"] if "infra" in (p["role"] + p["owns"]).lower()), research[0])
    juniors = [p["id"] for p in world["people"] if p["level"] == "junior" and p["id"] != world["late_joiner"]]
    report_author = juniors[0] if juniors else lead
    tables = {t["id"]: t for t in facts["tables"]}
    look = _fact_lookup(facts)
    exp_channel = next((c["id"] for c in world["channels"] if any(k in c["id"] for k in ("experiment", "analysis", "finding")) or "experiment" in c["purpose"].lower()), None) or \
        next(c["id"] for c in world["channels"] if not c["private"] and c["id"] != "general" and lead in c["members"] and exec_ not in c["members"])
    proj_channel = next((c["id"] for c in world["channels"] if c["id"].startswith("proj") or "project" in c["purpose"].lower()), exp_channel)
    infra_channel = next((c["id"] for c in world["channels"] if "infra" in c["id"]), exp_channel)
    product_channel = next((c["id"] for c in world["channels"] if "product" in c["id"]), "general")
    leadership = next((c["id"] for c in world["channels"] if c["private"] and exec_ in c["members"] and not c["id"].startswith("dm-")), None)
    core_room = next((c["id"] for c in world["channels"] if c["private"] and exec_ not in c["members"] and not c["id"].startswith("dm-")), None)

    _views = summary_views(facts["tables"])
    _sched = {t["id"] for t in scheduled_progressions(facts["tables"], _views)}
    def _note(t):
        if t["id"] in _views: return f"; summary view of {t['overlaps_with']} — do not schedule rows from this table"
        if t["role"] == "ablation_progression" and t["id"] not in _sched: return "; not scheduled this week (run budget / unnamed rows) — do not schedule rows from this table"
        return ""
    tables_md = "\n\n".join(table_markdown(t, with_ids=True) + f"\nrole: {t['role']}; progression_order: {t['progression_order']}; release_row: {t['release_row']}" + _note(t) for t in facts["tables"])
    base_vars = {"method_name": facts["method_name"], "internal_model_name": world["internal_model_name"], "company": world["company_name"],
                 "product": world["product"], "customer": world["customer_name"], "milestone_a": world["milestone_a"], "milestone_b": world["milestone_b"],
                 "people": _people_digest(world), "channels": _channels_digest(world), "days": _days_digest(days), "tables": tables_md,
                 "config_facts": "\n".join(f"- {c['id']}: {c['name']} = {c['value']} ({c['context']})" for c in facts["config_facts"]),
                 "research_ids": ", ".join(research), "exp_channel": exp_channel, "infra_channel": infra_channel, "product_channel": product_channel,
                 "infra_person": infra, "pm": pm, "lead": lead}

    # ------------------------------------------------------------ A1 runs + invented facts
    release_pairs = [(t["id"], row["group"], row["label"]) for t in facts["tables"] if t["role"] == "main_results" and t.get("release_row") for row in t["rows"] if row["label"] == t["release_row"]]
    overlapped = summary_views(facts["tables"]) | {t["id"] for t in facts["tables"] if t["role"] == "ablation_progression" and t["id"] not in _sched}
    def _dup_row(tid, group, label):  # does the detailed table (or any table in the cluster) carry the same row?
        t = tables.get(tid) or {}
        others = [x for x in facts["tables"] if x["id"] != tid and (x["id"] == t.get("overlaps_with") or x.get("overlaps_with") == tid)]
        if t.get("role") == "ablation_progression" and tid not in _sched: return True  # an unscheduled progression is off-limits as a whole
        return any(row["label"] == label and row["group"] == group for x in others for row in x["rows"])

    def v_runs(p: RunsPlan):
        errs = []
        ids = [r.run_id for r in p.runs]
        if len(ids) != len(set(ids)): errs.append("run_id values must be unique")
        bad_ids = [i for i in ids if not re.fullmatch(rf"{re.escape(V.get('id_prefix', 'R'))}-\d\d", i)]
        if bad_ids: errs.append(f"run ids must look like {V.get('id_prefix', 'R')}-00, {V.get('id_prefix', 'R')}-01, …: {bad_ids[:3]}")
        prev = None
        for r in p.runs:
            t = tables.get(r.table_id)
            if not t: errs.append(f"{r.run_id}: unknown table_id {r.table_id}"); continue
            if not any(row["label"] == r.row_label and row["group"] == r.row_group for row in t["rows"]):
                valid = [(row["group"], row["label"]) for row in t["rows"]][:12]
                errs.append(f"{r.run_id}: (group '{r.row_group}', row '{r.row_label}') is not in {r.table_id}; valid (group,row) pairs include {valid}")
            else:  # the run's name must never carry the figures of its own row — the name becomes the tracker's config and every question's label
                row = next(row for row in t["rows"] if row["label"] == r.row_label and row["group"] == r.row_group)
                leaked = [c for c in row["cells"] if c and re.search(r"\d", c) and util.contains_value(r.label, c)]
                if leaked: errs.append(f"{r.run_id}: the label '{r.label}' contains the row's own figure {leaked[0]} — name what is measured, never its value")
            if r.table_id in overlapped and r.kind != "baseline" and _dup_row(r.table_id, r.row_group, r.row_label): errs.append(f"{r.run_id}: row '{r.row_label}' of {r.table_id} also appears in the detailed table {tables[r.table_id].get('overlaps_with')}; schedule it from there instead")
            if r.owner not in research: r.owner = next((o.owner for o in p.runs if o.table_id == r.table_id and o.owner in research), research[0])  # owners are research people; repaired, not rejected
            if not 0 <= r.day_offset <= 6: errs.append(f"{r.run_id}: day_offset must be 0..6 (before the freeze)")
            if not re.fullmatch(r"\d\d:\d\d", r.time): errs.append(f"{r.run_id}: time must be HH:MM")
            elif not ("07:00" <= r.time <= "23:30"): errs.append(f"{r.run_id}: runs finish between 07:00 and 23:30")
            cur = (r.day_offset, r.time)
            if prev and cur < prev: errs.append(f"{r.run_id}: runs must be listed in chronological order (day, time) — {r.run_id} is earlier than the previous run")
            prev = cur
        prog = scheduled_progressions(facts["tables"], _views)
        for t in prog:
            got = [r for r in p.runs if r.table_id == t["id"] and r.kind in ("progression", "release")]
            labels = [r.row_label for r in got]
            if labels != t["progression_order"]:
                errs.append(f"{t['id']}: the progression rows must appear as consecutive runs (kind progression, last one release) in exactly this order: {t['progression_order']} (got {labels})")
            if got and got[-1].kind != "release": errs.append(f"{t['id']}: the last progression row ({got[-1].row_label}) must have kind 'release'")
        for (tid, grp, lab) in release_pairs:
            if not any(r.table_id == tid and r.row_group == grp and r.row_label == lab and r.kind == "release" for r in p.runs):
                errs.append(f"a 'release' run is required for table {tid}, group '{grp}', row '{lab}'")
        wk = {r.day_offset for r in p.runs if r.kind in ("progression", "release")}
        if len([r for r in p.runs if r.kind in ("progression", "release")]) >= 4 and not ({3, 4} & wk):
            errs.append("at least one progression/release run must finish on day 3 or 4 (the weekend) so results evolve over the week")
        if not any(r.day_offset == 6 for r in p.runs if r.kind == "release"): errs.append("at least one release run must land on day 6 (the last results before the freeze)")
        if len(p.runs) > 14: errs.append("keep at most 14 runs")
        if not 2 <= len(p.invented) <= 4: errs.append("2 to 4 invented facts")
        for x in p.invented:
            x.name = re.sub(r"^\s*(invented|synthetic|made-up)\s+", "", x.name, flags=re.I).strip()  # the prompt's label for these items must never become their name
            if re.search(r"\binvented\b", x.name, re.I): errs.append(f"{x.id}: the name must not contain the word 'invented' — name the measurement itself")
            if x.owner not in people: errs.append(f"{x.id}: unknown owner")
            if x.channel not in channels: errs.append(f"{x.id}: unknown channel {x.channel}")
            elif x.owner not in channels[x.channel]["members"]: errs.append(f"{x.id}: {x.owner} is not a member of {x.channel}")
            if not util.num_strings(x.value): errs.append(f"{x.id}: value must contain a number")
            if not 1 <= x.day_offset <= 6: errs.append(f"{x.id}: day_offset must be 1..6")
            if not ("07:00" <= x.time <= "23:30"): errs.append(f"{x.id}: time between 07:00 and 23:30")
        return errs
    base_vars["release_rows"] = "\n".join(f"- table {tid}, group '{grp}', row '{lab}'" for (tid, grp, lab) in release_pairs) or (
        "(none designated — the last progression row is the release)" if _sched else
        "(none designated and no progression table — pick the headline row of the main-results table (the latest year / the adopted scenario), schedule it on day 6 before 12:00, and give it kind 'release')")
    plan_a = llm.json("03_timeline/runs", base_vars, RunsPlan, tag="plan_runs", validator=v_runs, effort="high")

    known_ds = {r.dataset for r in plan_a.runs if r.dataset}
    for r in plan_a.runs:  # a run without a dataset takes the one its table title names (the rows report on it)
        if not r.dataset:
            title = tables.get(r.table_id, {}).get("title", "") + " " + tables.get(r.table_id, {}).get("caption", "")
            hit = [d for d in known_ds if d.lower() in title.lower()]
            if len(hit) == 1: r.dataset = hit[0]
    _lj_repair_runs(plan_a, world, research)
    # ------------------------------------------------------------ A2 errors + decisions
    runs_md = "\n".join(f"- {r.run_id} ({r.kind}) day {r.day_offset} {r.time} by {r.owner}: {r.label} [{r.table_id} / {r.row_group} / {r.row_label}] dataset {r.dataset}" for r in plan_a.runs)
    inv_md = "\n".join(f"- {x.id}: {x.name} = {x.value} (owner {x.owner}, first posted day {x.day_offset} {x.time} in {x.channel})" for x in plan_a.invented)
    row_facts = {}
    for r in plan_a.runs:
        t = tables[r.table_id]
        row = next(row for row in t["rows"] if row["label"] == r.row_label and (row["group"] == r.row_group or not r.row_group))
        row_facts[r.run_id] = [(h, c, f) for h, c, f in zip(t["headers"], row["cells"], row["fact_ids"]) if f]
    facts_md = "\n".join(f"- {r.run_id}: " + ", ".join(f"{f}={c} ({h})" for h, c, f in row_facts[r.run_id]) for r in plan_a.runs)
    vis_md = "\n".join(f"- {fig['id']} ({fig['caption'][:70]}): " + "; ".join(f"{v['id']}: {v['statement']} [{v['value']}]" for v in fig["image_only_facts"][:5]) for fig in facts["figures"])
    run_time = {r.run_id: (r.day_offset, r.time) for r in plan_a.runs}
    fact_run, fact_first = fact_schedule([r.model_dump() for r in plan_a.runs], row_facts)
    draft_first = first_draft_times([r.model_dump() for r in plan_a.runs], row_facts)
    inv_time = {x.id: (x.day_offset, x.time) for x in plan_a.invented}
    inv_val = {x.id: x.value for x in plan_a.invented}
    inv_owner = {x.id: x.owner for x in plan_a.invented}
    runs = {r.run_id: r.model_dump() for r in plan_a.runs}
    doc_days = {k: v for k, v in DOC_SCHEDULE.items()}
    ds_vals = {}
    for r in plan_a.runs:
        if r.dataset:
            for _, c, _ in row_facts[r.run_id]: ds_vals.setdefault(r.dataset, set()).add(c)
    _elig = sorted({f for r in plan_a.runs for _, _, f in row_facts[r.run_id] if f and (r.day_offset, r.time) < doc_days["report_v1"] and r.kind != "baseline"})
    vars_b = dict(base_vars, runs=runs_md, invented=inv_md, run_facts=facts_md, visual_facts=vis_md, leadership=leadership or "general", doc_value_eligible=", ".join(_elig) or "(none)",
                  core_room=core_room or exp_channel, report_author=report_author, report_v1=f"day {doc_days['report_v1'][0]} {doc_days['report_v1'][1]}",
                  report_v2=f"day {doc_days['report_v2'][0]} {doc_days['report_v2'][1]}", deck_v1=f"day {doc_days['deck_v1'][0]} {doc_days['deck_v1'][1]}",
                  deck_v2=f"day {doc_days['deck_v2'][0]} {doc_days['deck_v2'][1]}", n_errors=str(config.SETTINGS["n_errors"]), n_decisions=str(config.SETTINGS["n_decisions"]))

    run_first_fact = {}  # run id → its first numeric fact id (models sometimes name the run instead of a fact)
    for r in plan_a.runs:
        tl = row_facts.get(r.run_id, [])
        run_first_fact[r.run_id] = next((f for h, c, f in tl if re.match(r"-?\d", str(c))), (tl[0][2] if tl else None))

    def v_ed(p: ErrorsDecisions):
        errs = []
        kinds = [e.kind for e in p.errors]
        for need in ["tracker_typo", "image_cell", "doc_value", "channel_value", "doc_headline"]:
            if kinds.count(need) != 1: errs.append(f"exactly one error of kind {need} is required (got {kinds.count(need)})")
        if len(p.errors) > config.SETTINGS["n_errors"] + 1: errs.append(f"at most {config.SETTINGS['n_errors'] + 1} errors")
        dm_count = sum(e.caught_in == "dm" for e in p.errors)
        if dm_count < 2: errs.append("at least 2 errors must be caught in a DM")
        for e in p.errors:
            if e.fact_id not in look and e.fact_id not in inv_val and e.fact_id in run_first_fact:
                e.fact_id = run_first_fact[e.fact_id]  # the model named the run instead of one of its facts
            f = look.get(e.fact_id)
            if e.fact_id in inv_val: true = inv_val[e.fact_id]
            elif f: true = f["value"]
            else: errs.append(f"{e.id}: unknown fact_id {e.fact_id}"); continue
            if e.kind == "unfixed_inconsistency":
                if not e.fact_id.startswith("V"): errs.append(f"{e.id}: unfixed_inconsistency must reference an image-only fact id (V…)")
            else:
                if e.fact_id.startswith("V"): errs.append(f"{e.id}: only unfixed_inconsistency may reference a V… fact")
                if e.fact_id not in inv_val and (not util.num_strings(str(true)) or len(str(true)) > 60):  # invented values are phrases with a number; table cells must be values
                    errs.append(f"{e.id}: fact {e.fact_id} has the non-numeric value '{str(true)[:40]}'; errors must corrupt a numeric fact — eligible: {[k for k, x in look.items() if util.num_strings(str(x.get('value', ''))) and len(str(x.get('value', ''))) <= 40][:40]}")
                if e.wrong_value.strip() == str(true).strip(): errs.append(f"{e.id}: wrong_value must differ from the true value {true}")
                if e.kind != "doc_headline" and not util.num_strings(e.wrong_value): errs.append(f"{e.id}: wrong_value must be a number-like string")
            for pid in (e.actor, e.caught_by):
                if pid not in people: errs.append(f"{e.id}: unknown person {pid}")
            # timing
            if e.fact_id in fact_run: wt = run_time[fact_run[e.fact_id]]
            elif e.fact_id in inv_time: wt = inv_time[e.fact_id]
            else: wt = (0, "00:00")
            if e.kind == "doc_value":
                if e.fact_id in fact_first and fact_first[e.fact_id] >= doc_days["report_v1"]: errs.append(f"{e.id}: doc_value must reference a fact first recorded before report v1 (day {doc_days['report_v1'][0]} {doc_days['report_v1'][1]}); {e.fact_id} first appears at day {fact_first[e.fact_id][0]} {fact_first[e.fact_id][1]}")
                wt = doc_days["report_v1"]
            if e.kind == "doc_headline":
                if e.fact_id in fact_first and fact_first[e.fact_id] >= doc_days["deck_v1"]: errs.append(f"{e.id}: doc_headline must reference a fact first recorded before deck v1 (day {doc_days['deck_v1'][0]} {doc_days['deck_v1'][1]})")
                wt = doc_days["deck_v1"]
            if e.kind == "image_cell":
                if e.fact_id not in draft_first:
                    errs.append(f"{e.id}: image_cell needs a fact whose row appears in a posted progression/release draft image")
                else:
                    wt = draft_first[e.fact_id]
            ct, ft = (e.day_offset_caught, e.time_caught), (e.day_offset_fixed, e.time_fixed)
            if ct < wt or (e.kind == "image_cell" and ct == wt): errs.append(f"{e.id}: caught ({ct}) must be after the value first appears ({wt})")
            if ft < ct: errs.append(f"{e.id}: fixed ({ft}) must be at or after caught ({ct})")
            if e.kind == "doc_value" and ft != doc_days["report_v2"]: errs.append(f"{e.id}: a doc_value error is fixed exactly when report v2 is posted: day {doc_days['report_v2'][0]} {doc_days['report_v2'][1]}")
            if e.kind == "doc_headline" and ft != doc_days["deck_v2"]: errs.append(f"{e.id}: a doc_headline error is fixed exactly when deck v2 is posted: day {doc_days['deck_v2'][0]} {doc_days['deck_v2'][1]}")
            if e.kind == "tracker_typo" and e.fact_id not in fact_run: errs.append(f"{e.id}: tracker_typo must reference a metric fact reported by a run")
            elif e.kind == "tracker_typo" and runs[fact_run[e.fact_id]]["kind"] not in {"progression", "release"}:
                errs.append(f"{e.id}: tracker_typo needs a progression/release fact so its wrong tracker entry is actually created")
            if e.kind == "image_cell" and e.fact_id not in fact_run: errs.append(f"{e.id}: image_cell must reference a metric fact of a progression/release run")
            if e.kind == "channel_value" and e.fact_id not in inv_val: errs.append(f"{e.id}: channel_value must reference an invented fact (X…)")
            if e.kind == "doc_headline" and e.fact_id not in fact_run: errs.append(f"{e.id}: doc_headline must reference a metric fact")
            if e.kind == "unfixed_inconsistency" and ft[0] < 6: errs.append(f"{e.id}: an unfixed inconsistency is flagged on day 6 and deferred")
            if e.day_offset_fixed > 7: errs.append(f"{e.id}: fixes happen by day 7")
        for e in p.errors:  # internal ids in a note are replaced by the metric's name in code (the model keeps writing them)
            def _name(m):
                k = m.group(0); f_ = look.get(k)
                if f_: return f"{f_.get('column') or f_.get('name') or 'the value'} of {f_.get('row') or ''}".strip(" of")
                return next((x.name for x in plan_a.invented if x.id == k), "that value")
            e.note = re.sub(r"\b[FVKC]\d{3}\b|\bX-\d\d\b", _name, e.note)
        for d in p.decisions:
            for fld in ("title", "body", "rationale"):
                txt = getattr(d, fld, "") or ""
                if util.bad_words(txt) or re.search(r"\btab\d+\b|\s/\s*/\s|\{\{", txt): errs.append(f"{d.id}: {fld} must read like a decision-log entry in the company's words — no internal ids (tab1, F001), no empty '/ /' fragments; name the table, row and values instead")
            rl = d.rationale.strip().lower()
            if not re.search(r"\b(because|so that|since|otherwise|to avoid|to keep|to make sure|to prevent|would|risk|without|so|as|ensures?|keeps?|preserves?|lets?|allows?|avoids?|prevents?|makes?|enables?|reduces?|protects?|guarantees?|means)\b", rl):
                errs.append(f"{d.id}: rationale must state a reason in words (e.g. 'because …', 'so that …', '… keeps the rows comparable'), not just list numbers")
            if re.match(r"^[^a-zA-Z]*\d", d.rationale.strip()):
                errs.append(f"{d.id}: rationale must start with words, not with a number")
            blob = d.title + " " + d.body + " " + d.rationale
            # a decision can only quote results and measurements that exist when it is taken
            dt = (d.day_offset, d.time)
            for rid in set(util.run_id_re(V.get("id_prefix", "R")).findall(blob)):
                if rid in run_time and run_time[rid] > dt: errs.append(f"{d.id}: quotes {rid}, which only finishes at day {run_time[rid][0]} {run_time[rid][1]} (decision is day {dt[0]} {dt[1]}); move the decision later or drop the reference")
            for msg in util.dataset_mismatch(blob, ds_vals): errs.append(f"{d.id}: {msg}")  # (values quoted before they exist are repaired by a time shift below)
            if re.search(r"\bE-\d\d\b", blob): errs.append(f"{d.id}: do not write error ids like E-01 in decision text; name the metric that slipped instead")
        dids = [d.id for d in p.decisions]
        if len(dids) != len(set(dids)): errs.append("decision ids must be unique")
        if not 4 <= len(p.decisions) <= config.SETTINGS["n_decisions"] + 1: errs.append(f"4 to {config.SETTINGS['n_decisions'] + 1} decisions")
        priv = [d for d in p.decisions if d.rationale_visibility == "private"]
        if not priv: errs.append("at least one decision has a private rationale")
        for d in p.decisions:
            if d.by not in people: errs.append(f"{d.id}: unknown person {d.by}")
            if d.rationale_visibility == "private":
                c = channels.get(d.private_channel or "")
                if not c or not c["private"] or d.by not in c["members"]: errs.append(f"{d.id}: private rationale needs private_channel = a private room containing {d.by} (e.g. {leadership})")
            if d.related_error_id and d.related_error_id not in [e.id for e in p.errors]: errs.append(f"{d.id}: unknown related_error_id")
            if not 0 <= d.day_offset <= 7: errs.append(f"{d.id}: day_offset 0..7")
        head = next((e for e in p.errors if e.kind == "doc_headline"), None)
        if head and not any(d.related_error_id == head.id and d.rationale_visibility == "private" for d in p.decisions):
            errs.append("the doc_headline error must be resolved by a decision with related_error_id = its id and a private rationale")
        unf = next((e for e in p.errors if e.kind == "unfixed_inconsistency"), None)
        if unf and not any(d.related_error_id == unf.id for d in p.decisions):
            errs.append("the unfixed_inconsistency error needs a decision (post-freeze fix list) with related_error_id = its id")
        return errs
    plan_b = llm.json("03_timeline/errors_decisions", vars_b, ErrorsDecisions, tag="plan_errors", validator=v_ed, effort="high")
    _repair_errors(plan_b, world, runs, fact_run, inv_owner, report_author, pm, research)
    _lj_repair_errors(plan_b, world, lead, research, run_time, fact_run, inv_time)
    for d in plan_b.decisions:  # a decision that quotes a later run/value is moved after it (never rejected at this point)
        blob = d.title + " " + d.body + " " + d.rationale
        need = [run_time[rid] for rid in set(util.run_id_re(V.get("id_prefix", "R")).findall(blob)) if rid in run_time]
        for n in set(util.num_strings(blob)):
            if len(n) < 3 and "." not in n: continue
            owners = [fid for fid, f in look.items() if f["value"] == n and fid in fact_run] + [xid for xid, v in inv_val.items() if v == n]
            if owners: need.append(min(fact_first[fid] if fid in fact_first else inv_time[fid] for fid in owners))
        if need and max(need) > (d.day_offset, d.time):
            dd, tt = max(need); mm = min(int(tt[:2]) * 60 + int(tt[3:]) + 35, 22 * 60)
            if dd <= 7: d.day_offset, d.time = dd, f"{mm // 60:02d}:{mm % 60:02d}"
    for d in plan_b.decisions:  # a post-freeze list is decided after the freeze message (day 6 20:55)
        if "fix list" in d.title.lower() or "post-freeze" in d.title.lower():
            if d.day_offset < 6 or (d.day_offset == 6 and d.time <= "20:55"): d.day_offset, d.time = 6, "21:05"
    for d in plan_b.decisions:  # internal ids in decision text → the values / names they stand for
        err_label = {}
        for e in plan_b.errors:
            f_ = look.get(e.fact_id)
            err_label[e.id] = (f"the {f_.get('column') or f_.get('name') or f_.get('label') or 'value'} slip" if f_ else next((f"the {x.name} slip" for x in plan_a.invented if x.id == e.fact_id), "that slip"))  # a config fact or claim has no column
        def _sub(txt):
            txt = re.sub(r"\b([FVKC]\d{3})\b", lambda m: look[m.group(1)]["value"] if m.group(1) in look else "", txt)
            txt = re.sub(r"\bX-(\d\d)\b", lambda m: next((x.name for x in plan_a.invented if x.id == m.group(0)), ""), txt)
            txt = re.sub(r"\bE-\d\d\b", lambda m: err_label.get(m.group(0), ""), txt)
            txt = re.sub(r"(\s*,\s*){2,}", ", ", txt); txt = re.sub(r",\s*(and|or)\b", r" \1", txt); txt = re.sub(r"\bfor\s*(and|or)\b", r"for \1", txt)
            return re.sub(r"\s{2,}", " ", txt).replace(" ,", ",").replace(" ;", ";").strip()
        d.title, d.body, d.rationale = _sub(d.title), _sub(d.body), _sub(d.rationale)

    # ------------------------------------------------------------ A3 emails
    err_md = "\n".join(f"- {e.id} {e.kind}: {e.fact_id} wrong '{e.wrong_value}' by {e.actor}, caught by {e.caught_by} ({e.caught_in}) day {e.day_offset_caught} {e.time_caught}, fixed day {e.day_offset_fixed} {e.time_fixed}" for e in plan_b.errors)
    dec_md = "\n".join(f"- {d.id} day {d.day_offset} {d.time} by {d.by}: {d.title} — {d.body} (rationale {d.rationale_visibility})" for d in plan_b.decisions)
    vars_c = dict(vars_b, errors=err_md, decisions=dec_md, late_joiner=world["late_joiner"], customer_contact=world["customer_contact"], exec_id=exec_)

    def v_em(p: EmailsPlan):
        errs = []
        if not 6 <= len(p.emails) <= config.SETTINGS["n_emails"] + 1: errs.append(f"6 to {config.SETTINGS['n_emails'] + 1} emails")
        if not any(e.from_id is None for e in p.emails): errs.append("at least one inbound email from the customer contact (from_id null) on day 1")
        if not any(e.external_to and e.attach_doc == "deck_v2" for e in p.emails): errs.append("one outbound email to the customer must attach deck_v2 after it exists (day 6 evening)")
        if not any(world["late_joiner"] in e.to_ids for e in p.emails): errs.append(f"one welcome email to the late joiner ({world['late_joiner']}) on day 7 assigning a post-freeze task")
        for e in p.emails:
            for pid in [e.from_id] + e.to_ids + e.cc_ids:
                pass  # unknown ids (typically the external contact's first name) are repaired below, not rejected

            if not 0 <= e.day_offset <= 8: errs.append(f"{e.id}: day_offset 0..8")
            pass
            for v in e.required_values:
                if not util.num_strings(v) and len(v) > 40: errs.append(f"{e.id}: required_values must be short value strings")
        return errs
    plan_c = llm.json("03_timeline/emails", vars_c, EmailsPlan, tag="plan_emails", validator=v_em, effort="medium")
    for e in plan_c.emails:  # an attachment that does not exist yet is simply dropped
        if e.attach_doc and (e.day_offset, e.time) < DOC_SCHEDULE[e.attach_doc]:
            e.attach_doc = None
    for e in plan_c.emails:  # a mail brief names things, never fact ids (the writer would copy F492 into the mail and the id check would reject it forever)
        e.purpose = re.sub(r"\b([FVKC]\d{3})\b", lambda m: (f"the {look[m.group(1)].get('column') or look[m.group(1)].get('name') or look[m.group(1)].get('label') or 'figure'} figure" if m.group(1) in look else "that figure"), e.purpose)
        e.purpose = re.sub(r"(?<![A-Za-z])X-(\d\d)\b", lambda m: next((x.name for x in plan_a.invented if x.id == m.group(0)), "that measurement"), e.purpose)
    for e in plan_c.emails:  # ids that are not employees (the external contact's first name, a typo) are stripped; the contact becomes external
        if e.from_id is not None and e.from_id not in people: e.from_id = None
        if any(x not in people for x in e.to_ids): e.external_to = True
        e.to_ids = [x for x in e.to_ids if x in people]; e.cc_ids = [x for x in e.cc_ids if x in people]
    for e in plan_c.emails:  # the newcomer never writes mail before their first day
        if e.from_id == world["late_joiner"] and e.day_offset < 8: e.from_id = lead
    for e in plan_c.emails:  # nobody receives mail before their first day
        if world["late_joiner"] in e.to_ids + e.cc_ids and e.day_offset < 8: e.day_offset, e.time = 8, "09:05"
    for e in plan_c.emails:  # inbound mail without an internal recipient goes to the PM
        if e.from_id is None and not e.to_ids:
            e.to_ids = [pm]
    for e in plan_c.emails:  # deterministic repairs: keep the key numbers only; a requirement must be a threshold, not the measurement
        e.required_values = [look[v]["value"] if v in look else v for v in e.required_values if v in look or not util.INTERNAL_ID_RE.search(str(v))][:6]  # ids become their values; stray ids are dropped
        if e.from_id is None and e.day_offset <= 2:
            fixed = []
            for val in e.required_values:
                hit = next((x for x in plan_a.invented if util.contains_value(x.value, val) or util.contains_value(val, x.value)), None)
                if hit:
                    nums = util.num_strings(hit.value)
                    try:
                        n = float(nums[0].replace(",", "")); thr = n * 1.15
                        thr_s = f"{thr:.0f}" if thr >= 20 else f"{thr:.1f}"
                        unit = hit.value.replace(nums[0], "", 1).strip()
                        val = f"under {thr_s} {unit}".strip()
                        e.purpose += f" (requirement stated as a threshold: {val})"
                    except (ValueError, IndexError):
                        pass
                fixed.append(val)
            e.required_values = fixed

    # ------------------------------------------------------------ A4 evolving facts (conversation-driven updates)
    def v_ev(p: EvolvingFacts):
        errs = []
        if not 3 <= len(p.facts) <= 5: errs.append("3 to 5 evolving facts")
        kinds = [f.kind for f in p.facts]
        if "owner" not in kinds: errs.append("one fact of kind owner is required")
        if "date" not in kinds: errs.append("one fact of kind date is required")
        if kinds.count("numeric") > 1: errs.append("at most one numeric fact")
        if not any(ver.how == "correction" for f in p.facts for ver in f.versions): errs.append("at least one version with how='correction'")
        for f in p.facts:
            if not 2 <= len(f.versions) <= 3: errs.append(f"{f.id}: 2 to 3 versions")
            prev = None
            for i, ver in enumerate(f.versions):
                if ver.channel not in channels: errs.append(f"{f.id}: unknown channel {ver.channel}"); continue
                if ver.by not in channels[ver.channel]["members"]: errs.append(f"{f.id}: {ver.by} is not a member of {ver.channel}")
                if not ("08:00" <= ver.time <= "22:00"): errs.append(f"{f.id}: times between 08:00 and 22:00")
                cur = (ver.day_offset, ver.time)
                if prev and cur <= prev: errs.append(f"{f.id}: versions must be strictly chronological")
                prev = cur
                if i == 0 and ver.day_offset > 2: errs.append(f"{f.id}: first version on day 0-2")
                if ver.day_offset > 7: errs.append(f"{f.id}: last version by day 7")
                if f.kind == "owner" and ver.value not in people: errs.append(f"{f.id}: owner values must be person ids")
                if ver.how == "initial" and i > 0: errs.append(f"{f.id}: only the first version is 'initial'")
                if i == 0 and ver.how != "initial": errs.append(f"{f.id}: the first version must be 'initial'")
            vals = [ver.value for ver in f.versions]
            if len(set(vals)) < 2: errs.append(f"{f.id}: versions must change the value")
            if f.kind == "numeric" and not all(util.num_strings(x) for x in vals): errs.append(f"{f.id}: numeric values need a number")
        return errs
    plan_d = llm.json("03_timeline/evolving", dict(vars_c), EvolvingFacts, tag="plan_evolving", validator=v_ev, effort="medium")
    _lj_repair_evolving(plan_d, world, channels, lead, research)

    # ------------------------------------------------------------ A5 document arcs (artifacts going back and forth)
    fig_md = "\n".join(f"- {f['id']}: {f['description'][:120]}" for f in facts["figures"]) or "(none — this source has no figures: do not use review_kind add_figure; use wording or mismatch instead)"
    dms = {frozenset(c["members"]): c["id"] for c in world["channels"] if c["id"].startswith("dm-")}

    def v_arcs(p: DocArcs):
        errs = []
        if not 2 <= len(p.arcs) <= 3: errs.append("2 or 3 arcs")
        if not facts["figures"]:  # nothing to add: an add_figure arc is repaired into a wording review rather than rejected forever
            for a in p.arcs:
                if a.review_kind == "add_figure": a.review_kind, a.figure_id = "wording", ""
        kinds = [a.review_kind for a in p.arcs]
        if "mismatch" not in kinds: errs.append("one arc with review_kind mismatch (kind memo_table)")
        if "add_figure" not in kinds and facts["figures"]: errs.append("one arc with review_kind add_figure")
        # (a DM review is arranged by code below when the model does not manage it)
        for a in p.arcs:
            for pid in (a.author, a.reviewer):
                if pid not in people: errs.append(f"{a.id}: unknown person {pid}")
            if a.author == a.reviewer: errs.append(f"{a.id}: reviewer must differ from author")
            if a.channel not in channels: errs.append(f"{a.id}: unknown channel {a.channel}")
            elif a.author not in channels[a.channel]["members"]: errs.append(f"{a.id}: author not in {a.channel}")
            if a.review_channel.startswith("dm-"):
                if dms.get(frozenset({a.author, a.reviewer})) != a.review_channel: errs.append(f"{a.id}: review_channel must be the DM between {a.author} and {a.reviewer}: {dms.get(frozenset({a.author, a.reviewer}), 'none exists — use the posting channel')}")
            elif a.review_channel != a.channel: errs.append(f"{a.id}: review_channel is the posting channel or a DM")
            if a.reviewer not in channels[a.channel]["members"]: errs.append(f"{a.id}: reviewer must be a member of {a.channel}")
            t1, tr, t2 = (a.day_offset_v1, a.time_v1), (a.day_offset_review, a.time_review), (a.day_offset_v2, a.time_v2)
            if not 1 <= a.day_offset_v1 <= 5: errs.append(f"{a.id}: v1 on day 1-5")
            if not (t1 < tr < t2): errs.append(f"{a.id}: v1 < review < v2 in time")
            if a.day_offset_v2 > 6: errs.append(f"{a.id}: v2 by day 6")
            if a.review_kind == "mismatch":
                if a.kind != "memo_table": errs.append(f"{a.id}: mismatch arcs have kind memo_table")
                f = look.get(a.wrong_fact_id)
                if not f or a.wrong_fact_id not in fact_run: errs.append(f"{a.id}: wrong_fact_id must be a metric fact of a run")
                else:
                    rt = fact_first[a.wrong_fact_id]
                    if rt >= t1: errs.append(f"{a.id}: the run of {a.wrong_fact_id} finishes at day {rt[0]} {rt[1]}, after v1")
                    if not a.wrong_value or a.wrong_value.strip() == f["value"]: errs.append(f"{a.id}: wrong_value must differ from {f['value']}")
                    pass  # wrong_value is normalised to a bare number below
            if a.review_kind == "add_figure" and a.figure_id not in {f["id"] for f in facts["figures"]}: errs.append(f"{a.id}: figure_id must be one of {[f['id'] for f in facts['figures']]}")
        return errs
    plan_e = llm.json("03_timeline/arcs", dict(vars_b, figures=fig_md), DocArcs, tag="plan_arcs", validator=v_arcs, effort="medium")
    _lj_repair_arcs(plan_e, world, channels, dms)
    _rre_ = util.run_id_re(V.get("id_prefix", "R")); _fin = {r.run_id: (r.day_offset, r.time) for r in plan_a.runs}
    for a in plan_e.arcs:  # a note cannot summarise a run that has not finished: move the arc after it, or drop the reference
        late = [rid for rid in set(_rre_.findall(a.note)) if rid in _fin and _fin[rid] > (a.day_offset_v1, a.time_v1)]
        if not late: continue
        dd, tt = max(_fin[rid] for rid in late); mm = min(int(tt[:2]) * 60 + int(tt[3:]) + 35, 22 * 60)
        if dd <= 5:
            shift = (dd, f"{mm // 60:02d}:{mm % 60:02d}")
            if (a.day_offset_v1, a.time_v1) < shift: a.day_offset_v1, a.time_v1 = shift
            if (a.day_offset_review, a.time_review) <= shift: a.day_offset_review, a.time_review = dd, f"{min(mm + 60, 22 * 60) // 60:02d}:{min(mm + 60, 22 * 60) % 60:02d}"
            if (a.day_offset_v2, a.time_v2) <= (a.day_offset_review, a.time_review): a.day_offset_v2, a.time_v2 = min(dd + 1, 6), "09:00"
        else:
            a.note = re.sub(r"\s{2,}", " ", re.sub(rf"\b(?:{'|'.join(map(re.escape, late))})\b", "the earlier", a.note)).replace("the earlier comparison runs", "the comparison runs")
    for a in plan_e.arcs:
        if a.review_kind == "add_figure" and a.kind == "diagram": a.kind = "note"
    fig_ids = [f["id"] for f in facts["figures"][:8]]
    for a in plan_e.arcs:  # the figure a reviewer asks to add must already be shared in chat when v2 is posted
        if a.review_kind != "add_figure" or a.figure_id not in fig_ids: continue
        t2 = (a.day_offset_v2, a.time_v2)
        if FIG_SLOTS[fig_ids.index(a.figure_id)] < t2: continue
        earlier = [fid for fid in fig_ids if FIG_SLOTS[fig_ids.index(fid)] < (a.day_offset_review, a.time_review)]
        if earlier:
            a.figure_id = earlier[-1]
        else:
            d, hh = FIG_SLOTS[fig_ids.index(a.figure_id)]
            a.day_offset_review, a.time_review = d, util.add_min(f"2000-01-01T{hh}", 40)[11:]
            a.day_offset_v2, a.time_v2 = d, util.add_min(f"2000-01-01T{hh}", 95)[11:]
            if a.day_offset_v2 > 6: a.day_offset_v2 = 6
    for a in plan_e.arcs:  # deterministic repairs: bare wrong values; one review in a DM
        if a.review_kind == "mismatch" and a.wrong_fact_id in look:
            m = re.search(r"-?\d[\d,]*(?:\.\d+)?%?", a.wrong_value or "")
            true = look[a.wrong_fact_id]["value"]
            a.wrong_value = m.group() if m and m.group() != true.strip() else (true[::-1] if true.strip().replace(".", "").isdigit() else "0")
    if plan_e.arcs and not any(a.review_channel.startswith("dm-") for a in plan_e.arcs):
        for a in plan_e.arcs:
            pair = next((tuple(k) for k in dms if len(k) == 2 and any(x in channels[a.channel]["members"] for x in k)), None)
            if pair and pair[0] not in channels[a.channel]["members"]: pair = (pair[1], pair[0])
            if pair:
                a.author, a.reviewer = pair[0], pair[1]; a.review_channel = dms[frozenset(pair)]; break

    plan = {"runs": [r.model_dump() for r in plan_a.runs], "invented": [x.model_dump() for x in plan_a.invented], "evolving": [f.model_dump() for f in plan_d.facts], "arcs": [a.model_dump() for a in plan_e.arcs],
            "errors": [e.model_dump() for e in plan_b.errors], "decisions": [d.model_dump() for d in plan_b.decisions],
            "emails": [e.model_dump() for e in plan_c.emails], "doc_schedule": DOC_SCHEDULE, "roles": {"lead": lead, "exec": exec_, "pm": pm, "intern": intern,
            "infra": infra, "report_author": report_author, "deck_author": pm, "research": research},
            "channels": {"exp": exp_channel, "proj": proj_channel, "infra": infra_channel, "product": product_channel, "leadership": leadership, "core": core_room},
            "row_facts": {k: [{"header": h, "value": c, "fact_id": f} for h, c, f in v] for k, v in row_facts.items()}}
    run.save("03_timeline", "plan.json", plan)

    # ------------------------------------------------------------ B deterministic event expansion
    events = _expand(plan, facts, world, days)
    validate_error_events(events, plan["errors"])
    # ------------------------------------------------------------ C chatter per day (LLM)
    events = _chatter(llm, plan, world, days, events, base_vars)
    events.sort(key=lambda e: (e["t"], e["id"]))
    run.save("03_timeline", "events.json", events)
    report = {"n_events": len(events), "by_kind": _count(events, "kind"), "by_channel": _count(events, "channel"), "chatter_soft_fallback_days": list(SOFT_DAYS),
              "errors": [(e["id"], e["kind"], e["caught_in"]) for e in plan["errors"]], "decisions": len(plan["decisions"]), "emails": len(plan["emails"])}
    run.save("03_timeline", "verify_report.json", report)
    run.mark_done("03_timeline")
    return events



SOFT_DAYS: list = []


def _date_shown(value: str, days) -> str:
    """'day 5 18:00' / 'day 5' written by the planner → the calendar wording the team would use ('Mon Aug 31 18:00')."""
    m = re.fullmatch(r"\s*day\s*(\d)\s*(?:at\s*)?(\d\d:\d\d)?\s*", str(value), re.I)
    if not m or int(m.group(1)) > 8: return value
    t = ts(days, int(m.group(1)), m.group(2) or "09:00")
    return util.human(t) if m.group(2) else util.human(t)[:-6]


def _joined_day(world) -> int:
    return 8  # the late joiner's first day (config: day 8 of the 9-day window)


def _lj_repair_runs(plan_a, world, research):
    lj = world["late_joiner"]
    others = [p for p in research if p != lj] or [p["id"] for p in world["people"] if p["id"] != lj]
    for r in plan_a.runs:
        if r.owner == lj: r.owner = others[0]
    for x in plan_a.invented:
        if x.owner == lj: x.owner = others[0]


def _lj_repair_errors(plan_b, world, lead, research, run_time, fact_run, inv_time):
    """Nobody acts before their first day: decisions, mistakes and catches by the newcomer move to the lead / a colleague."""
    lj = world["late_joiner"]; jd = _joined_day(world)
    others = [p for p in research if p != lj] or [lead]
    for d in plan_b.decisions:
        if d.by == lj and d.day_offset < jd: d.by = lead
    for e in plan_b.errors:
        if e.actor == lj and e.day_offset_caught < jd: e.actor = others[0]
        if e.caught_by == lj and e.day_offset_caught < jd: e.caught_by = next((p for p in others if p != e.actor), lead)


def _lj_repair_evolving(plan_d, world, channels, lead, research):
    lj = world["late_joiner"]; jd = _joined_day(world)
    for f in plan_d.facts:
        for ver in f.versions:
            if ver.day_offset >= jd: continue
            if ver.by == lj:
                ver.by = next((m for m in channels.get(ver.channel, {}).get("members", []) if m != lj), lead)
            if f.kind == "owner" and ver.value == lj:
                ver.value = next((p for p in research if p != lj), lead)


def _lj_repair_arcs(plan_e, world, channels, dms):
    lj = world["late_joiner"]; jd = _joined_day(world)
    for a in plan_e.arcs:
        if a.day_offset_v1 >= jd: continue
        members = [m for m in channels.get(a.channel, {}).get("members", []) if m != lj]
        if a.author == lj and members: a.author = members[0]
        if a.reviewer == lj and members: a.reviewer = next((m for m in members if m != a.author), members[0])
        if a.review_channel.startswith("dm-"):
            a.review_channel = dms.get(frozenset({a.author, a.reviewer})) or a.channel


def _repair_errors(plan_b, world, runs, fact_run, inv_owner, report_author, pm, research):
    """Actors are a property of the value, not a choice: the run owner slips in the tracker/image, the fact owner
    in the channel, the report author in the report, the PM in the deck. If the planned DM does not exist, use a
    DM partner of the actor or fall back to public."""
    dms = {frozenset(c["members"]): c["id"] for c in world["channels"] if c["id"].startswith("dm-")}
    for e in plan_b.errors:
        if e.kind in ("tracker_typo", "image_cell") and e.fact_id in fact_run: e.actor = runs[fact_run[e.fact_id]]["owner"]
        elif e.kind == "channel_value" and e.fact_id in inv_owner: e.actor = inv_owner[e.fact_id]
        elif e.kind == "doc_value": e.actor = report_author
        elif e.kind == "doc_headline": e.actor = pm
        if e.caught_by == e.actor and e.kind != "image_cell":
            e.caught_by = next((p for p in research if p != e.actor), e.caught_by)
        if e.caught_in == "dm" and frozenset({e.actor, e.caught_by}) not in dms:
            partner = next((set(k) - {e.actor} for k in dms if e.actor in k), None)
            if partner: e.caught_by = list(partner)[0]
            else: e.caught_in = "public"
        if e.kind == "image_cell" and e.caught_in == "dm" and e.caught_by == e.actor:
            partner = next((set(k) - {e.actor} for k in dms if e.actor in k), None)
            if partner: e.caught_by = list(partner)[0]
            else: e.caught_in = "public"; e.caught_by = next((p for p in research if p != e.actor), e.caught_by)
        # the free-text note must name the same people as the repaired fields (it is quoted in event gists)
        first = {q["id"]: q["name"].split()[0] for q in world["people"]}
        for pid, fn in first.items():
            if pid not in (e.actor, e.caught_by) and re.search(rf"\b{re.escape(fn)}\b", e.note or ""):
                e.note = re.sub(rf"\b{re.escape(fn)}\b", first.get(e.actor, fn), e.note)


def ctx_of(f):
    return (f" of {f['row']}" + (f" ({f['group']})" if f.get("group") else "")) if f.get("row") else ""


def _count(events, key):
    c = {}
    for e in events:
        k = e.get(key) or "-"; c[k] = c.get(k, 0) + 1
    return c


def _dm(world, a, b):
    for c in world["channels"]:
        if c["id"].startswith("dm-") and set(c["members"]) == {a, b}:
            return c["id"]
    return None


def _fig_desc(f):
    """Content description of a figure for event gists: first sentence of the visual description, never the
    publication caption ('Fig. 3: …' is seed vocabulary the company must not use)."""
    d = (f.get("description") or "").strip()
    if not d:
        d = re.sub(r"^\s*(Fig\.?|Figure|Table|Tab\.?)\s*\d+[.:]?\s*", "", f.get("caption", ""))
    return re.split(r"(?<=[.!?])\s", d)[0][:110]


def _expand(plan, facts, world, days):
    """Turn the plan into concrete events. Each event: id, t, kind, actor, channel, requires (verbatim value strings),
    forbids (value strings that must NOT appear), refs, gist."""
    ev, ids = [], util.Ids("ev")
    R = plan["roles"]; C = plan["channels"]
    people = {p["id"]: p for p in world["people"]}
    tables = {t["id"]: t for t in facts["tables"]}
    figs = {f["id"]: f for f in facts["figures"]}
    look = {f["id"]: f for f in facts["metric_facts"]}
    inv = {x["id"]: x for x in plan["invented"]}
    errors = plan["errors"]
    err_by_kind = {e["kind"]: e for e in errors}
    fact_run, _ = fact_schedule(plan["runs"], plan["row_facts"])
    runs = {r["run_id"]: r for r in plan["runs"]}

    def add(t, kind, actor, channel, gist, requires=(), forbids=(), refs=None, **extra):
        e = {"id": ids.next(), "t": t, "kind": kind, "actor": actor, "channel": channel, "gist": gist, "requires": list(requires),
             "forbids": list(forbids), "refs": refs or {}}
        e.update(extra); ev.append(e); return e

    # day 0: kickoff
    def _short_owns(t):  # the kickoff names each owner's first responsibility; the full list lives in the world, not in one chat message
        t = re.split(r"[;,.]", t.strip())[0].strip()
        return t if len(t) <= 70 else t[:67].rsplit(" ", 1)[0] + "…"
    owners = "; ".join(f"{p['id']} — {_short_owns(p['owns'])}" for p in world["people"] if p["id"] != world["late_joiner"] and p["level"] != "exec")
    add(ts(days, 0, "09:02"), "announce", R["exec"], "general", f"Two dates this week: {world['milestone_a']} on {util.dayname(days[6]+'T00:00')} {days[6]}, and {world['milestone_b']} on {util.dayname(days[7]+'T00:00')} {days[7]}.")
    add(ts(days, 0, "09:30"), "kickoff", R["lead"], C["proj"], f"Kickoff for {world['project_codename']}: owners — {owners}. {V['tracker_role'][0].upper() + V['tracker_role'][1:]}.", refs={"decision": None})
    # config freeze decision(s) on day 0/1 → decisions are emitted below by their own times

    # runs
    progressive_tables = {run["table_id"] for run in plan["runs"] if run["kind"] in {"progression", "release"}}
    e_typo, e_cell = err_by_kind.get("tracker_typo"), err_by_kind.get("image_cell")
    for r in plan["runs"]:
        t = ts(days, r["day_offset"], r["time"])
        rf = plan["row_facts"][r["run_id"]]
        values = [x["value"] for x in rf if re.fullmatch(r"-?\d[\d,]*\.?\d*", x["value"] or "")]
        if r["kind"] == "baseline":
            add(t, "tracker_update", r["owner"], None, f"{r['run_id']} baseline row recorded in the {V['tracker_short']}", refs={"run": r["run_id"]}, values={x["fact_id"]: x["value"] for x in rf})
            continue
        if r["kind"] == "analysis":
            _nm = f"{r['run_id']} ({r['label']})" if V.get("ids_in_text", True) else r["label"]
            add(t, "run_report", r["owner"], C["exp"], f"{_nm} analysis result on {r['dataset']}: " + ", ".join(f"{x['header']} {x['value']}" for x in rf), requires=values, refs={"run": r["run_id"], "facts": [x["fact_id"] for x in rf]})
            add(t, "tracker_update", r["owner"], None, f"{r['run_id']} row", refs={"run": r["run_id"]}, values={x["fact_id"]: x["value"] for x in rf})
            continue
        # progression / release
        prev_runs = [q for q in plan["runs"] if q["table_id"] == r["table_id"] and q["kind"] in ("progression", "release") and (q["day_offset"], q["time"]) < (r["day_offset"], r["time"])]
        if prev_runs:
            launch_t = util.add_min(ts(days, prev_runs[-1]["day_offset"], prev_runs[-1]["time"]), 25)
            src = next((look[x["fact_id"]].get("source") for x in rf if look.get(x["fact_id"], {}).get("source")), r["dataset"] or "the source")
            add(launch_t, "run_launch", r["owner"], C["exp"], V.get("launch_gist", "{start} {rid} ({label}), ETA {day} {time}").format(start=V["item_start"], rid=r["run_id"], label=r["label"], day=util.dayname(t), time=r["time"], source=src, dataset=r["dataset"]), refs={"run": r["run_id"]})
        req = list(values)
        src = next((look[x["fact_id"]].get("source") for x in rf if look.get(x["fact_id"], {}).get("source")), r["dataset"] or "the source")
        rep = add(t, "run_report", r["owner"], C["exp"], V.get("report_gist", "{rid} ({label}, {dataset}) {done}: {values}").format(rid=r["run_id"], label=r["label"], dataset=r["dataset"], done=V["item_done"], source=src, values=", ".join(f"{x['header']} {x['value']}" for x in rf)) + (f". This is the {V['release']}." if r["kind"] == "release" else ""),
                  requires=req, refs={"run": r["run_id"], "facts": [x["fact_id"] for x in rf]})
        tv = {x["fact_id"]: x["value"] for x in rf}
        if e_typo and fact_run.get(e_typo["fact_id"]) == r["run_id"]:
            add(util.add_min(t, 2), "share_log", r["owner"], C["exp"], f"{V['log']} for {r['run_id'] if V.get('ids_in_text', True) else 'the ' + r['label'] + ' figure'} attached, for anyone who wants to check the decimals", refs={"run": r["run_id"], "doc": f"log_{r['run_id']}"}, doc=f"log_{r['run_id']}")
            tv = dict(tv); tv[e_typo["fact_id"]] = e_typo["wrong_value"]
            add(t, "tracker_update", r["owner"], None, f"{r['run_id']} row (with a typo: {look[e_typo['fact_id']]['column']} entered as {e_typo['wrong_value']})", refs={"run": r["run_id"], "error": e_typo["id"]}, values=tv, note=f"typo: {look[e_typo['fact_id']]['column']} entered as {e_typo['wrong_value']}")
        else:
            add(t, "tracker_update", r["owner"], None, f"{r['run_id']} row", refs={"run": r["run_id"]}, values=tv)
        # progression tables: attach a draft image of the table so far (with the image_cell error if scheduled)
        if r["table_id"] in progressive_tables:
            rows_done = list(dict.fromkeys(q["row_label"] for q in plan["runs"] if q["table_id"] == r["table_id"] and q["kind"] in ("baseline", "progression", "release") and (q["day_offset"], q["time"]) <= (r["day_offset"], r["time"])))
            wrong = None
            if e_cell and fact_run.get(e_cell["fact_id"]) in [q["run_id"] for q in plan["runs"] if q["row_label"] in rows_done and q["table_id"] == r["table_id"]] and ts(days, e_cell["day_offset_caught"], e_cell["time_caught"]) > t:
                wrong = e_cell
            img_id = f"{r['table_id']}_draft_{r['run_id']}"
            rep["file"] = img_id
            rep["image"] = {"id": img_id, "table": r["table_id"], "rows": rows_done, "wrong": {"fact_id": wrong["fact_id"], "value": wrong["wrong_value"]} if wrong else None,
                            "final": r["kind"] == "release"}
            if wrong:
                rep["image_error"] = wrong["id"]; rep["refs"]["error"] = wrong["id"]
    # invented facts
    e_chan = err_by_kind.get("channel_value")
    for x in plan["invented"]:
        t = ts(days, x["day_offset"], x["time"])
        if e_chan and e_chan["fact_id"] == x["id"]:
            add(t, "invented_post", x["owner"], x["channel"], f"{x['name']}: {e_chan['wrong_value']} (WRONG — will be corrected)", requires=[e_chan["wrong_value"]], forbids=[x["value"]], refs={"invented": x["id"], "error": e_chan["id"]}, wrong=True)
        else:
            add(t, "invented_post", x["owner"], x["channel"], f"{x['name']}: {x['value']} — {x['rationale']}", requires=[x["value"]], refs={"invented": x["id"]})
    # evolving facts: each version is a post; corrections mention the previous value
    for y in plan.get("evolving", []):
        prev = None
        for i, ver in enumerate(y["versions"]):
            t = ts(days, ver["day_offset"], ver["time"])
            shown = people[ver["value"]]["name"].split()[0] if y["kind"] == "owner" and ver["value"] in people else _date_shown(ver["value"], days)
            req = [shown]
            if ver["how"] == "correction" and prev:
                gist = f"corrects their earlier statement about {y['name']}: it is {shown}, not {prev}. ({ver['note']})"; req.append(prev)
            elif ver["how"] == "reassignment":
                gist = f"{y['name']} is reassigned: now {shown}" + (f" (was {prev})" if prev else "") + f". {ver['note']}"
            elif ver["how"] == "update":
                gist = f"update on {y['name']}: now {shown}" + (f" (was {prev})" if prev else "") + f". {ver['note']}"
            else:
                gist = f"states {y['name']}: {shown}. {ver['note']}"
            add(t, "evolve_post", ver["by"], ver["channel"], gist, requires=req, refs={"evolving": y["id"], "version": i}, value=ver["value"], shown=shown)
            prev = shown
    # document arcs: v1 → review (channel or DM) → ack → v2
    for a in plan.get("arcs", []):
        t1, tr, t2 = ts(days, a["day_offset_v1"], a["time_v1"]), ts(days, a["day_offset_review"], a["time_review"]), ts(days, a["day_offset_v2"], a["time_v2"])
        add(t1, "doc_share", a["author"], a["channel"], f"posts '{a['title']}' v1 ({a['kind']}): {a['note']}", refs={"doc": f"arc_{a['id']}_v1", "arc": a["id"]}, doc=f"arc_{a['id']}_v1")
        if a["review_kind"] == "mismatch":
            f = look[a["wrong_fact_id"]]
            add(tr, "arc_review", a["reviewer"], a["review_channel"], f"points out that in '{a['title']}' the {f['column']} for {f['row']} reads {a['wrong_value']} but the tracker says {f['value']}", requires=[a["wrong_value"], f["value"]], refs={"arc": a["id"], "fact": a["wrong_fact_id"]})
            add(util.add_min(tr, 7), "arc_ack", a["author"], a["review_channel"], "oh right — will fix and re-upload", refs={"arc": a["id"]})
            add(t2, "doc_share", a["author"], a["channel"], f"re-uploads '{a['title']}' v2 with the {f['column']} value corrected to {f['value']}", requires=[f["value"]], refs={"doc": f"arc_{a['id']}_v2", "arc": a["id"], "fact": a["wrong_fact_id"]}, doc=f"arc_{a['id']}_v2")
        elif a["review_kind"] == "add_figure":
            fig = figs[a["figure_id"]]
            add(tr, "arc_review", a["reviewer"], a["review_channel"], f"asks whether the figure showing {fig['description'][:80]} could be added to '{a['title']}'", refs={"arc": a["id"], "figure": a["figure_id"]})
            add(util.add_min(tr, 6), "arc_ack", a["author"], a["review_channel"], "good idea, adding it", refs={"arc": a["id"]})
            add(t2, "doc_share", a["author"], a["channel"], f"posts '{a['title']}' v2 with the figure added", refs={"doc": f"arc_{a['id']}_v2", "arc": a["id"], "figure": a["figure_id"]}, doc=f"arc_{a['id']}_v2")
        else:
            add(tr, "arc_review", a["reviewer"], a["review_channel"], f"asks for a rewording of the opening of '{a['title']}' (too internal / unclear)", refs={"arc": a["id"]})
            add(util.add_min(tr, 6), "arc_ack", a["author"], a["review_channel"], "will reword and repost", refs={"arc": a["id"]})
            add(t2, "doc_share", a["author"], a["channel"], f"posts '{a['title']}' v2, reworded", refs={"doc": f"arc_{a['id']}_v2", "arc": a["id"]}, doc=f"arc_{a['id']}_v2")
    # figures shared by the intern (spread over days 1..6, before docs) — image-only facts are forbidden in text
    fig_list = list(facts["figures"])
    for i, f in enumerate(fig_list[:8]):
        d, hh = FIG_SLOTS[i]
        add(ts(days, d, hh), "share_figure", R["intern"], C["proj"], f"draft of the figure showing {_fig_desc(f)}", forbids=[v["value"] for v in f["image_only_facts"] if len(v["value"]) <= 12], refs={"figure": f["id"]}, file=f["id"])
    # errors: caught / fixed events
    for e in errors:
        tc, tf = ts(days, e["day_offset_caught"], e["time_caught"]), ts(days, e["day_offset_fixed"], e["time_fixed"])
        f = look.get(e["fact_id"]) or inv.get(e["fact_id"]) or {}
        true = f.get("value", "")
        ch_public = {"tracker_typo": C["exp"], "image_cell": C["exp"], "channel_value": inv.get(e["fact_id"], {}).get("channel", C["infra"]),
                     "doc_value": C["proj"], "doc_headline": C["leadership"] or C["product"], "unfixed_inconsistency": C["proj"]}[e["kind"]]
        ch = _dm(world, e["actor"], e["caught_by"]) if e["caught_in"] == "dm" else ch_public
        label = f.get("column") or f.get("name") or (f"the {f['row']} figure" if f.get("row") else "the value")
        if e["kind"] == "unfixed_inconsistency":
            fig_txt = _fig_desc(figs[f["figure"]]) if f.get("figure") in figs else "the shared figure"
            add(tc, "error_caught", e["caught_by"], ch, f"flags an inconsistency inside the image showing {fig_txt}: {f.get('statement')}", requires=[true] if len(true) <= 12 else [], refs={"error": e["id"], "figure": f.get("figure")})
            continue
        if e["kind"] == "doc_headline":
            add(tc, "error_caught", e["caught_by"], ch, f"questions whether the {V['customer']} {V['deck_short']} may show the single headline number {e['wrong_value']} ({label}{ctx_of(f)}); asks for a decision", requires=[e["wrong_value"]], refs={"error": e["id"], "fact": e["fact_id"]})
            continue
        posted_by = people[e["actor"]]["name"].split()[0]
        add(tc, "error_caught", e["caught_by"], ch, f"notices that {label}{ctx_of(f)} was given as {e['wrong_value']} by {posted_by}; the real value is {true}.", requires=[e["wrong_value"], true], refs={"error": e["id"], "fact": e["fact_id"]})
        if e["kind"] == "tracker_typo":
            add(util.add_min(tc, 10), "error_ack", e["actor"], ch, "acknowledges the slip and says it will be fixed", refs={"error": e["id"]})
            add(tf, "correction", e["actor"], C["exp"], f"{V['tracker_short']} row corrected: {label} = {true}", requires=[true], refs={"error": e["id"], "fact": e["fact_id"]})
            add(tf, "tracker_fix", e["actor"], None, f"{label} corrected to {true}", refs={"error": e["id"], "fact": e["fact_id"], "run": fact_run.get(e["fact_id"])}, values={e["fact_id"]: true}, note=f"{label} corrected {e['wrong_value']} → {true}")
        elif e["kind"] == "image_cell":
            if e["caught_in"] == "dm" and e["actor"] != e["caught_by"]:
                add(util.add_min(tc, 6), "error_ack", e["actor"], ch, "will repost a fixed image in the channel", refs={"error": e["id"]})
            rid = fact_run.get(e["fact_id"]); r = runs[rid]
            rows_done = list(dict.fromkeys(q["row_label"] for q in plan["runs"] if q["table_id"] == r["table_id"] and q["kind"] in ("baseline", "progression", "release") and ts(days, q["day_offset"], q["time"]) <= tf))
            add(tf, "correction", e["actor"], C["exp"], f"reposts the draft table image with the {label} cell fixed ({true}, not {e['wrong_value']})", requires=[true, e["wrong_value"]], refs={"error": e["id"], "fact": e["fact_id"]}, file=f"{r['table_id']}_fixed", image={"id": f"{r['table_id']}_fixed", "table": r["table_id"], "rows": rows_done, "wrong": None, "final": False})
        elif e["kind"] == "channel_value":
            x = inv[e["fact_id"]]
            add(tf, "correction", e["actor"], x["channel"], f"corrects the earlier post: {x['name']} = {true}, not {e['wrong_value']}", requires=[true, e["wrong_value"]], refs={"error": e["id"], "invented": x["id"]}, file=f"chart_{x['id']}")
        elif e["kind"] == "doc_value":
            add(util.add_min(tc, 5), "error_ack", e["actor"], ch, "acknowledges; the fix goes into report v2 with a changelog line, without announcing the slip in the channel", refs={"error": e["id"]})
        elif e["kind"] == "doc_headline":
            pass  # resolved by the private decision + deck v2
    # decisions
    for d in plan["decisions"]:
        t = ts(days, d["day_offset"], d["time"])
        if d["rationale_visibility"] == "private":
            pc = d["private_channel"]
            members = [m for m in world["channels"] if m["id"] == pc][0]["members"]
            others = [m for m in members if m != d["by"]]
            add(util.add_min(t, -14), "rationale_q", others[0] if others else d["by"], pc, f"raises the question behind {d['id']}: {d['title']}", refs={"decision": d["id"]})
            add(util.add_min(t, -8), "rationale", d["by"], pc, f"explains the reasoning: {d['rationale']}", refs={"decision": d["id"]}, private=True)
            if len(others) > 1:
                add(util.add_min(t, -4), "rationale_agree", others[1], pc, "agrees and asks to log only the decision, not the reasoning", refs={"decision": d["id"]})
            add(t, "decision", d["by"], pc, f"{d['id']} logged: {d['title']}", refs={"decision": d["id"]})
        else:
            ch = C["exp"] if d["day_offset"] >= 1 else C["proj"]
            members = [m for m in next(c for c in world["channels"] if c["id"] == ch)["members"] if m != d["by"] and people[m]["joined"] <= t[:10]]
            k = int(d["id"].split("-")[-1]) if d["id"].split("-")[-1].isdigit() else 0
            if members: members = members[k % len(members):] + members[:k % len(members)]  # a different colleague asks each time
            nums = [n for n in util.num_strings(d["rationale"]) if len(n) >= 3 or "." in n][:4]
            asks = ["asks, before it is logged, whether '{t}' is the right call and what the reasoning is", "pushes back a little on '{t}': is this really the best option, and why?", "wants the reasoning behind '{t}' spelled out before it goes into the decision log"][k % 3].format(t=d["title"])
            add(util.add_min(t, -16), "rationale_q", members[0] if members else d["by"], ch, asks, refs={"decision": d["id"]})
            add(util.add_min(t, -9), "rationale", d["by"], ch, f"argues for it in their own words: {d['rationale']}", requires=nums, refs={"decision": d["id"]})
            if len(members) > 1:
                add(util.add_min(t, -4), "rationale_agree", members[1], ch, ["agrees briefly (a short 'makes sense, go ahead')", "agrees after a one-line check of the numbers", "says fine by them, log it"][k % 3], refs={"decision": d["id"]})
            add(t, "decision", d["by"], ch, f"{d['id']}: {d['title']} — {d['body']} (logs it; the reasoning was just discussed above, do not restate it)", refs={"decision": d["id"]})
        add(t, "notion_decision", d["by"], None, d["title"], refs={"decision": d["id"]})
    # documents
    DS = plan["doc_schedule"]
    e_doc, e_head = err_by_kind.get("doc_value"), err_by_kind.get("doc_headline")
    add(ts(days, *DS["report_v1"]), "doc_share", R["report_author"], C["proj"], f"{V['deliverable_short']} DRAFT v1 attached — {V['items']} so far, the rest lands tomorrow; asks for a read of the first pages", refs={"doc": "report_v1", "error": e_doc["id"] if e_doc else None}, doc="report_v1")
    add(ts(days, *DS["report_v2"]), "doc_share", R["report_author"], C["proj"], f"{V['deliverable_short']} v2: changelog — summary corrected, all {V['items']}, analysis added", refs={"doc": "report_v2", "error": e_doc["id"] if e_doc else None}, doc="report_v2")
    add(ts(days, *DS["deck_v1"]), "doc_share", R["deck_author"], C["product"], f"{V['customer']} {V['deck_short']} v1 attached — asks the channel to tear it apart before the review", refs={"doc": "deck_v1", "error": e_head["id"] if e_head else None}, doc="deck_v1")
    add(ts(days, *DS["deck_v2"]), "doc_share", R["deck_author"], C["product"], f"{V['customer']} {V['deck_short']} v2: headline slide replaced per the decision", refs={"doc": "deck_v2", "error": e_head["id"] if e_head else None}, doc="deck_v2")
    if e_head:
        dm = _dm(world, R["lead"], R["pm"]) or C["product"]
        dec = next((d for d in plan["decisions"] if d.get("related_error_id") == e_head["id"]), None)
        tdec = ts(days, dec["day_offset"], dec["time"]) if dec else ts(days, 6, "15:14")
        add(util.add_min(tdec, 6), "error_caught", R["lead"], dm, f"tells the deck author the headline slide with {e_head['wrong_value']} has to go, per {dec['id'] if dec else 'the decision'}; replace it with the per-dataset table", requires=[e_head["wrong_value"]], refs={"error": e_head["id"], "decision": dec["id"] if dec else None})
        add(util.add_min(tdec, 11), "error_ack", R["pm"], dm, "agrees, v2 by the end of the afternoon", refs={"error": e_head["id"]})
    # freeze + meetings + emails + join
    add(ts(days, 6, "20:55"), "freeze", R["lead"], C["proj"], f"{world['milestone_a']} done; report v2 is the document of record. Thanks everyone.", refs={"doc": "report_v2"})
    for d, hh, title, kind in MEETINGS:
        att = [m for m in [R["lead"], R["exec"], R["pm"]] if m] if kind == "private" else [p["id"] for p in world["people"] if p["level"] != "exec" and p["id"] != world["late_joiner"]]
        add(ts(days, d, hh), "meeting", R["lead"], None, title, refs={"meeting": title}, attendees=att, private=(kind == "private"))
    for m in plan["emails"]:
        add(ts(days, m["day_offset"], m["time"]), "email", m["from_id"], None, m["purpose"], requires=m["required_values"], refs={"email": m["id"]}, email=m)
    lj = world["late_joiner"]
    add(ts(days, 8, "09:05"), "join", lj, "general", "first day, reading the project channel history to catch up")
    add(ts(days, 8, "09:10"), "welcome", R["lead"], "general", f"welcome; start with the Notion {V['tracker_short']} and decision log, then the report v2 pinned in the project channel")
    # the newcomer asks about the most confusing thing: the typo / trade-off; the actor of the typo answers
    if e_typo:
        f = look[e_typo["fact_id"]]
        add(ts(days, 8, "10:30"), "newcomer_q", lj, C["proj"], f"asks whether {f['column']} for {f['row']} in the report is a typo (it looks inconsistent with a nearby row)", refs={"error": e_typo["id"]})
        add(ts(days, 8, "10:35"), "newcomer_a", e_typo["actor"], C["proj"], f"explains it is not a typo in the report; the actual typo was in the {V['tracker_short']} ({e_typo['wrong_value']}) and was fixed on {util.dayname(ts(days, e_typo['day_offset_fixed'], e_typo['time_fixed']))}", requires=[e_typo["wrong_value"]], refs={"error": e_typo["id"]})
    return ev



def _repair_chatter(dc, day_ev, channels, present):
    """Deterministic repairs before validation (the model gets these wrong repeatedly, and each is mechanical):
    drop events by absent people / non-members, move a reply into the channel of the event it answers, push a reply
    after its event, drop answers that do not point at a question, keep exactly one answer per question."""
    by_id = {e["id"]: e for e in day_ev}
    keep, remap = [], {}
    for i, c in enumerate(dc.events):
        if c.actor not in present or c.channel not in channels: continue
        if c.about != "general":
            ref = by_id.get(c.about)
            if ref is None: continue
            if ref["channel"] != c.channel and not c.channel.startswith("dm-") and c.actor in channels[ref["channel"]]["members"]: c.channel = ref["channel"]
            if c.time < ref["t"][11:]: c.time = util.add_min(ref["t"], 3)[11:]
        if c.actor not in channels[c.channel]["members"]: continue
        remap[i] = len(keep); keep.append(c)
    for c in keep:
        if c.kind == "answer": c.answers = remap.get(c.answers, -1)
    out = []
    for c in keep:
        if c.kind == "answer":
            if not (0 <= c.answers < len(keep)) or keep[c.answers].kind != "question": continue
            q = keep[c.answers]
            if c.channel != q.channel: c.channel = q.channel
            if c.actor == q.actor or c.actor not in channels[c.channel]["members"]: continue
            if c.time <= q.time: c.time = util.add_min(f"2000-01-01T{q.time}", 4)[11:]
        out.append(c)
    final, remap2 = [], {}
    for i, c in enumerate(keep):
        if c not in out: continue
        remap2[i] = len(final); final.append(c)
    seen_q = set()
    result = []
    for c in final:
        if c.kind == "answer":
            c.answers = remap2.get(c.answers, -1)
            if c.answers < 0 or c.answers in seen_q: continue
            seen_q.add(c.answers)
        result.append(c)
    answered = {c.answers for c in result if c.kind == "answer"}
    result2 = []
    idx = {id(c): i for i, c in enumerate(final)}
    for c in result:
        if c.kind == "question" and idx[id(c)] not in answered: continue
        result2.append(c)
    remap3 = {idx[id(c)]: k for k, c in enumerate(result2)}
    for c in result2:
        if c.kind == "answer": c.answers = remap3.get(c.answers, c.answers)
    dc.events = result2


def _chatter(llm, plan, world, days, events, base_vars):
    """LLM adds reactive chatter (questions, acks, short comments) per day, tied to skeleton events."""
    if config.SETTINGS["chatter_per_day"] == 0:
        return list(events)
    people = {p["id"]: p for p in world["people"]}
    channels = {c["id"]: c for c in world["channels"]}
    out = list(events); ids = util.Ids("ch")
    for di, day in enumerate(days):
        day_ev = [e for e in events if e["t"][:10] == day and e["channel"]]
        if not day_ev:
            continue
        digest = "\n".join(f"- {e['id']} {e['t'][11:]} #{e['channel']} {e['actor']} [{e['kind']}]: {e['gist'][:160]}" for e in day_ev)
        allowed_numbers = {n for e in day_ev for n in e["requires"]} | {n for e in day_ev for n in util.num_strings(e["gist"])} | {n for e in day_ev for k in ("run", "decision", "error") if e["refs"].get(k) for n in util.num_strings(str(e["refs"][k]))}
        present = [p["id"] for p in world["people"] if p["joined"] <= day]

        def validate(dc: DayChatter):
            errs = []
            _repair_chatter(dc, day_ev, channels, present)
            if len(dc.events) > config.SETTINGS["chatter_per_day"]: errs.append(f"at most {config.SETTINGS['chatter_per_day']} events")
            for c in dc.events:
                if c.channel not in channels: errs.append(f"unknown channel {c.channel}"); continue
                if c.actor not in channels[c.channel]["members"]: errs.append(f"{c.actor} is not a member of {c.channel}")
                if c.actor not in present: errs.append(f"{c.actor} is not at the company on {day}")
                if c.about != "general":
                    ref = next((e for e in day_ev if e["id"] == c.about), None)
                    if not ref: errs.append(f"about={c.about} is not an event id of this day")
                    elif ref["channel"] != c.channel and not c.channel.startswith("dm-"): errs.append(f"{c.about} happened in {ref['channel']}, reply there (or discuss it in a DM)")
                    elif c.time < ref["t"][11:]: errs.append(f"reply to {c.about} must be after {ref['t'][11:]}")
                for n in util.num_strings(c.gist):
                    if not any(util.contains_value(a, n) for a in allowed_numbers) and n not in ("1", "2", "3"):
                        errs.append(f"gist introduces a number not in the day's events: {n}")
            # every question gets exactly one answer, from someone else in the same channel, later the same day
            for qi, c in enumerate(dc.events):
                if c.kind != "question": continue
                ans = [x for x in dc.events if x.kind == "answer" and x.answers == qi]
                if len(ans) != 1: errs.append(f"question #{qi} ('{c.gist[:50]}') needs exactly one 'answer' event with answers={qi}")
                for x in ans:
                    if x.channel != c.channel: errs.append(f"answer to #{qi} must be in {c.channel}")
                    if x.actor == c.actor: errs.append(f"answer to #{qi} must come from someone other than {c.actor}")
                    if x.time <= c.time: errs.append(f"answer to #{qi} must be after {c.time}")
            for x in dc.events:
                if x.kind == "answer" and not (0 <= x.answers < len(dc.events) and dc.events[x.answers].kind == "question"):
                    errs.append("an 'answer' event must point (answers=index) at a question event in this list")
            return errs
        try:
            dc = llm.json("03_timeline/chatter", dict(base_vars, day=f"day {di}: {day} ({util.dayname(day + 'T00:00')})", events=digest,
                                                       present=", ".join(present)), DayChatter, tag=f"chatter_{di}", validator=validate, effort="low")
        except RuntimeError:
            dc = llm.json("03_timeline/chatter", dict(base_vars, day=f"day {di}: {day} ({util.dayname(day + 'T00:00')})", events=digest,
                                                       present=", ".join(present)), DayChatter, tag=f"chatter_{di}_soft", effort="low", salt="soft")
            keep = []
            for c in dc.events:
                if c.kind == "answer": continue  # kept only together with its question below
                if validate(DayChatter(events=[c])) == []: keep.append(c)
            for qi, c in enumerate(dc.events):  # a question survives only with its answer, re-indexed
                if c.kind != "question" or c not in keep: continue
                ans = [x for x in dc.events if x.kind == "answer" and x.answers == qi]
                if ans and validate(DayChatter(events=[c, ans[0].model_copy(update={"answers": 0})])) == []:
                    keep.append(ans[0].model_copy(update={"answers": keep.index(c)}))
                else:
                    keep.remove(c)
            dc = DayChatter(events=keep)
            SOFT_DAYS.append({"day": day, "kept": len(keep)})
        for c in dc.events:
            out.append({"id": ids.next(), "t": f"{day}T{c.time}", "kind": "chatter", "actor": c.actor, "channel": c.channel, "gist": c.gist,
                        "requires": [], "forbids": [], "refs": {"about": c.about, "answers": c.answers if c.kind == "answer" else None}, "chatter_kind": c.kind})
    return out
