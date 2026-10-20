"""Static leaderboard page: index.html parses, references only existing local assets, and the snapshot builder
drops hidden rows and contact fields."""
import json
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
STATIC = HERE / "static"
sys.path.insert(0, str(HERE))

import deploy_static  # noqa: E402

EMOJI_RANGES = ((0x1F300, 0x1FAFF), (0x2600, 0x27BF), (0x1F000, 0x1F2FF))


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs, self.open_tags, self.errors = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag not in ("meta", "link", "img", "br", "hr", "input", "source", "path", "circle", "ellipse"):
            self.open_tags.append(tag)
        for key, value in attrs:
            if key in ("src", "href") and value:
                self.refs.append(value)

    def handle_endtag(self, tag):
        if self.open_tags and self.open_tags[-1] == tag:
            self.open_tags.pop()
        elif tag in self.open_tags:
            self.errors += 1


class StaticPageTest(unittest.TestCase):
    def setUp(self):
        self.html = (STATIC / "index.html").read_text(encoding="utf-8")

    def test_parses_and_local_assets_exist(self):
        p = _Collector()
        p.feed(self.html)
        p.close()
        self.assertEqual(p.errors, 0, "mismatched closing tags")
        self.assertEqual(p.open_tags, [], f"unclosed tags: {p.open_tags}")
        for ref in p.refs:
            if ref.startswith(("http://", "https://", "data:", "#", "mailto:")):
                continue
            path = STATIC / ref.split("?")[0].split("#")[0]
            self.assertTrue(path.exists() or ref == "results.snapshot.jsonl", f"missing local asset {ref}")
        self.assertIn("<title>MMEnterprise-Bench Leaderboard</title>", self.html)

    def test_no_emoji_in_html(self):
        for ch in self.html:
            cp = ord(ch)
            self.assertFalse(any(lo <= cp <= hi for lo, hi in EMOJI_RANGES), f"emoji U+{cp:04X} in index.html")

    def test_vocabulary_matches_rows_py(self):
        from rows import ALL_SETTINGS, DEFAULT_SETTING, SETTINGS
        from validate import TASKS
        for s in SETTINGS + [ALL_SETTINGS, DEFAULT_SETTING]:
            self.assertIn(json.dumps(s, ensure_ascii=False), self.html)
        for t in TASKS:
            self.assertIn(json.dumps(t), self.html)

    def test_embed_is_gated_behind_space_probe(self):
        # the iframe must not carry a src until the Hub API confirms the Space is reachable
        self.assertIn('id="submit-frame" data-src="https://happy8825-mmenterprise-bench-leaderboard.hf.space/', self.html)
        self.assertNotIn('<iframe src=', self.html)
        self.assertIn("api/spaces/happy8825/mmenterprise-bench-leaderboard", self.html)
        self.assertIn("function isHidden(", self.html)

    def test_readme_front_matter(self):
        text = (STATIC / "README.md").read_text(encoding="utf-8")
        for line in ("sdk: static", "app_file: index.html", "title: MMEnterprise-Bench Leaderboard", "license: mit"):
            self.assertIn(line, text)

    def test_snapshot_drops_hidden_and_contact(self):
        text = "\n".join([
            json.dumps({"id": "a", "hidden": False, "contact_email": "x@y.z", "model": "A", "overall": 1.0}),
            json.dumps({"id": "b", "hidden": True, "model": "B"}),
            "not json",
            json.dumps([1, 2]),
            json.dumps({"id": "c", "meta": {"Contact": "p", "keep": 1}}),
            json.dumps({"id": "d", "hidden": 1}),
            json.dumps({"id": "e", "hidden": "true"}),
            json.dumps({"id": "f", "hidden": " Yes "}),
            json.dumps({"id": "g", "hidden": "false"}),
            json.dumps({"id": "h", "hidden": 0}),
            json.dumps({"id": "i", "hidden": None}),
        ])
        rows, hidden, bad = deploy_static.build_snapshot(text)
        self.assertEqual([r["id"] for r in rows], ["a", "c", "g", "h", "i"])
        self.assertEqual((hidden, bad), (4, 2))
        self.assertNotIn("contact_email", rows[0])
        self.assertEqual(rows[1]["meta"], {"keep": 1})


if __name__ == "__main__":
    unittest.main()
