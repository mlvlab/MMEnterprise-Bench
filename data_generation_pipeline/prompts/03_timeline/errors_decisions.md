You script the human mistakes and the decisions of the week. Mistakes are deliberate and scripted: each has a wrong value, a person who slips, a person who notices, a place where it is noticed (a public channel or a private DM) and a moment when it is fixed. Decisions are recorded in a decision log; some have a rationale that is discussed only in a private room.
---USER---
Company: {{company}}. System: {{internal_model_name}}. Lead: {{lead}}. PM: {{pm}}. Report author: {{report_author}}.

People:
{{people}}

Channels (private rooms: {{leadership}}, {{core_room}}; DMs listed above):
{{channels}}

Days:
{{days}}

{{V_items}} (chronological):
{{runs}}

{{V_value}} facts per {{V_item}} (id=value (column)):
{{run_facts}}

Invented facts:
{{invented}}

Image-only facts in figures (V… ids):
{{visual_facts}}

Documents are posted at fixed times: report v1 {{report_v1}}, report v2 {{report_v2}}, deck v1 {{deck_v1}}, deck v2 {{deck_v2}}.

Produce exactly {{n_errors}} errors (plus optionally one unfixed_inconsistency), one of each kind:
- tracker_typo: a {{V_value}} fact of a progression/release {{V_item}} is entered wrongly in the Notion {{V_tracker_short}} at report time (transposed digits, e.g. 75.0 → 57.0). The chat post is right, the {{V_tracker_short}} is wrong. Caught the next morning by a senior in the results channel (where {{V_items}} are posted) (public) or in a DM. fixed shortly after.
- image_cell: a draft table image posted with a {{V_item_report}} has one wrong cell (a {{V_value}} fact of an earlier row); the author notices late at night and tells a senior in a DM (caught_in dm), reposting a fixed image next morning.
- channel_value: an invented fact is first posted with a wrong number (wrong column / unit slip); the owner tells the PM in a DM within 10 minutes, then corrects the channel a few minutes later.
- doc_value: report v1 carries a wrong value on its summary page (a {{V_value}} fact of the release {{V_item}} replaced by the same {{V_value}} from an earlier progression row — the wrong_value must be that earlier row's value). The lead catches it the next morning in a DM to the report author; fixed when report v2 is posted. Its fact_id must be one of the facts whose {{V_item}} finished before report v1: {{doc_value_eligible}}.
- doc_headline: deck v1 puts a single headline number (a release-run {{V_value}}, wrong_value = that number formatted as a headline like "61%") on a slide; leadership decides against any single headline number; fixed when deck v2 is posted (day 6). Caught in the private leadership room (caught_in public, caught_by the exec or lead).
- optional unfixed_inconsistency: if two image-only facts of one figure contradict each other (e.g. two different timestamps for the same event), reference one V… id; it is flagged on day 6 evening and deferred to a post-freeze fix list.

Produce 4 to {{n_decisions}} decisions with ids D-01…, in time order:
- day 0: configuration frozen (concrete values from the configuration facts).
- the {{V_release}} decision at the release {{V_item}}'s time (with the trade-off in numbers if a later row beat it on some {{V_value}}).
- a decision about a configuration knob justified by an invented operational fact (day 6 afternoon), public rationale.
- the "no single headline number for the {{V_customer}}" decision (day 6, by the lead, rationale private in {{leadership}}, related_error_id = the doc_headline error). Its body says what goes on the slide instead (a per-dataset table and a relative-improvement sentence with the true numbers).
- day 6 after 21:00 (the freeze is announced at 20:55; a post-freeze list cannot exist before it): the post-freeze fix list (status open) referencing the unfixed inconsistency if any.
Every decision body uses exact values from the facts. A rationale answers "why?": it starts with the reason in words (the risk avoided, the benefit gained, the trade-off accepted — e.g. 'because the larger setting adds no accuracy but breaks the budget'), and cites at most 4 numbers as support at the end. A list of numbers is not a rationale.
{{V_id_in_text_rule}}
