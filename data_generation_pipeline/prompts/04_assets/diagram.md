You specify a simple block diagram (4-8 boxes, arrows with short labels) that an engineer would draw of the system for a colleague.
---USER---
System: {{name}} — {{one_sentence}}
Steps:
{{steps}}
Diagram purpose: {{title}}

Return {"title": "...", "nodes": [{"id", "label"}], "edges": [{"src", "dst", "label"}]} with node labels ≤ 4 words and edge labels ≤ 3 words.
