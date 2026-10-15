"""Scripted errors must exist before detection and correction."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.stages import s03_timeline as timeline


class TimelineTests(unittest.TestCase):
    def fixture(self):
        days = [(date(2026, 8, 26) + timedelta(days=i)).isoformat() for i in range(9)]
        people = [{"id": name, "name": name.title(), "level": level, "owns": "evaluation", "joined": days[0]}
                  for name, level in [("mira", "lead"), ("owen", "senior"), ("alex", "exec"), ("new", "junior")]]
        world = {"people": people, "late_joiner": "new", "milestone_a": "Freeze", "milestone_b": "Review", "project_codename": "Atlas",
                 "channels": [{"id": "general", "members": [p["id"] for p in people]}]}
        runs = [{"run_id": rid, "day_offset": day, "time": time, "kind": kind, "owner": owner,
                 "table_id": "tab1", "row_label": row, "row_group": "", "label": row, "dataset": "Cedar"}
                for rid, day, time, kind, owner, row in [
                    ("R-00", 0, "09:00", "baseline", "mira", "Baseline"),
                    ("R-01", 4, "09:00", "release", "mira", "Atlas release"),
                    ("R-02", 6, "10:00", "analysis", "owen", "Atlas release")]]
        row_facts = {run["run_id"]: [{"fact_id": "F001" if run["row_label"] == "Baseline" else "F002",
                                     "header": "Recall@1 (%)", "value": "72.0" if run["row_label"] == "Baseline" else "81.5"}]
                     for run in runs}
        errors = [{"id": eid, "kind": kind, "fact_id": fid, "wrong_value": value, "actor": "mira", "caught_by": "owen",
                   "caught_in": "public", "day_offset_caught": 5, "time_caught": "10:00", "day_offset_fixed": 5,
                   "time_fixed": "11:00", "note": "A transposed digit."}
                  for eid, kind, fid, value in [("E-01", "tracker_typo", "F002", "18.5"), ("E-02", "image_cell", "F001", "27.0")]]
        plan = {"runs": runs, "row_facts": row_facts, "errors": errors, "invented": [], "decisions": [], "emails": [],
                "roles": {"lead": "mira", "exec": "alex", "pm": "owen", "intern": "owen", "report_author": "mira", "deck_author": "owen"},
                "channels": {key: "general" for key in ["exp", "proj", "product", "infra", "leadership"]},
                "doc_schedule": timeline.DOC_SCHEDULE}
        facts = {"tables": [{"id": "tab1", "role": "main_results", "overlaps_with": ""}], "figures": [],
                 "metric_facts": [{"id": fid, "table": "tab1", "row": row, "column": "Recall@1 (%)", "value": value, "group": ""}
                                  for fid, row, value in [("F001", "Baseline", "72.0"), ("F002", "Atlas release", "81.5")]]}
        return plan, facts, world, days

    def test_repeated_analysis_cannot_replace_the_canonical_release_or_delay_availability(self):
        plan, _, _, _ = self.fixture()
        canonical, first = timeline.fact_schedule(plan["runs"], plan["row_facts"])
        self.assertEqual(canonical["F002"], "R-01")
        self.assertEqual(first["F002"], (4, "09:00"))
        earlier = {**plan["runs"][0], "run_id": "R-early", "row_label": "Atlas release"}
        plan["runs"].append(earlier)
        plan["row_facts"]["R-early"] = plan["row_facts"]["R-01"]
        canonical, first = timeline.fact_schedule(plan["runs"], plan["row_facts"])
        self.assertEqual(canonical["F002"], "R-01")
        self.assertEqual(first["F002"], (0, "09:00"))

    def test_wrong_tracker_and_baseline_image_values_exist_before_their_corrections(self):
        plan, facts, world, days = self.fixture()
        events = timeline._expand(plan, facts, world, days)
        timeline.validate_error_events(events, plan["errors"])
        wrong = next(event for event in events if event["kind"] == "tracker_update" and event.get("values", {}).get("F002") == "18.5")
        fixed = next(event for event in events if event["kind"] == "tracker_fix")
        self.assertEqual(wrong["refs"]["run"], "R-01")
        self.assertEqual(fixed["refs"]["run"], "R-01")
        self.assertEqual(fixed["values"]["F002"], "81.5")
        self.assertLess(wrong["t"], fixed["t"])
        corrupt_image = next(event for event in events if event.get("image", {}).get("wrong"))
        self.assertEqual(corrupt_image["image"]["wrong"], {"fact_id": "F001", "value": "27.0"})
        self.assertIn("Baseline", corrupt_image["image"]["rows"])
        corrected_image = next(event for event in events if event["kind"] == "correction" and event.get("image"))
        self.assertIsNone(corrected_image["image"]["wrong"])
        self.assertIn("Baseline", corrected_image["image"]["rows"])
        self.assertLess(corrupt_image["t"], corrected_image["t"])
        for missing in [wrong, corrupt_image]:
            with self.assertRaisesRegex(ValueError, "actual wrong record"):
                timeline.validate_error_events([event for event in events if event is not missing], plan["errors"])

    def test_baseline_fact_is_not_visible_in_a_draft_until_the_first_image_post(self):
        plan, _, _, _ = self.fixture()
        _, first = timeline.fact_schedule(plan["runs"], plan["row_facts"])
        drafts = timeline.first_draft_times(plan["runs"], plan["row_facts"])
        self.assertEqual(first["F001"], (0, "09:00"))
        self.assertEqual(drafts["F001"], (4, "09:00"))
        plan["runs"][1]["kind"] = "analysis"
        self.assertEqual(timeline.first_draft_times(plan["runs"], plan["row_facts"]), {})


if __name__ == "__main__":
    unittest.main()
