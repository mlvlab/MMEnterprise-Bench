"""Offline checks for complete-project QA validation and evidence exports."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import config
from pipeline.records import G, Atoms, full_project, record_block
from pipeline.rule_questions import candidates as rule_candidates
from pipeline.stages import s07_validate as qc, s08_export as export


class RecordsAndQCTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.images = [self.base / "report_page.png", self.base / "plot.png"]
        for index, path in enumerate(self.images):
            Image.new("RGB", (24, 16), "white" if index else "red").save(path)
        self.long_text = "A complete project note. " * 70 + " Final supported value is 7."
        self.time = "2026-08-26T12:00"
        self.world = {"company_name": "Example Company", "people": [{"id": "p1", "name": "Mira Lee", "joined": "2026-08-26"}],
                      "channels": [{"id": "general", "name": "general", "members": ["p1"]}]}
        self.corpus = {
            "slack": [{"id": "m1", "t": self.time, "who": "p1", "ch": "general", "text": self.long_text, "file": "figure", "doc": "report"}],
            "emails": [{"id": "E01", "t": self.time, "from_id": "p1", "from": "Mira Lee", "to": ["p1"], "cc": [], "subject": "Result", "body": "Value: 7.", "doc": "report"}],
            "tracker": [{"run": "R01", "history": [{"t": self.time, "by": "p1", "v": {"value": "7"}}]}],
            "decisions": [{"id": "D01", "t": self.time, "by": "p1", "title": "Approved", "body": "Use 7."}],
            "meetings": [{"id": "M01", "t": self.time, "title": "Review", "attendees": ["p1"], "body": "Validated the report."}],
            "claims": [{"id": "C01", "claim": "Published value is 7", "history": [{"t": self.time, "status": "verified"}]}],
        }
        self.assets = {"docs": [{"id": "report", "name": "report_v1.pdf", "pages": [str(self.images[0])],
                                  "spec": {"pages": [{"title": "Results", "blocks": [{"type": "paragraph", "text": "unrendered source annotation"}]}]}}],
                       "images": [{"id": "figure", "name": "plot.png", "desc": "private curator annotation", "path": str(self.images[1])}]}
        sources = {("02_world", "world.json"): self.world, ("04_assets", "assets.json"): self.assets, ("05_render", "corpus.json"): self.corpus}
        self.source_run = SimpleNamespace(dir=self.base, load=lambda stage, name: deepcopy(sources[(stage, name)]))
        self.g = G(self.source_run)
        self.atoms = Atoms(self.g)
        self.candidate = {"id": "Q01", "q": "What is the recorded value?", "answer_status": "answerable", "gold": {"answer": "7"},
                          "family": "Extraction", "primary_task": "Extraction", "task": "Extraction", "category": "Evidence Reasoning",
                          "evidence": ["S001"], "evidence_ids": ["S001"], "evidence_paths": [["S001"], ["E01"]],
                          "solution": [{"step": "Locate the current report.", "record_ids": ["S001"]},
                                       {"step": "Read the stated value.", "record_ids": ["S001"]}]}
        self.original_qc = deepcopy(config.QC)
        config.QC.update(max_images=4, max_project_images=128, max_project_chars=1_000_000)

    def tearDown(self):
        config.QC.clear()
        config.QC.update(self.original_qc)
        self.tmp.cleanup()

    def test_record_index_preserves_full_text_and_original_image_mapping(self):
        text, images = full_project(self.atoms)
        self.assertEqual(len(self.atoms.items), 8)
        self.assertIn(self.long_text, text)
        for source in ("Gmail", "Notion tracker", "Notion decisions", "Notion meetings", "Notion claims"):
            self.assertIn(source, text)
        self.assertNotIn("private curator annotation", text)
        self.assertNotIn("unrendered source annotation", text)
        self.assertEqual(images, [str(path) for path in self.images])
        self.assertIn("Attached image 1: DOC:report:p1; report_page.png", text)
        self.assertIn("Attached image 2: IMG:figure; plot.png", text)
        text, images = record_block(["IMG:figure", "IMG:figure"], self.atoms)
        self.assertEqual(len(images), 1)
        self.assertEqual(text.count("[IMG:figure]"), 1)

    def test_missing_original_image_fails_instead_of_using_annotations(self):
        self.images[1].unlink()
        with self.assertRaisesRegex(ValueError, "Original image is unavailable"):
            full_project(self.atoms)

    def test_projection_preserves_alternatives_and_checks_all_references(self):
        normalized = qc.project_candidate(self.candidate, self.g, self.atoms)
        self.assertEqual(normalized["evidence_paths"], [["S001"], ["E01"]])
        for changed in (
            {"evidence_paths": [["S001"], ["not-a-record"]]},
            {"evidence_paths": [["E01"], ["S001"]]},
            {"q": "X" * (config.QA["question_max_chars"] + 1)},
            {"solution": []},
            {"evidence_paths": [["S001", "E01"], ["E01", "S001"]]},
            {"solution": [{"step": "Read a record.", "record_ids": ["not-a-record"]}]},
            {"task": "Computation"},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                qc.project_candidate({**self.candidate, **changed}, self.g, self.atoms)

    def test_unanswerable_has_no_gold_evidence_but_can_cite_context_in_solution(self):
        candidate = self.unanswerable()
        candidate["solution"] = [{"step": "The project records the measured value but omits the serving cost.", "record_ids": ["S001"]},
                                 {"step": "No other record establishes the requested cost.", "record_ids": []}]
        normalized = qc.project_candidate(candidate, self.g, self.atoms)
        self.assertEqual(normalized["evidence_paths"], [])
        candidate["evidence_paths"] = [["S001"]]
        with self.assertRaisesRegex(ValueError, "empty gold evidence"):
            qc.project_candidate(candidate, self.g, self.atoms)

    def test_rule_questions_separate_unchanged_values_from_temporal_corrections(self):
        earlier = "2026-08-26T10:00"
        self.corpus["tracker"][0]["history"] = [
            {"t": "2026-08-26T09:00", "by": "p1", "v": {"value": "", "latency_ms": ""}},
            {"t": earlier, "by": "p1", "v": {"value": "9", "latency_ms": "12"}},
            {"t": self.time, "by": "p1", "v": {"value": "7", "latency_ms": "12"}},
        ]
        g = G(self.source_run)
        atoms = Atoms(g)
        items = rule_candidates(g, atoms)
        extraction = [item for item in items if item["family"] == "Extraction"]
        temporal = [item for item in items if item["family"] == "Temporal"]
        self.assertEqual(len(extraction), 1)
        self.assertEqual(extraction[0]["q"], "What latency_ms does the tracker list for R01?")
        self.assertEqual(extraction[0]["gold"]["answer"], "12")
        self.assertEqual(extraction[0]["evidence"], [f"T:R01@{self.time}"])
        self.assertEqual(len(temporal), 1)
        self.assertIn(earlier, temporal[0]["q"])
        self.assertEqual(temporal[0]["gold"]["answer"], "9")
        self.assertEqual(temporal[0]["evidence"], [f"T:R01@{earlier}"])
        self.assertIn("value=9", atoms.items[temporal[0]["evidence"][0]]["text"])
        for item in items:
            self.assertEqual(qc.project_candidate(item, g, atoms)["evidence"], item["evidence"])

    def test_perturbation_wording_cannot_be_revised_with_stale_provenance(self):
        candidate = self.unanswerable()
        candidate["perturbation"] = {"source_id": "A01", "original": "latency", "replacement": "serving cost"}
        with self.assertRaisesRegex(ValueError, "source provenance"):
            qc.apply_fix(candidate, {"q": "What is the annual serving cost?"}, self.atoms)
        unchanged = qc.apply_fix(candidate, {"q": candidate["q"]}, self.atoms)
        self.assertEqual(unchanged["perturbation"], candidate["perturbation"])
        fixed = qc.apply_fix(candidate, {"gold": "The project records do not establish the serving cost.",
                                       "solution": candidate["solution"]}, self.atoms)
        self.assertEqual(fixed["q"], candidate["q"])
        self.assertEqual(fixed["perturbation"], candidate["perturbation"])

    def unanswerable(self):
        return {**deepcopy(self.candidate), "answer_status": "unanswerable", "family": "Unanswerability", "primary_task": "Unanswerability",
                "task": "Unanswerability", "category": "Unanswerability", "q": "What is the serving cost?", "gold": {"answer": "Not specified."},
                "evidence": [], "evidence_ids": [], "evidence_paths": [],
                "solution": [{"step": "Identify the requested cost measure.", "record_ids": []},
                             {"step": "No record supplies that cost.", "record_ids": []}]}

    @staticmethod
    def audit():
        return {"final_value": {"ok": True, "superseding_records": [], "note": ""},
                "competing_answer": {"answer": None, "records": [], "equally_valid": False},
                "numbers": {"wrong_or_unverified": [], "note": ""}, "status": {"ok": True, "note": ""},
                "evidence_sufficient": True, "verdict": "pass", "category": "none", "fix": None, "note": ""}

    def test_unanswerable_audit_sees_all_uncited_images_and_complete_text(self):
        calls = []
        def answer(name, variables, schema, **kwargs):
            calls.append((name, variables, kwargs))
            value = ({"answer": "Not specified.", "knowable": False} if name.endswith("/solve") else
                     {"asked_object": "cost", "verdict": "match", "cause": "none", "reason": "Both abstain."} if name.endswith("/grade") else self.audit())
            return schema.model_validate(value)
        result = qc.check(self.unanswerable(), self.atoms, "incomplete digest", SimpleNamespace(json=answer))
        self.assertTrue(result["passed"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0][1]["records"], "(no cited records)")
        self.assertNotIn("gold", calls[0][1])
        name, variables, kwargs = calls[-1]
        self.assertEqual(name, "07_validate/audit")
        self.assertIn(self.long_text, variables["project_records"])
        self.assertNotIn("incomplete digest", variables["project_records"])
        self.assertEqual(kwargs["images"], [str(path) for path in self.images])
        self.assertEqual(variables["evidence_paths"], [])
        self.assertEqual(variables["family"], "Unanswerability")
        self.assertTrue(variables["family_definition"])

    def test_complete_context_capacity_gate_runs_before_api_calls(self):
        calls = []
        llm = SimpleNamespace(json=lambda *args, **kwargs: calls.append(args))
        config.QC["max_project_images"] = 1
        with self.assertRaisesRegex(ValueError, "max_project_images"):
            qc.check(self.unanswerable(), self.atoms, "", llm)
        self.assertFalse(calls)
        config.QC["max_project_images"] = 128
        config.QC["max_project_chars"] = 100
        with self.assertRaisesRegex(ValueError, "max_project_chars"):
            qc.check(self.unanswerable(), self.atoms, "", llm)
        self.assertFalse(calls)

    def test_fix_revalidates_evidence_and_does_not_retain_stale_alternatives(self):
        with self.assertRaisesRegex(ValueError, "corrected solution"):
            qc.apply_fix(self.candidate, {"evidence_add": ["IMG:figure"]}, self.atoms)
        corrected = [{"step": "Identify the current reported metric.", "record_ids": ["S001"]},
                     {"step": "Read its value in the attached chart.", "record_ids": ["IMG:figure"]}]
        updated = qc.apply_fix(self.candidate, {"evidence_add": ["IMG:figure"], "solution": corrected}, self.atoms)
        self.assertEqual(updated["evidence"], ["S001", "IMG:figure"])
        self.assertEqual(updated["evidence_paths"], [["S001", "IMG:figure"]])
        self.assertEqual(updated["solution"], corrected)
        self.assertEqual(self.candidate["evidence_paths"], [["S001"], ["E01"]])
        revised = qc.apply_fix(self.candidate, {"gold": "7 units", "evidence": ["E01"], "evidence_paths": [["E01"], ["S001"]],
                                               "solution": [{"step": "Identify the measurement in the email.", "record_ids": ["E01"]},
                                                            {"step": "Read the value and its unit.", "record_ids": ["E01"]}]}, self.atoms)
        self.assertEqual(revised["evidence"], revised["evidence_ids"])
        self.assertEqual(revised["evidence_paths"], [["E01"], ["S001"]])
        self.assertEqual(revised["solution"][0]["record_ids"], ["E01"])
        with self.assertRaisesRegex(ValueError, "unknown records"):
            qc.apply_fix(self.candidate, {"evidence_add": ["unknown"]}, self.atoms)

    def test_export_preserves_alternative_paths_and_original_pixels(self):
        with patch.object(config, "RUNS_DIR", self.base / "outputs"):
            run = config.Run("test_project", str(config.PKG / "examples/nist_csf_2_0.pdf"))
            item = {**self.candidate, "evidence": ["IMG:figure"], "evidence_ids": ["IMG:figure"],
                    "evidence_paths": [["IMG:figure"], ["E01"]], "solution": [{"step": "Find the metric in the chart.", "record_ids": ["IMG:figure"]},
                                                              {"step": "Read the chart value.", "record_ids": ["IMG:figure"]}]}
            run.save("07_validate", "qa.json", {"items": [item]})
            with patch.object(export, "G", return_value=self.g), patch.object(export, "Atoms", return_value=self.atoms):
                export.run(run)
            out = run.dir / "08_export"
            row = json.loads((out / "qa/rows.json").read_text())[0]
            units = json.loads((out / "corpus/units.json").read_text())
            self.assertEqual(row["evidence_paths"], [["test_project::IMG:figure"], ["test_project::E01"]])
            self.assertEqual(row["primary_task"], "Extraction")
            self.assertEqual(row["task_group"], "Evidence Reasoning")
            self.assertEqual(row["solution"][0]["record_ids"], ["test_project::IMG:figure"])
            for image in self.images:
                self.assertTrue(any((out / unit["image"]).read_bytes() == image.read_bytes() for unit in units if unit["image"]))
            self.assertTrue(run.done("08_export"))


if __name__ == "__main__":
    unittest.main()
