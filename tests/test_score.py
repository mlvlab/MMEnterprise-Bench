"""Bring-your-own-agent scorer: judged, pre-judged, missing predictions, recall, outputs."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for running without pip install
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import make_dataset
from mock_judge import MARKER, start
from mmenterprise_bench import Dataset, Judge, score_predictions
from mmenterprise_bench.score import load_predictions, metrics_markdown


class FakeClient:
    """Judge client that marks a candidate correct when it equals the reference (no HTTP)."""
    def __init__(self):
        self.bodies = []

    def judge(self, spec, body):
        self.bodies.append(body)
        fields = json.loads(body["messages"][-1]["content"])
        assert set(fields) == {"question", "reference_answer", "candidate_answer"}
        return {"correct": fields["candidate_answer"] == fields["reference_answer"], "reason": "fake", "usage": {}}


def write_jsonl(path, items):
    Path(path).write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dataset = Dataset(make_dataset(self.root / "dataset"))
        self.out = self.root / "out"

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_predictions_count_as_incorrect(self):
        predictions = self.root / "p.jsonl"
        write_jsonl(predictions, [{"id": "p01::Q1", "answer": "42", "note": "kept"},
                                  {"id": "p01::Q3", "answer": "Run 8"},
                                  {"id": "p99::X", "answer": "unknown id"}])
        client = FakeClient()
        metrics = score_predictions(self.dataset, predictions, self.out, judge=Judge(client=client, model="fake"), quiet=True)
        self.assertEqual((metrics["n"], metrics["submitted"], metrics["missing"], metrics["correct"]), (4, 2, 2, 1))
        self.assertEqual(metrics["accuracy"], 25.0)
        self.assertEqual(metrics["observed_accuracy"], 50.0)
        self.assertEqual(metrics["unknown_prediction_ids"], ["p99::X"])
        self.assertEqual(metrics["tasks"]["Extraction"]["accuracy"], 100.0)
        self.assertEqual(metrics["tasks"]["Temporal"]["accuracy"], 0.0)
        self.assertEqual(metrics["categories"]["Unanswerability"]["missing"], 1)
        self.assertEqual(metrics["answerability"]["answerable"]["n"], 3)
        self.assertEqual(metrics["evidence_modality"]["multi"]["correct"], 1)
        self.assertEqual(metrics["evidence_count"]["3+"]["missing"], 1)
        self.assertIsNone(metrics["retrieval"])
        self.assertEqual(len(client.bodies), 2)
        self.assertTrue(metrics["complete"])
        # outputs
        graded = [json.loads(line) for line in (self.out / "graded.jsonl").read_text().splitlines()]
        self.assertEqual([g["id"] for g in graded], [row["id"] for row in self.dataset.rows])
        self.assertEqual(graded[0]["note"], "kept")
        self.assertEqual(graded[1]["status"], "missing")
        self.assertIs(graded[1]["correct"], False)
        self.assertEqual(json.loads((self.out / "metrics.json").read_text())["accuracy"], 25.0)
        text = (self.out / "metrics.md").read_text()
        self.assertIn("**Accuracy: 25.0%** (1 / 4 questions; 2 missing count as incorrect", text)
        self.assertIn("| Extraction | 1 | 1 | 1 | 100.0 | 100.0 |", text)
        self.assertIn("### Per category", text)
        self.assertEqual(len(list((self.out / "cache/judgments").glob("*.json"))), 2)
        # rerun hits the cache
        client2 = FakeClient()
        score_predictions(self.dataset, predictions, self.out, judge=Judge(client=client2, model="fake"), quiet=True)
        self.assertEqual(len(client2.bodies), 0)
        # another judge model is a different cache key
        client3 = FakeClient()
        score_predictions(self.dataset, predictions, self.out, judge=Judge(client=client3, model="other"), quiet=True)
        self.assertEqual(len(client3.bodies), 2)

    def test_pre_judged_mode_needs_no_judge(self):
        predictions = self.root / "p.jsonl"
        write_jsonl(predictions, [{"id": "p01::Q1", "answer": "x", "correct": True, "judge_reason": "ok"},
                                  {"id": "p01::Q2", "answer": "", "status": "unsupported"},
                                  {"id": "p01::Q3", "answer": "y", "correct": False},
                                  {"id": "p02::Q4", "answer": "z", "correct": True}])
        metrics = score_predictions(self.dataset, predictions, self.out, pre_judged=True, quiet=True)
        self.assertEqual((metrics["correct"], metrics["accuracy"]), (2, 50.0))
        self.assertEqual(metrics["statuses"]["unsupported"], 1)
        self.assertTrue(metrics["judge"]["pre_judged"])
        write_jsonl(predictions, [{"id": "p01::Q1", "answer": "x"}])
        with self.assertRaisesRegex(ValueError, "pre-judged"):
            score_predictions(self.dataset, predictions, None, pre_judged=True, quiet=True)
        with self.assertRaisesRegex(ValueError, "judge is required"):
            score_predictions(self.dataset, predictions, None, quiet=True)

    def test_retrieval_recall_from_retrieved_unit_ids(self):
        predictions = {"p01::Q1": {"id": "p01::Q1", "answer": "42", "retrieved_unit_ids": ["u000", "u999"]},
                       "p01::Q3": {"id": "p01::Q3", "answer": "Run 7", "retrieved_unit_ids": ["u007"]},
                       "p01::Q2": {"id": "p01::Q2", "answer": "Not established.", "retrieved_unit_ids": ["u001"]}}
        metrics = score_predictions(self.dataset, predictions, None, judge=Judge(client=FakeClient()), retriever="8b", k=10, quiet=True)
        recall = metrics["retrieval"]
        self.assertEqual(recall["answerable_questions"], 3)          # unanswerable excluded
        self.assertEqual(recall["questions_with_retrieved_ids"], 2)   # Q4 missing counts as 0
        self.assertEqual(recall["evidence_recall"], 50.0)             # (0.5 + 1 + 0) / 3
        self.assertEqual(recall["observed_evidence_recall"], 75.0)
        self.assertEqual(recall["complete_evidence_questions"], 1)
        self.assertEqual(recall["micro_evidence_recall"], 33.33)     # 2 of 6 gold units (Q4 missing)
        self.assertEqual(metrics["reference_retrieval"]["retriever"], "8b")
        self.assertEqual(metrics["correct"], 3)
        self.assertIn("Evidence recall of the submitted", metrics_markdown(metrics))

    def test_invalid_predictions_file(self):
        predictions = self.root / "p.jsonl"
        write_jsonl(predictions, [{"id": "p01::Q1", "answer": "a"}, {"id": "p01::Q1", "answer": "b"}])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_predictions(predictions)
        predictions.write_text('{"answer": "no id"}\n')
        with self.assertRaisesRegex(ValueError, "string 'id'"):
            load_predictions(predictions)
        write_jsonl(predictions, [{"id": "p01::Q1", "answer": 42}])
        metrics = score_predictions(self.dataset, predictions, None, judge=Judge(client=FakeClient()), quiet=True)
        self.assertEqual(metrics["statuses"]["invalid"], 1)

    def test_limit_and_question_ids(self):
        predictions = {"p01::Q1": {"id": "p01::Q1", "answer": "42"}}
        metrics = score_predictions(self.dataset, predictions, None, judge=Judge(client=FakeClient()), limit=2, quiet=True)
        self.assertEqual((metrics["n"], metrics["accuracy"]), (2, 50.0))
        metrics = score_predictions(self.dataset, predictions, None, judge=Judge(client=FakeClient()), question_ids=["p01::Q1"], quiet=True)
        self.assertEqual((metrics["n"], metrics["accuracy"]), (1, 100.0))


class MockHttpJudgeTests(unittest.TestCase):
    """End to end over HTTP with tests/mock_judge.py (correct iff the answer contains the marker)."""

    @classmethod
    def setUpClass(cls):
        cls.server, cls.base = start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_score_over_http_and_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dataset = Dataset(make_dataset(root / "dataset"))
            predictions = root / "p.jsonl"
            write_jsonl(predictions, [{"id": "p01::Q1", "answer": "42 " + MARKER}, {"id": "p01::Q2", "answer": "Not established."},
                                      {"id": "p01::Q3", "answer": "wrong"}])
            judge = Judge(model="mock", base_url=self.base, concurrency=2)
            metrics = score_predictions(dataset, predictions, root / "out", judge=judge, quiet=True)
            self.assertEqual((metrics["correct"], metrics["missing"], metrics["accuracy"]), (2, 1, 50.0))
            self.assertEqual(metrics["judge"]["api_calls"], 3)
            sent = self.server.received[-1]
            self.assertEqual(sent["model"], "mock")
            self.assertEqual(sent["response_format"], {"type": "json_object"})
            self.assertEqual(set(json.loads(sent["messages"][-1]["content"])), {"question", "reference_answer", "candidate_answer"})
            judge = Judge(model="mock", base_url=self.base)
            metrics = score_predictions(dataset, predictions, root / "out", judge=judge, quiet=True)
            self.assertEqual((metrics["judge"]["api_calls"], metrics["judge"]["cache_hits"]), (0, 3))

    def test_hosted_openai_requires_key(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                Judge().check_credentials()
        Judge(base_url=self.base).check_credentials()   # a local endpoint may run without a key


if __name__ == "__main__":
    unittest.main()
