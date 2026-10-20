"""Pure helpers of app.py / rows.py that need no running server (the app module is imported with local storage)."""
import os
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="mmeb-lb-test-")
os.environ.setdefault("STORAGE", "local")
os.environ.setdefault("LOCAL_STORAGE_DIR", _TMP)
os.environ.setdefault("ADMIN_PASSWORD", "test-password-for-unit-tests")

import app  # noqa: E402
from rows import DEFAULT_SETTING, SETTINGS, short_setting  # noqa: E402


class _Req:
    """Minimal stand-in for gr.Request: headers + client.host."""

    def __init__(self, host):
        self.headers = {}
        self.client = type("C", (), {"host": host})()


class HelperTest(unittest.TestCase):
    def test_short_setting(self):
        self.assertEqual(short_setting("Released retrieval 2B · k=100"), "2B · k=100")
        self.assertEqual(short_setting("Own retrieval"), "Own retrieval")
        self.assertEqual(len({short_setting(s) for s in SETTINGS}), len(SETTINGS))

    def test_widths_sum_to_100_for_both_layouts(self):
        for cols in (app.MAIN_COLS, [c for c in app.MAIN_COLS if c != "Setting"], app.TASK_COLS,
                     [c for c in app.TASK_COLS if c != "Setting"]):
            widths = app._widths(cols, app.MAIN_WEIGHTS if "Overall" in cols else app.TASK_WEIGHTS)
            self.assertEqual(len(widths), len(cols))
            self.assertAlmostEqual(sum(float(w.rstrip("%")) for w in widths), 100.0, places=1)
        self.assertEqual(len(app.PENDING_WIDTHS), len(app.PENDING_COLS))
        self.assertEqual(len(app.APPROVED_WIDTHS), len(app.APPROVED_COLS))

    def test_link_markdown_only_for_plain_urls(self):
        self.assertEqual(app._link_md("https://example.org/x"), "[link](https://example.org/x)")
        self.assertEqual(app._link_md('https://a.com) <img src=x onerror=alert(1)> [x](javascript:alert(1)'), "")
        self.assertEqual(app._link_md("javascript:alert(1)"), "")
        self.assertTrue(app.LINK_RE.match("https://arxiv.org/abs/1234.5678v2"))
        self.assertFalse(app.LINK_RE.match("https://a.com/<script>"))

    def test_admin_lockout_is_per_client(self):
        app._ADMIN_FAILS.clear()
        app.ADMIN_FAIL_DELAY_SECONDS = 0.0
        attacker, owner = _Req("203.0.113.9"), _Req("198.51.100.4")
        self.assertTrue(app._is_admin("test-password-for-unit-tests", owner))
        for _ in range(app.ADMIN_FAIL_LIMIT):
            self.assertFalse(app._is_admin("nope", attacker, delay=True))
        # the attacker's client is locked out, even with the right password ...
        self.assertFalse(app._is_admin("test-password-for-unit-tests", attacker))
        # ... but the owner (another client) is not
        self.assertTrue(app._is_admin("test-password-for-unit-tests", owner))
        self.assertEqual(len(app._ADMIN_FAILS), 1)
        app._ADMIN_FAILS.clear()

    def test_admin_actions_count_one_failure_per_request(self):
        app._ADMIN_FAILS.clear()
        app.ADMIN_FAIL_DELAY_SECONDS = 0.0
        req = _Req("203.0.113.10")
        for handler, args in ((app.admin_approve, ("x — y", "note")), (app.admin_reject, ("x — y", "reason")),
                              (app.admin_hide, ("x — y",)), (app.admin_unhide, ("x — y",)), (app.admin_refresh, ())):
            out = handler("wrong", *args, request=req)
            self.assertEqual(out[1], app.WRONG_PASSWORD)
            self.assertFalse(out[0]["visible"])
        self.assertEqual(app._ADMIN_FAILS[app._client_key(req)]["count"], 5)
        app._ADMIN_FAILS.clear()

    def test_rounding_is_half_up_from_exact_counts(self):
        self.assertEqual(app._round1("61.25"), "61.3")          # not banker's 61.2
        self.assertEqual(app._fmt(61.25), "61.3")
        self.assertEqual(app._fmt(None), "–")
        self.assertEqual(app._fmt_count({"correct": 49, "n": 80}, 61.25), "61.3")      # Sol 2B·k=10 Attribution
        self.assertEqual(app._fmt_count({"correct": 767, "n": 1613}, 47.55), "47.6")   # Gemini 3.8 Flash 2B·k=10
        self.assertEqual(app._fmt_count({"correct": 69, "n": 212}, 32.55), "32.5")     # 32.547 -> 32.5 (README)
        self.assertEqual(app._fmt_count(None, 53.45), "53.5")                           # fallback: stored value

    def test_seeded_rows_render_like_the_paper_tables(self):
        import json
        from pathlib import Path

        from seed_results import seed_rows

        baselines = os.environ.get("MMEB_BASELINES_METRICS")
        if not baselines or not os.path.exists(baselines):
            self.skipTest("set MMEB_BASELINES_METRICS to the baselines metrics.json")
        rows = seed_rows(json.loads(Path(baselines).read_text()))
        app.STORAGE.write_results(rows)
        try:
            main, task, _ = app.leaderboard_tables(DEFAULT_SETTING)
            by_model = {r[1]: r for r in main["value"].values.tolist()}
            self.assertEqual(by_model["Gemini 3.8 Flash"][3], "47.6")
            tasks = {r[1]: r for r in task["value"].values.tolist()}
            self.assertEqual(tasks["GPT-5.6 Sol"][task["headers"].index("Attribution")], "61.3")
            self.assertEqual(tasks["GPT-5.6 Sol"][task["headers"].index("Temporal")], "32.5")
        finally:
            app.STORAGE.write_results([])

    def test_setting_choices_keep_full_values(self):
        self.assertEqual([v for _, v in app.SETTING_CHOICES], SETTINGS)
        self.assertEqual(app.SETTING_CHOICES[0], ("2B · k=10", DEFAULT_SETTING))


if __name__ == "__main__":
    unittest.main()
