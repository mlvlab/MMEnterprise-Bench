import copy
import json
import os
import tempfile
import unittest

from validate import (N_QUESTIONS, ValidationError, load_metrics_bytes, validate_metrics, validate_predictions)

from tests.fixtures import SAMPLE_METRICS


def fresh_sample():
    """The sample scorer output, as a fresh judge run (the shipped sample is a pre-judged re-aggregation)."""
    sample = copy.deepcopy(SAMPLE_METRICS)
    sample["judge"] = {"model": "gpt-5.6-luna", "base_url": "https://api.openai.com/v1", "pre_judged": False,
                       "api_calls": 1613, "cache_hits": 0}
    return sample


class ValidateMetricsTest(unittest.TestCase):
    def test_accepts_sample(self):
        summary = validate_metrics(fresh_sample())
        self.assertEqual(summary["overall"], 52.63)
        self.assertEqual(summary["correct"], 849)
        self.assertEqual(summary["missing"], 0)
        self.assertEqual(summary["categories"], {"ER": 58.62, "CR": 34.43, "UNS": 65.52})
        self.assertEqual(summary["tasks"]["Extraction"], 67.54)
        self.assertEqual(len(summary["tasks"]), 8)
        self.assertEqual(summary["judge_model"], "gpt-5.6-luna")
        self.assertFalse(summary["pre_judged"])
        self.assertIsNone(summary["retrieval_recall"])

    def test_accepts_sample_bytes_roundtrip(self):
        data = json.dumps(fresh_sample()).encode("utf-8")
        self.assertEqual(validate_metrics(load_metrics_bytes(data))["overall"], 52.63)

    def test_rejects_pre_judged_sample_unless_overridden(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(copy.deepcopy(SAMPLE_METRICS))
        self.assertIn("pre_judged", str(ctx.exception))
        summary = validate_metrics(copy.deepcopy(SAMPLE_METRICS), allow_pre_judged=True)
        self.assertTrue(summary["pre_judged"])

    def test_rejects_wrong_category_or_answerability_n(self):
        sample = fresh_sample()
        sample["categories"]["Evidence Reasoning"]["n"] = 1000
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("categories.Evidence Reasoning.n", str(ctx.exception))
        sample = fresh_sample()
        sample["answerability"]["answerable"]["n"] = 1500
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("answerability.answerable.n", str(ctx.exception))

    def test_rejects_inconsistent_evidence_groups(self):
        sample = fresh_sample()
        sample["evidence_modality"]["text"]["correct"] = 10
        sample["evidence_modality"]["text"]["accuracy"] = round(100 * 10 / 693, 2)
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("evidence_modality", str(ctx.exception))
        sample = fresh_sample()
        sample["evidence_count"]["3+"]["n"] = 300
        sample["evidence_count"]["3+"]["submitted"] = 300
        sample["evidence_count"]["3+"]["accuracy"] = round(100 * 75 / 300, 2)
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("sum of 'evidence_count' n", str(ctx.exception))

    def test_deeply_nested_json_is_rejected_cleanly(self):
        data = ("[" * 200000).encode("utf-8")
        with self.assertRaises(ValidationError) as ctx:
            load_metrics_bytes(data)
        self.assertIn("not valid", str(ctx.exception))
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as handle:
            handle.write('{"id": "a", "answer": "b"}\n' + "[" * 200000 + "\n")
        self.addCleanup(os.remove, path)
        with self.assertRaises(ValidationError) as ctx:
            validate_predictions(path)
        self.assertIn("line 2", str(ctx.exception))

    def test_rejects_tampered_accuracy(self):
        sample = fresh_sample()
        sample["accuracy"] = 60.0
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("accuracy", str(ctx.exception))

    def test_rejects_tampered_correct(self):
        sample = fresh_sample()
        sample["correct"] = 900                      # accuracy no longer matches, task sums no longer match
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("correct", str(ctx.exception))

    def test_rejects_tampered_task_correct(self):
        sample = fresh_sample()
        sample["tasks"]["Temporal"]["correct"] += 10
        sample["tasks"]["Temporal"]["accuracy"] = round(100 * sample["tasks"]["Temporal"]["correct"] / 212, 2)
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("sum of 'tasks'", str(ctx.exception))

    def test_rejects_wrong_task_n(self):
        sample = fresh_sample()
        sample["tasks"]["Extraction"]["n"] = 200
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("'tasks.Extraction.n' is 200, expected 268", str(ctx.exception))

    def test_rejects_missing_task(self):
        sample = fresh_sample()
        del sample["tasks"]["Attribution"]
        with self.assertRaises(ValidationError):
            validate_metrics(sample)

    def test_rejects_partial_run(self):
        sample = fresh_sample()
        sample["questions"] = sample["n"] = 100
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("expected 1613", str(ctx.exception))

    def test_rejects_submitted_missing_mismatch(self):
        sample = fresh_sample()
        sample["missing"] = 5
        with self.assertRaises(ValidationError):
            validate_metrics(sample)

    def test_rejects_null_accuracy(self):
        sample = fresh_sample()
        sample["accuracy"] = None
        with self.assertRaises(ValidationError):
            validate_metrics(sample)

    def test_rejects_nan_and_infinity(self):
        for literal in ("NaN", "Infinity", "-Infinity"):
            text = json.dumps(fresh_sample()).replace('"accuracy": 52.63', f'"accuracy": {literal}', 1)
            with self.assertRaises(ValidationError) as ctx:
                load_metrics_bytes(text.encode("utf-8"))
            self.assertIn("not valid", str(ctx.exception))
        sample = fresh_sample()
        sample["accuracy"] = float("nan")
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("'accuracy' must be a number", str(ctx.exception))
        sample = fresh_sample()
        sample["categories"]["Evidence Reasoning"]["accuracy"] = float("inf")
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("categories.Evidence Reasoning.accuracy", str(ctx.exception))

    def test_evidence_groups_are_bounded(self):
        sample = fresh_sample()
        sample["evidence_modality"]["text"]["n"] = 600
        sample["evidence_modality"]["text"]["submitted"] = 600
        sample["evidence_modality"]["text"]["correct"] = 400
        sample["evidence_modality"]["text"]["accuracy"] = round(100 * 400 / 600, 2)
        for i in range(93):
            sample["evidence_modality"][f"bucket{i}"] = {"n": 1, "submitted": 1, "missing": 0, "correct": 0,
                                                         "accuracy": 0.0}
        sample["evidence_modality"]["bucket0"]["correct"] = 60
        sample["evidence_modality"]["bucket0"]["n"] = sample["evidence_modality"]["bucket0"]["submitted"] = 60
        sample["evidence_modality"]["bucket0"]["accuracy"] = 100.0
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("at most 32", str(ctx.exception))
        sample = fresh_sample()
        sample["evidence_count"]["<img src=x>"] = {"n": 0, "submitted": 0, "missing": 0, "correct": 0, "accuracy": 0}
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("invalid names", str(ctx.exception))
        sample = fresh_sample()
        sample["evidence_count"]["empty"] = {"n": 0, "submitted": 0, "missing": 0, "correct": 0, "accuracy": 0}
        with self.assertRaises(ValidationError) as ctx:
            validate_metrics(sample)
        self.assertIn("n must be > 0", str(ctx.exception))

    def test_summary_keeps_exact_counts(self):
        counts = validate_metrics(fresh_sample())["counts"]
        self.assertEqual(counts["overall"], {"correct": 849, "n": 1613})
        self.assertEqual(counts["categories"]["ER"], {"correct": 646, "n": 1102})
        self.assertEqual(counts["tasks"]["Attribution"], {"correct": 49, "n": 80})

    def test_rejects_non_object_and_oversize(self):
        with self.assertRaises(ValidationError):
            load_metrics_bytes(b"[1, 2, 3]")
        with self.assertRaises(ValidationError):
            load_metrics_bytes(b"{" + b" " * 1_100_000 + b"}")
        with self.assertRaises(ValidationError):
            load_metrics_bytes(b"not json")

    def test_accepts_missing_predictions_counted(self):
        sample = fresh_sample()
        # 13 missing predictions in Temporal: still consistent, so accepted, and 'missing' is recorded.
        sample["missing"], sample["submitted"] = 13, 1600
        sample["tasks"]["Temporal"]["missing"], sample["tasks"]["Temporal"]["submitted"] = 13, 199
        summary = validate_metrics(sample)
        self.assertEqual(summary["missing"], 13)


class ValidatePredictionsTest(unittest.TestCase):
    def _write(self, lines):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        self.addCleanup(os.remove, path)
        return path

    def test_counts_lines_and_warns(self):
        path = self._write([json.dumps({"id": f"q{i}", "answer": "x"}) for i in range(3)])
        info = validate_predictions(path)
        self.assertEqual(info["lines"], 3)
        self.assertTrue(any(str(N_QUESTIONS) in w for w in info["warnings"]))

    def test_full_file_has_no_warning(self):
        path = self._write([json.dumps({"id": f"q{i}", "answer": "x"}) for i in range(N_QUESTIONS)])
        self.assertEqual(validate_predictions(path)["warnings"], [])

    def test_rejects_bad_lines(self):
        with self.assertRaises(ValidationError):
            validate_predictions(self._write(['{"id": "a"}']))
        with self.assertRaises(ValidationError):
            validate_predictions(self._write(["not json"]))


if __name__ == "__main__":
    unittest.main()
