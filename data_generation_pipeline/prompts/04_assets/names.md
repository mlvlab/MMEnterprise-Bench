You name image files the way an engineer would save them on a shared drive: short, descriptive of the content, lowercase with underscores. Never number them like a publication ("fig1", "figure_3", "table2").
---USER---
Images (id: description):
{{images}}

Return one natural file name per id, unique, ≤ 40 characters, with the given extension, describing what the image shows (e.g. "pipeline_overview.png", "budget_sweep_curve.png"). For entries marked TABLE also return a short in-world title as a colleague would head the table (e.g. "Component ablation on the validation set", "Configuration comparison") — never "Table N", never "our"/"we". Return {"names": [{"id", "name", "title"}]}.
