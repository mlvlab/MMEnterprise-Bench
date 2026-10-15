"""Pillow drawing with deterministic, cmap-checked fallback for missing glyphs.

Supported text goes through Pillow unchanged, preserving DejaVu Sans kerning and
pixels. Missing glyphs use DejaVu Serif, then Matplotlib's bundled STIXGeneral
(regular faces, including in bold text).
Measurements and drawing share the same runs and baseline. Source strings are
never normalized or substituted. Unsupported glyphs fail before any text is drawn.
"""
from functools import lru_cache
from importlib.util import find_spec
import os
from pathlib import Path
import unicodedata

from fontTools.ttLib import TTFont
from PIL import ImageDraw, ImageFont


FALLBACK_FONT = str(Path(find_spec("matplotlib").origin).parent / "mpl-data/fonts/ttf/DejaVuSerif.ttf")


@lru_cache(maxsize=1)
def fallback_fonts():
    # Resolve package data without importing matplotlib or creating its cache.
    spec = find_spec("matplotlib")
    if spec is None or spec.origin is None:
        raise RuntimeError("Glyph fallback requires Matplotlib's bundled STIXGeneral.ttf")
    stix = Path(spec.origin).parent / "mpl-data/fonts/ttf/STIXGeneral.ttf"
    return (FALLBACK_FONT, str(stix))


@lru_cache(maxsize=16)
def _coverage(path):
    with TTFont(path, lazy=True) as face:
        return frozenset(cp for cp, name in (face.getBestCmap() or {}).items()
                         if name != ".notdef")


@lru_cache(maxsize=128)
def _fallback(path, size, layout_engine):
    return ImageFont.truetype(path, size, layout_engine=layout_engine)


def _runs(text, font):
    if not isinstance(text, str):
        raise TypeError("Glyph-safe rendering requires Unicode text")
    path = getattr(font, "path", None)
    if not isinstance(path, (str, bytes, os.PathLike)):
        raise TypeError("Glyph-safe rendering requires a filesystem-backed TrueType font")
    primary = _coverage(os.fsdecode(path))
    if all(ord(ch) in primary or ch == "\n" for ch in text):
        return [(text, font)]
    alternatives = [(path, _coverage(path)) for path in fallback_fonts()]
    # Keep combining marks with their base character when a fallback is needed.
    clusters = []
    for ch in text:
        if clusters and unicodedata.category(ch) in {"Mn", "Me"}:
            clusters[-1] += ch
        else:
            clusters.append(ch)
    result = []
    for cluster in clusters:
        codepoints = {ord(ch) for ch in cluster if ch != "\n"}
        if codepoints <= primary:
            face = font
        else:
            for fallback_path, supported in alternatives:
                if codepoints <= supported:
                    face = _fallback(fallback_path, font.size, font.layout_engine)
                    break
            else:
                missing = codepoints - primary
                for _, supported in alternatives:
                    missing -= supported
                names = ", ".join(f"U+{cp:04X}" for cp in sorted(missing or codepoints))
                attempted = ", ".join([os.fsdecode(path), *fallback_fonts()])
                raise ValueError(f"Cannot render glyph/combining cluster {names}: "
                                 f"not supported by {attempted}")
        if result and result[-1][1] is face:
            result[-1] = (result[-1][0] + cluster, face)
        else:
            result.append((cluster, face))
    if "\n" in text:
        raise ValueError("Multiline glyph fallback is unsupported; split text into lines before drawing")
    return result


class GlyphSafeDraw(ImageDraw.ImageDraw):
    """Shared drawing surface for generated tables, diagrams and document pages.

Fallback supports horizontal text with ascender/baseline anchors (la/ma/ra and
ls/ms/rs). Other layouts fail explicitly rather than silently misplacing glyphs.
Multiline fallback must be split into individual lines by the caller. All layouts
remain delegated unchanged when the primary font covers the text.
"""

    def _layout(self, xy, runs, font, anchor, direction, features, language,
                embedded_color):
        anchor = anchor or "la"
        if direction not in (None, "ltr") or anchor not in {"la", "ma", "ra", "ls", "ms", "rs"}:
            raise ValueError("Glyph fallback supports horizontal ascender/baseline anchors only")
        lengths = [super(GlyphSafeDraw, self).textlength(
            text, font=face, direction=direction, features=features,
            language=language, embedded_color=embedded_color) for text, face in runs]
        x, y = xy
        x -= sum(lengths) * {"l": 0, "m": 0.5, "r": 1}[anchor[0]]
        baseline = y + font.getmetrics()[0] if anchor[1] == "a" else y
        positioned = []
        for (text, face), length in zip(runs, lengths):
            positioned.append(((x, baseline), text, face))
            x += length
        return positioned

    def textlength(self, text, font=None, direction=None, features=None,
                   language=None, embedded_color=False, *, font_size=None):
        font = font or self._getfont(font_size)
        runs = _runs(text, font)
        options = dict(direction=direction, features=features, language=language,
                       embedded_color=embedded_color)
        if len(runs) == 1 and runs[0][1] is font:
            return super().textlength(text, font=font, **options)
        if direction not in (None, "ltr"):
            raise ValueError("Glyph fallback supports horizontal text only")
        return sum(super(GlyphSafeDraw, self).textlength(part, font=face, **options)
                   for part, face in runs)

    def text(self, xy, text, fill=None, font=None, anchor=None, spacing=4,
             align="left", direction=None, features=None, language=None,
             stroke_width=0, stroke_fill=None, embedded_color=False, *args, **kwargs):
        font = font or self._getfont(kwargs.get("font_size"))
        runs = _runs(text, font)
        options = dict(fill=fill, spacing=spacing, align=align, direction=direction,
                       features=features, language=language, stroke_width=stroke_width,
                       stroke_fill=stroke_fill, embedded_color=embedded_color)
        if len(runs) == 1 and runs[0][1] is font:
            return super().text(xy, text, font=font, anchor=anchor, *args, **options, **kwargs)
        positioned = self._layout(xy, runs, font, anchor, direction, features,
                                  language, embedded_color)
        for position, part, face in positioned:
            super().text(position, part, font=face, anchor="ls", *args, **options, **kwargs)

    def textbbox(self, xy, text, font=None, anchor=None, spacing=4, align="left",
                 direction=None, features=None, language=None, stroke_width=0,
                 embedded_color=False, *, font_size=None):
        font = font or self._getfont(font_size)
        runs = _runs(text, font)
        options = dict(spacing=spacing, align=align, direction=direction,
                       features=features, language=language, stroke_width=stroke_width,
                       embedded_color=embedded_color)
        if len(runs) == 1 and runs[0][1] is font:
            return super().textbbox(xy, text, font=font, anchor=anchor, **options)
        positioned = self._layout(xy, runs, font, anchor, direction, features,
                                  language, embedded_color)
        boxes = [super(GlyphSafeDraw, self).textbbox(position, part, font=face,
                 anchor="ls", **options) for position, part, face in positioned]
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))
