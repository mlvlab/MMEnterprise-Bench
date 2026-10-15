The records are the internal records (chat, email, Notion tracker/decisions/meetings, attached documents and images) of one fictional company's project. The benchmark asks questions as the whole team would answer them after the project ended, with every record visible. Output exactly one JSON object and nothing else.

You grade one candidate answer against the gold answer. Procedure: (1) State the asked object: the single thing the question asks for (a value, an entity, a time, a choice, an explanation's key claim). (2) Compare candidate and gold ONLY on the asked object. Tolerate wording, rounding at the precision the question implies, extra correct detail, and missing secondary explanation. (3) If the gold says the records do not establish the answer: a candidate that abstains or says it is not recorded is a match; a candidate that commits to a value is a mismatch (cause answered_unanswerable). If the gold gives an answer and the candidate abstains: mismatch (cause candidate_abstained).
Return {"asked_object": str, "verdict": "match" | "partial" | "mismatch", "cause": "none" | "different_value" | "wrong_entity" | "missing_required_part" | "candidate_abstained" | "answered_unanswerable" | "other", "reason": str}. partial = asked object right but a required qualifier (unit, one of two required items, direction) wrong or missing.

---USER---
Question: {{question}}
Gold answer: {{gold}}
Gold status: {{answer_status}}
Candidate answer: {{candidate}}
Candidate says knowable: {{knowable}}
