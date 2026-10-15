You write the content specification of an {{V_deliverable}} as a list of pages made of blocks (heading, paragraph, bullets, inline table, table image, figure, caption). A renderer turns it into page images. Write real content: full sentences, exact numbers, no placeholders.
---USER---
Company {{company}} — {{product}}. Internal system: {{name}} — {{one_sentence}}
Task and {{V_values}}: {{task}}
Method steps:
{{steps}}
Configuration:
{{config}}

This is report {{version}}, written by {{author}} on {{date}} for the milestone "{{milestone_a}}".

Results available at writing time (exact values; do not use anything not listed):
{{runs}}
{{V_release}} {{V_items}} so far:
{{release_runs}}
Operational measurements available (world-only values, exact): {{invented}}
Decisions recorded so far:
{{decisions}}
Figures available (figure_id: description):
{{figures}}
Charts available (usable as figure_id):
{{charts}}
Table images available (table_id):
{{tables}}

{{error_instruction}}

Structure (4-6 pages, ≤ 8 blocks per page):
- Page 1 "Summary": two paragraphs (4-6 sentences each) on what the system does and what this week established; an INLINE table (headers + rows) with the headline numbers per dataset for the baseline vs the {{V_release}} (only datasets with results so far); a paragraph interpreting them; a bullets block "Contents".
- Page 2 "Method and configuration": paragraph(s), one or two figure blocks (system overview / mechanism), a bullets block with the configuration values.
- Page 3 "Results": table image blocks for the main results and the progression/ablation tables that exist, each with a caption block interpreting the trend in numbers (which row is the {{V_release}} and what it trades off).
- Page 4 "Analysis": figure/chart blocks with captions; comparison tables if any.
- Optional page 5 "Qualitative examples": figure blocks with captions; page 6 / last page: "Open items" (v1: what is still running/missing before the milestone) or "Known issues at freeze" (v2).
For v1, results not yet available are described as pending. Never mention a publication, paper or conference.
