"""A small flow-layout engine that turns a DocSpec (pages of blocks) into page images: A4 pages for reports,
16:9 slides for decks. Blocks: heading, paragraph, bullets, table (inline rows, or a facts table rendered inline
from `tables`, or an image asset), figure (image asset), big_number, caption. Report pages overflow into
continuation pages; slides use a two-column layout when a figure and bullets share a slide."""
from __future__ import annotations
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw
from .images import font, INK, MUTED, LINE, ACC, SOFT, HL, clean_label
from .glyphs import GlyphSafeDraw

REPORT = {"size": (1240, 1754), "margin": 84, "body": 24, "h1": 38, "h2": 27, "small": 17, "table": 18}
DECK = {"size": (1600, 900), "margin": 72, "body": 26, "h1": 40, "h2": 28, "small": 18, "table": 22}
MAX_COLS = 7  # metric columns per inline table chunk


class Canvas:
    def __init__(self, style, bg="white"):
        self.st = style; self.im = Image.new("RGB", style["size"], bg); self.d = GlyphSafeDraw(self.im)
        self.y = style["margin"]; self.x = style["margin"]; self.w = style["size"][0] - 2 * style["margin"]
        self.layout_errors = []; self.painted_text_count = 0; self._painting_footer = False
        draw_text = self.d.text
        def checked_text(xy, value, *args, **kwargs):
            box = self.d.textbbox(xy, value, *args, **{k: v for k, v in kwargs.items() if k not in ("fill", "stroke_fill")})
            limit = self.im.height if self._painting_footer else self.im.height - self.st["margin"] - 44
            if box[0] < 0 or box[1] < 0 or box[2] > self.im.width or box[3] > limit:
                self.layout_errors.append({"kind": "text_bounds", "text": str(value), "bbox": list(box), "bottom_limit": limit})
            self.painted_text_count += 1
            return draw_text(xy, value, *args, **kwargs)
        self.d.text = checked_text

    def room(self):
        return self.st["size"][1] - self.st["margin"] - 44 - self.y

    def text_lines(self, s, sz, width=None):
        """Use the same wrapping for pagination and painting."""
        chars = max(12, int((width or self.w) / (sz * 0.55)))
        lines = []
        for para in str(s).split("\n"):
            lines += textwrap.wrap(para, chars) or [""]
        # Character estimates alone miss wide glyphs and long headings. Use
        # identical measured lines in both height estimation and painting.
        face = font(sz, sz >= self.st["h2"])
        available = width or self.w
        if any(self.d.textlength(line, font=face) > available for line in lines):
            lines = [line for para in str(s).split("\n") for line in _wrap_pixels(self.d, para, face, available)]
        return lines

    def text(self, s, sz, bold=False, color=INK, width=None, lh=1.42, x=None):
        f = font(sz, bold); width = width or self.w; x = self.x if x is None else x
        lines = self.text_lines(s, sz, width)
        for ln in lines:
            self.d.text((x, self.y), ln, font=f, fill=color); self.y += int(sz * lh)
        return len(lines) * int(sz * lh)

    def image(self, path, maxw=None, maxh=None, x=None, center=True):
        im = Image.open(path).convert("RGB")
        maxw = maxw or self.w; maxh = min(maxh if maxh is not None else self.room(), self.room() - 10)
        if maxh <= 0:
            raise ValueError("No room for image: paginate before drawing")
        scale = min(maxw / im.width, maxh / im.height, 2.0)
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
        x = self.x if x is None else x
        if center and maxw == self.w:
            x = self.x + (self.w - im.width) // 2
        if x < 0 or x + im.width > self.im.width or self.y < 0 or self.y + im.height > self.im.height - self.st["margin"] - 44:
            raise ValueError("Image exceeds document body bounds")
        self.im.paste(im, (x, self.y)); self.d.rectangle([x, self.y, x + im.width, self.y + im.height], outline=LINE, width=1)
        self.y += im.height + 10
        return im.height

    def table(self, headers, rows, sz=None, hl_rows=(), width=None, page_break=None):
        """rows: list of lists of strings. Nothing is ever truncated: columns are packed into chunks that fit the
        width at their natural size (the first column is repeated in every chunk); a chunk that still does not fit
        is rendered at a smaller font; oversized prose cells use measured-width wrapping."""
        sz = sz or self.st["table"]
        if not headers:
            return self
        width = width or self.w
        cols = list(range(len(headers)))
        def nat(j, f, fb):  # widest line of any cell, measured in the font it will be drawn with (highlighted rows are bold)
            return int(max([self.d.textlength(ln, font=fb) for ln in _wrap2(str(headers[j]), 18)] +
                           [self.d.textlength(ln, font=(fb if i in hl_rows else f)) for i, r in enumerate(rows) for ln in _wrap2(str(r[j]) if j < len(r) else "", 26)])) + 22
        f, fb = font(sz), font(sz, True)
        import re as _re
        def numeric_col(j):
            vals = [str(r[j]).strip() for r in rows if j < len(r) and str(r[j]).strip()]
            return vals and sum(bool(_re.fullmatch(r"[-\d.,%]+|[-–—]", v)) for v in vals) >= 0.6 * len(vals)
        keys = [0]
        if len(cols) > 2 and not numeric_col(1):
            keys = [0, 1]
        key_w = sum(nat(j, f, fb) for j in keys)
        chunks, cur, cur_w = [], [], key_w
        for j in cols[len(keys):]:
            w = nat(j, f, fb)
            if cur and cur_w + w > width:
                chunks.append(cur); cur, cur_w = [], key_w
            cur.append(j); cur_w += w
        if cur or not chunks:
            chunks.append(cur)
        canvas = self
        for ci, ch in enumerate(chunks):
            idx = keys + ch
            canvas = canvas._table_chunk([clean_label(headers[j]) for j in idx], [[(clean_label(r[j]) if j < len(r) else "") for j in idx] for r in rows], sz, hl_rows, width, page_break)
            if ci < len(chunks) - 1:
                canvas.y += 6
        return canvas

    def _table_chunk(self, cols, rows, sz, hl_rows, width, page_break=None):
        requested_sz = sz
        # Preserve compact tables; long prose must wrap rather than overflow
        # after the minimum font size is reached.
        while True:
            f, fb = font(sz), font(sz, True)
            hw = [[self.d.textlength(ln, font=fb) for ln in _wrap2(h, 18)] for h in cols]
            widths = []
            for j in range(len(cols)):
                w = max(hw[j] + [self.d.textlength(ln, font=(fb if i in hl_rows else f)) for i, r in enumerate(rows) for ln in _wrap2(r[j], 26)])
                widths.append(int(w) + 22)
            if sum(widths) <= width or sz <= 12:
                break
            sz -= 1
        if sum(widths) > width:
            sz = requested_sz
            f, fb = font(sz), font(sz, True)
            minimum = 48
            if width < minimum * len(cols):
                raise ValueError("Table chunk has too many columns for readable wrapping")
            weights = [w ** .5 for w in widths]
            available = width - minimum * len(cols)
            widths = [minimum + int(available * w / sum(weights)) for w in weights]
            widths[-1] += width - sum(widths)
            hlines = [_wrap_pixels(self.d, h, fb, widths[j] - 22) for j, h in enumerate(cols)]
            wrapped_rows = [[_wrap_pixels(self.d, cell, fb if i in hl_rows else f, widths[j] - 22)
                             for j, cell in enumerate(row)] for i, row in enumerate(rows)]
        else:
            f, fb = font(sz), font(sz, True)
            hlines = [_wrap2(h, 18) for h in cols]
            wrapped_rows = [[_wrap2(cell, 26) for cell in row] for row in rows]
        lh = int(sz * 1.3)
        hh = max(len(x) for x in hlines) * lh + 12
        canvas = self

        def header(c):
            x = c.x
            c.d.rectangle([x, c.y, x + sum(widths), c.y + hh], fill=SOFT)
            cx = x
            for lines, w in zip(hlines, widths):
                for k, ln in enumerate(lines):
                    c.d.text((cx + 10, c.y + 6 + k * lh), ln, font=fb, fill=INK)
                cx += w
            c.y += hh

        first_rh = max((len(cell) for cell in wrapped_rows[0]), default=0) * lh + 10 if rows else 0
        if page_break and canvas.room() < hh + first_rh:
            canvas = page_break()
        if page_break and canvas.room() < hh + first_rh:
            raise ValueError("Table header and first row do not fit a continuation page")
        header(canvas)
        for i, r in enumerate(rows):
            cells = wrapped_rows[i]
            rh = max(len(c) for c in cells) * lh + 10
            if page_break and canvas.room() < rh:
                canvas = page_break()
                header(canvas)
            if page_break and canvas.room() < rh:
                raise ValueError("Table row does not fit a continuation page")
            x = canvas.x
            if i in hl_rows:
                canvas.d.rectangle([x, canvas.y, x + sum(widths), canvas.y + rh], fill=HL)
            cx = x
            for j, w in enumerate(widths):
                ff = fb if i in hl_rows else f
                for k, ln in enumerate(cells[j]):
                    canvas.d.text((cx + 10, canvas.y + 5 + k * lh), ln, font=ff, fill=INK)
                cx += w
            canvas.d.line([(x, canvas.y + rh), (x + sum(widths), canvas.y + rh)], fill=LINE, width=1); canvas.y += rh
        canvas.y += 10
        return canvas

    def footer(self, s):
        self.d.line([(self.st["margin"], self.st["size"][1] - 52), (self.st["size"][0] - self.st["margin"], self.st["size"][1] - 52)], fill=LINE, width=1)
        self._painting_footer = True
        try:
            self.d.text((self.st["margin"], self.st["size"][1] - 42), _fit(self.d, s, font(13), self.w), font=font(13), fill=MUTED)
        finally:
            self._painting_footer = False


def _wrap_pixels(draw, text, face, width):
    """Lossless measured wrapping, including tokens longer than a cell."""
    lines, line = [], ""
    for word in str(text).split():
        candidate = (line + " " + word).strip()
        if draw.textlength(candidate, font=face) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = ""
        for char in word:
            if line and draw.textlength(line + char, font=face) > width:
                lines.append(line)
                line = ""
            if draw.textlength(char, font=face) > width:
                raise ValueError("Table column narrower than a single glyph")
            line += char
    return lines + ([line] if line else []) or [""]


def _wrap2(text, n):
    text = ' '.join(str(text).split())
    """Split a cell into at most two lines of roughly n characters, breaking at spaces or slashes."""
    text = str(text).strip()
    if len(text) <= n:
        return [text]
    cut = max(text.rfind(" ", 0, n + 1), text.rfind("/", 0, n + 1), text.rfind("(", 0, n + 1))
    if cut <= 0:
        cut = n
    return [text[:cut].strip(), text[cut:].strip()]


def _fit(d, text, f, maxw):
    if d.textlength(text, font=f) <= maxw:
        return text
    while len(text) > 3 and d.textlength(text + "…", font=f) > maxw:
        text = text[:-1]
    return text + "…"


def _facts_rows(t):
    has_group = any(r.get("group") for r in t["rows"])
    headers = (["Group"] if has_group else []) + ["Configuration"] + list(t["headers"])
    rows = [(([r.get("group", "")] if has_group else []) + [r["label"]] + list(r["cells"])) for r in t["rows"]]
    hl = [i for i, r in enumerate(t["rows"]) if r.get("label") == t.get("release_row")]
    return headers, rows, hl


def render_doc(spec: dict, assets: dict, out_dir: Path, stem: str, kind: str, header: str, tables: dict | None = None) -> tuple[list[str], list[list[int]]]:
    """spec: {filename, pages:[{title, blocks:[...]}]}; assets: id → image path; tables: 'table_<id>' → facts table
    (rendered inline instead of embedding the wide image). Returns (page image paths, page_map) where page_map[i] lists
    the 1-based rendered pages that spec page i+1 produced (a long page overflows into continuation pages)."""
    st = DECK if kind == "pptx" else REPORT
    tables = tables or {}
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []; page_map = []
    page_no = 0
    doc_title = spec["pages"][0]["title"] if spec["pages"] else spec.get("filename", "")

    def new_page(title=None, cont=False):
        nonlocal page_no
        page_no += 1
        c = Canvas(st)
        if kind == "pptx":
            c.d.rectangle([0, 0, st["size"][0], 10], fill=ACC)
            if title:
                c.text(title + (" (cont.)" if cont else ""), st["h1"], True); c.y += 12
        else:
            c.d.text((st["margin"], 36), _fit(c.d, header, font(13), c.w), font=font(13), fill=MUTED)
            c.d.line([(st["margin"], 58), (st["size"][0] - st["margin"], 58)], fill=LINE, width=1)
            c.y = 96
            if title:
                c.text(title + (" (cont.)" if cont else ""), st["h1"], True); c.y += 6
                c.d.line([(st["margin"], c.y), (st["margin"] + 60, c.y)], fill=ACC, width=3); c.y += 22
        c.body_top = c.y
        return c

    for pg in spec["pages"]:
        first_no = page_no + 1
        c = new_page(pg["title"])
        blocks = list(pg["blocks"])

        def continue_page(setup=None):
            nonlocal c
            c.footer(f"{header} · {'slide' if kind == 'pptx' else 'page'} {page_no}")
            paths.append(_save(c, out_dir, stem, page_no))
            c = new_page(pg["title"], cont=True)
            if setup:
                setup()

        def flow_text(text, size, *, gap=0, width=None, x=None, color=INK, bold=False, lh=1.42, setup=None):
            """Keep an item together when possible; split an oversized item by line.

            Report, plain-slide and two-column-slide bullets share this path. A
            block-level 160px estimate cannot bound a long list (or long item).
            """
            lines = c.text_lines(text, size, width)
            line_height = int(size * lh)
            height = len(lines) * line_height
            capacity = st["size"][1] - st["margin"] - 44 - c.body_top
            if height <= capacity and height > c.room():
                continue_page(setup)
            if height <= c.room():
                c.text(text, size, bold=bold, lh=lh, width=width, x=x, color=color)
            else:
                for line in lines:
                    if c.room() < line_height:
                        continue_page(setup)
                    if c.room() < line_height:
                        raise ValueError("Document title leaves no room for a text line")
                    c.text(line, size, bold=bold, lh=lh, width=width, x=x, color=color)
            c.y += gap

        def table_page_break():
            continue_page()
            return c

        # deck: figure + bullets side by side
        if kind == "pptx":
            figs = [b for b in blocks if b.get("type") == "figure" and b.get("figure_id") in assets]
            buls = [b for b in blocks if b.get("type") in ("bullets", "paragraph")]
            if len(figs) == 1 and buls and not any(b.get("type") in ("table", "big_number") for b in blocks):
                left_w = int(c.w * 0.58)
                fig_bottom = c.y
                def left_panel():
                    nonlocal fig_bottom
                    top = c.y
                    caption = figs[0].get("text", "")
                    caption_h = len(c.text_lines(caption, st["small"], left_w)) * int(st["small"] * 1.42) if caption else 0
                    c.image(assets[figs[0]["figure_id"]], maxw=left_w,
                            maxh=max(1, min(st["size"][1] - top - 120, c.room() - caption_h - 10)), x=c.x, center=False)
                    if caption:
                        c.text(caption, st["small"], color=MUTED, width=left_w)
                    fig_bottom = c.y
                    c.y = top
                left_panel()
                rx = c.x + left_w + 40; rw = c.w - left_w - 40
                for b in buls:
                    if b["type"] == "bullets":
                        for it in b.get("items", []):
                            flow_text("• " + it, st["body"], gap=8, width=rw, x=rx, setup=left_panel)
                    else:
                        flow_text(b.get("text", ""), st["body"], gap=10, width=rw, x=rx, setup=left_panel)
                for b in blocks:
                    if b.get("type") == "caption":
                        c.y = max(c.y, fig_bottom)
                        flow_text(b.get("text", ""), st["small"], color=MUTED)
                c.footer(f"{header} · slide {page_no}"); paths.append(_save(c, out_dir, stem, page_no)); page_map.append(list(range(first_no, page_no + 1))); continue
        for b in blocks:
            t = b.get("type")
            need = 160
            if t == "table":  # estimate the table height so it is never cut at the page bottom
                nrows = len(b.get("rows") or []) or (sum(len(r["cells"]) and 1 for r in tables[b["table_id"]]["rows"]) if b.get("table_id") in tables else 8)
                need = int((nrows + 1) * st["table"] * 1.3) + 60 + (nrows + 1) * 10
            if kind != "pptx" and c.room() < need and t in ("table", "figure", "paragraph", "bullets", "heading") and c.y > c.st["margin"] + 140:
                c.footer(f"{header} · page {page_no}"); paths.append(_save(c, out_dir, stem, page_no)); c = new_page(pg["title"], cont=True)
            if t == "heading":
                c.y += 8; flow_text(b.get("text", ""), st["h2"], bold=True, color=ACC, gap=6)
            elif t == "paragraph":
                flow_text(b.get("text", ""), st["body"], gap=12)
            elif t == "bullets":
                for it in b.get("items", []):
                    flow_text("• " + it, st["body"], gap=4)
                c.y += 10
            elif t == "caption":
                flow_text(b.get("text", ""), st["small"], color=MUTED, gap=10)
            elif t == "big_number":
                c.y += 16; flow_text(b.get("text", ""), int(st["h1"] * 3.6), bold=True, color=ACC, lh=1.1, gap=10)
                if b.get("sub"): flow_text(b["sub"], st["h2"], gap=8)
            elif t == "table":
                if b.get("rows"):
                    c = c.table(b.get("headers", []), b["rows"], hl_rows=tuple(b.get("highlight_rows", []) or []), page_break=table_page_break)
                elif b.get("table_id") in tables:
                    h, rows, hl = _facts_rows(tables[b["table_id"]])
                    c = c.table(h, rows, sz=st["table"] - (2 if len(rows) > 10 else 0), hl_rows=tuple(hl), page_break=table_page_break)
                elif b.get("table_id") in assets:
                    c.image(assets[b["table_id"]])
                if b.get("text"): flow_text(b["text"], st["small"], color=MUTED, gap=8)
            elif t == "figure":
                if b.get("figure_id") in assets:
                    c.image(assets[b["figure_id"]], maxh=min(c.room() - 40, int(st["size"][1] * 0.48)))
                if b.get("text"): flow_text(b["text"], st["small"], color=MUTED, gap=8)
        c.footer(f"{header} · {'slide' if kind == 'pptx' else 'page'} {page_no}")
        paths.append(_save(c, out_dir, stem, page_no))
        page_map.append(list(range(first_no, page_no + 1)))
    return paths, page_map


def _save(c: Canvas, out_dir: Path, stem: str, n: int) -> str:
    if c.layout_errors:
        raise ValueError(f"Refusing clipped document page {stem}/{n}: {c.layout_errors[:3]}")
    p = out_dir / f"{stem}_p{n}.jpg"
    c.im.save(p, quality=84, optimize=True)
    return str(p)
