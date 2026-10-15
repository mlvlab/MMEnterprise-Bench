"""Load released workspace records and their original visual attachments."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re

from . import archetype


class G:
    """Project files and indexes used by QA generation and export."""

    def __init__(self, run):
        self.run = run
        self.world = run.load("02_world", "world.json")
        self.assets = run.load("04_assets", "assets.json")
        self.corpus = run.load("05_render", "corpus.json")
        self.people = [person["id"] for person in self.world["people"]]
        self.pinfo = {person["id"]: person for person in self.world["people"]}
        self.ch = {channel["id"]: channel for channel in self.world["channels"]}

    def aud_all(self):
        return set(self.people)


class Atoms:
    def __init__(self, g: G):
        self.vocabulary = archetype.load(g.run)
        self.g = g
        self.items: dict[str, dict] = {}
        self.order: list[str] = []
        self._build()

    def add(self, aid, kind, ref, releases, text, extra=None):
        if aid in self.items:
            raise ValueError(f"Duplicate workspace record ID: {aid}")
        self.items[aid] = {"id": aid, "kind": kind, "ref": ref, "releases": releases, "text": text, **(extra or {})}
        self.order.append(aid)

    def _build(self):
        g = self.g
        for i, m in enumerate(sorted(g.corpus["slack"], key=lambda m: m["t"]), 1):
            aid = f"S{i:03d}"
            ch = g.ch[m["ch"]]
            where = ("DM " + "/".join(g.pinfo[p]["name"].split()[0] for p in ch["members"])) if m["ch"].startswith("dm-") else f"#{ch['name'].lstrip('#')}"
            txt = f"{g.pinfo[m['who']]['name'].split()[0]}: {m['text']}"
            if m.get("file"): txt += f"  [attached image IMG:{m['file']}]"
            if m.get("doc"): txt += f"  [attached document {m['doc']}]"
            self.add(aid, "chat", f"slack:{m['id']}", [(m["t"], set(ch["members"]))], txt, {"where": where, "msg": m})
        for e in g.corpus["emails"]:
            aud = set(e["to"]) | set(e["cc"]) | ({e["from_id"]} if e["from_id"] else set())
            frm = e["from"] if e["from_id"] is None else g.pinfo[e["from_id"]]["name"]
            to = ", ".join(g.pinfo[p]["name"].split()[0] for p in e["to"] if p in g.pinfo)
            txt = f"mail from {frm} to {to}" + (f", cc {', '.join(g.pinfo[p]['name'].split()[0] for p in e['cc'])}" if e["cc"] else "") + f" — subject: {e['subject']}\n{e['body']}"
            if e.get("doc"): txt += f"\n[attached document {e['doc']}]"
            self.add(e["id"], "email", f"gmail:{e['id']}", [(e["t"], aud)], txt, {"where": "Gmail"})
        for r in g.corpus["tracker"]:
            for h in r["history"]:
                aid = f"T:{r['run']}@{h['t']}"
                cells = ", ".join(f"{k}={v}" for k, v in h["v"].items() if v not in ("", None) and k not in ("run",))
                txt = f"{self.vocabulary['tracker']} row {r['run']} (version by {g.pinfo[h['by']]['name'].split()[0]}): {cells}" + (f" — note: {h['note']}" if h.get("note") else "")
                self.add(aid, "tracker", f"notion:tracker:{r['run']}", [(h["t"], g.aud_all())], txt, {"where": "Notion tracker"})
        for d in g.corpus["decisions"]:
            self.add(d["id"], "decision", f"notion:decision:{d['id']}", [(d["t"], g.aud_all())], f"decision by {g.pinfo[d['by']]['name'].split()[0]} — {d['title']}: {d['body']}", {"where": "Notion decisions"})
        for m in g.corpus["meetings"]:
            aud = set(m["private_to"]) if m.get("private_to") else g.aud_all()
            self.add(f"N:{m['id']}", "meeting", f"notion:meeting:{m['id']}", [(m["t"], aud)], f"meeting notes '{m['title']}' (attendees {', '.join(g.pinfo[p]['name'].split()[0] for p in m['attendees'])}): {m['body']}", {"where": "Notion meetings" + (" (private)" if m.get("private_to") else "")})
        for c in g.corpus["claims"]:
            for i, h in enumerate(c["history"]):
                aid = f"K:{c['id']}" if i == 0 else f"K:{c['id']}@{h['t']}"
                txt = f"claims board: {c['claim']} — " + "; ".join(f"{x['t']}: {x['status']}" for x in c["history"][: i + 1])
                self.add(aid, "claim", f"notion:claims:{c['id']}", [(h["t"], g.aud_all())], txt, {"where": "Notion claims"})
        # documents: released by share messages and emails; each page is an atom
        doc_rel = defaultdict(list)
        for m in g.corpus["slack"]:
            if m.get("doc"): doc_rel[m["doc"]].append((m["t"], set(g.ch[m["ch"]]["members"])))
        for e in g.corpus["emails"]:
            if e.get("doc"): doc_rel[e["doc"]].append((e["t"], set(e["to"]) | set(e["cc"]) | ({e["from_id"]} if e["from_id"] else set())))
        img_rel = defaultdict(list)
        for m in g.corpus["slack"]:
            if m.get("file"): img_rel[m["file"]].append((m["t"], set(g.ch[m["ch"]]["members"])))
        for d in g.assets["docs"]:
            rel = doc_rel.get(d["id"])
            if not rel: continue
            if d.get("text"):
                self.add(f"DOC:{d['id']}:p1", "doc", f"doc:{d['id']}:p1", rel, f"document '{d['name']}':\n{d['text']}", {"where": "attachment", "doc": d["id"], "page": 1, "path": (d["pages"][0] if d.get("pages") else None), "paths": list(d.get("pages", []))})
                continue
            for pi, pg in enumerate(d.get("spec", {}).get("pages", []), 1):
                parts = [f"document '{d['name']}' page {pi} — {pg.get('title', '')}"]
                for b in pg["blocks"]:
                    if b["type"] in ("heading", "paragraph", "caption", "big_number") and b.get("text"): parts.append(b["text"])
                    if b.get("items"): parts += ["• " + x for x in b["items"]]
                    if b.get("rows"): parts.append("table: " + " | ".join(b.get("headers", [])) + " :: " + " / ".join(", ".join(r) for r in b["rows"]))
                    if b.get("table_id"): parts.append(f"[table image {b['table_id']}]")
                    if b.get("figure_id"):
                        parts.append(f"[figure IMG:{b['figure_id']}]"); img_rel[b["figure_id"]] += rel
                pm = d.get("page_map") or [[k] for k in range(1, len(d.get("pages", [])) + 1)]
                idxs = pm[pi - 1] if pi - 1 < len(pm) else []
                paths = [d["pages"][i - 1] for i in idxs if 0 < i <= len(d.get("pages", []))]
                if not paths:
                    raise ValueError(f"Rendered document page is missing: {d['id']} page {pi}")
                self.add(f"DOC:{d['id']}:p{pi}", "doc", f"doc:{d['id']}:p{pi}", rel, "\n".join(parts), {"where": "attachment", "doc": d["id"], "page": pi, "path": (paths[0] if paths else None), "paths": paths})
        for im in g.assets["images"]:
            rel = img_rel.get(im["id"])
            if not rel: continue
            self.add(f"IMG:{im['id']}", "image", f"file:{im['id']}", sorted(rel, key=lambda entry: entry[0]),
                     f"image '{im['name']}'", {"where": "attachment", "path": im["path"]})


def record_text(atom: dict) -> str:
    text = atom["text"].split("\nvisible only inside the image:")[0]
    if atom.get("path") or atom.get("paths"):
        match = re.match(r"(?:image|document) '([^']+)'", text)
        name = match.group(1) if match else atom["id"]
        text = name + (f" · page {atom['page']}" if atom.get("page") else "")
    timestamp = min((time for time, _ in atom["releases"]), default="")
    return f"[{atom['id']}] {timestamp} | {atom.get('where', '')}\n{text}"


def record_block(ids: list[str], atoms: Atoms) -> tuple[str, list[str]]:
    """Return complete visible text plus an ordered attachment manifest."""
    records, images = [], []
    image_records = defaultdict(list)
    for aid in dict.fromkeys(ids):
        if aid not in atoms.items:
            raise ValueError(f"Unknown evidence record: {aid}")
        atom = atoms.items[aid]
        records.append(record_text(atom))
        paths = atom.get("paths") or ([atom["path"]] if atom.get("path") else [])
        if atom.get("kind") == "image" and not paths:
            raise ValueError(f"Original image is missing for record: {aid}")
        for path in paths:
            if not Path(path).is_file():
                raise ValueError(f"Original image is unavailable for record: {aid}")
            if path not in images:
                images.append(path)
            image_records[path].append(aid)
    if images:
        manifest = [f"Attached image {index}: {', '.join(image_records[path])}; {Path(path).name}"
                    for index, path in enumerate(images, 1)]
        records.append("Image attachment order:\n" + "\n".join(manifest))
    return "\n\n".join(records), images


def full_project(atoms: Atoms) -> tuple[str, list[str]]:
    """Include every released record and original image without truncation."""
    return record_block(atoms.order, atoms)
