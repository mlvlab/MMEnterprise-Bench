You schedule a week of work for a company team: {{V_org_frame}}. The tables of the source become {{V_items}} that are {{V_item_done}} at concrete times during the week, so that the team's knowledge evolves: early rows of a progression table come first, the adopted row comes last, and the headline table lands on the last day before the freeze.
---USER---
Company: {{company}} — {{product}}. Internal name of the work: {{internal_model_name}} (the source calls it {{method_name}}).
{{V_customer}}: {{customer}}. Milestones: {{milestone_a}} (day 6), {{milestone_b}} (day 7).

People:
{{people}}

Channels:
{{channels}}

Days:
{{days}}

Tables (cells carry fact ids in brackets):
{{tables}}

Configuration / setup facts:
{{config_facts}}

Produce:
1. {{V_items}} — 6 to 14 {{V_items}} in chronological order (ids {{V_id_prefix}}-00, {{V_id_prefix}}-01, …). Rules:
   - kind "baseline": {{V_baseline_hint}}. Use for the first row(s) of tables that compare against such a reference.
   - kind "progression": every row of every table with role ablation_progression (skip tables annotated "do not schedule" — summary views of another table, or progressions that do not fit this week), in exactly the listed progression order, as consecutive {{V_items}}. Spread them so at least one is {{V_item_done}} on day 3 or day 4 (the weekend). Consecutive progression {{V_items}} are at least 18 hours apart.
   - kind "release": the rows whose figures go into the deliverable ({{V_release_row_hint}}). The last progression row is a release. Additionally schedule exactly these release rows (day 6 before 12:00 unless already covered by a progression {{V_item}}):
{{release_rows}}
   - kind "analysis": rows from comparison tables (alternatives side by side) — day 5 or 6.
   - owner: {{V_research_people}} only ({{research_ids}}); the same person owns all rows of a progression table. Times are HH:MM between 07:00 and 23:30; list {{V_items}} in chronological order.
   - Give each {{V_item}} a human label (a name for what is measured — never containing the row's figures or any value) and its dataset (the dataset / series / scope / sector the row belongs to; '' if none); table_id / row_group / row_label must match the tables exactly.
2. invented — 2 to 4 world-only facts the company would measure for its own product but the source does not contain: e.g. {{V_requirement_hint}}. Each has a concrete value with unit, an owner ({{infra_person}} for infrastructure/tooling numbers), the channel it is first posted in ({{infra_channel}} for infra, {{product_channel}} for {{V_customer}}-facing), a day (1..6) and time, and a rationale tied to the product. One of them must be a time or cost that depends on a knob from the configuration facts (e.g. number of turns / steps / sources / scenarios), so it can justify a decision later.
