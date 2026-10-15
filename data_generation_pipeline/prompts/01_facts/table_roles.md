You classify the tables of a document so a later step can turn them into a week of work inside a company. A "progression" table is one whose rows form a natural sequence that a team would produce one after another: cumulative configurations (stage 1 → stage 1+2), budgets 1 → 2 → 3, years or survey waves, scenario steps, phases, versions. Its rows become successive items on successive days, and the last row is the one the team adopts.
---USER---
The work is called {{method_name}}.

Tables:
{{tables}}

For every table return:
- table_id
- role: "main_results" (the headline table: the main comparison or the summary figures the document is known for), "ablation_progression" (rows form a natural sequence as described above), "comparison" (alternatives side by side, not a sequence), or "other" (templates, examples, non-numeric, definitions).
- progression_order: for ablation_progression, the row labels in the order they would be produced (exact labels as given); otherwise [].
- release_row: the exact row label whose figures go into the final deliverable (the final/best configuration, the latest year or wave, the headline scenario, the adopted option) — '' if none.
- overlaps_with: if this table's rows are a subset of another, more detailed table, the id of that table; otherwise ''.
- notes: one sentence.
