"""Stage 04 — in-world assets.

Deterministic: figure crops become company figures; every table is rendered as an image; draft tables (with the
scripted wrong cell) are rendered from the event script; the evaluator log is written from the run's facts.
LLM: chart specs (rendered with matplotlib) and document specs — tech report v1/v2 and customer deck v1/v2 — which
are rendered by the flow-layout engine. Validation: every referenced asset exists and was available when the
document was written; v1 documents carry exactly the scripted error and v2 documents do not.
Output: 04_assets/assets.json, img/, docs/"""
from __future__ import annotations
import re, shutil
from pathlib import Path
from .. import config, util, archetype
V = archetype.ARCHETYPES[archetype.DEFAULT]
from ..llm import LLM
from ..schemas import ChartSpecs, DocSpec, ImageNames, DiagramSpec
from ..render.images import render_table, render_chart, render_diagram
from ..render.docs import render_doc
from .s03_timeline import ts


def _rows_of(table, labels):
    return [r for r in table["rows"] if r["label"] in labels] if labels else table["rows"]


def run(run: config.Run, llm: LLM):
    global V
    V = archetype.load(run)
    facts = run.load("01_facts", "facts.json"); world = run.load("02_world", "world.json")
    plan = run.load("03_timeline", "plan.json"); events = run.load("03_timeline", "events.json")
    days = world["days"]
    out = run.stage_dir("04_assets"); img = out / "img"; docd = out / "docs"; img.mkdir(exist_ok=True); docd.mkdir(exist_ok=True)
    tables = {t["id"]: t for t in facts["tables"]}
    look = {f["id"]: f for f in facts["metric_facts"]}
    inv = {x["id"]: x for x in plan["invented"]}
    errors = {e["kind"]: e for e in plan["errors"]}
    assets, images, docs = {}, [], []

    # ---- figures (named by content, never by publication numbering)
    def v_names(n: ImageNames):
        errs = []
        got = {x.id: x.name for x in n.names}
        for f in facts["figures"]:
            nm = got.get(f["id"], "")
            if not nm: errs.append(f"missing name for {f['id']}")
            elif not re.fullmatch(r"[a-z0-9_]{4,40}\.(png|jpg)", nm): errs.append(f"{f['id']}: '{nm}' must be lowercase_with_underscores.png, ≤ 40 chars")
            elif re.search(r"(fig|figure|table|tab)_?\d", nm): errs.append(f"{f['id']}: '{nm}' must not use publication numbering")
        for t in facts["tables"]:
            nm = got.get("table_" + t["id"], ""); ti = next((x.title for x in n.names if x.id == "table_" + t["id"]), "")
            if not nm or not ti: errs.append(f"missing name/title for table_{t['id']}")
            elif re.search(r"(fig|figure|table|tab)_?\d", nm) or not re.fullmatch(r"[a-z0-9_]{4,40}\.png", nm): errs.append(f"table_{t['id']}: name must be lowercase_with_underscores.png without publication numbering")
            if ti and (re.search(r"\btable\s*\d|\bfig(?:ure)?\.?\s*\d", ti, re.I) or re.search(r"\bour\b|\bwe\b|\bproposed\b", ti, re.I)): errs.append(f"table_{t['id']}: title must be an in-world title (no 'Table N', no 'our/we')")
        if len(set(got.values())) != len(got): errs.append("names must be unique")
        return errs
    names = llm.json("04_assets/names", {"images": "\n".join(f"- {f['id']}: {f['description'][:140]} (.png)" for f in facts["figures"]) + "\n" +
                                          "\n".join(f"- table_{t['id']}: TABLE — {t['title'][:140]}; columns {', '.join(t['headers'][:6])} (.png)" for t in facts["tables"])},
                     ImageNames, tag="names", validator=v_names, effort="low")
    name_of = {x.id: x.name for x in names.names}
    title_of = {x.id: (x.title or "") for x in names.names}
    for t in facts["tables"]:  # in-world titles replace the seed captions everywhere tables are drawn
        t["title"] = title_of.get("table_" + t["id"]) or f"{world['internal_model_name']} results"
    for f in facts["figures"]:
        name = name_of.get(f["id"]) or f"{util.slug(' '.join(f['description'].split()[:4]))}.png"
        p = img / name; shutil.copy(f["path"], p); assets[f["id"]] = str(p)
        images.append({"id": f["id"], "name": name, "kind": "figure", "path": str(p), "desc": f["description"],
                       "image_only": [v["value"] for v in f["image_only_facts"]]})
    # ---- final tables
    for t in facts["tables"]:
        tid = f"table_{t['id']}"
        rel = [i for i, r in enumerate(t["rows"]) if r["label"] == t.get("release_row")]
        nm = name_of.get(tid) or f"{util.slug(t['title'])}.png"
        p = render_table(img / nm, t["title"], t["headers"], t["rows"], sub=f"{world['internal_model_name']} — internal results table", hl_rows=rel)
        assets[tid] = str(p)
        images.append({"id": tid, "name": nm, "kind": "table", "path": str(p), "desc": f"{t['title']} (internal results table)", "title": t["title"], "image_only": []})
    # ---- draft table images from the event script
    for e in events:
        im = e.get("image")
        if not im:
            continue
        t = tables[im["table"]]; rows = _rows_of(t, im["rows"])
        wrong = None
        if im.get("wrong"):
            f = look[im["wrong"]["fact_id"]]
            ri = next((i for i, r in enumerate(rows) if r["label"] == f["row"] and (r["group"] == f["group"])), None)
            ci = t["headers"].index(f["column"]) if f["column"] in t["headers"] else None
            if ri is not None and ci is not None:
                wrong = (ri, ci, im["wrong"]["value"])
        # Keep the real event time, but do not turn the generator's draft/final
        # state into a visible authority hint. Rows and scripted errors are unchanged.
        title = f"{t['title']} — {util.human(e['t'])}"
        stem = util.slug(t["title"], 28)
        nm = f"{stem}_{'fixed' if im['id'].endswith('_fixed') else ('final' if im['final'] else 'draft_' + util.dayname(e['t']).lower())}.png"
        k = 2
        while any(i["name"] == nm for i in images):
            nm = nm.replace(".png", f"_{k}.png") if k == 2 else re.sub(r"_\d+\.png$", f"_{k}.png", nm); k += 1
        p = render_table(img / nm, title, t["headers"], rows, sub="", wrong=wrong)
        assets[im["id"]] = str(p)
        images.append({"id": im["id"], "name": nm, "kind": "table_draft", "path": str(p),
                       "desc": f"{t['title']} — {'final table' if im['final'] else 'draft, rows so far as of ' + util.human(e['t'])}", "title": t["title"],
                       "curator": (f"scripted wrong cell: {f['column']} of {f['row']} shows {im['wrong']['value']} instead of {f['value']}" if wrong else ""), "image_only": [wrong[2]] if wrong else []})
    # ---- eval logs
    for e in events:
        if e["kind"] == "share_log":
            rid = e["refs"]["run"]; r = next(x for x in plan["runs"] if x["run_id"] == rid)
            lines = ([f"# {V['log']} — {r['dataset'] or 'validation'} — {V['item']} {rid}", f"{V['item']}_id: {rid}", f"config: {r['label']}"] if V.get("ids_in_text", True) else [f"# {V['log']} — {r['label']}", f"{V['item']}: {r['label']}", f"source: {r['dataset'] or 'as cited in the evidence table'}"]) + [f"owner: {r['owner']}", "----"]
            for rf in plan["row_facts"][rid]:
                key = re.sub(r"[^a-z0-9]+", "_", rf["header"].lower()).strip("_")
                lines.append(f"{key}: {rf['value']}")
            lines += ["----", f"elapsed: {41 + len(lines)}m", "status: ok"] if V["archetype"] == "research_lab" else ["----", f"checked: {len(lines) - 5} figures against the cited source", "status: verified"]
            did = e["doc"]; p = docd / f"{did}.log"; p.write_text("\n".join(lines))
            docs.append({"id": did, "name": (f"eval_{rid}.log" if V["archetype"] == "research_lab" else f"source_note_{re.sub(r'[^a-z0-9]+', '_', r['label'].lower()).strip('_')[:40]}.txt"), "kind": "log", "text": "\n".join(lines), "pages": [], "created": e["t"],
                         "desc": f"{V['log_desc']} ({rid})."})
    # ---- charts (LLM)
    prog = [t for t in facts["tables"] if t["role"] == "ablation_progression" and not t.get("overlaps_with")]
    prog_runs = [r for r in plan["runs"] if r["kind"] in ("progression", "release") and prog and r["table_id"] == prog[0]["id"]]
    runs_md = "\n".join(f"- {r['run_id']} ({r['label']}): " + ", ".join(f"{rf['header']}={rf['value']}" for rf in plan["row_facts"][r["run_id"]]) for r in prog_runs) or "(no progression runs)"
    inv_md = "\n".join(f"- {x['id']}: {x['name']} = {x['value']} — {x['rationale']}" for x in plan["invented"])
    chan_err = errors.get("channel_value")
    need_inv = chan_err["fact_id"] if chan_err else (plan["invented"][0]["id"] if plan["invented"] else "")
    fact_vals = set()  # the leading number of every run cell: "2.59 (100%)" → 2.59, "18.7G" → 18.7, "1,170" → 1170
    for rid in plan["row_facts"]:
        for rf in plan["row_facts"][rid]:
            m = re.match(r"\s*-?\d[\d,]*(?:\.\d+)?", rf["value"])
            if m: fact_vals.add(float(m.group().replace(",", "")))

    def v_charts(cs: ChartSpecs):
        errs = []
        if not 1 <= len(cs.charts) <= 3: errs.append("1 to 3 charts")
        if need_inv and not any(c.invented_id == need_inv for c in cs.charts): errs.append(f"one chart must have invented_id = {need_inv}")
        for c in cs.charts:
            if c.owner not in {p["id"] for p in world["people"]}: errs.append(f"{c.id}: unknown owner")
            for s in c.series:
                if len(s.values) != len(c.x_labels): errs.append(f"{c.id}/{s.name}: values length must equal x_labels length")
                if s.source == "facts":
                    bad = [v for v in s.values if v not in fact_vals]
                    if bad: errs.append(f"{c.id}/{s.name}: values {bad} are not metric facts from the runs; use exact table values or mark source 'invented'")
            if c.invented_id:
                x = inv.get(c.invented_id)
                if not x: errs.append(f"{c.id}: unknown invented_id"); continue
                nums = [float(n.replace(",", "")) for n in util.num_strings(x["value"])]
                cands = list(nums)
                low = x["value"].lower()
                if len(nums) >= 2 and "hour" in low and "min" in low: cands.append(nums[0] * 60 + nums[1])  # "2 hours 40 minutes" → 160
                if len(nums) >= 2 and "min" in low and "sec" in low: cands.append(nums[0] * 60 + nums[1])
                if cands and not any(abs(v - cv) <= max(1e-6, 0.005 * abs(cv)) for s in c.series for v in s.values for cv in cands):
                    # the model plotted around the true value but not on it: move the nearest point onto it (the value must be a fact)
                    best = min(((s, i) for s in c.series for i in range(len(s.values))), key=lambda si: abs(si[0].values[si[1]] - cands[0]), default=None)
                    if best: best[0].values[best[1]] = cands[0]
        return errs
    cs = llm.json("04_assets/charts", {"runs": runs_md, "invented": inv_md, "need_inv": need_inv or "(none)", "people": ", ".join(p["id"] for p in world["people"]),
                                        "config_facts": "\n".join(f"- {c['name']} = {c['value']}" for c in facts["config_facts"][:15]), "internal_model_name": world["internal_model_name"]},
                  ChartSpecs, tag="charts", validator=v_charts)
    inv_owner = {x["id"]: x["owner"] for x in plan["invented"]}
    for c in cs.charts:
        if c.invented_id in inv_owner: c.owner = inv_owner[c.invented_id]
        elif c.owner == world["late_joiner"] or c.owner not in {p["id"] for p in world["people"]}: c.owner = plan["roles"]["lead"]
        cid = f"chart_{c.invented_id}" if c.invented_id else c.id
        spec = c.model_dump(); spec["id"] = cid
        nm = f"{util.slug(c.title, 36)}.png"; k = 2
        while any(i["name"] == nm for i in images):
            nm = f"{util.slug(c.title, 34)}_{k}.png"; k += 1
        p = render_chart(img / nm, spec); assets[cid] = str(p)
        images.append({"id": cid, "name": nm, "kind": "chart", "path": str(p), "desc": f"Chart by {c.owner}: {c.title}", "title": c.title, "image_only": [f"{v:g}" for s in c.series for v in s.values][:8], "spec": spec})
    # ---- documents (LLM specs, rendered)
    DS = plan["doc_schedule"]
    fig_share = {e["refs"].get("figure"): e["t"] for e in events if e["kind"] == "share_figure"}
    chart_t = {}
    for e in events:
        if e.get("file", "").startswith("chart_"):
            chart_t.setdefault(e["file"], e["t"])
    release_runs = [r for r in plan["runs"] if r["kind"] == "release"]
    e_doc, e_head = errors.get("doc_value"), errors.get("doc_headline")
    method = {"name": world["internal_model_name"], "one_sentence": facts["one_sentence"], "task": facts["task_summary"],
              "steps": "\n".join("- " + s for s in facts["pipeline_steps"]), "config": "\n".join(f"- {c['name']}: {c['value']}" for c in facts["config_facts"][:14])}

    run_t = {r["run_id"]: ts(days, r["day_offset"], r["time"]) for r in plan["runs"]}

    def material(t):
        runs_done = [r for r in plan["runs"] if ts(days, r["day_offset"], r["time"]) <= t and r["kind"] != "baseline"]
        base = [r for r in plan["runs"] if r["kind"] == "baseline"]
        rmd = "\n".join(f"- {r['run_id']} [{r['kind']}] {r['label']} on {r['dataset']}: " + ", ".join(f"{rf['header']}={rf['value']}" for rf in plan["row_facts"][r["run_id"]]) for r in base + runs_done)
        figs = [f for f in facts["figures"] if fig_share.get(f["id"], "9999") <= t]
        fmd = "\n".join(f"- {f['id']}: {f['description'][:140]}" for f in figs)
        # a table image may only appear in a document once every run feeding it has finished; before that, the latest
        # draft (rows so far) stands in — so a report written on day 5 cannot show day-6 results
        run_t_by_table = {}
        for r in plan["runs"]:
            if r["kind"] != "baseline": run_t_by_table.setdefault(r["table_id"], []).append(ts(days, r["day_offset"], r["time"]))
        table_ids = {f"table_{x['id']}" for x in facts["tables"] if x["role"] != "other" and all(rt <= t for rt in run_t_by_table.get(x["id"], []))}
        tmd = "\n".join(f"- table_{x['id']}: {x['title'][:90]} (role {x['role']})" for x in facts["tables"] if f"table_{x['id']}" in table_ids) or "(no complete table yet)"
        drafts = {}
        for i in images:
            if i["kind"] == "table_draft" and "_draft_" in i["id"]:
                rid = i["id"].split("_draft_")[1]
                if rid in run_t and run_t[rid] <= t and f"table_{i['id'].split('_draft_')[0]}" not in table_ids:
                    tid = i["id"].split("_draft_")[0]
                    if tid not in drafts or run_t[rid] > run_t[drafts[tid].split("_draft_")[1]]: drafts[tid] = i["id"]
        dmd = "\n".join(f"- {did}: draft table image, rows finished so far (as of {util.human(t)})" for did in drafts.values())
        cmd = "\n".join(f"- {cid}: {next(i for i in images if i['id']==cid)['desc']}" for cid, ct in chart_t.items() if ct <= t)
        dec = "\n".join(f"- {d['id']}: {d['title']} — {d['body']}" for d in plan["decisions"] if ts(days, d["day_offset"], d["time"]) <= t)
        invs = "\n".join(f"- {x['name']} = {x['value']}" for x in plan["invented"] if ts(days, x["day_offset"], x["time"]) <= t)
        return {"runs": rmd, "figures": (fmd + ("\n" + dmd if dmd else "")) or "(none yet)", "tables": tmd, "charts": cmd or "(none yet)", "decisions": dec or "(none yet)", "invented": invs or "(none yet)",
                "fig_ids": {f["id"] for f in figs} | set(drafts.values()), "chart_ids": {cid for cid, ct in chart_t.items() if ct <= t}, "table_ids": table_ids}

    ds_vals = {}
    for r in plan["runs"]:
        if r.get("dataset"):
            for rf in plan["row_facts"][r["run_id"]]: ds_vals.setdefault(r["dataset"], set()).add(rf["value"])

    def _note_now(note: str, mat: dict) -> str:
        """An arc note that names a run which has not finished at writing time would make the writer cite it; name it generically."""
        _r = util.run_id_re(V.get("id_prefix", "R")); done = set(_r.findall(mat["runs"]))
        return re.sub(r"\s{2,}", " ", _r.sub(lambda m: m.group(0) if m.group(0) in done else "the earlier", note or ""))

    def _material_checks(blob: str, mat: dict, extra_allowed: str = "") -> list[str]:
        """A document only states what exists when it is written: runs listed in the material, values from the
        material, no seed vocabulary or internal ids, no dataset mix-ups."""
        errs = [f"never write {w}" for w in util.bad_words(_strip_asset_ids(blob))]
        _rre = util.run_id_re(V.get("id_prefix", "R"))
        done_ids = set(_rre.findall(mat["runs"]))
        for rid in sorted(set(_rre.findall(blob)) - done_ids):
            errs.append(f"{rid} has not finished at writing time; only these {V['items']} exist: {sorted(done_ids)}")
        allowed = mat["runs"] + " " + mat["invented"] + " " + mat["decisions"] + " " + mat["charts"] + " " + method["config"] + " " + extra_allowed
        for n in sorted(set(util.num_strings(blob))):
            if (len(n) < 3 and "." not in n) or re.fullmatch(r"(19|20)\d\d", n): continue
            if not util.contains_value(allowed, n): errs.append(f"the number {n} is not in the material; state only the listed {V['values']}, measurements and configuration values")
        # documents summarise several series in one sentence; with many overlapping series (budget outlooks, deficit paths) the
        # one-series-per-sentence check produces only false positives, so it is enforced only for worlds with a few series
        if len(ds_vals) <= 4: errs += util.dataset_mismatch(blob, ds_vals)
        return errs

    def doc_validator(kind, version, mat, wrong, true, wrong_label):
        def v(spec: DocSpec):
            errs = []
            blob = " ".join(b.text + " " + "\n".join(b.items) + " " + "\n".join(" | ".join(r) for r in b.rows) + " " + b.sub for pg in spec.pages for b in pg.blocks)
            errs += _material_checks(blob, mat, extra_allowed=f"{wrong} {true}")
            if kind == "pdf" and not 4 <= len(spec.pages) <= 6: errs.append("a report has 4 to 6 pages")
            if kind == "pptx" and not 5 <= len(spec.pages) <= 7: errs.append("a deck has 5 to 7 slides")
            for pg in spec.pages:
                if len(pg.blocks) > (5 if kind == "pptx" else 8): errs.append(f"page '{pg.title}': too many blocks")
                for b in pg.blocks:
                    if b.type == "figure" and b.figure_id and b.figure_id not in mat["fig_ids"] | mat["chart_ids"]: errs.append(f"figure_id {b.figure_id} does not exist yet / unknown; available: {sorted(mat['fig_ids'] | mat['chart_ids'])}")
                    if b.type == "table" and b.table_id and b.table_id not in mat["table_ids"] and b.table_id not in mat["fig_ids"]: errs.append(f"table_id {b.table_id} is not complete at writing time; complete tables: {sorted(mat['table_ids']) or 'none'} — use an inline table of finished rows or a draft image instead")
                    if b.type == "table" and b.rows and any(len(r) != len(b.headers) for r in b.rows): errs.append(f"page '{pg.title}': inline table rows must match headers length")
            if not any(b.type == "figure" for pg in spec.pages for b in pg.blocks): errs.append("include at least one figure block")
            if not any(b.type == "table" for pg in spec.pages for b in pg.blocks): errs.append("include at least one table block")
            if version == "v1":
                if kind == "pdf" and true and not _has_cell(spec, true, 1): errs.append(f"page 1 needs an inline table with a cell exactly equal to {true} ({wrong_label})")
                if kind == "pptx" and wrong and not any(b.type == "big_number" and util.contains_value(b.text, wrong or "") for pg in spec.pages for b in pg.blocks): errs.append(f"deck v1 needs a big_number block whose text is the headline {wrong}")
            else:
                core = " ".join(b.text + " " + "\n".join(b.items) + " " + "\n".join(" | ".join(r) for r in b.rows) + " " + b.sub for pg in spec.pages for b in pg.blocks if not re.search(r"changelog|corrected|revision", (b.text + " " + "\n".join(b.items)).lower()))
                if wrong and (not true or wrong != true):
                    # the wrong value may still occur where the material itself carries it (a small number such as 50 can be another fact)
                    tok = lambda text: len(re.findall(r"(?<![\w.,])" + re.escape(str(wrong)) + r"(?![\w.,]*\d)", text))
                    material = mat["runs"] + " " + mat["invented"] + " " + mat["decisions"] + " " + mat["charts"] + " " + method["config"]
                    if tok(core) > tok(material): errs.append(f"v2 must not contain the wrong value {wrong} except inside a one-line changelog/corrected note")
                if true and not util.contains_value(blob, true): errs.append(f"v2 must contain the corrected value {true}")
                if kind == "pptx" and any(b.type == "big_number" for pg in spec.pages for b in pg.blocks): errs.append("deck v2 must not have a big_number headline; use an inline table instead")
                if kind == "pptx" and not any(b.type == "table" and b.rows for pg in spec.pages for b in pg.blocks): errs.append("deck v2 needs an inline per-dataset table")
            return errs
        return v

    for did, kind, author in [("report_v1", "pdf", plan["roles"]["report_author"]), ("report_v2", "pdf", plan["roles"]["report_author"]),
                              ("deck_v1", "pptx", plan["roles"]["deck_author"]), ("deck_v2", "pptx", plan["roles"]["deck_author"])]:
        t = ts(days, *DS[did]); mat = material(t); version = did[-2:]
        err = e_doc if kind == "pdf" else e_head
        wrong = err["wrong_value"] if err else ""
        f = look.get(err["fact_id"]) if err else None
        true = f["value"] if f else ""
        wrong_label = f"{f['column']} of {f['row']} ({f['group']})" if f else ""
        dec = next((d for d in plan["decisions"] if d.get("related_error_id") == (e_head or {}).get("id")), None)
        prompt = "04_assets/report" if kind == "pdf" else "04_assets/deck"
        v = dict(method, version=version, company=world["company_name"], product=world["product"], customer=world["customer_name"], author=author,
                 date=util.human(t), milestone_a=world["milestone_a"], milestone_b=world["milestone_b"], **{k: mat[k] for k in ("runs", "figures", "tables", "charts", "decisions", "invented")},
                 error_instruction=_error_instruction(kind, version, err, f, dec, plan), release_runs="\n".join(f"- {r['run_id']} {r['label']} on {r['dataset']}: " + ", ".join(f"{rf['header']}={rf['value']}" for rf in plan["row_facts"][r["run_id"]]) for r in release_runs if ts(days, r["day_offset"], r["time"]) <= t) or "(none yet)")
        spec = llm.json(prompt, v, DocSpec, tag=did, validator=doc_validator(kind, version, mat, wrong, true, wrong_label), effort="high")
        spec.filename = re.sub(r"\.[A-Za-z0-9]+$", "", spec.filename.strip().replace(" ", "_")) + ("." + kind)
        sd = spec.model_dump(); _scrub_spec(sd, mat, {i["id"]: i["name"] for i in images}, version)
        if kind == "pdf" and version == "v1" and wrong and true:
            _inject_wrong(sd, true, wrong, page_limit=1)  # the scripted slip is injected by code
        pages, page_map = render_doc(sd, assets, docd, did, kind, f"{world['company_name']} · {spec.filename}", tables={f"table_{x['id']}": x for x in facts["tables"]})
        docs.append({"id": did, "name": spec.filename, "kind": kind, "pages": pages, "page_map": page_map, "created": t, "spec": sd,
                     "desc": {"report_v1": f"{V['deliverable'].capitalize()} — draft v1 by {author}.", "report_v2": f"{V['deliverable'].capitalize()} — v2, the document of record.",
                              "deck_v1": f"{V['deck'].capitalize()} — v1 by {author}.", "deck_v2": f"{V['deck'].capitalize()} — v2 by {author}, after the review decision."}[did],
                     "curator": {"report_v1": f"scripted error: {wrong_label} shown as {wrong}", "report_v2": "error corrected; all results", "deck_v1": f"scripted headline slide '{wrong}'", "deck_v2": "headline replaced by a per-dataset table"}[did]})
    # ---- document arcs: v1 → v2 (memo with a wrong cell, note/diagram gaining a figure, rewording)
    for a in plan.get("arcs", []):
        did1, did2 = f"arc_{a['id']}_v1", f"arc_{a['id']}_v2"
        t1, t2 = ts(days, a["day_offset_v1"], a["time_v1"]), ts(days, a["day_offset_v2"], a["time_v2"])
        diagram_id = ""
        if a["kind"] == "diagram":
            dspec = llm.json("04_assets/diagram", {"name": world["internal_model_name"], "one_sentence": facts["one_sentence"], "steps": "\n".join("- " + s for s in facts["pipeline_steps"]), "title": a["title"]}, DiagramSpec, tag=f"diagram_{a['id']}", effort="low")
            diagram_id = f"diagram_{a['id']}"
            p = render_diagram(img / f"{diagram_id}.png", dspec.model_dump()); assets[diagram_id] = str(p)
            images.append({"id": diagram_id, "name": f"{util.slug(a['title'])}_diagram.png", "kind": "chart", "path": str(p), "desc": f"Diagram drawn by {a['author']}: {dspec.title}", "image_only": [n.label for n in dspec.nodes][:6]})
        f = look.get(a.get("wrong_fact_id") or "")
        for version, did, t in (("v1", did1, t1), ("v2", did2, t2)):
            mat = material(t)
            if a["review_kind"] == "mismatch" and f:
                label = f"{f['column']} for {f['row']}" + (f" ({f['group']})" if f.get("group") else "")
                instr = (f"Include an INLINE table (headers + rows) in which one cell is exactly {f['value']} ({label})." if version == "v1"
                         else f"Include an INLINE table in which one cell is exactly {f['value']} ({label}). Do not add a changelog, correction announcement, or a summary naming the repaired cell. Keep ordinary version/date metadata.")
            elif a["review_kind"] == "add_figure":
                instr = ("v1 has NO figure block." if version == "v1" else f"v2 adds exactly one figure block with figure_id {a['figure_id']} and a one-line caption.")
            else:
                instr = ("v1 opens with a dense, jargon-heavy paragraph." if version == "v1" else "v2 opens with a plain-language paragraph a new colleague could follow; keep the rest.")
            if diagram_id: instr += f" Include a figure block with figure_id {diagram_id} in both versions."

            def v_arc(spec: DocSpec, version=version, instr=instr, kind=a["review_kind"], fid=a.get("figure_id", ""), mat=mat):
                errs = []
                blob = " ".join(b.text + " " + "\n".join(b.items) + " " + "\n".join(" | ".join(r) for r in b.rows) for pg in spec.pages for b in pg.blocks)
                if not 1 <= len(spec.pages) <= 2: errs.append("1 or 2 pages")
                figs_used = [b.figure_id for pg in spec.pages for b in pg.blocks if b.type == "figure" and b.figure_id]
                avail = mat["fig_ids"] | mat["chart_ids"] | {x for x in assets if x.startswith("diagram_")} | ({fid} if fid else set())
                for x in figs_used:
                    if x not in avail: errs.append(f"figure_id {x} does not exist yet / unknown; available now: {sorted(avail)}")
                for b in (b for pg in spec.pages for b in pg.blocks if b.type == "table" and b.table_id):
                    if b.table_id not in mat["table_ids"]: errs.append(f"table_id {b.table_id} is not complete at writing time; use an inline table of finished rows")
                errs += _material_checks(blob, mat, extra_allowed=(f"{f['value']} {a.get('wrong_value', '')}" if f else ""))
                if kind == "mismatch" and f:
                    if not _has_cell(spec, f["value"]): errs.append(f"include an inline table with a cell exactly equal to {f['value']}")
                if kind == "add_figure":
                    non_diag = [x for x in figs_used if not x.startswith("diagram_")]
                    if version == "v1" and non_diag: errs.append("v1 must not contain a figure block (other than the diagram)")
                    if version == "v2" and fid not in figs_used: errs.append(f"v2 must contain a figure block with figure_id {fid}")
                return errs
            spec = llm.json("04_assets/arc_doc", {"company": world["company_name"], "name": world["internal_model_name"], "one_sentence": facts["one_sentence"], "title": a["title"], "kind": a["kind"], "version": version,
                                                 "author": a["author"], "date": util.human(t), "note": _note_now(a["note"], mat), "runs": mat["runs"], "figures": mat["figures"], "diagram_id": diagram_id or "(none)", "instruction": instr},
                            DocSpec, tag=f"{did}", validator=v_arc, effort="medium")
            spec.filename = re.sub(r"\.[A-Za-z0-9]+$", "", spec.filename.strip().replace(" ", "_")) + ".pdf"
            sd = spec.model_dump(); _scrub_spec(sd, mat, {i["id"]: i["name"] for i in images}, version)
            if a["review_kind"] == "mismatch" and f and version == "v1":
                _inject_wrong(sd, f["value"], a["wrong_value"])
            pages, page_map = render_doc(sd, assets, docd, did, "pdf", f"{world['company_name']} · {spec.filename}", tables={f"table_{x['id']}": x for x in facts["tables"]})
            docs.append({"id": did, "name": spec.filename, "kind": "pdf", "pages": pages, "page_map": page_map, "created": t, "spec": sd, "arc": a["id"],
                         "desc": f"'{a['title']}' {version} by {a['author']}.",
                         "curator": f"{a['review_kind']} arc" + (f"; v1 shows {a['wrong_value']} instead of {f['value']}" if a["review_kind"] == "mismatch" and f and version == "v1" else "")})
    run.save("04_assets", "assets.json", {"images": images, "docs": docs, "asset_paths": assets})
    run.save("04_assets", "verify_report.json", {"images": len(images), "docs": [(d["id"], len(d["pages"])) for d in docs], "charts": [c.id for c in cs.charts]})
    run.mark_done("04_assets")


ASSET_ID_RE = re.compile(r"\(?\b(?:fig\d+|tab\d+(?:_draft_[A-Z]{1,2}-\d\d)?|table_tab\d+|chart_X-\d\d|diagram_A-\d\d|tab\d+_fixed)\b\)?")


def _strip_asset_ids(text: str) -> str:
    return ASSET_ID_RE.sub("", text)


def _scrub_spec(sd: dict, mat: dict, names: dict | None = None, version: str = ""):
    """Deterministic repairs on a document spec: asset ids never appear in prose (the model saw them as figure/table
    ids) — they become the in-world file name, or vanish; a draft table image referenced as a table block becomes a
    figure block."""
    names = names or {}
    def fix(txt: str) -> str:
        def rep(m):
            tok = m.group(0); nm = names.get(tok.strip("()"), "")
            return (("(" if tok.startswith("(") else "") + nm + (")" if tok.endswith(")") else "")) if nm else ""
        txt = ASSET_ID_RE.sub(rep, txt)
        return re.sub(r"\s{2,}", " ", txt).replace(" .", ".").replace(" ,", ",").replace("()", "").strip()
    for pg in sd.get("pages", []):
        if version == "v2" and pg.get("title"): pg["title"] = re.sub(r"\bv1\b", "v2", pg["title"])
        for b in pg.get("blocks", []):
            for k in ("text", "sub"):
                if b.get(k): b[k] = fix(b[k])
            if b.get("items"): b["items"] = [fix(x) for x in b["items"]]
            if b.get("rows"): b["rows"] = [[fix(str(c)) for c in r] for r in b["rows"]]
            if b.get("type") == "table" and b.get("table_id") and b["table_id"] in mat.get("fig_ids", set()) and b["table_id"] not in mat.get("table_ids", set()):
                b["type"], b["figure_id"], b["table_id"] = "figure", b["table_id"], ""


def _inject_wrong(spec: dict, true: str, wrong: str, page_limit: int | None = None) -> bool:
    """Replace the first inline-table cell equal to the true value with the wrong value (code-side error
    injection, so the LLM only ever writes correct documents)."""
    pages = spec["pages"][:page_limit] if page_limit else spec["pages"]
    for pg in pages:
        for b in pg["blocks"]:
            if b.get("type") == "table" and b.get("rows"):
                for r in b["rows"]:
                    for j, cell in enumerate(r):
                        if str(cell).strip() == str(true).strip():
                            r[j] = wrong; return True
    return False


def _has_cell(spec, true, page_limit=None) -> bool:
    pages = spec.pages[:page_limit] if page_limit else spec.pages
    norm = lambda x: re.sub(r"\s+", " ", str(x).replace("-\n", "")).strip().lower()
    return any(norm(c) == norm(true) or util.contains_value(str(c), str(true)) for pg in pages for b in pg.blocks if b.type == "table" for r in b.rows for c in r)


def _error_instruction(kind, version, err, f, dec, plan):
    if not err or not f:
        return "No scripted error for this document."
    label = f"{f['column']} for {f['row']}" + (f" ({f['group']})" if f.get("group") else "")
    if kind == "pdf":
        if version == "v1":
            return f"Page 1 must contain an INLINE table (headers + rows) in which one cell is exactly {f['value']} ({label})."
        return f"CORRECTION (must be present in the table itself): page 1 lists {label} as {f['value']}; the value {err['wrong_value']} must not appear anywhere. Do not add a changelog or prose announcing the correction. Keep ordinary version/date metadata and the full results."
    else:
        if version == "v1":
            return f"SCRIPTED HEADLINE (must be present): one slide has a big_number block with text '{err['wrong_value']}' and a sub-text naming the metric and dataset ({label})."
        body = dec["body"] if dec else "a per-dataset table and a relative-improvement sentence"
        return f"REPLACEMENT (must be present): no big_number block. The headline slide becomes an inline table with one row per dataset (true values) plus a one-line relative-improvement sentence, per decision {dec['id'] if dec else ''}: {body}. The string '{err['wrong_value']}' must not appear."
