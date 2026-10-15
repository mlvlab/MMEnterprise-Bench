"""Small helpers: world-clock arithmetic, slugs, ids, text search."""
from __future__ import annotations
import re, json, unicodedata
from datetime import datetime, timedelta

FMT = "%Y-%m-%dT%H:%M"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def parse(t: str) -> datetime:
    return datetime.strptime(t, FMT)


def fmt(d: datetime) -> str:
    return d.strftime(FMT)


def add_min(t: str, minutes: int) -> str:
    return fmt(parse(t) + timedelta(minutes=minutes))


def day_of(t: str) -> str:
    return t[:10]


def dayname(t: str) -> str:
    return DAYS[parse(t).weekday()]


def human(t: str) -> str:
    d = parse(t)
    return f"{DAYS[d.weekday()]} {d.strftime('%b')} {d.day} {d.strftime('%H:%M')}"


def slug(s: str, n: int = 40) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s[:n] or "x"


def num_strings(s: str) -> list[str]:
    """All numeric tokens in a string, e.g. '33.3', '1,170', '5e-7'."""
    return re.findall(r"\d(?:[\d,]*\d)?(?:\.\d+)?(?:e-?\d+)?", s)  # tokens end in a digit: no trailing "." or "," from punctuation


_SPACES = re.compile("[\u00a0\u202f\u2007\u2009\u2002\u2003\u2008\u200a\u3000]")


def plain_spaces(s: str) -> str:
    """PDF text keeps numbers and units apart with non-breaking / thin spaces ('1.6\u00a0million'); compare on plain spaces."""
    return _SPACES.sub(" ", s)


def contains_value(text: str, value: str) -> bool:
    """Loose containment for a value string: exact, or numeric with/without thousands separators / trailing zeros."""
    if not value:
        return False
    text, value = plain_spaces(text), plain_spaces(value).strip()
    if value in text:
        return True
    v = value.replace(",", "")
    if v in text.replace(",", ""):
        return True
    try:
        f = float(v)
        for cand in {f"{f:g}", f"{f:.1f}", f"{f:.2f}", str(int(f)) if f == int(f) else ""}:
            if cand and re.search(rf"(?<![\d.]){re.escape(cand)}(?![\d.])", text.replace(",", "")):
                return True
    except ValueError:
        pass
    return False


SEED_WORDS = ["paper", "arxiv", "conference", "camera-ready", "submission", "reviewer", "publication", "eccv", "cvpr", "neurips", "iclr", "aaai", "acl"]
# pipeline-internal identifiers that must never surface in world text, gold answers or questions
INTERNAL_ID_RE = re.compile(r"\b[FVKC]\d{3}\b|(?<![A-Za-z])X-\d\d\b|\bE-\d\d\b|\bev\d{3}\b|\bch\d{3}\b|\b(?:fig|tab)\d+\b|\btable_tab\d|\bchart_[A-Z]|\barc_A-\d\d|\b(?:report|deck)_v\d\b")
INTERNAL_WORDS = ["Rendered from", "main_results", "ablation_progression", "table_draft", "image_only", "invented fact", "invented item", "invented measurement"]


def bad_words(text: str) -> list[str]:
    """Seed/publication vocabulary, publication-style numbering and internal ids in a piece of world text."""
    low = text.lower()
    bad = [w for w in SEED_WORDS if re.search(rf"\b{re.escape(w)}\b", low)]
    if INTERNAL_ID_RE.search(text): bad.append("internal ids such as F001 / X-01 / E-01 / ev012 / fig3 / *_draft_* (use names and values instead)")
    if re.search(r"\b(Fig\.?|Figure|Tab\.?|Table)\s?\d+\b", text): bad.append("publication-style numbering such as 'Fig. 2' or 'Table 3' (describe the image or document by its content instead)")
    for w in INTERNAL_WORDS:
        if w.lower() in low: bad.append(f"the internal label '{w}'")
    if re.search(r"\{\{\w+\}\}", text): bad.append("an unfilled placeholder like {{customer}}")
    return bad


_COLLECTION_RE = re.compile(r"\b(benchmarks|datasets|tasks|suites?|across|several|multiple|three|four|five|six|all)\b", re.I)


def dataset_mismatch(text: str, ds_vals: dict, allowed=()) -> list[str]:
    """Sentences that name exactly one dataset/group must not quote a value that belongs to another one
    (ds_vals: dataset → set of its value strings). Values the plan requires verbatim in this text are exempt: the
    plan may legitimately put two series in one sentence."""
    if not ds_vals: return []
    ds_vals = {d: {tok for v in vals for tok in num_strings(str(v))} for d, vals in ds_vals.items()}  # compare numeric tokens: '18.5%' and '1,478' are quoted as 18.5 and 1478
    allv = set().union(*ds_vals.values()) - {n for a in allowed for n in num_strings(str(a))} - {str(a) for a in allowed}
    out = []
    def _words(d): return [w for w in re.findall(r"[a-z][a-z-]{5,}", d.lower()) if w not in ("average", "actual", "percentage", "projections", "comparison", "changes", "outlook", "budget")]
    for seg in re.split(r"(?<=[.;\n])\s+|\n|\"\s*[\]\[,:]+\s*\"", text):  # sentence ends, and JSON cell / key boundaries when the blob is a raw dump
        low = seg.lower()
        # a series counts as named when its label appears, or a distinctive word of it does (summary sentences combine
        # series: "GDP grows 1.8 percent while the ten-year deficit reaches 23.1 trillion" names two series, not one)
        named = [d for d in ds_vals if d and (d.lower() in low or any(w in low for w in _words(d)))]
        if len(named) != 1: continue
        d = named[0]
        for n in num_strings(seg):
            if len(n) < 3 and "." not in n: continue
            if n in allv and n not in ds_vals[d]:
                owners = [o for o, vs in ds_vals.items() if n in vs]
                if any(_COLLECTION_RE.search(o) for o in owners): continue  # a value from a multi-benchmark row ("four long video benchmarks") is legitimately quoted next to one member benchmark
                out.append(f"'{n}' is quoted for {d} but it is not a {d} value")
    return out


def jdump(o) -> str:
    return json.dumps(o, ensure_ascii=False, indent=1)


class Ids:
    def __init__(self, prefix: str, start: int = 1):
        self.prefix = prefix; self.n = start - 1

    def next(self) -> str:
        self.n += 1
        return f"{self.prefix}{self.n:03d}"


def value_free_label(text: str, n: int = 12) -> str:
    """A label with every number (and its unit) removed and leading stop words trimmed, for names that must not carry a value."""
    t = re.sub(r"[£$€]?\d[\d,.]*\s*(%|percent|million|billion|trillion|GWh|tonnes|minutes?|hours?|days?|per cent)?", " ", text or "")
    words = re.sub(r"\s{2,}", " ", t).strip().split()
    while words and words[0].lower() in ("of", "the", "a", "an", "to", "at", "in", "for", "per", "and", "or"): words = words[1:]
    return " ".join(words[:n]).rstrip(",;:")


def unique_labels(labels: list[str]) -> list[str]:
    """Append a short ordinal to labels that collide after normalisation."""
    seen, out = {}, []
    for l in labels:
        k = l.lower()
        seen[k] = seen.get(k, 0) + 1
        out.append(l if seen[k] == 1 else f"{l} ({seen[k]})")
    return out


def run_id_re(prefix: str = "R"):
    """Regex for item ids of one archetype (R-03 in a research lab, F-03 in an analysis firm)."""
    return re.compile(rf"\b{re.escape(prefix)}-\d\d\b")
