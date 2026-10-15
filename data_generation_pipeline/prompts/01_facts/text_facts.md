You read a technical document and extract the facts a product/research team inside a company would work with: what the system is, how it is configured, what it claims with numbers, and the steps of the method. Copy every number exactly as written; the validator rejects numbers that do not occur in the text.
---USER---
Document text (may be truncated):
<<<
{{doc_text}}
>>>

Tables already transcribed (for reference; do not repeat their cells as claims unless the text states them):
{{tables}}

Extract:
- method_name: the proposed system's name.
- one_sentence: what it does, in product language (no "we propose", no "paper").
- task_summary: the task, the datasets/benchmarks and the metrics, 2-3 sentences.
- components: named models, datasets, baselines, modules (10-25 short strings).
- config_facts: 8-20 configuration / hyper-parameter / setup facts with exact values (learning rates, sizes, counts, thresholds, model names used as components, budgets).
- claims: 6-12 quantitative claims with the exact number strings they rest on, a source hint, and a label: a short noun phrase (3-8 words) naming what the leading number measures, written WITHOUT any digit, unit or value (the label becomes the name of a tracked item, so a value inside it would give the answer away).
- pipeline_steps: 4-8 ordered steps of the method as an engineer would list them.
