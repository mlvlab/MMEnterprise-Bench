"""export-contexts output format and build_context."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for running without pip install
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import PNG, make_dataset
from mmenterprise_bench import Dataset, build_context, export_contexts
import hashlib


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.dataset = Dataset(make_dataset(self.root / "dataset"))

    def tearDown(self):
        self.temp.cleanup()

    def test_export_format(self):
        output = self.root / "ctx.jsonl"
        count = export_contexts(self.dataset, "2b", 10, output, limit=2)
        self.assertEqual(count, 2)
        lines = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(lines), 2)
        first = lines[0]
        self.assertEqual(set(first), {"id", "project", "question", "timestamp", "records", "retriever", "k"})
        self.assertEqual((first["id"], first["question"], first["retriever"], first["k"]), ("p01::Q1", "What is the value?", "2b", 10))
        self.assertEqual(len(first["records"]), 10)
        self.assertEqual(set(first["records"][0]), {"unit_id", "record_id", "timestamp", "text", "image_path"})
        self.assertEqual(first["records"][0]["image_path"], "media/image.png")   # dataset-relative
        self.assertIsNone(first["records"][1]["image_path"])
        # presentation order = Dataset.selected (timestamp, order, id)
        self.assertEqual([r["unit_id"] for r in first["records"]], [u["id"] for u in self.dataset.selected(self.dataset.rows[0], "2b", 10)])
        # no gold leakage unless asked
        for forbidden in ("42", "reference_answer", "gold_unit_ids", "answer_status"):
            self.assertNotIn(forbidden, json.dumps(first))

    def test_include_gold_and_selection(self):
        output = self.root / "ctx.jsonl"
        export_contexts(self.dataset, "8b", 10, output, question_ids=["p01::Q3"], include_gold=True)
        [line] = [json.loads(line) for line in output.read_text().splitlines()]
        self.assertEqual(line["id"], "p01::Q3")
        self.assertEqual((line["reference_answer"], line["task"], line["category"]), ("Run 7", "Temporal", "Conflict Resolution"))
        self.assertEqual(line["gold_unit_ids"], ["u007"])
        self.assertEqual(line["records"][0]["unit_id"], "u050")   # 8b ranking differs from 2b
        with self.assertRaisesRegex(ValueError, "Unknown question"):
            export_contexts(self.dataset, "2b", 10, output, question_ids=["nope"])

    def test_build_context_absolute_paths(self):
        context = build_context(self.dataset, "p01::Q1", retriever="2b", k=10)
        self.assertEqual(len(context), 10)
        self.assertEqual(set(context[0]), {"unit_id", "record_id", "timestamp", "text", "image_path", "image_sha256"})
        self.assertTrue(Path(context[0]["image_path"]).is_absolute())
        self.assertEqual(context[0]["image_sha256"], hashlib.sha256(PNG).hexdigest())
        self.assertEqual(build_context(self.dataset, self.dataset.rows[0]), context)
        with self.assertRaises(KeyError):
            build_context(self.dataset, "missing")


if __name__ == "__main__":
    unittest.main()
