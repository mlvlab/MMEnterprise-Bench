You write one day of a Slack channel for a small engineering company, one message per scripted event. People write like coworkers: short, reactive, specific. Values that an event MUST CONTAIN are written verbatim (same digits, same unit). Values that an event MUST NOT CONTAIN are never written — they exist only inside an attached image or in a private room. You never add numbers that are not in the event's description.
---USER---
Company: {{company}}. The system is called {{system}}.
Channel {{channel}} — {{purpose}}. Members: {{members}}.
How each member writes:
{{voices}}

Day: {{day}}. {{state}}
Last messages in this channel before today:
{{context}}

Scripted events for today (id time actor [kind]: what happens):
{{events}}

Write exactly one message per event id, with the same actor and time (HH:MM). Rules:
- {{V_item_report}}s list their values plainly ("<{{V_value}} name> <value> · <{{V_value}} name> <value> · …"); a message with an attached image or document mentions it in words ("draft table attached", "v2 attached") without repeating what is only visible inside the image.
- Corrections are explicit about wrong → right ("57.0 was a typo, it's 75.0").
- Questions end with a question mark; acknowledgements are one line; the exec is terse; the intern is eager; nobody narrates.
- {{V_id_in_text_rule}}
- Never mention the words in the MUST NOT CONTAIN lists, never mention a publication or paper.
Return {"messages": [{"event_id", "time", "actor", "text"}]}.
