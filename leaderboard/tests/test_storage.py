import copy
import json
import os
import tempfile
import unittest
from datetime import timedelta

import rows as rows_mod
import storage as storage_mod
from storage import LocalStorage, make_submission_id, slugify, submission_timestamp, utc_now
from validate import validate_metrics

from tests.fixtures import SAMPLE_METRICS


def fresh_metrics():
    sample = copy.deepcopy(SAMPLE_METRICS)
    sample["judge"] = {"model": "gpt-5.6-luna", "pre_judged": False}
    return sample


def meta_for(model="Test Agent", email="someone@example.org", setting=rows_mod.DEFAULT_SETTING):
    metrics = fresh_metrics()
    return {
        "model": model, "organization": "Test Lab", "link": "https://example.org", "contact_email": email,
        "setting": setting, "judge": "gpt-5.6-luna", "notes": "", "submitted_at": "2026-01-01T00:00:00Z",
        "status": "pending", "admin_notes": "", "consent": True, "summary": validate_metrics(metrics),
        "predictions": None, "warnings": [],
    }, json.dumps(metrics)


class LocalStorageFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = LocalStorage(self.tmp.name)

    def _approve(self, sid):
        meta = self.store.get_meta(sid)
        summary = validate_metrics(self.store.get_metrics(sid), allow_pre_judged=True)
        row = rows_mod.build_row(row_id=sid, model=meta["model"], organization=meta["organization"],
                                 link=meta["link"], setting=meta["setting"], judge=meta["judge"],
                                 date="2026-01-02", source="community", summary=summary)
        self.store.append_result(row)
        self.store.update_meta(sid, {"status": "approved"})
        return row

    def test_submission_layout_and_predictions(self):
        meta, metrics_text = meta_for()
        fd, pred_path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as handle:
            handle.write('{"id": "a", "answer": "b"}\n')
        self.addCleanup(os.remove, pred_path)
        sid = self.store.create_submission(meta, metrics_text, pred_path)
        self.assertRegex(sid, r"^\d{8}T\d{6}Z_test-agent$")
        folder = os.path.join(self.tmp.name, "queue", "submissions", sid)
        self.assertEqual(sorted(os.listdir(folder)), ["meta.json", "metrics.json", "predictions.jsonl"])
        stored = self.store.get_meta(sid)
        self.assertEqual(stored["status"], "pending")
        self.assertEqual(stored["id"], sid)
        self.assertEqual(self.store.get_metrics(sid)["accuracy"], 52.63)
        self.assertEqual([m["id"] for m in self.store.list_submissions("pending")], [sid])

    def test_duplicate_ids_get_suffix(self):
        meta, metrics_text = meta_for()
        meta["id"] = "20260101T000000Z_test-agent"
        first = self.store.create_submission(dict(meta), metrics_text, None)
        second = self.store.create_submission(dict(meta), metrics_text, None)
        self.assertNotEqual(first, second)
        self.assertTrue(second.startswith(first))

    def test_approve_appends_result_and_marks_meta(self):
        meta, metrics_text = meta_for()
        sid = self.store.create_submission(meta, metrics_text, None)
        self.assertEqual(self.store.read_results(), [])
        row = self._approve(sid)
        results = self.store.read_results()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], sid)
        self.assertEqual(results[0]["overall"], 52.63)
        self.assertEqual(results[0]["categories"], {"ER": 58.62, "CR": 34.43, "UNS": 65.52})
        self.assertEqual(len(results[0]["tasks"]), 8)
        self.assertEqual(results[0]["source"], "community")
        self.assertNotIn("contact_email", results[0])
        self.assertEqual(self.store.get_meta(sid)["status"], "approved")
        self.assertEqual(self.store.list_submissions("pending"), [])
        # approving again replaces rather than duplicates the row
        self.store.append_result(row)
        self.assertEqual(len(self.store.read_results()), 1)

    def test_reject_keeps_results_empty(self):
        meta, metrics_text = meta_for()
        sid = self.store.create_submission(meta, metrics_text, None)
        self.store.update_meta(sid, {"status": "rejected", "admin_notes": "not a fresh run"})
        self.assertEqual(self.store.read_results(), [])
        stored = self.store.get_meta(sid)
        self.assertEqual(stored["status"], "rejected")
        self.assertEqual(stored["admin_notes"], "not a fresh run")
        self.assertEqual([m["id"] for m in self.store.list_submissions("rejected")], [sid])

    def test_hide_and_unhide(self):
        meta, metrics_text = meta_for()
        sid = self.store.create_submission(meta, metrics_text, None)
        self._approve(sid)
        self.assertEqual(len(self.store.visible_results()), 1)
        self.assertTrue(self.store.set_result_hidden(sid, True))
        self.assertEqual(self.store.visible_results(), [])
        self.assertEqual(len(self.store.read_results()), 1)
        self.assertTrue(self.store.set_result_hidden(sid, False))
        self.assertEqual(len(self.store.visible_results()), 1)
        self.assertFalse(self.store.set_result_hidden("nope", True))

    def test_rate_limit_counts_recent_submissions_per_contact(self):
        meta, metrics_text = meta_for(email="Spam@Example.org")
        for _ in range(3):
            self.store.create_submission(dict(meta), metrics_text, None)
        old = dict(meta, id=make_submission_id("old", utc_now() - timedelta(days=2)))
        self.store.create_submission(old, metrics_text, None)
        self.assertEqual(self.store.count_recent_by_contact("spam@example.org"), 3)
        self.assertEqual(self.store.count_recent_by_contact("other@example.org"), 0)

    def test_results_roundtrip_and_sorting_helpers(self):
        rows = [{"id": "a", "overall": 1.0}, {"id": "b", "overall": 2.0, "hidden": True}]
        self.store.write_results(rows)
        self.assertEqual(self.store.read_results(), rows)
        self.assertEqual([r["id"] for r in self.store.visible_results()], ["a"])
        self.assertEqual(storage_mod.results_from_jsonl(storage_mod.results_to_jsonl(rows)), rows)

    def test_helpers(self):
        self.assertEqual(slugify("GPT-5.6 Sol  (test)!"), "gpt-5-6-sol-test")
        self.assertEqual(slugify(""), "entry")
        when = submission_timestamp("20260102T030405Z_x")
        self.assertEqual((when.year, when.hour, when.second), (2026, 3, 5))
        self.assertIsNone(submission_timestamp("garbage"))


class GetStorageTest(unittest.TestCase):
    def test_local_backend_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["STORAGE"] = "local"
            os.environ["LOCAL_STORAGE_DIR"] = tmp
            try:
                store = storage_mod.get_storage()
            finally:
                os.environ.pop("STORAGE")
                os.environ.pop("LOCAL_STORAGE_DIR")
            self.assertIsInstance(store, LocalStorage)
            self.assertTrue(os.path.isdir(os.path.join(tmp, "queue", "submissions")))


if __name__ == "__main__":
    unittest.main()
