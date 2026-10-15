"""Stage 01 — canonical facts.

Deterministic: every table cell becomes a metric fact with its full 2-D context (table, row group, row, column).
LLM: text facts (method name, one-sentence description, configuration values, quantitative claims, pipeline steps)
and a role for every table (main results / progression-style ablation / comparison / other).
Verification: every number the LLM returns must occur verbatim in the document text; progression row labels must be
real row labels. Output: 01_facts/facts.json"""
from __future__ import annotations
import re
from .. import config, util
from ..llm import LLM
from ..schemas import TextFacts, TableRoles


def _doc_text(ingest) -> str:
    return "\n".join(p["text"] for p in ingest["pages"])


def _metric_facts(tables):
    facts, ids = [], util.Ids("F")
    out_tables = []
    for t in tables:
        tt = t["table"]
        rows = []
        _dedupe_labels(tt)
        for g in tt["row_groups"]:
            for r in g["rows"]:
                fids = []
                for h, cell in zip(tt["column_headers"], r["cells"]):
                    cell = (cell or "").strip()
                    if cell == "" or len(cell) > 60:  # a sentence in a cell (taxonomy / requirement tables) is prose, not a value the team tracks
                        fids.append(None); continue
                    fid = ids.next()
                    facts.append({"id": fid, "kind": "metric", "table": t["id"], "table_title": tt["title"],
                                  "group": g["group"], "row": r["label"], "column": h, "value": cell,
                                  "numeric": bool(re.fullmatch(r"-?[$£€]?\d[\d,]*\.?\d*\s*(%|[A-Za-z]{1,6})?", cell)), "page": t["page"],
                                  "source": (tt["title"] or f"table {t['id']}") + (f", p. {t['page']}" if t.get("page") else "")})
                    fids.append(fid)
                rows.append({"group": g["group"], "label": r["label"], "cells": r["cells"], "fact_ids": fids})
        out_tables.append({"id": t["id"], "title": tt["title"], "caption": t["caption"], "page": t["page"],
                           "headers": tt["column_headers"], "rows": rows, "path": t["path"], "notes": tt.get("notes", "")})
    return facts, out_tables


def _dedupe_labels(tt):
    """Rows of one table must have unique labels so later stages can reference them. Duplicates (e.g. three
    'Stage 1 + Stage 2' rows distinguished only by checkmark columns) get the non-numeric cells appended."""
    seen = {}
    for g in tt["row_groups"]:
        for r in g["rows"]:
            key = (g["group"], r["label"])
            seen[key] = seen.get(key, 0) + 1
    dup = {k for k, n in seen.items() if n > 1}
    counters = {}
    for g in tt["row_groups"]:
        for r in g["rows"]:
            key = (g["group"], r["label"])
            if key in dup:
                marks = [h for h, c in zip(tt["column_headers"], r["cells"]) if c and not re.fullmatch(r"-?\d[\d,]*\.?\d*", c.strip())]
                stem = r["label"].strip() or next((c.strip() for c in r["cells"] if c and not re.search(r"\d", c)), "row")
                base = f"{stem} [{', '.join(marks)}]" if marks else stem
                counters[base] = counters.get(base, 0) + 1
                r["label"] = base if counters[base] == 1 else f"{base} #{counters[base]}"


def table_markdown(t, with_ids=False) -> str:
    lines = [f"### {t['id']}: {t['title']}", "| group | row | " + " | ".join(t["headers"]) + " |"]
    for r in t["rows"]:
        cells = r["cells"]
        if with_ids:
            cells = [f"{c} [{f}]" if f else c for c, f in zip(cells, r["fact_ids"])]
        lines.append(f"| {r['group']} | {r['label']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def run(run: config.Run, llm: LLM):
    ingest = run.load("00_ingest", "ingest.json")
    text = _doc_text(ingest)
    metric_facts, tables = _metric_facts(ingest["tables"])

    def validate_text(tf: TextFacts):
        errs = []
        for c in tf.config_facts:
            for n in util.num_strings(c.value):
                if not util.contains_value(text, n):
                    errs.append(f"config value '{c.value}' ({c.name}) is not in the document text")
                    break
        for k in tf.claims:
            for n in k.numbers:
                if not util.contains_value(text, n):
                    errs.append(f"claim number '{n}' is not in the document text: {k.statement[:60]}")
        if len(tf.config_facts) < 5:
            errs.append("list at least 5 configuration facts")
        if len(tf.claims) < 6:
            errs.append("list at least 6 quantitative claims")
        return errs

    tf = llm.json("01_facts/text_facts", {"doc_text": text[:200000], "tables": "\n\n".join(table_markdown(t) for t in tables)},
                  TextFacts, tag="text_facts", validator=validate_text, effort="medium")

    labels = {t["id"]: [r["label"] for r in t["rows"]] for t in tables}

    def validate_roles(tr: TableRoles):
        errs = []
        seen = set()
        for r in tr.tables:
            if r.table_id not in labels:
                errs.append(f"unknown table_id {r.table_id}"); continue
            seen.add(r.table_id)
            for lab in r.progression_order:
                if lab not in labels[r.table_id]:
                    errs.append(f"{r.table_id}: progression label '{lab}' is not a row label; valid: {labels[r.table_id]}")
            if r.release_row and r.release_row not in labels[r.table_id]:
                errs.append(f"{r.table_id}: release_row '{r.release_row}' is not a row label")
            if r.role == "ablation_progression" and len(r.progression_order) < 2:
                errs.append(f"{r.table_id}: a progression needs at least 2 ordered rows")
        for tid in labels:
            if tid not in seen:
                errs.append(f"table {tid} is missing from the answer")
        return errs

    roles = llm.json("01_facts/table_roles", {"method_name": tf.method_name, "tables": "\n\n".join(table_markdown(t) for t in tables)},
                     TableRoles, tag="table_roles", validator=validate_roles) if tables else TableRoles(tables=[])
    role_by = {r.table_id: r.model_dump() for r in roles.tables}
    for t in tables:
        t.update(role_by.get(t["id"], {"role": "other", "progression_order": [], "release_row": "", "overlaps_with": "", "notes": ""}))

    # ids for config facts / claims / image-only facts
    cids, kids, vids = util.Ids("C"), util.Ids("K"), util.Ids("V")
    config_facts = [dict(c.model_dump(), id=cids.next()) for c in tf.config_facts]
    claims = [dict(k.model_dump(), id=kids.next()) for k in tf.claims]
    figures = []
    for f in ingest["figures"]:
        vf = f["visual"]
        figures.append({"id": f["id"], "caption": f["caption"], "page": f["page"], "path": f["path"], "description": vf["description"],
                        "image_only_facts": [dict(x, id=vids.next()) for x in vf["image_only_facts"]],
                        "text_labels": vf["text_labels"], "key_numbers": vf["key_numbers"]})
    if not any(f["numeric"] for f in metric_facts) and claims:
        # documents without numeric tables (strategies, frameworks): the quantitative claims become the week's items
        rows = []
        for k in claims:
            # the first number string that is a figure (digits, not a year or a date): claims often lead with a count word
            n0 = next((n for n in (k["numbers"] or []) if re.search(r"\d", n) and not re.fullmatch(r"(19|20)\d\d", n.strip()) and not re.search(r"(?i)\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", n)), "")
            st = k["statement"].strip()
            # document metadata (years, versions, edition dates) and number words are not figures the team verifies
            if not re.search(r"\d", n0) or re.fullmatch(r"(19|20)\d\d", n0.strip()) or re.search(r"\b(version|edition|published|revision|dated|draft)\b", st, re.I):
                continue
            # the row's name must not carry its own value: the model's value-free label, else the statement with numbers removed
            label = (k.get("label") or "").strip()
            if not label or re.search(r"\d", label) or util.contains_value(label, n0): label = util.value_free_label(st)
            if len(label) > 90: label = label[:90].rsplit(" ", 1)[0]
            rows.append({"group": "", "label": label, "cells": [n0], "fact_ids": [], "source": (k.get("source_hint") or "").strip() or "the document text"})
        if len(rows) >= 3:
            rows = rows[:12]
            for r, l in zip(rows, util.unique_labels([r["label"] for r in rows])): r["label"] = l
            fid = util.Ids("F", start=len(metric_facts) + 1)
            for r in rows:
                mf = {"id": fid.next(), "kind": "metric", "table": "claims", "table_title": "Key figures", "group": "", "row": r["label"], "column": "value",
                      "value": r["cells"][0], "numeric": True, "page": 0, "source": (r.get("source") or "the document text")}
                metric_facts.append(mf); r["fact_ids"] = [mf["id"]]
            tables.append({"id": "claims", "title": "Key figures stated in the text", "caption": "Key figures", "page": 0, "headers": ["value"], "rows": rows,
                           "role": "ablation_progression", "progression_order": [r["label"] for r in rows], "release_row": rows[-1]["label"], "overlaps_with": "", "notes": "synthesized from quantitative claims", "path": ""})
    usable = sum(f["numeric"] for f in metric_facts)
    if usable < 3:  # nothing the week's items could be built from: no numeric cells and no quantitative claims
        raise RuntimeError(f"seed unfit for a numeric-progression world: {usable} numeric table cells / key figures, {len(claims)} claims, {len(metric_facts)} short cells")
    facts = {"title": ingest["title"], "method_name": tf.method_name, "one_sentence": tf.one_sentence, "task_summary": tf.task_summary,
             "components": tf.components, "pipeline_steps": tf.pipeline_steps, "config_facts": config_facts, "claims": claims,
             "tables": tables, "metric_facts": metric_facts, "figures": figures}
    run.save("01_facts", "facts.json", facts)
    run.save("01_facts", "verify_report.json", {"n_metric_facts": len(metric_facts), "n_numeric": sum(f["numeric"] for f in metric_facts),
                                                "n_config": len(config_facts), "n_claims": len(claims), "n_figures": len(figures),
                                                "table_roles": {t["id"]: t["role"] for t in tables}})
    run.mark_done("01_facts")
    return facts
