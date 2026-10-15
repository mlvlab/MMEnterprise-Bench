You decide which of two candidate crops contains a table.
---USER---
Two crops (A, then B) were taken from a page, one above and one below this caption:
"{{caption}}"

Exactly one of them should contain the table the caption describes (the other contains other content such as figures, text or a different table). Answer with JSON {"pick": "A" or "B", "reason": "..."}. If both contain tables, pick the one whose content matches the caption.
