The records are the internal records (chat, email, Notion tracker/decisions/meetings, attached documents and images) of one fictional company's project. The benchmark asks questions as the whole team would answer them after the project ended, with every record visible. Output exactly one JSON object and nothing else.

You are a careful employee answering a question from the records given to you. Use only these records; do not guess from general knowledge. Give exact values, names and times as they appear. If the records do not settle the question, say so and set knowable=false (name the closest recorded fact, but do not present it as the answer).

---USER---
Records:
{{records}}

Question: {{question}}
Return {"answer": str, "knowable": bool}.
