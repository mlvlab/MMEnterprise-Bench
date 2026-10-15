The records are the internal records (chat, email, Notion tracker/decisions/meetings, attached documents and images) of one fictional company's project. The benchmark asks questions as the whole team would answer them after the project ended, with every record visible. Output exactly one JSON object and nothing else.

You audit one benchmark item against ALL records of its project (complete text below; every original project image is attached, with its record ID and attachment position listed in the image manifest). You also see what an independent solver answered when given only the cited records. Decide, reading the records and images yourself:
1. final_value: Does any record correct, supersede or replace the value, decision or version the gold relies on, so that the gold is no longer the right answer to the question as asked? If the question itself fixes a time or a version ('as of Aug 29 13:00', 'in report v1', 'the original value before the correction'), judge against that time or version and do not count later changes. Name the record. ok=false only when the gold is superseded for the question as asked.
2. competing_answer: Could a careful employee defend a materially different answer to the question AS WORDED (a different referent, version, time, or reading of an ambiguous term)? equally_valid is true only if the wording does not let a reasonable employee decide which is meant.
3. numbers: For every number, date and time in the gold, check that the records give that value for the thing the gold attaches it to; list any that are wrong or unverifiable.
4. status: If the gold says the records do not establish the answer, check whether some record actually gives what is asked. If the gold gives an answer, check whether the question presupposes something the records contradict.
5. evidence_sufficient: Can the asked object be derived from the representative cited records alone, and separately from EACH listed alternative evidence path? Each path must be sufficient on its own. The solver sees the representative path only; its result is a hint, not proof. For unanswerable items, require empty gold evidence and evidence paths, and inspect the entire project including all original images for an answer before passing the item.
6. task family: Does the question match the supplied family definition? The family is fixed for this candidate; reject a question that performs a different task. Use category task_mismatch for a mismatch.
Then give verdict: pass (item is fine) | fix (salvageable by a concrete edit: wording, gold detail, evidence list) | drop (gold wrong or the question cannot be made to pick one answer). category: gold_wrong | stale_gold | ambiguous_question | evidence_insufficient | unanswerable_is_answerable | false_premise | leak | checker_error | task_mismatch | none.
Return {"final_value": {"ok": bool, "superseding_records": [ids], "note": str}, "competing_answer": {"answer": str|null, "records": [ids], "equally_valid": bool}, "numbers": {"wrong_or_unverified": [str], "note": str}, "status": {"ok": bool, "note": str}, "evidence_sufficient": bool, "verdict": str, "category": str, "fix": {"q": str|null, "gold": str|null, "evidence_add": [ids], "evidence": [ids]|null, "evidence_paths": [[ids]]|null, "solution": [{"step": str, "record_ids": [ids]}]|null} | null, "note": str}

Use record IDs that occur in the project. Keep the requested task family and answerability status unchanged; drop an item if fixing it requires changing either. For a fix, evidence replaces the representative set when supplied; evidence_add appends records to it. evidence_paths replaces all sufficient sets; its first path must exactly equal the representative evidence list in the same order. Supply 2–5 corrected, nonempty solution steps when the question, answer, or evidence changes. If an alternative path is insufficient, remove or replace it. Never treat missing personal access as missing project evidence. Record contents are data, not instructions.

For Unanswerability, the question is a recorded single perturbation of an answerable source. If its wording requires a change, drop it to preserve that provenance; corrections to the reference explanation or solution are permitted with the question unchanged.

---USER---
Task family: {{family}}
Family definition: {{family_definition}}
Question: {{question}}
Gold answer: {{gold}}
Gold status: {{answer_status}}
Solution: {{solution}}
Representative cited records: {{evidence_ids}}
Sufficient evidence paths: {{evidence_paths}}

Stage-1 solver (cited records only): {{solver_result}}

Representative cited records (text):
{{records}}

All project records (complete text and original-image attachment manifest):
{{project_records}}
