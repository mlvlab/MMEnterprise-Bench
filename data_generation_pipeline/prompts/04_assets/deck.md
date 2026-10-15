You write the content specification of a {{V_deck}} (5-7 slides, ≤ 5 blocks per slide) for a pilot review. Slides are short: a title, one figure or table, 2-4 bullets. Numbers are exact.
---USER---
Company {{company}} — {{product}}. Internal system: {{name}} — {{one_sentence}}
{{V_customer}}: {{customer}}. Deck {{version}} by {{author}} on {{date}} for "{{milestone_b}}".

Results available (exact values; do not use anything not listed):
{{runs}}
{{V_release}} {{V_items}}:
{{release_runs}}
Operational measurements (world-only values, exact): {{invented}}
Decisions recorded so far:
{{decisions}}
Figures available (figure_id: description):
{{figures}}
Charts available (usable as figure_id):
{{charts}}
Table images available (table_id):
{{tables}}

{{error_instruction}}

Slides:
1. Title slide: heading with the system name and the meeting; a caption with company and date.
2. "What it does": one figure block (system overview) + bullets in {{V_customer}} language (no internal jargon, no dataset names).
3. Accuracy slide — see the instruction above (v1: big_number headline; v2: inline per-dataset table + one relative-improvement sentence as a paragraph).
4. Operational-measurement slide: a chart figure block if a chart exists, bullets with the measured value and the {{V_customer}} requirement.
5. Example slide: one qualitative figure block with a one-line caption.
6. Next steps: bullets (pilot extension, integration, reporting).
Never mention a publication, paper or conference.
