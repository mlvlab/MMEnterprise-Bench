"""Bring-your-own-agent scorer: predictions.jsonl -> metrics.json / metrics.md / graded.jsonl.

Scoring follows the paper exactly: the judge sees only {question, reference_answer, candidate_answer};
``correct`` is boolean; accuracy = 100 x correct / N where N is every question in the selection
(the full benchmark by default). Questions without a prediction count as incorrect and are reported
separately as ``missing``; ``observed_accuracy`` is the accuracy over submitted questions only.
"""
from __future__ import annotations

import json
from pathlib import Path

from .contexts import select_rows
from .data import TASKS, task_of
from .pipeline import atomic_json

CATEGORIES = ("Evidence Reasoning", "Conflict Resolution", "Unanswerability")
MODALITIES = ("text", "image", "multi")
EVIDENCE_COUNTS = ("1", "2", "3+")
PRESERVED_KEYS = ("correct", "judge_reason", "status")


def load_predictions(path):
    """Read a predictions file; returns ``{question_id: prediction}``. Duplicate IDs are an error."""
    predictions = {}
    with Path(path).open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path}:{number}: invalid JSON ({exc})") from None
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                raise ValueError(f"{path}:{number}: each line must be a JSON object with a string 'id'")
            if value["id"] in predictions:
                raise ValueError(f"{path}:{number}: duplicate prediction for {value['id']}")
            predictions[value["id"]] = value
    return predictions


def evidence_count_group(row):
    count = row.get("evidence_unit_count")
    if count is None:
        count = len(row.get("frozen_representative_retrieval_units", []))
    return "1" if count == 1 else "2" if count == 2 else "3+" if count >= 3 else None


def summarize(items):
    submitted = [item for item in items if item["status"] != "missing"]
    correct = sum(item["correct"] is True for item in items)
    n = len(items)
    return {"n": n, "submitted": len(submitted), "missing": n - len(submitted), "correct": correct,
            "accuracy": round(100 * correct / n, 2) if n else None,
            "observed_accuracy": round(100 * correct / len(submitted), 2) if submitted else None}


def _groups(graded, key):
    return {name: summarize([item for item in graded if item.get(key) == name]) for name in _names(key)}


def _names(key):
    return {"category": CATEGORIES, "task": TASKS, "answer_status": ("answerable", "unanswerable"),
            "evidence_modality": MODALITIES, "evidence_count_group": EVIDENCE_COUNTS}[key]


def retrieval_recall(rows, predictions):
    """Evidence Recall@k of the agent's own ``retrieved_unit_ids`` (macro over answerable questions)."""
    fractions, observed, complete, recovered, total, lengths = [], [], 0, 0, 0, []
    for row in rows:
        gold = set(row.get("frozen_representative_retrieval_units", []))
        if row.get("answer_status") == "unanswerable" or not gold:
            continue
        retrieved = predictions.get(row["id"], {}).get("retrieved_unit_ids")
        total += len(gold)
        if isinstance(retrieved, list):
            found = len(gold & set(retrieved))
            fraction = found / len(gold)
            observed.append(fraction)
            lengths.append(len(retrieved))
            complete += int(gold <= set(retrieved))
            recovered += found
        else:
            fraction = 0.0                      # no retrieved IDs submitted: nothing recovered
        fractions.append(fraction)
    if not observed:
        return None
    return {"answerable_questions": len(fractions), "questions_with_retrieved_ids": len(observed),
            "mean_retrieved_per_question": round(sum(lengths) / len(lengths), 2),
            "evidence_recall": round(100 * sum(fractions) / len(fractions), 2),
            "observed_evidence_recall": round(100 * sum(observed) / len(observed), 2),
            "complete_evidence_questions": complete,
            "micro_evidence_recall": round(100 * recovered / total, 2) if total else None}


def grade_rows(rows, predictions, judge=None, pre_judged=False, progress=None):
    """Attach ``status``/``correct``/``judge_reason`` to every selected question."""
    graded, pending = [], []
    for row in rows:
        prediction = predictions.get(row["id"])
        item = {key: value for key, value in (prediction or {}).items() if key not in PRESERVED_KEYS}
        item.update(id=row["id"], project=row.get("project"), task=task_of(row), category=row.get("category"),
                    answer_status=row.get("answer_status"), evidence_modality=row.get("evidence_modality"),
                    evidence_count_group=evidence_count_group(row) if row.get("answer_status") == "answerable" else None,
                    reference_answer=row["gold"]["answer"])
        if prediction is None:
            item.update(status="missing", correct=False, judge_reason="No prediction submitted")
        elif pre_judged:
            if prediction.get("status") == "unsupported":
                item.update(status="unsupported", correct=False, judge_reason=prediction.get("judge_reason") or "Unsupported input")
            elif isinstance(prediction.get("correct"), bool):
                item.update(status="graded", correct=prediction["correct"], judge_reason=prediction.get("judge_reason"))
            else:
                raise ValueError(f"--pre-judged requires a boolean 'correct' field for {row['id']}")
        elif not isinstance(prediction.get("answer"), str):
            item.update(status="invalid", correct=False, judge_reason="Prediction has no string 'answer'")
        else:
            item.update(status="pending", correct=None, judge_reason=None)
            pending.append((item, (row["q"], row["gold"]["answer"], prediction["answer"])))
        graded.append(item)
    if pending:
        if judge is None:
            raise ValueError("A judge is required unless --pre-judged is used")
        results = judge.judge_many([request for _, request in pending], progress=progress)
        for (item, _), result in zip(pending, results):
            if result.get("error"):
                item.update(status="error", correct=None, judge_reason=None, error=result["error"])
            else:
                item.update(status="graded", correct=result["correct"], judge_reason=result["reason"])
    return graded


def build_metrics(rows, graded, predictions, reference_retrieval=None, judge_info=None):
    errors = [item for item in graded if item["status"] == "error"]
    metrics = {"questions": len(rows), **summarize(graded),
               "statuses": {status: sum(item["status"] == status for item in graded)
                            for status in ("graded", "missing", "invalid", "unsupported", "error")},
               "unknown_prediction_ids": sorted(set(predictions) - {row["id"] for row in rows}),
               "complete": not errors,
               "categories": _groups(graded, "category"), "tasks": _groups(graded, "task"),
               "answerability": _groups(graded, "answer_status"),
               "evidence_modality": _groups([g for g in graded if g["answer_status"] == "answerable"], "evidence_modality"),
               "evidence_count": _groups([g for g in graded if g["answer_status"] == "answerable"], "evidence_count_group"),
               "retrieval": retrieval_recall(rows, predictions), "reference_retrieval": reference_retrieval,
               "judge": judge_info}
    if errors:
        metrics["accuracy"] = None
        metrics["error"] = f"{len(errors)} judgments failed; rerun to complete them (cached judgments are reused)"
    return metrics


def _fmt(value):
    return "–" if value is None else f"{value:.1f}"


def _table(title, groups, first="Group"):
    lines = [f"| {first} | n | submitted | correct | accuracy (%) | observed acc. (%) |", "|---|---:|---:|---:|---:|---:|"]
    for name, summary in groups.items():
        lines.append(f"| {name} | {summary['n']} | {summary['submitted']} | {summary['correct']} | "
                     f"{_fmt(summary['accuracy'])} | {_fmt(summary['observed_accuracy'])} |")
    return f"### {title}\n\n" + "\n".join(lines) + "\n"


def metrics_markdown(metrics, label="predictions"):
    head = [f"## MMEnterprise-Bench results: {label}", "",
            f"**Accuracy: {_fmt(metrics['accuracy'])}%** ({metrics['correct']} / {metrics['n']} questions; "
            f"{metrics['missing']} missing count as incorrect; observed accuracy over the {metrics['submitted']} "
            f"submitted: {_fmt(metrics['observed_accuracy'])}%)", ""]
    if metrics.get("error"):
        head += [f"**Incomplete:** {metrics['error']}", ""]
    if metrics.get("judge"):
        head += [f"Judge: `{metrics['judge'].get('model')}`" + (" (pre-judged input)" if metrics["judge"].get("pre_judged") else ""), ""]
    parts = [_table("Per category", metrics["categories"], "Category"),
             _table("Per task", metrics["tasks"], "Task"),
             _table("Answerability", metrics["answerability"], "Status"),
             _table("Evidence modality (answerable)", metrics["evidence_modality"], "Modality"),
             _table("Evidence record count (answerable)", metrics["evidence_count"], "Gold records")]
    tail = []
    if metrics.get("retrieval"):
        r = metrics["retrieval"]
        tail += ["### Evidence recall of the submitted `retrieved_unit_ids`", "",
                 f"Evidence Recall (macro over {r['answerable_questions']} answerable questions; questions without "
                 f"`retrieved_unit_ids` count 0): **{_fmt(r['evidence_recall'])}%**; observed over the "
                 f"{r['questions_with_retrieved_ids']} questions that submitted IDs: {_fmt(r['observed_evidence_recall'])}%; "
                 f"complete-evidence questions: {r['complete_evidence_questions']}; micro recall: "
                 f"{_fmt(r['micro_evidence_recall'])}%; mean retrieved per question: {r['mean_retrieved_per_question']}.", ""]
    if metrics.get("reference_retrieval"):
        r = metrics["reference_retrieval"]
        tail += [f"Reference: released `{r['retriever']}` retriever at k={r['k']} reaches Evidence Recall@k = "
                 f"{_fmt(r['evidence_recall'])}% on the same questions.", ""]
    if metrics.get("unknown_prediction_ids"):
        tail += [f"Ignored {len(metrics['unknown_prediction_ids'])} prediction(s) whose IDs are not in the selection.", ""]
    return "\n".join(head) + "\n" + "\n".join(parts) + "\n" + "\n".join(tail)


def score_predictions(dataset, predictions, output_dir=None, judge=None, pre_judged=False, retriever="2b", k=10,
                      limit=None, question_ids=None, label=None, progress=None, quiet=False):
    """Score ``predictions`` (path or ``{id: prediction}``) and return the metrics dict.

    When ``output_dir`` is given, writes ``metrics.json``, ``metrics.md`` and ``graded.jsonl`` there; the judge
    cache lives in ``<output_dir>/cache/judgments`` unless the ``Judge`` was created with its own ``cache_dir``.
    ``retriever``/``k`` only select the released ranking reported as a recall reference (set ``retriever=None``
    to skip it).
    """
    if not isinstance(predictions, dict):
        label = label or Path(predictions).name
        predictions = load_predictions(predictions)
    label = label or "predictions"
    output = Path(output_dir) if output_dir else None
    if judge is not None and output is not None and judge.cache_dir is None:
        judge.cache_dir = output / "cache/judgments"
    if judge is not None and not pre_judged:
        judge.check_credentials()
    rows = select_rows(dataset, limit, question_ids)
    if progress is None and not quiet:
        progress = lambda done, total: print(f"judged {done}/{total}", flush=True)
    graded = grade_rows(rows, predictions, judge, pre_judged, progress)
    reference = None
    if retriever:
        reference = {"retriever": retriever, "k": k, **dataset.recall(rows, retriever, k)}
        reference["evidence_recall"] = None if reference["evidence_recall"] is None else round(reference["evidence_recall"], 2)
        reference["micro_evidence_recall"] = None if reference["micro_evidence_recall"] is None else round(reference["micro_evidence_recall"], 2)
    judge_info = {"pre_judged": True} if pre_judged else {"model": judge.spec["model_id"], "base_url": judge.spec["base_url"],
                                                          "pre_judged": False, "api_calls": judge.calls,
                                                          "cache_hits": judge.cache_hits} if judge else None
    metrics = build_metrics(rows, graded, predictions, reference, judge_info)
    metrics["label"] = label
    if output is not None:
        output.mkdir(parents=True, exist_ok=True)
        atomic_json(output / "metrics.json", metrics)
        (output / "metrics.md").write_text(metrics_markdown(metrics, label), encoding="utf-8")
        with (output / "graded.jsonl").open("w", encoding="utf-8") as stream:
            for item in graded:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    if not quiet:
        print(metrics_markdown(metrics, label))
        if output is not None:
            print(f"Wrote {output / 'metrics.json'}, {output / 'metrics.md'}, {output / 'graded.jsonl'}")
    return metrics
