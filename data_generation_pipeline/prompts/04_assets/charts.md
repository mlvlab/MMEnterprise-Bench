You specify 1-3 small charts an engineer would post in chat. Values on charts are either exact {{V_value}} facts from the {{V_items}} or explicitly world-only operational numbers.
---USER---
System: {{internal_model_name}}. People: {{people}}.

Progression {{V_items}} ({{V_value}} facts, exact):
{{runs}}

World-only operational facts:
{{invented}}

Configuration knobs:
{{config_facts}}

Produce:
1. chart_progress — a bar or line chart of 1-2 {{V_values}} across the progression {{V_items}} (x_labels = {{V_item}} ids or short labels; series source "facts"; values copied exactly). Owner: the person who owns the {{V_items}}. Skip if there are no progression {{V_items}}.
2. chart for the operational fact {{need_inv}} (invented_id = that id): show how it varies with a configuration knob (e.g. a measured value per setting of a size, budget or step count), 3-4 x values, series source "invented", one of the values must equal the fact's true value, and a threshold line if the fact has a requirement/SLA. Owner: the fact's owner.
Keep titles short; captions one line.
