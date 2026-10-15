"""Workspace vocabulary for research teams and analysis firms."""
from __future__ import annotations
import json
from . import config

ARCHETYPES: dict[str, dict] = {
    "research_lab": {
        "label": "research lab shipping a system",
        "org_frame": "a small engineering company whose research team is finishing a release built on a technical method; the method is simply 'the system we are shipping'",
        "author_frame": "the team produced these results this week",
        "item": "run", "items": "runs", "item_done": "finished", "item_start": "launching", "item_report": "run report", "item_kind_hint": "experiment run on a dataset",
        "tracker": "Experiment Tracker", "tracker_short": "tracker", "tracker_role": "the Notion tracker is the source of truth for numbers, not chat",
        "value": "metric", "values": "metrics", "table_word": "result table", "tables_word": "result tables",
        "release": "release configuration", "release_row_hint": "the final/best configuration of the system",
        "ordered_hint": "cumulative configurations that would naturally be run one after another (stage 1 → stage 1+2, budget 1 → 2 → 3)",
        "core_hint": "the headline comparison against baselines on several datasets/benchmarks",
        "baseline_hint": "zero-shot / off-the-shelf rows that exist from day 0 (recorded, not run this week)",
        "deliverable": "internal technical report", "deliverable_short": "report", "deliverable_kind": "technical report",
        "deck": "customer-facing slide deck for a pilot review", "deck_short": "deck",
        "log": "raw evaluator log", "log_desc": "Raw evaluator output; proves the true values when the tracker is wrong",
        "customer": "customer", "customer_hint": "a pilot customer with an SLA",
        "requirement_hint": "an end-to-end latency per turn/step with a customer SLA, an index build time, a cost per query, a throughput",
        "milestone_a_hint": "a release-candidate freeze plus an internal technical report", "milestone_b_hint": "a customer pilot review with a slide deck",
        "research_people": "research people", "senior_roles": "one must be an ML infrastructure engineer, one a senior researcher",
        "infra_keywords": ["infra", "platform", "systems"], "research_keywords": ["research", "scientist", "ml ", "engineer"],
        "owns_hint": "ablation runs, index/latency, figures, customer deck",
        "absent_kinds": "datasets, metrics or systems", "absent_examples": "a well-known related benchmark, a standard metric not reported, a competing system not compared",
        "claims_board": "Release Claims", "claims_board_desc": "Each claim in the deliverable must point at a verified tracker row or figure.",
        "experiments_channel": "an experiments channel (research people only, not the exec, not the PM)",
        "system_word": "system",
        "status_running": "running", "status_done": "done", "col_dataset": "dataset", "col_config": "config",
        "launch_gist": "{start} {rid} ({label}), ETA {day} {time}",
        "report_gist": "{rid} ({label}, {dataset}) {done}: {values}",
        "cite_sources": False, "id_prefix": "R", "ids_in_text": True,
        "id_in_text_rule": "Runs are referred to by id (R-03) and name in messages and notes.",
    },
    "analysis_firm": {
        "label": "analysis / policy research firm writing a report for a client",
        "org_frame": "a small research consultancy whose analysts are finishing a report for a client; the numbers in the source document are the findings the team verifies and writes up this week",
        "author_frame": "the team gathered and verified these figures this week from sources it consulted",
        "item": "finding", "items": "findings", "item_done": "cross-checked", "item_start": "pulling", "item_report": "finding post", "item_kind_hint": "a figure the analyst pulled from a source and cross-checked",
        "tracker": "Evidence Table", "tracker_short": "evidence table", "tracker_role": "the Notion evidence table is the source of truth for figures, not chat",
        "value": "figure", "values": "figures", "table_word": "data table", "tables_word": "data tables",
        "release": "published figure", "release_row_hint": "the row whose figures go into the final deliverable (the latest year, the headline scenario, the adopted option)",
        "ordered_hint": "rows in a natural sequence — years or waves of a survey, scenario steps, versions, phases — that the team would verify one after another",
        "core_hint": "the headline table of the document (the main comparison or summary figures)",
        "baseline_hint": "reference rows already known on day 0 (prior-year values, published baselines, the status quo)",
        "deliverable": "client report", "deliverable_short": "report", "deliverable_kind": "policy brief / analysis report",
        "deck": "client briefing deck", "deck_short": "deck",
        "log": "source note", "log_desc": "The analyst's source note listing where each figure was verified; proves the true values when the evidence table is wrong",
        "customer": "client", "customer_hint": "the client commissioning the report (a ministry, an agency, an NGO or a company)",
        "requirement_hint": "a turnaround time per client request, hours per dataset refresh, the number of sources cross-checked per figure, a cost per report section, a coverage percentage",
        "milestone_a_hint": "a content freeze plus the internal draft report", "milestone_b_hint": "a client briefing with a slide deck",
        "research_people": "analysts", "senior_roles": "one must be a data/tooling engineer who runs the pipelines, one a senior analyst",
        "infra_keywords": ["data", "tooling", "engineer", "operations", "platform"], "research_keywords": ["analyst", "research", "economist", "policy"],
        "owns_hint": "a chapter's figures, the evidence table, charts, the client deck, source checks",
        "absent_kinds": "indicators, sources or comparators", "absent_examples": "a related indicator not reported, a well-known source not consulted, a comparison country/sector/year not covered",
        "claims_board": "Report Claims", "claims_board_desc": "Each claim in the deliverable must point at a verified evidence-table row or figure.",
        "experiments_channel": "an analysis channel where findings are posted (analysts only, not the exec, not the PM)",
        "system_word": "report",
        "status_running": "extracted", "status_done": "cross-checked", "col_dataset": "source", "col_config": "figure",
        "launch_gist": "{start} the {label} figure from {source}; cross-check due {day} {time}",
        "report_gist": "{label} {done} against {source}: {values}",
        "cite_sources": True, "id_prefix": "F", "ids_in_text": False,
        "id_in_text_rule": "In messages, notes, emails and questions people refer to a finding by its name (\"the governance gap figure\"), never by its id — the id is a key that appears only in the evidence table.",
    },
}
DEFAULT = "research_lab"


def load(run: config.Run) -> dict:
    """Vocabulary for a run: from 02_world/archetype.json if the archetype step ran, else research_lab (old runs)."""
    p = run.dir / "02_world" / "archetype.json"
    a = DEFAULT
    if p.exists():
        try: a = json.loads(p.read_text()).get("archetype", DEFAULT)
        except Exception: a = DEFAULT
    v = dict(ARCHETYPES.get(a, ARCHETYPES[DEFAULT])); v["archetype"] = a
    if not v.get("ids_in_text", True):  # the tracker key follows the company's own convention: its initials
        w = run.dir / "02_world" / "world.json"
        try:
            name = json.loads(w.read_text()).get("company_name", "") if w.exists() else ""
            initials = "".join(x[0] for x in name.replace("-", " ").split() if x[0].isalpha())[:3].upper()
            if len(initials) >= 2: v["id_prefix"] = initials
        except Exception: pass
    return v


def prompt_vars(v: dict) -> dict:
    return {f"V_{k}": (", ".join(x) if isinstance(x, list) else x) for k, x in v.items()}
