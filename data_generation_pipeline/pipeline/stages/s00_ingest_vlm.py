"""Page-level visual extraction: one vision call per page lists every visual element
(charts, diagrams, photos, tables — captioned or not) with a rough box; Python only snaps that box to what the page
actually renders (drawings, embedded images, non-body text blocks), then the same crop_check loop, third-party credit
filter, visual_facts and table_extract as the rules-based variant. Output: 00_ingest/ingest.json (same schema)."""
from __future__ import annotations
import re
from typing import Literal
import fitz  # PyMuPDF
from pydantic import BaseModel, Field
from .. import config, util
from ..llm import LLM
from ..schemas import CropCheck, VisualFacts, TableExtract
from .s00_ingest import _blocks, _layout_global, _is_body, _is_running, _render, _credit_line, _own_credit, CAP_RE


class PageItem(BaseModel):
    kind: Literal["figure", "table", "photo", "decorative"]
    caption: str = ""
    description: str = ""
    box: list[float] = Field(description="[left, top, right, bottom] in percent of the page")
    third_party: bool = False
    values_printed: bool = False


class PageScan(BaseModel):
    items: list[PageItem]


def _frac_inside(a: fitz.Rect, b: fitz.Rect) -> float:
    """Fraction of a's area that lies inside b."""
    inter = a & b
    return (inter.width * inter.height) / max(1e-6, a.width * a.height) if not inter.is_empty else 0.0


def _primitives(page):
    W, H = page.rect.width, page.rect.height
    prims = []
    for d in page.get_drawings():
        r = d.get("rect")
        if r is None or r.is_empty or r.width * r.height < 4: continue
        if r.width > W * 0.9 and r.height > H * 0.9: continue  # page frame / background
        prims.append(fitz.Rect(r))
    for im in page.get_image_info():
        r = fitz.Rect(im["bbox"])
        if r.width > W * 0.95 and r.height > H * 0.95: continue  # full-page scan background
        prims.append(r)
    return prims


def _snap(page, blocks, lay, prims, box_pct, kind):
    """Rough VLM box → the union of the rendered primitives and non-body text blocks it covers."""
    W, H = page.rect.width, page.rect.height
    r = fitz.Rect(box_pct[0] / 100 * W, box_pct[1] / 100 * H, box_pct[2] / 100 * W, box_pct[3] / 100 * H) & page.rect
    if r.is_empty or r.width < 20 or r.height < 20: return None
    big = fitz.Rect(r.x0 - W * 0.03, r.y0 - H * 0.03, r.x1 + W * 0.03, r.y1 + H * 0.03) & page.rect
    parts = [p for p in prims if _frac_inside(p, big) >= 0.5]
    parts += [b["bbox"] for b in blocks if not _is_body(b, lay) and not CAP_RE.match(b["text"]) and not _is_running(b, page, lay) and _frac_inside(b["bbox"], big) >= 0.6]
    if not parts: return r
    u = fitz.Rect(parts[0])
    for p in parts[1:]: u |= p
    if u.width * u.height > 2.5 * r.width * r.height: u = u & big  # a background rectangle dragged the union out
    u = fitz.Rect(u.x0 - 4, u.y0 - 4, u.x1 + 4, u.y1 + 4) & page.rect
    return u if (u.width >= 20 and u.height >= 20) else r


def _caption_near(blocks, rect, vlm_caption):
    """Prefer the printed caption block adjacent to the crop (exact text), else what the VLM read."""
    cands = [b for b in blocks if CAP_RE.match(b["text"]) and (abs(b["bbox"].y0 - rect.y1) < 40 or abs(b["bbox"].y1 - rect.y0) < 40)]
    if cands: return min(cands, key=lambda b: min(abs(b["bbox"].y0 - rect.y1), abs(b["bbox"].y1 - rect.y0)))["text"].strip()
    return (vlm_caption or "").strip()


SOURCE_RE = re.compile(r"^\s*(source|sources|note|notes|credit|photo)\s*[:.]|\b(GAO|NIST|CBO|OECD|ONS)-?\d{2,}|©|\(c\)", re.I)


def _cut_source_strip(rect, blocks):
    """A 'Source: …' / report-number / © line inside the lower part of a crop names the seed's publisher: the crop ends
    above it. Only text blocks that start inside the lower 40 % of the crop are considered."""
    r = fitz.Rect(rect); low = r.y0 + r.height * 0.6
    strips = [b for b in blocks if SOURCE_RE.search(b["text"].strip()) and b["bbox"].y0 >= low and b["bbox"].y0 < r.y1 and b["bbox"].x0 < r.x1 and b["bbox"].x1 > r.x0]
    if not strips: return None
    r.y1 = max(r.y0 + 20, min(b["bbox"].y0 for b in strips) - 2)
    return r


def _in_text(cell: str, page_text: str) -> bool:
    """A numeric cell occurs in the page text as a whole token (not as part of another number)."""
    v = cell.rstrip("%").strip()
    return re.search(rf"(?<![\d.]){re.escape(v)}(?![\d.])", page_text) is not None


def run(run: config.Run, llm: LLM):
    out = run.stage_dir("00_ingest"); pages_dir = out / "pages"; crops_dir = out / "crops"
    pages_dir.mkdir(exist_ok=True); crops_dir.mkdir(exist_ok=True)
    doc = fitz.open(run.seed_pdf)
    doc_front = " ".join(doc[i].get_text() for i in range(min(3, doc.page_count))).lower()
    lay = _layout_global(doc)
    pages, figures, tables = [], [], []
    skipped = {"third_party": [], "decorative": [], "unusable": []}
    nfig = ntab = 0
    title = ""
    if doc.page_count:
        b0 = _blocks(doc[0])
        if b0: title = max(b0, key=lambda b: b["size"])["text"]
    for i, page in enumerate(doc):
        ppath = pages_dir / f"page-{i+1:02d}.png"
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(str(ppath))
        page_text = page.get_text("text"); pages.append({"n": i + 1, "text": page_text})
        blocks = _blocks(page)
        scan = llm.json("00_ingest/page_scan", {"page": i + 1, "n_pages": doc.page_count, "page_text": page_text[:5000]}, PageScan, images=[ppath], tag="page_scan", effort="low")
        prims = _primitives(page)
        taken = []
        for it in sorted(scan.items, key=lambda x: (x.box[1] if len(x.box) == 4 else 0)):
            if len(it.box) != 4: continue
            if it.kind == "decorative": skipped["decorative"].append({"page": i + 1, "desc": it.description[:80]}); continue
            if it.kind == "photo" and not (it.caption or "").strip().lower().startswith(("fig", "chart", "map")):  # portraits, cover photos, logos: not data
                skipped["decorative"].append({"page": i + 1, "desc": "photo: " + it.description[:70]}); continue
            if re.search(r"\b(portrait|headshot|logo|signature|photograph of (a|the) (man|woman|person|minister|official))\b", it.description, re.I):
                skipped["decorative"].append({"page": i + 1, "desc": it.description[:80]}); continue
            if it.third_party: skipped["third_party"].append({"page": i + 1, "desc": it.description[:80], "credit": "VLM-flagged"}); continue
            kind = "table" if it.kind == "table" else "figure"
            cap0 = (it.caption or "").strip().lower()
            if cap0.startswith(("fig", "chart")): kind = "figure"   # the printed caption is authoritative for the kind
            elif cap0.startswith("tab"): kind = "table"
            rect = _snap(page, blocks, lay, prims, it.box, kind)
            if rect is None: continue
            if any(_frac_inside(rect, t) > 0.7 for t in taken): continue  # duplicate of an element already taken on this page
            if kind == "figure": nfig += 1; fid = f"fig{nfig}"
            else: ntab += 1; fid = f"tab{ntab}"
            caption = _caption_near(blocks, rect, it.caption)
            path = crops_dir / f"{fid}.png"
            ok = _render(page, rect, path) is not None; check = None
            for _ in range(4):  # the same verification loop as the rules-based variant
                if not ok: break
                check = llm.json("00_ingest/crop_check", {"kind": kind, "caption": caption or it.description}, CropCheck, images=[path], tag="crop_check", effort="low")
                if check.contains == "none": break
                adj = fitz.Rect(rect); h = rect.height
                if check.extra_text_top: adj.y0 += h * 0.15
                if check.extra_text_bottom: adj.y1 -= h * 0.15
                if check.cut_top: adj.y0 -= max(h * 0.2, 60)
                if check.cut_bottom: adj.y1 += max(h * 0.2, 60)
                adj &= page.rect
                if adj == rect or (check.complete and not check.extra_text_top and not check.extra_text_bottom): break
                rect = adj; ok = _render(page, rect, path) is not None
            if ok and check is not None:
                cut = _cut_source_strip(rect, blocks)
                if cut is not None and cut != rect: rect = cut; ok = _render(page, rect, path) is not None
            if not ok or check is None or check.contains == "none":
                skipped["unusable"].append({"page": i + 1, "desc": it.description[:80]})
                if kind == "figure": nfig -= 1
                else: ntab -= 1
                path.unlink(missing_ok=True); continue
            for b in blocks:  # never ship a page header / footer inside an in-world image
                if _is_running(b, page, lay) and b["bbox"].intersects(rect):
                    if b["bbox"].y0 < (rect.y0 + rect.y1) / 2: rect.y0 = max(rect.y0, b["bbox"].y1 + 2)
                    else: rect.y1 = min(rect.y1, b["bbox"].y0 - 2)
                    ok = _render(page, rect, path) is not None
            if check.running_header: rect.y0 += rect.height * 0.08; ok = _render(page, rect, path) is not None
            credit = _credit_line(page, rect, [rect.x0, rect.y0, rect.x1, rect.y1])
            if credit and not _own_credit(credit, doc_front):
                skipped["third_party"].append({"page": i + 1, "desc": it.description[:80], "credit": credit[:120]})
                if kind == "figure": nfig -= 1
                else: ntab -= 1
                path.unlink(missing_ok=True); continue
            taken.append(fitz.Rect(rect))
            rec = {"id": fid, "kind": kind, "number": str(nfig if kind == "figure" else ntab), "caption": caption, "page": i + 1,
                   "bbox": [round(v, 1) for v in rect], "path": str(path), "check": check.model_dump(), "scan": {"kind": it.kind, "description": it.description}}
            if kind == "figure":
                vf = llm.json("00_ingest/visual_facts", {"caption": caption or it.description}, VisualFacts, images=[path], tag="visual_facts")
                rec["visual"] = vf.model_dump(); figures.append(rec)
                if it.values_printed:  # a chart with data labels is also a data table: its values become facts
                    def vchart(te: TableExtract, _pt=page_text):
                        nums = [c.strip() for g in te.row_groups for r in g.rows for c in r.cells if re.fullmatch(r"-?\d[\d,]*\.?\d*%?", c.strip())]
                        hits = [c for c in nums if _in_text(c, _pt)]
                        strong = {c for c in hits if len(re.sub(r"[^\d]", "", c)) >= 2}  # single digits match axis ticks anywhere
                        return [] if (nums and len(hits) >= 0.6 * len(nums) and len(strong) >= 2 and te.column_headers) else ["the chart's printed values must be copied exactly as printed (most of them occur in the page text)"]
                    try:
                        te = llm.json("00_ingest/table_extract", {"caption": caption or it.description, "page_text": page_text[:6000]}, TableExtract, images=[path], tag="table_extract", validator=vchart, retries=1)
                        ntab += 1
                        tables.append(dict(rec, id=f"tab{ntab}", kind="table", number=str(ntab), from_figure=fid, table=te.model_dump()))
                    except RuntimeError as ex:  # values could not be read reliably: keep the figure only, and say so
                        skipped.setdefault("chart_values", []).append({"id": fid, "page": i + 1, "reason": str(ex)[:160]})
            else:
                def validate(te: TableExtract, _pt=page_text):
                    errs = []
                    nums = [c for g in te.row_groups for r in g.rows for c in r.cells if re.fullmatch(r"-?\d[\d,]*\.?\d*%?", c.strip())]
                    if nums:
                        hit = sum(1 for c in nums if _in_text(c.strip(), _pt))
                        if hit < 0.9 * len(nums): errs.append(f"only {hit}/{len(nums)} numeric cells occur in the page text; copy digits exactly as printed")
                    if not te.column_headers: errs.append("column_headers must not be empty")
                    return errs
                try:
                    te = llm.json("00_ingest/table_extract", {"caption": caption or it.description, "page_text": page_text[:6000]}, TableExtract, images=[path], tag="table_extract", validator=validate)
                    rec["table"] = te.model_dump(); tables.append(rec)
                except RuntimeError as ex:  # a table whose digits are not in the text layer (scanned / chart-like) is left out rather than failing the seed
                    skipped.setdefault("tables_unreadable", []).append({"id": rec.get("id"), "page": i + 1, "reason": str(ex)[:160]})
    ingest = {"title": title.strip(), "n_pages": doc.page_count, "pages": pages, "figures": figures, "tables": tables, "ingest_mode": "vlm"}
    run.save("00_ingest", "ingest.json", ingest)
    run.save("00_ingest", "verify_report.json", {"mode": "vlm", "figures": [{"id": f["id"], "page": f["page"], "complete": f["check"]["complete"], "caption": bool(f["caption"]), "n_image_only_facts": len(f["visual"]["image_only_facts"])} for f in figures],
                                                 "tables": [{"id": t["id"], "page": t["page"], "rows": sum(len(g["rows"]) for g in t["table"]["row_groups"]), "cols": len(t["table"]["column_headers"])} for t in tables],
                                                 "skipped_third_party": skipped["third_party"], "skipped_decorative": skipped["decorative"], "skipped_unusable": skipped["unusable"],
                                                 "skipped_chart_values": skipped.get("chart_values", []), "skipped_tables_unreadable": skipped.get("tables_unreadable", [])})
    run.mark_done("00_ingest")
    return ingest
