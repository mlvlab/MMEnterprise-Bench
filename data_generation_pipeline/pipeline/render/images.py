"""Deterministic image rendering: tables (drafts with a wrong cell, finals), charts (matplotlib from specs).
Everything here is document-agnostic: it draws whatever rows/series it is given."""
from __future__ import annotations
import os, textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from .glyphs import GlyphSafeDraw

from importlib.util import find_spec

FD = str(Path(find_spec("matplotlib").origin).parent / "mpl-data/fonts/ttf") + "/"


def font(sz, bold=False):
    return ImageFont.truetype(FD + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), sz)


INK = (28, 31, 39); MUTED = (95, 100, 112); LINE = (200, 204, 212); ACC = (43, 95, 142); RED = (163, 58, 58); SOFT = (236, 240, 247); HL = (232, 238, 247)


import re as _re


def clean_label(text):
    """Strip publication-style citation markers from labels before drawing them in company documents:
    'Method A [10]' → 'Method A', 'Model-8B (Author et al. 2025)' → 'Model-8B'."""
    t = _re.sub(r"\s*\[\d+(?:\s*,\s*\d+)*\]", "", str(text))
    t = _re.sub(r"\s*\([^()]*(?:et al\.?|\b(?:19|20)\d{2}\b)[^()]*\)", "", t)
    return t.strip()


def _fit(draw, text, f, maxw):
    """Shorten a string with an ellipsis so it fits maxw pixels."""
    if draw.textlength(text, font=f) <= maxw:
        return text
    while len(text) > 3 and draw.textlength(text + "…", font=f) > maxw:
        text = text[:-1]
    return text + "…"


def _one_line(x) -> str:
    """PIL cannot measure multi-line strings: collapse any whitespace (newlines from table extraction or long claims)."""
    return " ".join(str(x).split())


def render_table(path, title, headers, rows, sub="", hl_rows=(), wrong=None, sz=16):
    """rows: list of dicts {group,label,cells}. wrong: (row_index, col_index, wrong_value) replaces one cell.
    Column widths are derived from content so any table shape renders."""
    headers = [_one_line(h) for h in headers]
    rows = [dict(r, group=_one_line(r.get("group", "")), label=_one_line(r.get("label", "")), cells=[_one_line(c) for c in r.get("cells", [])]) for r in rows]
    rows = [dict(r, cells=(r["cells"] + [""] * len(headers))[:len(headers)]) for r in rows]  # ragged source tables: every row gets one cell per header
    title = _one_line(title) if title else title
    if wrong: wrong = (wrong[0], wrong[1], _one_line(wrong[2]))
    f, fb = font(sz), font(sz, True)
    tmp = GlyphSafeDraw(Image.new("RGB", (10, 10)))
    has_group = any(r.get("group") for r in rows)
    cols = (["Group"] if has_group else []) + ["Row"] + list(headers)
    cells = []
    for i, r in enumerate(rows):
        c = list(r["cells"])
        if wrong and wrong[0] == i:
            c[wrong[1]] = wrong[2]
        cells.append(([clean_label(r.get("group", ""))] if has_group else []) + [clean_label(r["label"])] + [clean_label(x) for x in c])
    def wrap(t, n=28):
        t = str(t).strip()
        if len(t) <= n: return [t]
        cut = max(t.rfind(" ", 0, n + 1), t.rfind("/", 0, n + 1), t.rfind("(", 0, n + 1)); cut = cut if cut > 0 else n
        return [t[:cut].strip(), t[cut:].strip()]
    wcells = [[wrap(v) for v in row] for row in cells]; wcols = [wrap(h, 20) for h in cols]
    widths = []
    for j in range(len(cols)):
        w = max([tmp.textlength(ln, font=fb) for ln in wcols[j]] + [tmp.textlength(ln, font=f) for row in wcells for ln in row[j]])
        widths.append(int(w) + 22)
    lh = int(sz * 1.3)
    hh = max(len(c) for c in wcols) * lh + 12
    tf = font(sz + 3, True); tw = int(tmp.textlength(title, font=tf))
    W = max(sum(widths) + 80, tw + 80, 420)
    rows_h = [max(len(c) for c in row) * lh + 10 for row in wcells]
    H = 60 + hh + sum(rows_h) + (70 if sub else 40)
    im = Image.new("RGB", (W, H), "white"); d = GlyphSafeDraw(im)
    d.text((40, 18), title, font=tf, fill=INK)
    x, y = 40, 56
    d.rectangle([x, y, x + sum(widths), y + hh], fill=SOFT)
    cx = x
    for lines, w in zip(wcols, widths):
        for k, ln in enumerate(lines): d.text((cx + 10, y + 6 + k * lh), ln, font=fb, fill=INK)
        cx += w
    y += hh
    for i, row in enumerate(wcells):
        rh = rows_h[i]
        if i in hl_rows:
            d.rectangle([x, y, x + sum(widths), y + rh], fill=HL)
        cx = x
        for j, w in enumerate(widths):
            ff = fb if i in hl_rows else f
            for k, ln in enumerate(row[j]): d.text((cx + 10, y + 5 + k * lh), ln, font=ff, fill=INK)
            cx += w
        d.line([(x, y + rh), (x + sum(widths), y + rh)], fill=LINE, width=1); y += rh
    if sub:
        d.text((40, y + 10), sub, font=font(12), fill=MUTED)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def render_diagram(path, spec):
    """Layered left-to-right block diagram: nodes are placed in columns by longest path (back-edges ignored for
    layering), forward edges are orthogonal, back-edges are routed below the diagram as dashed lines, and every
    edge label sits on a white pad at the middle of its vertical segment."""
    nodes = spec["nodes"][:10]; ids = [n["id"] for n in nodes]; edges = [e for e in spec["edges"] if e["src"] in ids and e["dst"] in ids]
    # back-edge detection by DFS
    adj = {i: [] for i in ids}
    for e in edges: adj[e["src"]].append(e["dst"])
    state, back = {}, set()
    def dfs(u):
        state[u] = 1
        for w in adj[u]:
            if state.get(w) == 1: back.add((u, w))
            elif w not in state: dfs(w)
        state[u] = 2
    for u in ids:
        if u not in state: dfs(u)
    fwd = [e for e in edges if (e["src"], e["dst"]) not in back]
    layer = {i: 0 for i in ids}
    for _ in range(len(ids)):
        for e in fwd: layer[e["dst"]] = max(layer[e["dst"]], layer[e["src"]] + 1)
    cols = {}
    for i in ids: cols.setdefault(layer[i], []).append(i)
    ncol = max(cols) + 1; nrow = max(len(v) for v in cols.values())
    bw, bh, gx, gy = (210, 66, 130, 60) if ncol <= 5 else (168, 60, 84, 56)
    W = 80 + ncol * bw + (ncol - 1) * gx + 80; H = 100 + nrow * bh + (nrow - 1) * gy + (90 if back else 60)
    im = Image.new("RGB", (W, H), "white"); d = GlyphSafeDraw(im)
    d.text((40, 24), spec["title"], font=font(22, True), fill=INK)
    pos = {}
    for c in range(ncol):
        col = cols.get(c, []); total = len(col) * bh + (len(col) - 1) * gy
        y0 = 90 + (nrow * bh + (nrow - 1) * gy - total) // 2
        for r, i in enumerate(col):
            pos[i] = (80 + c * (bw + gx), y0 + r * (bh + gy))
    lab = {n["id"]: n["label"] for n in nodes}
    for i, (x, y) in pos.items():
        d.rounded_rectangle([x, y, x + bw, y + bh], radius=10, fill=SOFT, outline=ACC, width=2)
        f = font(15 if ncol <= 5 else 13, True)
        words = lab[i].split(); lines = [lab[i]]
        if d.textlength(lab[i], font=f) > bw - 16 and len(words) > 1:
            k = len(words) // 2; lines = [" ".join(words[:k]), " ".join(words[k:])]
        for li, t in enumerate(lines):
            tw = d.textlength(t, font=f); d.text((x + (bw - tw) / 2, y + bh / 2 - 9 * len(lines) + li * 18), t, font=f, fill=INK)
    def arrow(p, direction):
        dx, dy = direction
        d.polygon([p, (p[0] - 9 * dx + 5 * dy, p[1] - 9 * dy + 5 * dx), (p[0] - 9 * dx - 5 * dy, p[1] - 9 * dy - 5 * dx)], fill=MUTED)
    def label_at(x, y, text):
        if not text: return
        f = font(12); tw = d.textlength(text, font=f)
        d.rectangle([x - tw / 2 - 4, y - 9, x + tw / 2 + 4, y + 9], fill="white"); d.text((x - tw / 2, y - 7), text, font=f, fill=MUTED)
    lane = 90 + nrow * bh + (nrow - 1) * gy + 40
    used_mid = {}
    for e in edges:
        (x0, y0), (x1, y1) = pos[e["src"]], pos[e["dst"]]
        if (e["src"], e["dst"]) in back:  # route below the diagram
            sx, sy = x0 + bw / 2 + 20, y0 + bh; tx, ty = x1 + bw / 2 - 20, y1 + bh
            pts = [(sx, sy), (sx, lane), (tx, lane), (tx, ty + 8)]
            for a, b in zip(pts, pts[1:]):
                _dash(d, a, b)
            arrow((tx, ty + 2), (0, -1)); label_at((sx + tx) / 2, lane, e.get("label", "")); lane += 18
        elif layer[e["dst"]] > layer[e["src"]]:  # forward, orthogonal through the mid-gap
            sx, sy = x0 + bw, y0 + bh / 2; tx, ty = x1, y1 + bh / 2
            mx = sx + gx / 2 + (layer[e["dst"]] - layer[e["src"]] - 1) * (bw + gx) / 2
            k = used_mid.get(mx, 0); used_mid[mx] = k + 1; mx += (k % 3 - 1) * 14
            d.line([(sx, sy), (mx, sy), (mx, ty), (tx - 2, ty)], fill=MUTED, width=2); arrow((tx, ty), (1, 0))
            label_at(mx, (sy + ty) / 2 if abs(sy - ty) > 24 else sy - 16, e.get("label", ""))
        else:  # same column: vertical
            up = y1 < y0
            sx, sy = x0 + bw / 2, (y0 if up else y0 + bh); tx, ty = x1 + bw / 2, (y1 + bh if up else y1)
            d.line([(sx, sy), (tx, ty + (2 if up else -2))], fill=MUTED, width=2); arrow((tx, ty), (0, -1 if up else 1))
            label_at(sx + 50, (sy + ty) / 2, e.get("label", ""))
    Path(path).parent.mkdir(parents=True, exist_ok=True); im.save(path); return path


def _dash(d, a, b, seg=8):
    import math
    L = math.hypot(b[0] - a[0], b[1] - a[1]); n = max(1, int(L / seg))
    for i in range(0, n, 2):
        t0, t1 = i / n, min(1, (i + 1) / n)
        d.line([(a[0] + (b[0] - a[0]) * t0, a[1] + (b[1] - a[1]) * t0), (a[0] + (b[0] - a[0]) * t1, a[1] + (b[1] - a[1]) * t1)], fill=MUTED, width=2)


def render_chart(path, spec):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = ["#3b6ea5", "#c0504d", "#4c8c4a", "#8064a2", "#f79646"]
    xs = spec["x_labels"]; series = spec["series"]
    fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=150)
    n = len(series)
    if spec["kind"] == "bar":
        width = 0.7 / max(n, 1)
        for k, s in enumerate(series):
            pos = [i + (k - (n - 1) / 2) * width for i in range(len(xs))]
            bars = ax.bar(pos, s["values"], width=width * 0.92, color=colors[k % len(colors)], label=s["name"])
            for b, v in zip(bars, s["values"]):
                ax.text(b.get_x() + b.get_width() / 2, b.get_height(), f"{v:g}", ha="center", va="bottom", fontsize=7.5)
        ax.set_xticks(range(len(xs))); ax.set_xticklabels(xs, fontsize=8)
    else:
        for k, s in enumerate(series):
            ax.plot(range(len(xs)), s["values"], marker="o", color=colors[k % len(colors)], label=s["name"], lw=1.8)
            for i, v in enumerate(s["values"]):
                ax.annotate(f"{v:g}", (i, v), textcoords="offset points", xytext=(0, 6), ha="center", fontsize=7.5)
        ax.set_xticks(range(len(xs))); ax.set_xticklabels(xs, fontsize=8)
    th = spec.get("threshold")
    if th:
        ax.axhline(th["value"], color="#444444", ls="--", lw=1)
        ax.text(len(xs) - 0.5, th["value"], f" {th['label']}", fontsize=7.5, ha="right", va="bottom")
    ax.set_ylabel(spec.get("y_label", ""), fontsize=8.5)
    ax.set_title(spec["title"], fontsize=9.5, loc="left")
    ax.spines[["top", "right"]].set_visible(False); ax.tick_params(labelsize=8)
    if n > 1:
        ax.legend(fontsize=7.5, frameon=False)
    if spec.get("caption"):
        fig.text(0.01, 0.01, spec["caption"][:140], fontsize=7, color="#666666")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor="white"); plt.close(fig)
    return path
