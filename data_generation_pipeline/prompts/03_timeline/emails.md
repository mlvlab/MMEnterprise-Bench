You plan the week's email traffic. Email is where external facts enter the company (the {{V_customer}}'s requirements) and where some internal facts are recorded for a few people only (memos, checklists, onboarding).
---USER---
Company: {{company}}. {{V_customer}} contact: {{customer_contact}} at {{customer}}. Lead: {{lead}}. PM: {{pm}}. Exec: {{exec_id}}. Late joiner: {{late_joiner}} (starts day 8).

People:
{{people}}

Days:
{{days}}

Invented facts:
{{invented}}

Errors:
{{errors}}

Decisions:
{{decisions}}

Documents: report v1 {{report_v1}}, report v2 {{report_v2}}, deck v1 {{deck_v1}}, deck v2 {{deck_v2}}.

Plan 6 to 8 emails (ids M-01…), chronological:
1. day 1: inbound from the {{V_customer}} contact (from_id null) to the PM stating a concrete requirement as a THRESHOLD that the later measurement will satisfy but does not equal (e.g. the {{V_customer}} asks for 'under 3 s' when the invented measurement is 2.8 s; 'at most 15 minutes' when the refresh takes 11 minutes). required_values = that threshold string; it must differ from every invented fact's value.
2. day 1: the PM forwards it to the infra owner, cc the lead.
3. day 5 morning: the lead's freeze checklist to the {{V_research_people}} (what must exist by the freeze).
4. day 6 evening: the lead's memo to the exec and the PM recording the private decision's wording, with the true numbers (required_values).
5. day 6 evening: the PM sends deck_v2 to the {{V_customer}} contact (external_to true, cc the exec).
6. day 8 morning (the late joiner's first day — never earlier): welcome email to the late joiner from a senior (cc the lead) assigning the post-freeze task.
7. day 7 evening: the {{V_customer}} contact replies after the review (extension, what convinced them — the invented operational value).
Optionally one more (a {{V_customer}} question or an internal reminder).
