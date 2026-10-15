You script facts that change during the week through conversation — not {{V_item}} results, but the kind of things that drift in a real team: who owns a task, a deadline, which option was chosen, a size or a budget, a name. Some change because of an honest slip ("oh wait, I mixed those up — it's X"), some because the situation changed (a reassignment, a moved deadline).
---USER---
Company: {{company}}. System: {{internal_model_name}}. {{V_customer}}: {{customer}}.

People:
{{people}}

Channels:
{{channels}}

Days:
{{days}}

Already scheduled {{V_items}}:
{{runs}}
Invented product facts:
{{invented}}
Errors already scripted (do not duplicate these values):
{{errors}}

Produce 3 to 5 evolving facts (ids Y-01…), each with 2-3 versions in chronological order:
- at least one of kind "owner" (values are person ids; a reassignment on day 2-5 in a public channel),
- at least one of kind "date" (a deadline that moves; values like 'day 6 20:00' written as a date string),
- at least one with a version of how="correction" where the speaker corrects their own earlier statement in the same channel within a day ("wait, that's wrong — it's …"),
- at most one numeric fact (a size / count / budget with unit), and it must not be a {{V_value}} from the {{V_items}}.
Each version has a day (first version day 0-2, last version day ≤ 7), a time between 08:00 and 22:00, a channel and a speaker who is a member of it. One fact may have a version stated only in a private room or DM (then its later versions may go public).
The `note` explains the change in one sentence, in the speaker's voice.
{{V_id_in_text_rule}}
