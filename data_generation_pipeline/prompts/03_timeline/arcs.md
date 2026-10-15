You script short document exchanges — the everyday back-and-forth of a team: someone posts a note or a memo, a colleague reacts ("that number doesn't match the {{V_tracker_short}}", "can you add the overview figure?", "reword the first paragraph"), the author acknowledges and posts v2. Some reactions happen in a DM, some in the channel.
---USER---
Company: {{company}}. System: {{internal_model_name}}.

People:
{{people}}

Channels (DMs listed):
{{channels}}

Days:
{{days}}

{{V_items}} and their {{V_value}} facts (fact ids in brackets):
{{run_facts}}

Figures available (figure id: description):
{{figures}}

Documents already scheduled: report v1 {{report_v1}}, report v2 {{report_v2}}, deck v1 {{deck_v1}}, deck v2 {{deck_v2}} — do not duplicate them.

Plan 2 or 3 document arcs (ids A-01…), all different in kind and review_kind:
- one "memo_table" arc with review_kind "mismatch": the author writes a one-page memo whose small table shows one {{V_value}} fact with a wrong value (wrong_fact_id from a {{V_item}} that has already finished before v1; wrong_value = a plausible slip); the reviewer notices it does not match the {{V_tracker_short}}; v2 fixes it.
- one "note" or "diagram" arc with review_kind "add_figure": v1 has no figure; the reviewer asks to add a specific figure (figure_id); v2 includes it.
- optionally a "note" arc with review_kind "wording".
At least one review happens in a DM between reviewer and author (review_channel = that DM id); the others in the posting channel. v1 is posted on day 1-5, the review within 24 hours, v2 within 24 hours of the review, all before day 7. Authors and reviewers are members of the channels they use.
{{V_id_in_text_rule}}
