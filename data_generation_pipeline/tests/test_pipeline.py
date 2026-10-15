"""Offline checks for resuming, validation gates, and portable exports."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_project
from pipeline import config, taxonomy
from pipeline.prompts import list_prompts, render_prompt
from pipeline.render.images import render_table
from pipeline.stages import s07_validate as qc, s08_export as export


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.original = {name: deepcopy(getattr(config, name)) for name in ["RUNS_DIR", "MODEL", "CRITIC_MODEL", "SETTINGS", "API", "QC", "QA", "QA_FAMILIES_FILE"]}
        self.original_spec = deepcopy(taxonomy.SPEC)
        self.settings = json.loads((config.PKG / "config.json").read_text())
        self.settings["seed_pdf"] = str(config.PKG / "examples/nist_csf_2_0.pdf")
        self.settings["output_dir"] = str(self.base / "outputs")
        self.settings["project_id"] = "test_project"
        self.settings["qa"]["families_file"] = str(taxonomy.DEFAULT_PATH)
        self.path = self.base / "config.json"
        self.path.write_text(json.dumps(self.settings))
        config.configure(self.settings, self.path)

    def tearDown(self):
        for name, value in self.original.items():
            setattr(config, name, value)
        taxonomy.SPEC = self.original_spec
        self.tmp.cleanup()

    def invoke(self, *extra):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            status = run_project.main(["--config", str(self.path), *extra])
        return status, output.getvalue()

    def test_dry_run_creates_no_outputs(self):
        with patch.dict(os.environ, {}, clear=True):
            status, output = self.invoke("--dry-run")
        self.assertEqual(status, 0, output)
        self.assertIn("08_export", output)
        self.assertFalse((self.base / "outputs").exists())

    def test_project_id_cannot_escape_output_directory(self):
        self.settings["project_id"] = "../escape"
        self.path.write_text(json.dumps(self.settings))
        status, _ = self.invoke("--dry-run")
        self.assertEqual(status, 1)

    def test_family_configuration_rejects_duplicates_and_wrong_categories(self):
        families = json.loads(taxonomy.DEFAULT_PATH.read_text())
        local = self.base / "families.json"
        families["families"][1] = deepcopy(families["families"][0])
        local.write_text(json.dumps(families))
        self.settings["qa"]["families_file"] = "families.json"
        self.path.write_text(json.dumps(self.settings))
        status, output = self.invoke("--dry-run")
        self.assertEqual(status, 1)
        self.assertIn("exactly once", output)
        families = json.loads(taxonomy.DEFAULT_PATH.read_text())
        families["families"][0]["category"] = "Conflict Resolution"
        local.write_text(json.dumps(families))
        status, output = self.invoke("--dry-run")
        self.assertEqual(status, 1)
        self.assertIn("Incorrect category", output)

    def test_family_edits_change_resume_fingerprint(self):
        local = self.base / "families.json"
        local.write_bytes(taxonomy.DEFAULT_PATH.read_bytes())
        self.settings["qa"]["families_file"] = "families.json"
        config.configure(self.settings, self.path)
        seed = Path(self.settings["seed_pdf"])
        first = run_project.fingerprint(self.settings, seed)
        families = json.loads(local.read_text())
        families["families"][0]["examples"] = ["Which deployment region is recorded?"]
        local.write_text(json.dumps(families))
        second = run_project.fingerprint(self.settings, seed)
        self.assertNotEqual(first, second)

    def test_candidate_allocation_preserves_requested_total(self):
        for target in (0, 3, 8, 90):
            quotas = taxonomy.quotas(target)
            self.assertEqual(sum(quotas.values()), target)
            self.assertEqual(list(quotas), taxonomy.TASKS)
            self.assertLessEqual(max(quotas.values()) - min(quotas.values()), 1)

    def test_generation_family_selection_is_answerable_and_unique(self):
        for families in ([], ["Unanswerability"], ["Extraction", "Extraction"], ["Unknown"]):
            with self.subTest(families=families):
                self.settings["qa"]["families"] = families
                self.path.write_text(json.dumps(self.settings))
                status, output = self.invoke("--dry-run")
                self.assertEqual(status, 1, output)
                self.assertIn("qa.families", output)
        self.settings["qa"]["families"] = ["Temporal", "Extraction"]
        self.path.write_text(json.dumps(self.settings))
        status, output = self.invoke("--dry-run")
        self.assertEqual(status, 0, output)
        self.assertEqual(taxonomy.quotas(5, ["Temporal", "Extraction"]), {"Temporal": 3, "Extraction": 2})

    def test_obsolete_qa_mode_cannot_enable_a_removed_generation_path(self):
        self.settings["qa_mode"] = "templates"
        self.path.write_text(json.dumps(self.settings))
        status, output = self.invoke("--dry-run")
        self.assertEqual(status, 1)
        self.assertIn("Unknown configuration fields", output)

    def test_environment_values_are_literal_and_do_not_override_exports(self):
        env_path = self.base / ".env"
        env_path.write_text("EXISTING=new\nLITERAL='$(touch /tmp/not_executed)'\n")
        with patch.dict(os.environ, {"EXISTING": "old"}, clear=True):
            run_project.load_environment(env_path)
            self.assertEqual(os.environ["EXISTING"], "old")
            self.assertEqual(os.environ["LITERAL"], "$(touch /tmp/not_executed)")

    def test_completed_stages_resume_and_changed_configuration_is_rejected(self):
        called = []
        def fake_import(name):
            stage = name.rsplit(".", 1)[-1][1:]
            stage = "00_ingest" if stage == "00_ingest_vlm" else stage
            def execute(run, llm):
                called.append(stage)
                run.mark_done(stage)
            return SimpleNamespace(run=execute)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test"}), patch.object(run_project.importlib, "import_module", side_effect=fake_import):
            status, output = self.invoke("--to", "01_facts")
            self.assertEqual(status, 0, output)
            self.assertEqual(called, ["00_ingest", "01_facts"])
            called.clear()
            status, output = self.invoke("--resume", "--to", "01_facts")
            self.assertEqual(status, 0, output)
            self.assertEqual(called, [])
            self.settings["settings"]["qa_target"] += 1
            self.path.write_text(json.dumps(self.settings))
            status, output = self.invoke("--resume", "--dry-run")
            self.assertEqual(status, 1)
            self.assertIn("Configuration, seed, or pipeline changed", output)

    def test_rerun_invalidates_all_downstream_stages(self):
        run = config.Run("test_project", self.settings["seed_pdf"])
        for stage in config.STAGES:
            run.mark_done(stage)
        (run.dir / "configuration.json").write_text(json.dumps({"fingerprint": run_project.fingerprint(self.settings, Path(self.settings["seed_pdf"]))}))
        fake = SimpleNamespace(run=lambda run, llm: run.mark_done("03_timeline"))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "offline-test"}), patch.object(run_project.importlib, "import_module", return_value=fake):
            status, output = self.invoke("--from", "03_timeline", "--to", "03_timeline")
        self.assertEqual(status, 0, output)
        self.assertTrue(run.done("03_timeline"))
        self.assertTrue(run.done("02_world"))
        self.assertFalse(run.done("04_assets"))
        self.assertFalse(run.done("08_export"))

    def test_every_prompt_renders_and_missing_values_fail(self):
        style = (config.PROMPT_DIR / "_shared/style.md").read_text()
        for name in list_prompts():
            text = (config.PROMPT_DIR / (name + ".md")).read_text() + style
            names = set(re.findall(r"\{\{(\w+)\}\}", text))
            variables = {key: "example" for key in names}
            system, user = render_prompt(name, variables)
            self.assertNotIn("{{", system + user, name)
        with self.assertRaises(KeyError):
            render_prompt("07_validate/solve", {})

    def test_prompt_slots_do_not_expand_code_inside_records(self):
        system, user = render_prompt("07_validate/solve", {
            "question": "Which template variable does the email use?",
            "records": "[S001] Use {{customer_id}} in the email body.",
        })
        self.assertIn("Use {{customer_id}} in the email body.", user)
        self.assertNotIn("{{records}}", user)

    def test_fonts_and_table_rendering(self):
        target = self.base / "table.png"
        render_table(target, "Results α → β", ["Value"], [{"group": "", "label": "Run A", "cells": ["12.5%"]}])
        self.assertTrue(target.read_bytes().startswith(b"\x89PNG"))

    def fixture(self):
        from PIL import Image
        image = self.base / "figure.png"
        Image.new("RGB", (24, 16), "white").save(image)
        items = {
            "S001": {"id": "S001", "text": "Mira: The value is 7.", "releases": [("2026-08-26T12:00", {"mira"})], "kind": "chat", "where": "#general"},
            "IMG:figure": {"id": "IMG:figure", "text": "image 'plot.png': secret curator annotation\nvisible only inside the image: answer 7", "path": str(image), "releases": [("2026-08-26T12:01", {"mira"})], "kind": "image", "where": "attachment"},
        }
        g = SimpleNamespace(world={"company_name": "Example Company", "people": [], "channels": []}, assets={"images": [{"id": "figure", "name": "plot.png"}], "docs": []})
        atoms = SimpleNamespace(order=list(items), items=items)
        return image, g, atoms

    def test_export_preserves_pixels_and_does_not_leak_visual_annotations(self):
        image, g, atoms = self.fixture()
        run = config.Run("test_project", self.settings["seed_pdf"])
        run.save("07_validate", "qa.json", {"items": [{"id": "Q01", "q": "What value is shown?", "gold": {"answer": "7"}, "answer_status": "answerable", "primary_task": "Extraction", "evidence_ids": ["IMG:figure"], "evidence": ["IMG:figure"], "evidence_paths": [["IMG:figure"]], "solution": [{"step": "Read the plotted value.", "record_ids": ["IMG:figure"]}, {"step": "The value is 7.", "record_ids": ["IMG:figure"]}]}]})
        with patch.object(export, "G", return_value=g), patch.object(export, "Atoms", return_value=atoms):
            export.run(run)
        out = run.dir / "08_export"
        units = json.loads((out / "corpus/units.json").read_text())
        rows = json.loads((out / "qa/rows.json").read_text())
        unit = next(u for u in units if u["image"])
        self.assertEqual((out / unit["image"]).read_bytes(), image.read_bytes())
        self.assertNotIn("secret curator", unit["text"])
        self.assertNotIn("answer 7", unit["text"])
        self.assertEqual(rows[0]["evidence"], [unit["record_id"]])
        self.assertEqual(rows[0]["frozen_representative_retrieval_units"], [unit["id"]])
        self.assertEqual(rows[0]["task"], "Extraction")
        self.assertTrue(run.done("08_export"))

    def test_validator_rejects_incomplete_or_contradictory_audits(self):
        _, _, atoms = self.fixture()
        candidate = {"id": "Q01", "q": "What value is shown?", "gold": {"answer": "7"}, "answer_status": "answerable", "primary_task": "Extraction", "evidence_ids": ["S001"], "evidence": ["S001"], "evidence_paths": [["S001"]], "solution": [{"step": "Read Mira's message.", "record_ids": ["S001"]}, {"step": "The recorded value is 7.", "record_ids": ["S001"]}]}
        audit = {"final_value": {"ok": True, "superseding_records": [], "note": ""}, "competing_answer": {"answer": None, "records": [], "equally_valid": False}, "numbers": {"wrong_or_unverified": ["7"], "note": ""}, "status": {"ok": True, "note": ""}, "evidence_sufficient": True, "verdict": "pass", "category": "gold_wrong", "fix": None, "note": ""}
        def answer(name, variables, schema, **kwargs):
            data = {"answer": "7", "knowable": True} if name.endswith("/solve") else {"asked_object": "value", "verdict": "match", "cause": "none", "reason": ""} if name.endswith("/grade") else audit
            return schema.model_validate(data)
        result = qc.check(candidate, atoms, "all records", SimpleNamespace(json=answer))
        self.assertFalse(result["passed"])
        with self.assertRaises(ValueError):
            qc.Audit.model_validate({"verdict": "pass"})
        with self.assertRaises(ValueError):
            qc.apply_fix(candidate, {"evidence_add": ["UNKNOWN"]}, atoms)


if __name__ == "__main__":
    unittest.main()
