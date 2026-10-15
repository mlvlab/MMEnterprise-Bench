"""Stage 05 — render the corpus: Slack messages, emails, Notion pages.

LLM: one call per (channel, day) writing every scripted event of that group as a short message; one call per
email; one call per meeting for its notes; a naturalness critic per busy channel-day with one rewrite.
Deterministic: the tracker (row versions from tracker events, including the scripted typo and its fix), the
decision log, the claims checklist, the directory.
Verification: every message maps 1:1 to an event, keeps the actor and time, contains every required value string,
contains no forbidden value (private facts, image-only facts), introduces no number that is not in its event, and
never uses source-document words. Output: 05_render/corpus.json"""
from __future__ import annotations
import re
from collections import defaultdict
from .. import config, util, archetype
V = archetype.ARCHETYPES[archetype.DEFAULT]
from ..llm import LLM
from ..schemas import SlackDay, EmailText, MeetingNotes, NaturalnessReview

SEED_WORDS = util.SEED_WORDS
SMALL = {str(i) for i in range(0, 13)}


def _bad_words(text):
    return util.bad_words(text)


def _allowed_numbers(e):
    s = set(util.num_strings(e["gist"])) | set(e["requires"]) | SMALL
    for v in e.get("requires", []):
        s |= set(util.num_strings(v))
    for k in ("run", "decision", "error"):
        r = e["refs"].get(k)
        if r: s |= set(util.num_strings(str(r)))
    return s


def _numbers_ok(text, e, extra=()):
    allowed = _allowed_numbers(e) | set(extra)
    bad = []
    for n in util.num_strings(text):
        if n in allowed or re.fullmatch(r"\d\d:\d\d", n) or any(util.contains_value(a, n) for a in allowed if a):
            continue
        # times like 14:30 come as '14' and '30'
        if re.search(rf"\b{re.escape(n)}:\d\d\b|\b\d\d:{re.escape(n)}\b", text):
            continue
        bad.append(n)
    return bad


def run(run: config.Run, llm: LLM):
    global V
    V = archetype.load(run)
    world = run.load("02_world", "world.json"); plan = run.load("03_timeline", "plan.json")
    events = run.load("03_timeline", "events.json"); assets = run.load("04_assets", "assets.json"); facts = run.load("01_facts", "facts.json")
    _titles = {t["id"]: (t.get("title") or t.get("caption") or ("the baseline table" if t.get("role") == "other" else f"the {t.get('role', 'results').replace('_', ' ')} table")) for t in facts["tables"]}
    _fact_phrase = {}
    for _rid, _rfs in plan.get("row_facts", {}).items():
        for _rf in _rfs: _fact_phrase[_rf["fact_id"]] = f"the {_rf.get('column') or _rf.get('label') or 'figure'} figure"
    for _cf in facts.get("config_facts", []): _fact_phrase.setdefault(_cf["id"], _cf.get("name") or "that setting")
    for _k in facts.get("claims", []): _fact_phrase.setdefault(_k["id"], _k.get("label") or "that claim")
    _inv_name = {x["id"]: x["name"] for x in plan.get("invented", [])}
    def _clean_gist(txt: str) -> str:
        txt = re.sub(r"\btab(\d+)\b", lambda mm: _titles.get("tab" + mm.group(1), "the table"), txt or "")
        txt = re.sub(r"\b([FVKC]\d{3})\b", lambda mm: _fact_phrase.get(mm.group(1), "that figure"), txt)  # writers must never see fact ids
        txt = re.sub(r"(?<![A-Za-z])X-(\d\d)\b", lambda mm: _inv_name.get(mm.group(0), "that measurement"), txt)
        return re.sub(r"\s*/\s*/\s*", " / ", txt).replace(" / :", ":").strip()
    for _e in events: _e["gist"] = _clean_gist(_e.get("gist", ""))
    for _d in plan.get("decisions", []):
        for _k in ("title", "body", "rationale"): _d[_k] = _clean_gist(_d.get(_k, ""))
    people = {p["id"]: p for p in world["people"]}; channels = {c["id"]: c for c in world["channels"]}
    img_name = {i["id"]: i["name"] for i in assets["images"]}; doc_name = {d["id"]: d["name"] for d in assets["docs"]}
    days = world["days"]
    ds_vals = {}
    for r in plan["runs"]:
        if r.get("dataset"):
            for rf in plan["row_facts"][r["run_id"]]: ds_vals.setdefault(r["dataset"], set()).add(rf["value"])

    def _name_docs(txt: str) -> str:  # planner text names attachments by id; people name them by file name
        return re.sub(r"\b(report_v[12]|deck_v[12]|arc_A-\d\d_v[12]|log_[A-Z]{1,2}-\d\d)\b", lambda m: doc_name.get(m.group(1), m.group(1)), txt)

    # ------------------------------------------------------------ Slack
    groups = defaultdict(list)
    for e in events:
        if e["channel"]:
            groups[(e["channel"], e["t"][:10])].append(e)
    slack, prev_tail = [], defaultdict(list)
    state_runs = []
    for (ch, day) in sorted(groups, key=lambda k: (k[1], k[0])):
        evs = sorted(groups[(ch, day)], key=lambda e: e["t"])
        c = channels[ch]
        ev_md = "\n".join(_event_line(e, img_name, doc_name) for e in evs)
        finished = [r for r in plan["runs"] if f"{days[r['day_offset']]}T{r['time']}" < f"{day}T00:00" and r["kind"] != "baseline"]
        state = "Runs finished before today: " + (", ".join(f"{r['run_id']} ({r['label']})" for r in finished) or "none")
        ctx = "\n".join(f"[{m['t'][5:]}] {m['who']}: {m['text'][:140]}" for m in prev_tail[ch][-4:]) or "(no earlier messages)"
        day_nums = set()
        for _e in evs: day_nums |= _allowed_numbers(_e)
        for _m in prev_tail[ch][-4:]: day_nums |= set(util.num_strings(_m["text"]))
        voices = "\n".join(f"- {p['id']} ({p['name']}, {p['role']}): {p['voice']}" for p in world["people"] if p["id"] in c["members"])
        ids = [e["id"] for e in evs]

        def validate(sd: SlackDay, evs=evs, ids=ids):
            errs = []
            got = [m.event_id for m in sd.messages]
            if sorted(got) != sorted(ids): errs.append(f"write exactly one message per event id; expected {ids}, got {got}")
            by_id = {e["id"]: e for e in evs}
            for m in sd.messages:
                e = by_id.get(m.event_id)
                if not e: continue
                if m.actor != e["actor"]: errs.append(f"{m.event_id}: actor must be {e['actor']}")
                if m.time != e["t"][11:]: errs.append(f"{m.event_id}: time must be {e['t'][11:]}")
                for v in e["requires"]:
                    if not util.contains_value(m.text, v): errs.append(f"{m.event_id}: text must contain the value '{v}' verbatim")
                for v in e["forbids"]:
                    if v and util.contains_value(m.text, v): errs.append(f"{m.event_id}: text must NOT contain '{v}' (it is only visible in the attached image / private)")
                # chatter may echo numbers already visible in this channel that day (other events, earlier messages)
                bad = _numbers_ok(m.text, e, extra=(day_nums if e["kind"] == "chatter" else ()))
                if bad: errs.append(f"{m.event_id}: remove numbers that are not part of this event: {bad}")
                bw = _bad_words(m.text)
                if bw: errs.append(f"{m.event_id}: never use the words {bw}")
                for msg in util.dataset_mismatch(m.text, ds_vals, allowed=e["requires"]): errs.append(f"{m.event_id}: {msg}")
                if len(m.text) > (1400 if e["kind"] in ("kickoff", "announce") else 700): errs.append(f"{m.event_id}: too long; chat messages are short")  # a kickoff lists every owner
                if e["kind"] == "chatter" and len(m.text) > 240: errs.append(f"{m.event_id}: chatter must be under 240 characters")
            return errs
        sd = llm.json("05_render/slack_day", {"company": world["company_name"], "system": world["internal_model_name"], "channel": c["name"], "purpose": c["purpose"],
                                               "members": ", ".join(c["members"]), "voices": voices, "day": f"{util.dayname(day + 'T00:00')} {day}", "state": state,
                                               "context": ctx, "events": ev_md}, SlackDay, tag=f"slack_{ch}_{day}", validator=validate, effort="medium")
        # optional critic + one rewrite for busy days
        if len(evs) >= 6:
            rv = llm.json("05_render/critic", {"channel": c["name"], "messages": "\n".join(f"[{m.time}] {m.actor}: {m.text}" for m in sd.messages)}, NaturalnessReview, tag=f"critic_{ch}_{day}", effort="low")
            if rv.score <= 2 and rv.problems:
                sd = llm.json("05_render/slack_day", {"company": world["company_name"], "system": world["internal_model_name"], "channel": c["name"], "purpose": c["purpose"],
                                                       "members": ", ".join(c["members"]), "voices": voices, "day": f"{util.dayname(day + 'T00:00')} {day}", "state": state,
                                                       "context": ctx + "\n\nA reviewer flagged the previous draft: " + "; ".join(rv.problems[:4]) + ". Fix these.", "events": ev_md},
                              SlackDay, tag=f"slack_{ch}_{day}_rw", validator=validate, effort="medium")
        by_id = {e["id"]: e for e in evs}
        for m in sorted(sd.messages, key=lambda m: m.time):
            e = by_id[m.event_id]
            rec = {"id": f"{ch}:{e['t']}", "ch": ch, "t": e["t"], "who": e["actor"], "text": m.text, "event": e["id"], "kind": e["kind"]}
            if e.get("file"): rec["file"] = e["file"]
            if e.get("doc"): rec["doc"] = e["doc"]
            slack.append(rec); prev_tail[ch].append(rec)
    # unique ids: two events at the same minute in one channel → suffix
    seen = {}
    for m in slack:
        k = m["id"]; seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1: m["id"] = f"{k}#{seen[k]}"
    # ------------------------------------------------------------ emails
    emails = []
    for e in [x for x in events if x["kind"] == "email"]:
        m = e["email"]
        m["purpose"] = _clean_gist(m.get("purpose", ""))
        m["required_values"] = [v for v in m.get("required_values", []) if not util.INTERNAL_ID_RE.search(str(v))]  # a fact id is never a value the mail must quote
        frm = people[m["from_id"]]["name"] if m["from_id"] else f"{world['customer_contact']} <{world['customer_contact_email']}>"
        to = (["(customer contact) " + world["customer_contact"]] if m["external_to"] else []) + [people[i]["name"] for i in m["to_ids"]]

        def v_mail(et: EmailText, m=m):
            errs = []
            for v in m["required_values"]:
                if not util.contains_value(et.body, v): errs.append(f"the body must contain '{v}' verbatim")
            bw = _bad_words(et.subject + " " + et.body)
            if bw: errs.append(f"never use the words {bw}")
            for msg in util.dataset_mismatch(et.subject + " " + et.body, ds_vals, allowed=list(m["required_values"]) + util.num_strings(m.get("purpose", ""))): errs.append(msg)  # the plan's wording may pair two series
            if len(et.body) > 1600: errs.append("keep the body under 1600 characters")
            return errs
        et = llm.json("05_render/email", {"company": world["company_name"], "customer": world["customer_name"], "customer_contact": world["customer_contact"], "from": frm,
                                           "to": ", ".join(to), "cc": ", ".join(people[i]["name"] for i in m["cc_ids"]) or "—", "date": util.human(e["t"]), "purpose": _name_docs(m["purpose"]),
                                           "required": ", ".join(m["required_values"]) or "(none)", "attachment": doc_name.get(m["attach_doc"] or "", "") or "(none)",
                                           "system": world["internal_model_name"]}, EmailText, tag=f"email_{m['id']}", validator=v_mail, effort="low")
        emails.append({"id": m["id"], "t": e["t"], "from": frm, "from_id": m["from_id"], "to": m["to_ids"], "cc": m["cc_ids"],
                       "external_to": (world["customer_contact"] + " <" + world["customer_contact_email"] + ">") if m["external_to"] else None,
                       "subject": et.subject, "body": et.body, "doc": m["attach_doc"]})
    # ------------------------------------------------------------ Notion: tracker (deterministic)
    runs = {r["run_id"]: r for r in plan["runs"]}
    headers = []
    for r in plan["runs"]:
        for rf in plan["row_facts"][r["run_id"]]:
            if rf["header"] not in headers: headers.append(rf["header"])
    headers = headers[:16]
    tracker_cols = list(dict.fromkeys(["run", V.get("col_dataset", "dataset"), V.get("col_config", "config")] + (["source"] if V.get("cite_sources") else []) + headers + ["status", "owner"]))  # no duplicate columns when the archetype's dataset column is already 'source'
    run_source = {}
    for r in plan["runs"]:
        srcs = [look_src for look_src in (next((f.get("source") for f in facts["metric_facts"] if f["id"] == rf["fact_id"]), None) for rf in plan["row_facts"][r["run_id"]]) if look_src]
        if srcs: run_source[r["run_id"]] = srcs[0]
    hist = defaultdict(list)
    look = {f["id"]: f for f in facts["metric_facts"]}
    for e in events:
        if e["kind"] == "run_launch":
            r = runs[e["refs"]["run"]]
            hist[r["run_id"]].append({"t": e["t"], "by": e["actor"], "v": _row(dict(r, source=run_source.get(r["run_id"])), {}, headers, V.get("status_running", "running"), look), "note": ""})
        elif e["kind"] in ("tracker_update", "tracker_fix"):
            rid = e["refs"].get("run"); r = runs.get(rid)
            if not r: continue
            r = dict(r, source=run_source.get(rid))
            prev = hist[rid][-1]["v"] if hist[rid] else _row(r, {}, headers, "", look)
            vals = dict(e.get("values", {}))
            status = "baseline" if r["kind"] == "baseline" else (f"{V['release']} ★" if r["kind"] == "release" else ("analysis" if r["kind"] == "analysis" else V.get("status_done", "done")))
            v = _row(r, vals, headers, status, look, prev)
            note = e.get("note", "")
            if note.lower().startswith("typo"): note = ""  # the wrong version must not announce its own error; the fix version's note stays
            hist[rid].append({"t": e["t"], "by": e["actor"], "v": v, "note": note})
    tracker = [{"run": rid, "history": sorted(h, key=lambda x: x["t"])} for rid, h in hist.items()]
    tracker.sort(key=lambda x: x["run"])
    # ------------------------------------------------------------ decisions, claims, meetings, directory
    decisions = []
    for d in plan["decisions"]:
        t = f"{days[d['day_offset']]}T{d['time']}"
        openish = d["day_offset"] >= 6 and re.search(r"fix|post|open|todo|later", d["title"].lower()) is not None
        dch = d.get("private_channel") if d["rationale_visibility"] == "private" else (plan["channels"]["exp"] if d["day_offset"] >= 1 else plan["channels"]["proj"])
        decisions.append({"id": d["id"], "t": t, "by": d["by"], "title": d["title"], "body": d["body"] + (f" Discussed in #{dch} before logging." if dch else ""),
                          "status": "open" if openish else "approved", "rationale_private": d["rationale_visibility"] == "private", "discussed_in": dch})
    report_t = {}
    for e in events:
        if e["kind"] == "run_report":
            for f in e["refs"].get("facts", []): report_t[f] = e["t"]
    fact_val_t = {}
    for f, t in report_t.items():
        fact_val_t.setdefault(look[f]["value"], t)
    claims = []
    freeze_t = f"{days[6]}T20:55"
    for k in facts["claims"]:
        ts_ = [fact_val_t[n] for n in k["numbers"] if n in fact_val_t]
        vt = max(ts_) if ts_ else freeze_t
        claims.append({"id": k["id"], "claim": k["statement"], "history": [{"t": f"{days[0]}T09:30", "status": "pending"}, {"t": vt, "status": "✓ verified (" + ", ".join(k["numbers"][:3]) + ")"}], "owner": plan["roles"]["lead"]})
    meetings = []
    for e in [x for x in events if x["kind"] == "meeting"]:
        day = e["t"][:10]
        vis = [m for m in slack if m["t"][:10] == day and m["t"] <= e["t"] and (channels[m["ch"]]["members"] and any(a in channels[m["ch"]]["members"] for a in e["attendees"]))]
        vis = [m for m in vis if not e.get("private") or channels[m["ch"]]["private"] or m["ch"] == "general"] if e.get("private") else [m for m in vis if not channels[m["ch"]]["private"]]
        prev_day = [m for m in slack if m["t"][:10] == days[max(0, days.index(day) - 1)] and not channels[m["ch"]]["private"]]
        digest = "\n".join(f"[{m['t'][5:]}] #{m['ch']} {m['who']}: {m['text'][:160]}" for m in (prev_day[-25:] + vis)[-40:])
        allowed = set()
        for m in prev_day + vis: allowed |= set(util.num_strings(m["text"]))

        def v_notes(mn: MeetingNotes, allowed=allowed):
            errs = []
            bad = [n for n in util.num_strings(mn.body) if n not in allowed and n not in SMALL and not re.fullmatch(r"\d\d:\d\d", n)]
            if bad: errs.append(f"notes may only contain numbers that appear in the messages: remove {bad}")
            bw = _bad_words(mn.body)
            if bw: errs.append(f"never use the words {bw}")
            if mn.body.count("•") < 3: errs.append("write at least 4 bullet lines starting with •")
            return errs
        mn = llm.json("05_render/meeting", {"title": e["refs"]["meeting"], "date": util.human(e["t"]), "attendees": ", ".join(people[a]["name"] for a in e["attendees"]),
                                             "messages": digest or "(quiet day)", "private": "private (leadership only)" if e.get("private") else "public"}, MeetingNotes, tag=f"meeting_{day}", validator=v_notes, effort="low")
        meetings.append({"id": f"M{len(meetings)+1}", "t": e["t"], "title": mn.title, "attendees": e["attendees"], "private_to": e["attendees"] if e.get("private") else None, "body": mn.body})
    corpus = {"slack": slack, "emails": emails, "tracker_cols": tracker_cols, "tracker": tracker, "decisions": decisions, "claims": claims, "meetings": meetings}
    run.save("05_render", "corpus.json", corpus)
    run.save("05_render", "verify_report.json", {"slack": len(slack), "emails": len(emails), "tracker_rows": len(tracker), "decisions": len(decisions), "claims": len(claims), "meetings": len(meetings),
                                                 "by_channel": {ch: sum(m["ch"] == ch for m in slack) for ch in channels}})
    run.mark_done("05_render")


def _event_line(e, img_name, doc_name):
    att = ""
    if e.get("file"): att = f" [attach image {img_name.get(e['file'], e['file'])}]"
    if e.get("doc"): att += f" [attach document {doc_name.get(e['doc'], e['doc'])}]"
    req = f" MUST CONTAIN: {e['requires']}" if e["requires"] else ""
    forb = f" MUST NOT CONTAIN: {[v for v in e['forbids'] if v]}" if any(e["forbids"]) else ""
    return f"- {e['id']} {e['t'][11:]} {e['actor']} [{e['kind']}{'/' + e['chatter_kind'] if e.get('chatter_kind') else ''}]: {e['gist']}{att}{req}{forb}"


def _row(r, vals, headers, status, look, prev=None):
    v = dict(prev or {})
    v.update({"run": r["run_id"], V.get("col_dataset", "dataset"): r["dataset"], V.get("col_config", "config"): r["label"], "status": status or v.get("status", ""), "owner": r["owner"]})
    if V.get("cite_sources") and r.get("source"): v["source"] = r["source"]
    for h in headers:
        v.setdefault(h, "")
    for fid, val in vals.items():
        f = look.get(fid)
        if f and f["column"] in headers:
            v[f["column"]] = val
    return v
