"""Validation of ``metrics.json`` files produced by ``mmeb score``.

The leaderboard never re-judges predictions; it only accepts the aggregate file written by the official
scorer and checks that it is structurally identical to a fresh ``mmeb score`` run over the full benchmark
(1,613 questions, fixed task/category sizes, internally consistent counts).
"""
from __future__ import annotations

import json
import math
import re
from typing import Any

N_QUESTIONS = 1613
MAX_METRICS_BYTES = 1_000_000            # 1 MB
MAX_PREDICTIONS_BYTES = 25 * 1024 * 1024  # 25 MB

TASK_N = {
    "Extraction": 268,
    "Attribution": 80,
    "Computation": 310,
    "Comparison": 212,
    "Interpretation": 232,
    "Cross-source": 212,
    "Temporal": 212,
    "Unanswerability": 87,
}
TASKS = list(TASK_N)
CATEGORY_N = {"Evidence Reasoning": 1102, "Conflict Resolution": 424, "Unanswerability": 87}
CATEGORIES = list(CATEGORY_N)
ANSWERABILITY_N = {"answerable": 1526, "unanswerable": 87}
N_ANSWERABLE = ANSWERABILITY_N["answerable"]
# evidence_modality / evidence_count are computed over the answerable questions only (see mmeb score)
EVIDENCE_GROUPS = ["evidence_modality", "evidence_count"]
CATEGORY_SHORT = {"Evidence Reasoning": "ER", "Conflict Resolution": "CR", "Unanswerability": "UNS"}
MAX_EVIDENCE_ENTRIES = 32                 # scorer buckets per evidence group (text/image/multi, 1/2/3+)
EVIDENCE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _+./-]{0,31}$")
REQUIRED_TOP = ["questions", "n", "submitted", "missing", "correct", "accuracy", "categories", "tasks",
                "answerability", "evidence_modality", "evidence_count", "judge"]


class ValidationError(ValueError):
    """Raised with a human-readable, multi-line message listing every problem found."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_num(value: Any) -> bool:
    """A finite JSON number (NaN / Infinity are not valid JSON and never come out of ``mmeb score``)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and (isinstance(value, int) or math.isfinite(value)))


def _reject_constant(name: str):
    raise ValueError(f"non-standard JSON constant {name}")


def load_metrics_bytes(data: bytes) -> dict:
    """Parse raw bytes into the metrics object, enforcing the size limit."""
    if len(data) > MAX_METRICS_BYTES:
        raise ValidationError(f"metrics.json is {len(data):,} bytes; the limit is {MAX_METRICS_BYTES:,} bytes.")
    try:
        obj = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)   # refuse NaN / Infinity
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:   # JSONDecodeError is a ValueError
        raise ValidationError(f"metrics.json is not valid UTF-8 JSON: {type(exc).__name__}") from exc
    if not isinstance(obj, dict):
        raise ValidationError("metrics.json must be a JSON object.")
    return obj


def _check_group(problems: list[str], name: str, group: Any, expected_n: dict[str, int] | None,
                 expected_keys: list[str] | None, total_correct: int | None, check_sum: bool,
                 total_n: int | None = None) -> None:
    if not isinstance(group, dict):
        problems.append(f"'{name}' must be an object.")
        return
    if not group:
        problems.append(f"'{name}' is empty.")
        return
    if expected_keys is None:   # scorer-defined buckets: bounded in number, name and size
        if len(group) > MAX_EVIDENCE_ENTRIES:
            problems.append(f"'{name}' has {len(group)} entries; at most {MAX_EVIDENCE_ENTRIES} are allowed.")
            return
        bad_keys = [k for k in group if not isinstance(k, str) or not EVIDENCE_KEY_RE.match(k)]
        if bad_keys:
            problems.append(f"'{name}' has {len(bad_keys)} entries with invalid names (letters, digits, space, _+./- "
                            f"only, at most 32 characters).")
            return
        empty = [k for k, v in group.items() if isinstance(v, dict) and _is_int(v.get("n")) and v["n"] <= 0]
        if empty:
            problems.append(f"'{name}' has empty buckets (n must be > 0): {', '.join(empty)}.")
    keys = expected_keys or list(group)
    missing = [k for k in keys if k not in group]
    if missing:
        problems.append(f"'{name}' is missing entries: {', '.join(missing)}.")
    extra = [k for k in group if expected_keys and k not in expected_keys]
    if extra:
        problems.append(f"'{name}' has unexpected entries: {', '.join(extra)}.")
    total = 0
    n_total = 0
    for key in keys:
        entry = group.get(key)
        if not isinstance(entry, dict):
            problems.append(f"'{name}.{key}' must be an object.")
            continue
        for field in ("n", "submitted", "missing", "correct"):
            if not _is_int(entry.get(field)):
                problems.append(f"'{name}.{key}.{field}' must be an integer.")
        if not _is_num(entry.get("accuracy")):
            problems.append(f"'{name}.{key}.accuracy' must be a number.")
        if expected_n and _is_int(entry.get("n")) and entry["n"] != expected_n[key]:
            problems.append(f"'{name}.{key}.n' is {entry['n']}, expected {expected_n[key]}.")
        if _is_int(entry.get("n")) and _is_int(entry.get("submitted")) and _is_int(entry.get("missing")):
            if entry["submitted"] + entry["missing"] != entry["n"]:
                problems.append(f"'{name}.{key}': submitted + missing != n.")
        if _is_int(entry.get("correct")) and _is_int(entry.get("submitted")):
            if not 0 <= entry["correct"] <= entry["submitted"]:
                problems.append(f"'{name}.{key}': correct must be between 0 and submitted.")
            if _is_int(entry.get("n")) and entry["n"] > 0 and _is_num(entry.get("accuracy")):
                expected = round(100 * entry["correct"] / entry["n"], 2)
                if abs(entry["accuracy"] - expected) > 0.011:
                    problems.append(f"'{name}.{key}.accuracy' is {entry['accuracy']}, expected {expected}.")
            total += entry["correct"]
        if _is_int(entry.get("n")):
            n_total += entry["n"]
    if check_sum and total_correct is not None and not missing:
        if total != total_correct:
            problems.append(f"sum of '{name}' correct counts ({total}) != expected correct ({total_correct}).")
    if total_n is not None and not missing and n_total != total_n:
        problems.append(f"sum of '{name}' n ({n_total}) != {total_n}.")


def validate_metrics(metrics: dict, allow_pre_judged: bool = False) -> dict:
    """Validate a parsed metrics object. Returns the headline summary; raises ValidationError otherwise."""
    problems: list[str] = []
    if not isinstance(metrics, dict):
        raise ValidationError("metrics.json must be a JSON object.")
    missing = [k for k in REQUIRED_TOP if k not in metrics]
    if missing:
        problems.append(f"Missing top-level keys: {', '.join(missing)}.")
    for field in ("questions", "n", "submitted", "missing", "correct"):
        if field in metrics and not _is_int(metrics[field]):
            problems.append(f"'{field}' must be an integer.")
    if _is_int(metrics.get("questions")) and metrics["questions"] != N_QUESTIONS:
        problems.append(f"'questions' is {metrics['questions']}, expected {N_QUESTIONS} (score the full benchmark).")
    if _is_int(metrics.get("n")) and metrics["n"] != N_QUESTIONS:
        problems.append(f"'n' is {metrics['n']}, expected {N_QUESTIONS}.")
    if _is_int(metrics.get("submitted")) and _is_int(metrics.get("missing")):
        if metrics["submitted"] + metrics["missing"] != N_QUESTIONS:
            problems.append("submitted + missing must equal 1613.")
    correct = metrics.get("correct") if _is_int(metrics.get("correct")) else None
    if correct is not None and _is_int(metrics.get("submitted")):
        if not 0 <= correct <= metrics["submitted"]:
            problems.append("'correct' must be between 0 and 'submitted'.")
    if "accuracy" in metrics:
        if not _is_num(metrics["accuracy"]):
            problems.append("'accuracy' must be a number (a null accuracy means the scorer did not finish).")
        elif correct is not None:
            expected = round(100 * correct / N_QUESTIONS, 2)
            if abs(metrics["accuracy"] - expected) > 0.011:
                problems.append(f"'accuracy' is {metrics['accuracy']} but correct/1613 gives {expected}.")

    if "tasks" in metrics:
        _check_group(problems, "tasks", metrics["tasks"], TASK_N, TASKS, correct, True)
    if "categories" in metrics:
        _check_group(problems, "categories", metrics["categories"], CATEGORY_N, CATEGORIES, correct, True)
    answerable_correct = None
    if "answerability" in metrics:
        _check_group(problems, "answerability", metrics["answerability"], ANSWERABILITY_N,
                     list(ANSWERABILITY_N), correct, True)
        try:
            value = metrics["answerability"]["answerable"]["correct"]
            answerable_correct = value if _is_int(value) else None
        except (KeyError, TypeError):
            answerable_correct = None
    for name in EVIDENCE_GROUPS:
        if name in metrics:   # groups over the 1,526 answerable questions; keys are the scorer's buckets
            _check_group(problems, name, metrics[name], None, None, answerable_correct, True, total_n=N_ANSWERABLE)

    judge = metrics.get("judge")
    if "judge" in metrics and not isinstance(judge, dict):
        problems.append("'judge' must be an object (fresh `mmeb score` runs record the judge model).")
    elif isinstance(judge, dict) and judge.get("pre_judged") is True and not allow_pre_judged:
        problems.append("judge.pre_judged is true: this file re-aggregates existing judgments instead of a fresh "
                        "`mmeb score` run with a judge. Re-score your predictions and upload the new metrics.json.")

    if problems:
        raise ValidationError("metrics.json was rejected:\n- " + "\n- ".join(problems))
    return summarize_metrics(metrics)


def _acc(group: Any, key: str):
    try:
        value = group[key]["accuracy"]
    except (KeyError, TypeError):
        return None
    return float(value) if _is_num(value) else None


def _count(group: Any, key: str):
    """``{"correct", "n"}`` of one bucket (exact integers; the display rounds from these, not from ``accuracy``)."""
    try:
        entry = group[key]
        correct, n = entry["correct"], entry["n"]
    except (KeyError, TypeError):
        return None
    if not (_is_int(correct) and _is_int(n)) or n <= 0:
        return None
    return {"correct": correct, "n": n}


def summarize_metrics(metrics: dict) -> dict:
    """Headline numbers stored in a leaderboard row (accuracies in percent)."""
    retrieval = metrics.get("retrieval")
    recall = None
    if isinstance(retrieval, dict) and _is_num(retrieval.get("evidence_recall")):
        recall = float(retrieval["evidence_recall"])
    return {
        "overall": float(metrics["accuracy"]),
        "correct": int(metrics["correct"]),
        "missing": int(metrics["missing"]),
        "categories": {CATEGORY_SHORT[c]: _acc(metrics["categories"], c) for c in CATEGORIES},
        "tasks": {t: _acc(metrics["tasks"], t) for t in TASKS},
        "answerability": {k: _acc(metrics["answerability"], k) for k in ("answerable", "unanswerable")},
        "evidence_modality": {k: _acc(metrics["evidence_modality"], k) for k in metrics["evidence_modality"]},
        "evidence_count": {k: _acc(metrics["evidence_count"], k) for k in metrics["evidence_count"]},
        "counts": {
            "overall": {"correct": int(metrics["correct"]), "n": N_QUESTIONS},
            "categories": {CATEGORY_SHORT[c]: _count(metrics["categories"], c) for c in CATEGORIES},
            "tasks": {t: _count(metrics["tasks"], t) for t in TASKS},
        },
        "retrieval_recall": recall,
        "judge_model": (metrics.get("judge") or {}).get("model") if isinstance(metrics.get("judge"), dict) else None,
        "pre_judged": bool((metrics.get("judge") or {}).get("pre_judged")) if isinstance(metrics.get("judge"), dict) else None,
    }


def validate_predictions(path: str) -> dict:
    """Check an optional predictions.jsonl: size limit, one JSON object per line with string id/answer."""
    import os

    size = os.path.getsize(path)
    if size > MAX_PREDICTIONS_BYTES:
        raise ValidationError(f"predictions.jsonl is {size / 1e6:.1f} MB; the limit is 25 MB.")
    lines = 0
    warnings: list[str] = []
    ids: set[str] = set()
    duplicates = 0
    with open(path, "rb") as handle:
        for number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError, RecursionError):
                raise ValidationError(f"predictions.jsonl line {number} is not valid JSON.")
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("answer"), str):
                raise ValidationError(f"predictions.jsonl line {number} must be an object with string 'id' and 'answer'.")
            if row["id"] in ids:
                duplicates += 1
            ids.add(row["id"])
            lines += 1
    if lines != N_QUESTIONS:
        warnings.append(f"predictions.jsonl has {lines} lines (expected {N_QUESTIONS}); it is stored as-is for audit.")
    if duplicates:
        warnings.append(f"predictions.jsonl has {duplicates} duplicate ids.")
    return {"lines": lines, "bytes": size, "warnings": warnings}
