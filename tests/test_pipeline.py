"""Offline protocol and resume tests."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for running without pip install
from mmenterprise_bench.data import Dataset
from mmenterprise_bench.pipeline import HERE, Pipeline, atomic_json, load_config
from mmenterprise_bench.providers import Client, ProviderError, judge_body, reader_body

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")


class FakeClient:
    def __init__(self, failure=None):
        self.answers, self.judgments, self.failure = [], [], failure

    def check_credentials(self, spec):
        pass

    def answer(self, spec, body):
        self.answers.append(body)
        if self.failure:
            raise self.failure
        question = body["messages"][-1]["content"][-1]["text"]
        return {"answer": "42" if "value" in question else "Not established.", "finish_reason": "stop", "usage": {}}

    def judge(self, spec, body):
        self.judgments.append(body)
        fields = json.loads(body["messages"][-1]["content"])
        assert set(fields) == {"question", "reference_answer", "candidate_answer"}
        return {"correct": fields["reference_answer"] == fields["candidate_answer"], "reason": "match", "usage": {}}


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "dataset"
        (self.data / "media").mkdir(parents=True)
        (self.data / "media/image.png").write_bytes(PNG)
        units = [{"id": f"u{i:03}", "record_id": f"p01::R{i}", "order": i,
                  "timestamp": f"2026-01-01T00:{i%60:02}", "text": f"Record {i}", "image": None}
                 for i in range(100)]
        units[0]["image"] = "media/image.png"
        units[0]["image_sha256"] = hashlib.sha256(PNG).hexdigest()
        rows = [{"id": "p01::Q1", "project": "p01", "q": "What is the value?", "gold": {"answer": "42"},
                 "task": "Extraction", "answer_status": "answerable", "frozen_representative_retrieval_units": ["u000", "u050"]},
                {"id": "p01::Q2", "project": "p01", "q": "Who has the unavailable ID?", "gold": {"answer": "Not established."},
                 "task": "Unanswerability", "answer_status": "unanswerable", "frozen_representative_retrieval_units": []}]
        atomic_json(self.data / "corpus/units.json", units)
        atomic_json(self.data / "qa/rows.json", rows)
        for size in ("2b", "8b"):
            atomic_json(self.data / f"retrieval/ranks_{size}.json", {"hits": {
                row["id"]: [{"id": unit["id"], "rank": i + 1} for i, unit in enumerate(units)] for row in rows}})
        self.config = load_config(HERE / "configs/default.json", {"dataset": str(self.data), "output": str(self.root / "results"),
            "retrievers": ["2b", "8b"], "k": [10, 100], "concurrency": 4, "judge_concurrency": 2})

    def tearDown(self):
        self.temp.cleanup()

    def test_end_to_end_resume_and_metrics(self):
        client = FakeClient()
        runner = Pipeline(self.config, client)
        summaries, pending = runner.run()
        self.assertEqual(pending, 0)
        self.assertEqual(len(client.answers), 4)
        self.assertEqual(len(client.judgments), 2)
        self.assertTrue(all(row["accuracy"] == 100 for row in summaries))
        self.assertEqual(summaries[0]["evidence_recall"], 50)
        self.assertEqual(summaries[1]["evidence_recall"], 100)
        rerun = FakeClient()
        Pipeline(self.config, rerun).run()
        self.assertEqual((len(rerun.answers), len(rerun.judgments)), (0, 0))

    def test_answer_only_preserves_existing_judgments(self):
        Pipeline(self.config, FakeClient()).run()
        client = FakeClient()
        summaries, pending = Pipeline(self.config, client).run("answer")
        self.assertEqual(pending, 0)
        self.assertEqual((len(client.answers), len(client.judgments)), (0, 0))
        self.assertTrue(all(row["accuracy"] == 100 for row in summaries))

    def test_task_metadata_change_regroups_existing_results(self):
        Pipeline(self.config, FakeClient()).run()
        path = self.data / "qa/rows.json"
        rows = json.loads(path.read_text())
        rows[0]["task"] = "Interpretation"
        atomic_json(path, rows)
        runner = Pipeline(self.config, FakeClient())
        summaries = runner.metrics()
        self.assertEqual(summaries[0]["tasks"]["Interpretation"]["questions"], 1)
        self.assertEqual(summaries[0]["tasks"]["Extraction"]["questions"], 0)
        runner.run()
        self.assertEqual((len(runner.client.answers), len(runner.client.judgments)), (0, 0))

    def test_reader_payload_has_only_workplace_records_and_question(self):
        runner = Pipeline(self.config, FakeClient())
        row = copy.deepcopy(runner.rows[0])
        row["gold"]["answer"] = "SECRET_REFERENCE"
        row["evidence"] = ["SECRET_EVIDENCE"]
        row["task"] = "SECRET_TASK"
        selected = runner.dataset.selected(row, "2b", 10)
        body = reader_body(runner.dataset, row, selected, self.config["reader_specs"]["luna"], self.config["reader_system"])
        serialized = json.dumps(body)
        for forbidden in ("SECRET_REFERENCE", "SECRET_EVIDENCE", "SECRET_TASK"):
            self.assertNotIn(forbidden, serialized)
        content = body["messages"][-1]["content"]
        self.assertEqual(content[0]["text"], "Record 0")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[-1]["text"].startswith("Question: "))
        payload = json.loads(judge_body(row, "candidate", self.config["judge_spec"], self.config["judge_system"])["messages"][-1]["content"])
        self.assertEqual(set(payload), {"question", "reference_answer", "candidate_answer"})

    def test_cache_binds_input_pixels_model_and_prompts(self):
        runner = Pipeline(self.config, FakeClient())
        row = runner.rows[0]
        selected = runner.dataset.selected(row, "2b", 10)
        baseline = runner.answer_key(row, "luna", selected)
        selected[0]["text"] += " changed"
        self.assertNotEqual(baseline, runner.answer_key(row, "luna", selected))
        selected[0]["text"] = "Record 0"
        self.assertEqual(baseline, runner.answer_key(row, "luna", selected))
        selected[0].pop("image_sha256")
        (self.data / "media/image.png").write_bytes(PNG + b"changed")
        self.assertNotEqual(baseline, runner.answer_key(row, "luna", selected))
        (self.data / "media/image.png").write_bytes(PNG)
        self.config["reader_system"] += " changed"
        self.assertNotEqual(baseline, runner.answer_key(row, "luna", selected))
        self.config["reader_system"] = self.config["reader_system"].removesuffix(" changed")
        self.config["reader_specs"]["luna"]["model_id"] += "-different"
        self.assertNotEqual(baseline, runner.answer_key(row, "luna", selected))
        grade = runner.grade_key(row, "42")
        row["gold"]["answer"] = "43"
        self.assertNotEqual(grade, runner.grade_key(row, "42"))

    def test_transient_errors_remain_pending(self):
        summaries, pending = Pipeline(self.config, FakeClient(ProviderError("rate_limit", retryable=True))).run()
        self.assertEqual(pending, 8)
        self.assertTrue(all(row["accuracy"] is None and row["unsupported"] == 0 for row in summaries))
        summaries, pending = Pipeline(self.config, FakeClient()).run()
        self.assertEqual(pending, 0)
        self.assertTrue(all(row["accuracy"] == 100 for row in summaries))

    def test_billing_failure_stops_new_requests(self):
        client = FakeClient(ProviderError("insufficient_quota", status=429))
        summaries, pending = Pipeline(self.config, client).run()
        self.assertEqual(pending, 8)
        self.assertLessEqual(len(client.answers), self.config["concurrency"])
        self.assertTrue(all(row["accuracy"] is None for row in summaries))

    def test_confirmed_capacity_errors_count_as_incorrect(self):
        summaries, pending = Pipeline(self.config, FakeClient(ProviderError("context_length_exceeded", capacity=True, status=400))).run()
        self.assertEqual(pending, 0)
        self.assertTrue(all(row["accuracy"] == 0 and row["unsupported"] == 2 for row in summaries))
        self.config["capacity_errors_as_incorrect"] = False
        self.assertTrue(all(row["accuracy"] is None for row in Pipeline(self.config).metrics()))

    def test_changed_judge_invalidates_only_judgments(self):
        Pipeline(self.config, FakeClient()).run()
        self.config["judge_system"] += " Updated rubric."
        runner = Pipeline(self.config, FakeClient())
        self.assertTrue(all(row["accuracy"] is None for row in runner.metrics()))
        runner.run()
        self.assertEqual(len(runner.client.answers), 0)
        self.assertEqual(len(runner.client.judgments), 2)

    def test_judge_retries_invalid_boolean(self):
        client = Client(attempts=2, backoff=0)
        invalid = {"answer": '{"correct":"true","reason":"x"}', "usage": {}, "returned_model": "m"}
        valid = {"answer": '{"correct":true,"reason":"x"}', "usage": {}, "returned_model": "m"}
        with patch.object(client, "answer", side_effect=[invalid, valid]) as mocked:
            result = client.judge({}, {})
            self.assertIs(result["correct"], True)
            self.assertEqual(mocked.call_count, 2)

    def test_checksum_mismatch_and_path_escape_rejected(self):
        data = Dataset(self.data)
        (self.data / "media/image.png").write_bytes(PNG + b"invalid")
        with self.assertRaisesRegex(ValueError, "checksum"):
            data.validate()
        data.units["u000"]["image"] = "../outside.png"
        with self.assertRaisesRegex(ValueError, "escapes"):
            data.image_path(data.units["u000"])


if __name__ == "__main__":
    unittest.main()
