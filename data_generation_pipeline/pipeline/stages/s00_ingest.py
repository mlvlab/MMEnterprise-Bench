"""Stage 00 — ingest the seed PDF.

Deterministic: page renders, page text, caption detection, column layout, crop proposals.
LLM (vision): crop verification loop, image-only facts per figure, table extraction from the table crop.
Verification: every numeric table cell must occur in the page text; crops must be judged complete by the VLM.
Output: 00_ingest/ingest.json + pages/ + crops/."""
from __future__ import annotations
import re, statistics
from pathlib import Path
import fitz  # PyMuPDF

from .. import config, util
from ..llm import LLM
from ..schemas import CropCheck, VisualFacts, TableExtract, PickSide

# "Figure 3:", "Fig. 3.", "Table 2-1." (report style, hyphenated chapter numbers), "Figure A-2", "Figure 4" alone on its line
CAP_RE = re.compile(r"^\s*(Fig(?:ure)?\.?|Tab(?:le)?\.?)\s*([A-Z]?\d+(?:[-–.]\d+)?)(?:\s*[.:]|\s*\n|\s*$)", re.I)


def _blocks(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        text = "".join(s["text"] for l in b["lines"] for s in l["spans"]).strip()
        sizes = [s["size"] for l in b["lines"] for s in l["spans"] if s["text"].strip()]
        if not text:
            continue
        out.append({"bbox": fitz.Rect(b["bbox"]), "text": text, "lines": len(b["lines"]),
                    "size": statistics.median(sizes) if sizes else 0})
    return out


def _layout_global(doc):
    """Body font size and column layout estimated over the whole document (per-page estimates are skewed by tables
    and figure labels)."""
    W = doc[0].rect.width
    sizes, wide_blocks = [], []
    for page in doc:
        for b in _blocks(page):
            sizes += [b["size"]] * len(b["text"])
            wide_blocks.append(b)
    body_size = statistics.median(sizes) if sizes else 10
    cnt = {}
    for page in doc:
        H = page.rect.height
        seen_page = set()
        for b in _blocks(page):
            if (b["bbox"].y1 < H * 0.15 or b["bbox"].y0 > H * 0.9) and b["lines"] <= 2 and len(b["text"]) < 80:
                k = _run_key(b, H)
                if k not in seen_page: seen_page.add(k); cnt[k] = cnt.get(k, 0) + 1
    recurring = {k for k, n in cnt.items() if n >= 3}
    body = [b for b in wide_blocks if b["lines"] >= 3 and abs(b["size"] - body_size) < 1.0 and len(b["text"]) / b["lines"] >= 35]
    if not body:
        return {"body_size": body_size, "cols": [(W * 0.1, W * 0.9)], "text_x0": W * 0.1, "text_x1": W * 0.9, "recurring": recurring}
    left = [b for b in body if b["bbox"].x1 < W * 0.55]
    right = [b for b in body if b["bbox"].x0 > W * 0.45]
    x0 = min(b["bbox"].x0 for b in body); x1 = max(b["bbox"].x1 for b in body)
    if len(left) >= 5 and len(right) >= 5 and len(left) + len(right) > 0.7 * len(body):
        cols = [(statistics.median([b["bbox"].x0 for b in left]), statistics.median([b["bbox"].x1 for b in left])),
                (statistics.median([b["bbox"].x0 for b in right]), statistics.median([b["bbox"].x1 for b in right]))]
    else:
        cols = [(x0, x1)]
    return {"body_size": body_size, "cols": cols, "text_x0": x0, "text_x1": x1, "recurring": recurring}


def _is_body(b, lay):
    colw = lay["cols"][0][1] - lay["cols"][0][0]
    return (b["lines"] >= 2 and abs(b["size"] - lay["body_size"]) < 1.0 and b["bbox"].width > 0.6 * colw
            and len(b["text"]) / b["lines"] >= 35)


def _xrange(page, lay, cap, siblings):
    """Horizontal extent for a caption: its column, the full text width for wide captions, or its own width when
    several captions share one row (side-by-side sub-figures)."""
    cb = cap["bbox"]
    tw = lay["text_x1"] - lay["text_x0"]
    if siblings:  # side-by-side panels: split the gaps between neighbouring captions
        allc = sorted([cb] + [s["bbox"] for s in siblings], key=lambda r: r.x0)
        i = [k for k, r in enumerate(allc) if r is cb][0]
        left = (allc[i - 1].x1 + cb.x0) / 2 if i > 0 else max(lay["text_x0"], cb.x0 - 40)
        right = (cb.x1 + allc[i + 1].x0) / 2 if i + 1 < len(allc) else min(lay["text_x1"], cb.x1 + 40)
        return left + 2, right - 2
    if len(lay["cols"]) == 2 and cb.width < 0.55 * tw:
        col = min(lay["cols"], key=lambda c: abs((c[0] + c[1]) / 2 - (cb.x0 + cb.x1) / 2))
        return col
    return lay["text_x0"], lay["text_x1"]


def _propose(page, blocks, lay, cap, kind, siblings=()):
    """Region above ('above') or below ('below') a caption block, bounded by the nearest body paragraph, caption or
    page header in the same horizontal band."""
    W, H = page.rect.width, page.rect.height
    cb = cap["bbox"]
    x0, x1 = _xrange(page, lay, cap, siblings)
    same_col = [b for b in blocks if b["bbox"].x1 > x0 + 5 and b["bbox"].x0 < x1 - 5 and b is not cap]
    header_bottom = 0
    for b in blocks:  # running header / page number in the top band (any column)
        if _is_running(b, page, lay) and b["bbox"].y1 < H * 0.15:
            header_bottom = max(header_bottom, b["bbox"].y1)
    footer_top = H
    for b in blocks:
        if b["bbox"].y0 > H * 0.92 and b["lines"] <= 2 and len(b["text"]) < 80:
            footer_top = min(footer_top, b["bbox"].y0)
    if kind == "above":
        tops = [b["bbox"].y1 for b in same_col if b["bbox"].y1 <= cb.y0 + 1 and (_is_body(b, lay) or CAP_RE.match(b["text"]))]
        top = max([header_bottom] + tops) if tops or header_bottom else max(40, header_bottom)
        r = fitz.Rect(x0 - 4, top + 3, x1 + 4, cb.y0 - 2)
        if r.height < 40:
            r.y0 = max(header_bottom + 3, cb.y0 - 220)
        return r
    else:
        bots = [b["bbox"].y0 for b in same_col if b["bbox"].y0 >= cb.y1 - 1 and (_is_body(b, lay) or CAP_RE.match(b["text"]))]
        bot = min(bots) if bots else min(H - 50, footer_top - 4)
        r = fitz.Rect(x0 - 4, cb.y1 + 2, x1 + 4, bot - 4)
        if r.height < 40:
            r.y1 = min(H - 40, cb.y1 + 220)
        return r


def _run_key(b, H):
    """Running headers/footers repeat the same short text at the same height on many pages."""
    return (re.sub(r"\d+", "#", b["text"].strip())[:40], int(b["bbox"].y0 // 12), "top" if b["bbox"].y1 < H * 0.15 else "bottom" if b["bbox"].y0 > H * 0.9 else "mid")


def _is_running(b, page, lay):
    H = page.rect.height
    if b["lines"] > 2 or len(b["text"]) >= 80 or CAP_RE.match(b["text"]):
        return False
    band = b["bbox"].y1 < H * 0.15 or b["bbox"].y0 > H * 0.9
    if not band:
        return False
    if re.fullmatch(r"\d{1,3}", b["text"].strip()) or re.match(r"^\d{1,3}\s{2,}\S", b["text"]):
        return True
    return _run_key(b, H) in lay.get("recurring", set())


def _credit_line(page, rect, cap_bbox) -> str:
    """A 'Source:/Credit:/©' line inside the crop or just below the caption, if any."""
    band = fitz.Rect(rect.x0, rect.y0, rect.x1, max(rect.y1, cap_bbox[3]) + 40)
    txt = page.get_text("text", clip=band)
    m = re.search(r"(?:^|\n)\s*((?:Sources?|Credit|Photo|Image|Courtesy)\s*:.*|©.*|Adapted from.*|Reproduced from.*)", txt)
    return m.group(1).strip() if m else ""


def _own_credit(credit: str, front: str) -> bool:
    """True when the credit names the publisher itself (its name appears on the front pages) or is a generic
    'authors' calculation' line; False for photo agencies and other organisations."""
    low = credit.lower()
    if any(k in low for k in ("shutterstock", "getty", "istock", "alamy", "unsplash", "reuters", "ap photo")): return False
    body = re.sub(r"^(sources?|credit|photo|image|courtesy)\s*:\s*", "", low).strip()
    if not body or any(k in body for k in ("author", "own calculation", "own analysis", "staff")): return True
    words = [w for w in re.findall(r"[a-z][a-z\-]{3,}", body) if w not in ("data", "from", "based", "using", "and", "the", "with")]
    return bool(words) and sum(w in front for w in words) >= max(1, len(words) // 2)


def _tabular_score(blocks, lay, rect):
    """How table-like the text inside rect is: many small-font or one-line blocks, few body paragraphs."""
    score = 0
    for b in blocks:
        if not b["bbox"].intersects(rect):
            continue
        if _is_body(b, lay):
            score -= 3
        elif b["size"] < lay["body_size"] - 0.4 or b["lines"] == 1:
            score += 1
    return score


def _render(page, rect, path, scale=2.6):
    rect = rect & page.rect
    if rect.height < 30 or rect.width < 60:
        return None
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect)
    pix.save(str(path)); return rect


def run(run: config.Run, llm: LLM):
    out = run.stage_dir("00_ingest"); pages_dir = out / "pages"; crops_dir = out / "crops"
    pages_dir.mkdir(exist_ok=True); crops_dir.mkdir(exist_ok=True)
    doc = fitz.open(run.seed_pdf)
    doc_front = " ".join(doc[i].get_text() for i in range(min(3, doc.page_count))).lower()
    skipped_credit = []
    pages, captions = [], []
    lay = _layout_global(doc)
    for i, page in enumerate(doc):
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5)).save(str(pages_dir / f"page-{i+1:02d}.png"))
        blocks = _blocks(page)
        pages.append({"n": i + 1, "text": page.get_text("text")})
        for b in blocks:
            m = CAP_RE.match(b["text"])
            if m:
                kind = "table" if m.group(1).lower().startswith("tab") else "figure"
                num = re.sub(r"[-–.]", "_", m.group(2))  # "1-1" → "1_1" so ids stay word-safe (fig1_1, tab2_3)
                captions.append({"page": i + 1, "kind": kind, "number": num, "caption": b["text"], "block": b, "lay": lay, "blocks": blocks})
    # de-duplicate: keep the first block per (kind, number)
    seen, uniq = set(), []
    for c in captions:
        k = (c["kind"], c["number"])
        if k in seen:
            continue
        seen.add(k); uniq.append(c)
    title = ""
    if doc.page_count:
        b0 = _blocks(doc[0])
        if b0:
            title = max(b0, key=lambda b: b["size"])["text"]

    figures, tables = [], []
    for c in uniq:
        page = doc[c["page"] - 1]
        fid = f"{'fig' if c['kind']=='figure' else 'tab'}{c['number']}"
        sibs = [o["block"] for o in uniq if o["page"] == c["page"] and o is not c and abs(o["block"]["bbox"].y0 - c["block"]["bbox"].y0) < 20]
        if c["kind"] == "figure":
            direction = "above"
        else:
            ra = _propose(page, c["blocks"], c["lay"], c["block"], "above", sibs); rb = _propose(page, c["blocks"], c["lay"], c["block"], "below", sibs)
            sa, sb = _tabular_score(c["blocks"], c["lay"], ra), _tabular_score(c["blocks"], c["lay"], rb)
            direction = "above" if sa > sb else "below"
            pa, pb = crops_dir / f"_{fid}_A.png", crops_dir / f"_{fid}_B.png"
            if _render(page, ra, pa) is not None and _render(page, rb, pb) is not None:
                pick = llm.json("00_ingest/pick_side", {"caption": c["caption"]}, PickSide, images=[pa, pb], tag="pick_side", effort="low")
                direction = "above" if pick.pick == "A" else "below"
            for q in (pa, pb):
                q.unlink(missing_ok=True)
        rect = _propose(page, c["blocks"], c["lay"], c["block"], direction, sibs)
        path = crops_dir / f"{fid}.png"
        ok = _render(page, rect, path) is not None
        check = None
        # --- VLM verification loop (max 4 adjustments)
        for it in range(4):
            if not ok:
                break
            check = llm.json("00_ingest/crop_check", {"kind": c["kind"], "caption": c["caption"]}, CropCheck, images=[path], tag="crop_check", effort="low")
            if check.contains == "none" and it == 0:
                direction = "below" if direction == "above" else "above"
                rect = _propose(page, c["blocks"], c["lay"], c["block"], direction, sibs); ok = _render(page, rect, path) is not None; continue
            adj = fitz.Rect(rect)
            h = rect.height
            if check.extra_text_top: adj.y0 += h * 0.15
            if check.extra_text_bottom: adj.y1 -= h * 0.15
            if check.cut_top: adj.y0 -= max(h * 0.2, 60)
            if check.cut_bottom: adj.y1 += max(h * 0.2, 60)
            if adj == rect or (check.complete and not check.extra_text_top and not check.extra_text_bottom):
                break
            rect = adj; ok = _render(page, rect, path) is not None
        if not ok or check is None or check.contains == "none":
            continue
        subs = [b for b in c["blocks"] if re.match(r"^\(?[a-f]\)\s", b["text"]) and b["lines"] <= 2 and b["bbox"].intersects(rect) and b["bbox"].y0 > rect.y0 + rect.height * 0.5]
        if subs:  # LaTeX sub-figure captions "(a) …" under the panels are part of the seed, not of the figure
            rect.y1 = min(rect.y1, min(b["bbox"].y0 for b in subs) - 2); ok = _render(page, rect, path) is not None
        for b in c["blocks"]:  # never ship a page header / footer inside an in-world image
            if _is_running(b, page, c["lay"]) and b["bbox"].intersects(rect):
                if b["bbox"].y0 < (rect.y0 + rect.y1) / 2: rect.y0 = max(rect.y0, b["bbox"].y1 + 2)
                else: rect.y1 = min(rect.y1, b["bbox"].y0 - 2)
                ok = _render(page, rect, path) is not None
        if check.running_header:
            rect.y0 += rect.height * 0.08; ok = _render(page, rect, path) is not None
        if not ok:
            continue
        credit = _credit_line(page, rect, c["block"]["bbox"])
        if credit and not _own_credit(credit, doc_front):
            skipped_credit.append({"id": fid, "page": c["page"], "credit": credit[:120]})
            continue  # third-party material (photo agency, another organisation) stays out of the company's assets
        rec = {"id": fid, "kind": c["kind"], "number": c["number"], "caption": c["caption"], "page": c["page"],
               "bbox": [round(v, 1) for v in rect], "path": str(path), "check": check.model_dump()}
        if c["kind"] == "figure":
            vf = llm.json("00_ingest/visual_facts", {"caption": c["caption"]}, VisualFacts, images=[path], tag="visual_facts")
            rec["visual"] = vf.model_dump(); figures.append(rec)
        else:
            page_text = pages[c["page"] - 1]["text"]
            def validate(t: TableExtract):
                errs, missing, total = [], [], 0
                for g in t.row_groups:
                    for r in g.rows:
                        if len(r.cells) != len(t.column_headers):
                            errs.append(f"row '{r.label}' has {len(r.cells)} cells but there are {len(t.column_headers)} column headers")
                        for cell in r.cells:
                            if re.fullmatch(r"-?\d[\d,]*\.?\d*", cell.strip()):
                                total += 1
                                if not util.contains_value(page_text, cell.strip()):
                                    missing.append(cell.strip())
                if total and len(missing) / total > 0.1:
                    errs.append("these numeric cells do not occur in the page text (re-read the image carefully): " + ", ".join(missing[:15]))
                return errs
            te = llm.json("00_ingest/table_extract", {"caption": c["caption"], "page_text": page_text[:6000]}, TableExtract, images=[path], tag="table_extract", validator=validate)
            rec["table"] = te.model_dump(); tables.append(rec)
    figures = figures[: config.SETTINGS["max_figures"]]
    tables = tables[: config.SETTINGS["max_tables"]]
    ingest = {"title": title, "n_pages": doc.page_count, "pages": pages, "figures": figures, "tables": tables}
    run.save("00_ingest", "ingest.json", ingest)
    report = {"figures": [{"id": f["id"], "page": f["page"], "complete": f["check"]["complete"], "n_image_only_facts": len(f["visual"]["image_only_facts"])} for f in figures],
              "tables": [{"id": t["id"], "page": t["page"], "rows": sum(len(g["rows"]) for g in t["table"]["row_groups"]), "cols": len(t["table"]["column_headers"])} for t in tables]}
    report["skipped_third_party"] = skipped_credit
    run.save("00_ingest", "verify_report.json", report)
    run.mark_done("00_ingest")
    return ingest
