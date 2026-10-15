"""Offline coverage for complete-context QA generation and interrupted batches."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import config, taxonomy
from pipeline.llm import LLM
from pipeline.prompts import render_prompt
from pipeline.qa_models import CandidateBatch, UnanswerableBatch
from pipeline.stages import s06_qa as qa


def response(variables):
    items = []
    for number in range(1, variables["n"] + 1):
        evidence = ["S001", "IMG:figure"]
        items.append({"id": f"{variables['id_prefix']}_{number:02d}",
                      "q": f"  What does {variables['family']} item {variables['id_prefix']}_{number} ask?  ",
                      "answer_status": "answerable",
                      "gold": {"answer": "7"},
                      "solution": [{"step": "Inspect the available measurement.", "record_ids": ["S001"]},
                                   {"step": "Read the plotted value.", "record_ids": ["IMG:figure"]}],
                      "evidence": evidence, "evidence_paths": [evidence]})
    return {"schema_version": "qa_v1", "items": items, "omitted": []}


class FakeLLM:
    model = "offline-model"

    def __init__(self, fail_at=None, omit=False):
        self.calls = []
        self.fail_at = fail_at
        self.omit = omit

    def json(self, prompt, variables, schema, **kwargs):
        self.calls.append({"prompt": prompt, "variables": deepcopy(variables), **kwargs})
        if self.fail_at == len(self.calls):
            raise RuntimeError("Simulated transport interruption")
        value = response(variables)
        if prompt == "06_qa/unanswerable":
            source = variables["source_questions"][0]
            for item in value["items"]:
                item.update(q=source["q"].replace("What", "Which missing metric", 1), answer_status="unanswerable",
                            gold={"answer": "The records do not establish the missing metric."}, evidence=[], evidence_paths=[],
                            perturbation={"source_id": source["id"], "original": "What", "replacement": "Which missing metric"})
        if self.omit:
            value["omitted"] = [{"id": item["id"], "reason": "No distinct grounded target remains."} for item in value["items"]]
            value["items"] = []
        batch = schema.model_validate(value)
        errors = kwargs["validator"](batch)
        if errors:
            raise AssertionError(errors)
        return batch


class FamilyQATests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.saved = {key: deepcopy(getattr(config, key)) for key in ["RUNS_DIR", "SETTINGS", "QA", "API"]}
        config.RUNS_DIR = self.base / "outputs"
        config.SETTINGS["qa_target"] = 7
        config.QA.update({"families": [family for family in taxonomy.TASKS if family != "Unanswerability"],
                          "batch_size": 1, "max_images": 10, "max_input_chars": 100000,
                          "max_output_tokens": 12000, "question_max_chars": 220})
        self.run = config.Run("test_project", str(self.base / "seed.pdf"))
        self.image = self.base / "image.png"
        Image.new("RGB", (8, 8), "white").save(self.image)
        self.g = SimpleNamespace(world={"company_name": "Example", "people": [], "channels": []})
        self.atoms = SimpleNamespace(order=["S001", "IMG:figure"], items={
            "S001": {"id": "S001", "text": "Mira: The value is 7.", "where": "#general", "kind": "chat",
                     "releases": [("2026-08-26T12:00", {"mira"})]},
            "IMG:figure": {"id": "IMG:figure", "text": "image 'plot.png': secret curator hint\nvisible only inside the image: hidden target 7",
                           "path": str(self.image), "where": "attachment", "kind": "image",
                           "releases": [("2026-08-26T12:01", {"mira"})]}})
        self.patches = [patch.object(qa, "G", return_value=self.g), patch.object(qa, "Atoms", return_value=self.atoms)]
        for current in self.patches:
            current.start()

    def tearDown(self):
        for current in reversed(self.patches):
            current.stop()
        for key, value in self.saved.items():
            setattr(config, key, value)
        self.tmp.cleanup()

    def validate(self, value, family="Extraction", expected_ids=None, existing=None):
        return qa.validate_batch(CandidateBatch.model_validate(value), family=family,
                                 expected_ids=expected_ids or ["Q_01"], record_ids=set(self.atoms.order),
                                 existing_questions=existing or [], max_chars=220)

    def value(self, family="Extraction"):
        return response({"n": 1, "family": family, "id_prefix": "Q"})

    def test_complete_family_flow_assigns_labels_and_supplies_original_images(self):
        engine = FakeLLM()
        qa.run(self.run, engine)
        saved = self.run.load("06_qa", "qa.json")
        self.assertEqual(len(engine.calls), 7)
        self.assertEqual(saved["requested"], 7)
        self.assertEqual({item["task"] for item in saved["items"]}, set(config.QA["families"]))
        self.assertTrue(self.run.done("06_qa"))
        for item in saved["items"]:
            self.assertEqual(item["q"], item["q"].strip())
            self.assertEqual(item["family"], item["task"])
            self.assertEqual(item["category"], taxonomy.GROUP_OF[item["task"]])
            self.assertEqual(item["evidence_ids"], item["evidence"])
        for call in engine.calls:
            self.assertEqual(call["prompt"], "06_qa/generate")
            self.assertEqual(call["images"], [str(self.image)])
            self.assertIn("Mira: The value is 7.", call["variables"]["records"])
            self.assertIn("Attached image 1", call["variables"]["records"])
            self.assertNotIn("secret curator", call["variables"]["records"])
            self.assertNotIn("hidden target", call["variables"]["records"])
        self.assertEqual(len(engine.calls[-1]["variables"]["existing_questions"]), 6)
        first_call = engine.calls[0]
        vocabulary = qa.archetype.prompt_vars(qa.archetype.load(self.run))
        _, rendered = render_prompt("06_qa/generate", {**vocabulary, **first_call["variables"]})
        rendered_example, _ = json.JSONDecoder().raw_decode(rendered[rendered.index('{\n  "schema_version"'):])
        self.assertEqual(rendered_example["items"][0]["id"], saved["items"][0]["id"])
        self.assertEqual(saved["items"][0]["id"], "Q01_000_01")

    def test_unknown_missing_or_nonrepresentative_evidence_is_rejected(self):
        changes = [
            {"evidence": ["UNKNOWN"], "evidence_paths": [["UNKNOWN"]]},
            {"evidence": ["S001"], "evidence_paths": [["IMG:figure"]]},
            {"evidence": ["IMG:figure", "S001"], "evidence_paths": [["S001", "IMG:figure"]]},
            {"evidence_paths": [["S001", "IMG:figure"], ["IMG:figure", "S001"]]},
            {"evidence": ["S001", "S001"], "evidence_paths": [["S001", "S001"]]},
        ]
        for change in changes:
            value = self.value()
            value["items"][0].update(change)
            with self.subTest(change=change):
                self.assertTrue(self.validate(value))
        value = self.value()
        value["items"][0]["solution"][0]["record_ids"] = ["UNKNOWN"]
        self.assertIn("Unknown record", " ".join(self.validate(value)))

    def test_family_status_duplicates_and_character_limit_are_enforced(self):
        self.assertTrue(self.validate(self.value(), family="Unanswerability"))
        self.assertTrue(self.validate(self.value(), family="unconfigured"))
        value = self.value()
        self.assertTrue(self.validate(value, existing=[value["items"][0]["q"].upper()]))
        value["items"][0]["q"] = "q" * 221
        self.assertTrue(self.validate(value))
        value["items"][0]["q"] = "   "
        with self.assertRaises(ValidationError):
            self.validate(value)
        value = self.value()
        value["items"][0]["family"] = "Extraction"
        with self.assertRaises(ValidationError):
            self.validate(value)

    def test_unanswerable_outputs_and_empty_evidence_are_rejected_by_schema(self):
        for change in [
            {"answer_status": "unanswerable"},
            {"evidence": []},
            {"evidence_paths": []},
            {"evidence_paths": [[]]},
        ]:
            value = self.value()
            value["items"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValidationError):
                CandidateBatch.model_validate(value)

    def test_unanswerability_perturbs_a_supported_source_and_can_resume(self):
        config.QA["families"].append("Unanswerability")
        config.SETTINGS["qa_target"] = 8
        engine = FakeLLM()
        qa.run(self.run, engine)
        item = self.run.load("06_qa", "qa.json")["items"][-1]
        self.assertEqual(item["family"], "Unanswerability")
        self.assertEqual(item["answer_status"], "unanswerable")
        self.assertEqual(item["evidence"], [])
        self.assertEqual(engine.calls[-1]["prompt"], "06_qa/unanswerable")
        self.assertEqual(engine.calls[-1]["images"], [str(self.image)])
        cached = FakeLLM()
        qa.run(self.run, cached)
        self.assertEqual(cached.calls, [])

    def test_unanswerability_rejects_unsourced_or_multiple_changes(self):
        source = {"id": "A1", "q": "What is the latency for Model A?"}
        item = {"id": "U1", "q": "What is the latency for Model B?", "answer_status": "unanswerable",
                "gold": {"answer": "Not recorded."}, "solution": [{"step": "Check the original model.", "record_ids": ["S001"]},
                {"step": "No value exists for the changed model.", "record_ids": []}], "evidence": [], "evidence_paths": [],
                "perturbation": {"source_id": "A1", "original": "Model A", "replacement": "Model B"}}
        def errors(value):
            batch = UnanswerableBatch.model_validate({"schema_version": "qa_v1", "items": [value], "omitted": []})
            return qa.validate_batch(batch, family="Unanswerability", expected_ids=["U1"], record_ids=set(self.atoms.order),
                                     existing_questions=[], max_chars=220, source_candidates=[source])
        self.assertEqual(errors(item), [])
        invalid = deepcopy(item)
        invalid["q"] = "What is the throughput for Model B?"
        self.assertTrue(errors(invalid))
        invalid = deepcopy(item)
        invalid["perturbation"]["source_id"] = "UNKNOWN"
        self.assertTrue(errors(invalid))

    def test_rule_candidates_share_quota_and_keep_released_evidence(self):
        config.QA["families"] = ["Attribution", "Extraction", "Temporal"]
        config.SETTINGS["qa_target"] = 6
        self.g.corpus = {"emails": [{"id": "S001", "subject": "Review", "t": "2026-08-26T12:00", "from_id": None,
                                    "from": "Mira Example"}], "tracker": []}
        engine = FakeLLM()
        qa.run(self.run, engine)
        items = self.run.load("06_qa", "qa.json")["items"]
        rules = [item for item in items if item["generation_method"] == "rule"]
        self.assertEqual(len(items), 6)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["gold"]["answer"], "Mira Example")
        self.assertEqual(rules[0]["evidence"], ["S001"])
        self.assertEqual(len(engine.calls), 5)

    def test_omissions_account_for_exact_slots_and_keep_reasons(self):
        value = self.value()
        value["omitted"] = [{"id": "Q_02", "reason": "No supported second question."}]
        self.assertFalse(self.validate(value, expected_ids=["Q_01", "Q_02"]))
        self.assertTrue(self.validate(value))
        value["omitted"][0]["id"] = "Q_01"
        self.assertTrue(self.validate(value))
        value["omitted"][0]["reason"] = ""
        with self.assertRaises(ValidationError):
            self.validate(value)
        with self.assertRaisesRegex(RuntimeError, "No QA candidates"):
            qa.run(self.run, FakeLLM(omit=True))
        saved = self.run.load("06_qa", "qa.json")
        self.assertEqual(len(saved["omitted"]), 7)
        self.assertFalse(self.run.done("06_qa"))

    def test_input_limits_and_missing_pixels_fail_before_model_call(self):
        for key, limit in [("max_images", 0), ("max_input_chars", 100)]:
            engine = FakeLLM()
            before = config.QA[key]
            config.QA[key] = limit
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Complete QA input"):
                qa.run(self.run, engine)
            self.assertEqual(engine.calls, [])
            config.QA[key] = before
        self.image.unlink()
        engine = FakeLLM()
        with self.assertRaises((ValueError, FileNotFoundError)):
            qa.run(self.run, engine)
        self.assertEqual(engine.calls, [])

    def test_interrupted_run_reuses_finished_batches_and_changed_pixels_invalidate_them(self):
        with self.assertRaisesRegex(RuntimeError, "interruption"):
            qa.run(self.run, FakeLLM(fail_at=2))
        self.assertFalse(self.run.done("06_qa"))
        self.assertEqual(len(self.run.load("06_qa", "qa.json")["items"]), 1)
        resumed = FakeLLM()
        qa.run(self.run, resumed)
        self.assertEqual(len(resumed.calls), 6)
        self.assertEqual(resumed.calls[0]["variables"]["family"], "Attribution")
        cached = FakeLLM()
        qa.run(self.run, cached)
        self.assertEqual(cached.calls, [])
        Image.new("RGB", (8, 8), "black").save(self.image)
        changed = FakeLLM()
        qa.run(self.run, changed)
        self.assertEqual(len(changed.calls), 7)

    def test_configured_subset_preserves_order_quotas_and_resume(self):
        config.QA["families"] = ["Interpretation", "Extraction"]
        config.SETTINGS["qa_target"] = 5
        interrupted = FakeLLM(fail_at=3)
        with self.assertRaisesRegex(RuntimeError, "interruption"):
            qa.run(self.run, interrupted)
        saved = self.run.load("06_qa", "qa.json")
        self.assertEqual(saved["requested"], 5)
        self.assertEqual(saved["family_counts"], {"Interpretation": 2})
        resumed = FakeLLM()
        qa.run(self.run, resumed)
        self.assertEqual([call["variables"]["family"] for call in resumed.calls],
                         ["Interpretation", "Extraction", "Extraction"])
        saved = self.run.load("06_qa", "qa.json")
        self.assertEqual(saved["family_counts"], {"Interpretation": 3, "Extraction": 2})
        self.assertEqual([item["id"] for item in saved["items"]],
                         ["Q01_000_01", "Q01_001_01", "Q01_002_01", "Q02_000_01", "Q02_001_01"])
        cached = FakeLLM()
        qa.run(self.run, cached)
        self.assertEqual(cached.calls, [])

    def test_llm_retries_invalid_batch_with_feedback(self):
        engine = LLM(self.run, model="offline-model")
        variables = {"n": 1, "family": "Extraction", "id_prefix": "Q"}
        wrong = response(variables)
        wrong["items"][0]["evidence"] = ["UNKNOWN"]
        raw_messages = []

        def raw(messages, *args, **kwargs):
            raw_messages.append(messages)
            return json.dumps(wrong if len(raw_messages) == 1 else response(variables))

        with patch("pipeline.llm.render_prompt", return_value=("system", "input")), patch.object(engine, "_raw", side_effect=raw):
            result = engine.json("06_qa/generate", variables, CandidateBatch, retries=1,
                                 validator=lambda batch: qa.validate_batch(batch, family="Extraction", expected_ids=["Q_01"],
                                     record_ids=set(self.atoms.order), existing_questions=[], max_chars=220))
        self.assertEqual(len(result.items), 1)
        self.assertEqual(len(raw_messages), 2)
        self.assertIn("Unknown record IDs", raw_messages[1][-1]["content"])
        self.assertTrue((self.run.dir / "rejections.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
