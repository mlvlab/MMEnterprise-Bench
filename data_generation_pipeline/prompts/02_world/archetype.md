You look at a summary of a source document and decide what kind of fictional company would plausibly have produced its content this week. The company will be the document's "author": its people generate, verify and write up the document's numbers and figures during one working week, and nobody in the company knows that a publication exists.
---USER---
Title: {{title}}
What it is: {{one_sentence}}
Task / scope summary: {{task_summary}}
Tables (id, title, first rows):
{{tables}}
Named components / entities: {{components}}

Choose exactly one archetype:
- "research_lab": the document reports experiments run by the authors (a proposed method or system evaluated on benchmarks; ablations; baselines). The company is an engineering team shipping that system.
- "analysis_firm": the document is a report, framework, strategy, survey, forecast, audit or standard whose numbers come from data gathering, modelling or expert judgement rather than from experiments the authors ran. The company is a research/analysis consultancy writing this report for a client.

Return {"archetype": "...", "reason": "one sentence"}.
