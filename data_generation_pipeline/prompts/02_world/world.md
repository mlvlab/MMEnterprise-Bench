You design a small fictional company: {{V_org_frame}}. The content comes from a source document, but in this company it is simply the work of the week. You must not mention that a publication exists.
---USER---
The work: {{method_name}} — {{one_sentence}}
Scope and measures: {{task_summary}}
Named entities in the source (components, datasets, sources, sectors, indicators, baselines): {{components}}
Steps of the work:
{{pipeline_steps}}

Design the company for a 9-day window (Wed → next Thu). The week has two milestones: an internal one at the end of day 7 (e.g. {{V_milestone_a_hint}}) and an external one on day 8 (e.g. {{V_milestone_b_hint}}). The last day (day 9) a new junior joins.

Return:
- company_name (invented, not a real company; must not be, or start like, any of these names already used by other worlds: {{avoid_names}}), tagline (one line: what they do, size, city), product (one sentence: what they sell and to whom).
- project_codename (an internal codename for this week's deliverable), project_sentence (what the team is finishing this week; refer to the work as the internal {{V_system_word}}), internal_model_name (what people call the {{V_system_word}} in chat; may reuse the source's name).
- customer_name, customer_contact (full invented name), customer_contact_email (at an invented .example domain) — the {{V_customer}}: {{V_customer_hint}}.
- milestone_a (internal, day 7), milestone_b (external, day 8).
- people: 7-9 people with realistic diverse names: exactly one exec (CEO), one lead who owns the deliverable and the {{V_deliverable_short}}, 2-3 seniors ({{V_senior_roles}}), 2 juniors (one of them is the late joiner), one intern (makes figures), one product manager who talks to the {{V_customer}}. Each has an `owns` sentence tied to parts of the work (e.g. {{V_owns_hint}}), a `voice`, and a distinct hex colour.
- channels: 'general' (everyone); a project channel (id starting with 'proj-'); {{V_experiments_channel}} (id must contain 'experiments' or 'analysis'); an 'infra' channel (id contains 'infra'; data/tooling/infrastructure); a 'product' channel (id contains 'product'; PM, lead, exec, infra); a private 'leadership' room (exec, lead, PM); a second private room for 2-3 core {{V_research_people}}; exactly 4 DM channels with ids 'dm-<a>-<b>' (private, 2 members): lead↔junior, junior↔senior, PM↔infra, lead↔PM.
- late_joiner: the junior who joins on the last day (must NOT be a member of the private rooms or DMs).
