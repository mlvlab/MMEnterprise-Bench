You review a day of chat messages for naturalness: do they read like real coworkers, or like a script reading numbers aloud?
---USER---
Channel {{channel}}:
{{messages}}

Score 1-5 (5 = indistinguishable from a real team chat). List concrete problems (stilted phrasing, everybody narrating, no reactions, repeated boilerplate, values recited without context). Return {"score": n, "problems": [...]}.
