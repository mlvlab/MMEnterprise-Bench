You transcribe a table from an image into structured JSON. Every cell string must be copied exactly as printed. Multi-level column headers are flattened into one leaf header per column (a parent header joined with its leaf header, e.g. "Test Accuracy"). Row groups (e.g. a dataset spanning several rows) become row_groups; if there are none, use one group with group "".
---USER---
Caption: "{{caption}}"

Text of the page (for cross-checking digits; the layout is unreliable, the image is authoritative for structure):
{{page_text}}

Transcribe the table in the attached image. Rules:
- column_headers: leaf headers only, in left-to-right order, excluding the row-label column(s).
- Each row: label = the row's method/system/setting name (join multiple label columns with ' · '), cells = one string per column header. Use "" for an empty cell and "-" for a dash/em-dash.
- Bold or highlighted rows are transcribed like any other row; mention which rows were bold in notes.
- Checkmarks become "✓", blanks stay "".
