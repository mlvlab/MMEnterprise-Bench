"""Resumable answer generation, judging, and reporting."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import tempfile
import threading

from .data import Dataset, TASKS, default_dataset_dir, digest, read_json, task_of
from .providers import Client, ProviderError, judge_body, reader_body

HERE = Path(__file__).resolve().parent          # package directory: configs/ and prompts/ live here
DEFAULT_CONFIG = HERE / "configs/default.json"


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
        temporary = output.name
    os.replace(temporary, path)


@contextmanager
def file_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_config(path, overrides=None):
    config = {**read_json(path), **(overrides or {})}
    registry = read_json(HERE / "configs/models.json")
    config["models"] = config.get("models", ["luna"])
    config["retrievers"] = [value.lower() for value in config.get("retrievers", ["2b"])]
    config["k"] = config.get("k", [10])
    config["reader_specs"] = {}
    for name in config["models"]:
        if name not in registry:
            raise ValueError("Unknown model: " + name)
        spec = dict(registry[name])
        overrides = config.get("model_overrides", {}).get(name, {})
        spec.update(overrides)
        for field in ("base_url", "model_id"):
            variable = spec.get(field + "_env")
            if variable and os.environ.get(variable):
                spec[field] = os.environ[variable]
        config["reader_specs"][name] = spec
    config["judge_spec"] = {**registry["luna"], **config.get("judge", {})}
    config["judge_spec"]["generation"] = config.get("judge", {}).get("generation", {
        "reasoning_effort": "low", "max_completion_tokens": 4096,
        "store": False, "response_format": {"type": "json_object"}})
    for field in ("base_url", "model_id"):
        variable = config["judge_spec"].get(field + "_env")
        if variable and os.environ.get(variable):
            config["judge_spec"][field] = os.environ[variable]
    if config["judge_spec"]["provider"] == "gemini":
        raise ValueError("The judge requires an OpenAI-compatible endpoint")
    for key, default in (("dataset", default_dataset_dir()), ("output", "outputs/eval")):
        value = Path(config.get(key, default))
        config[key] = str(value if value.is_absolute() else Path.cwd() / value)
    config["reader_system"] = (HERE / "prompts/answer_system.txt").read_text(encoding="utf-8").strip()
    config["judge_system"] = (HERE / "prompts/judge_system.txt").read_text(encoding="utf-8").strip()
    if not config["models"] or not config["retrievers"] or not config["k"]:
        raise ValueError("Select at least one model, retriever, and k")
    if not set(config["retrievers"]) <= {"2b", "8b"} or not set(config["k"]) <= {10, 50, 100}:
        raise ValueError("Supported retrievers: 2b, 8b; supported k: 10, 50, 100")
    if not 1 <= config.get("concurrency", 8) <= 256:
        raise ValueError("Concurrency must be between 1 and 256")
    if not 1 <= config.get("judge_concurrency", 8) <= 256:
        raise ValueError("Judge concurrency must be between 1 and 256")
    if config.get("limit") is not None and config["limit"] <= 0:
        raise ValueError("Limit must be a positive integer")
    return config


class Pipeline:
    def __init__(self, config, client=None):
        self.config = config
        self.dataset = Dataset(config["dataset"])
        self.rows = self.dataset.rows
        ids = config.get("question_ids")
        if ids:
            if not set(ids) <= {row["id"] for row in self.rows}:
                raise ValueError("Unknown question ID")
            self.rows = [row for row in self.rows if row["id"] in ids]
        if config.get("limit"):
            self.rows = self.rows[:config["limit"]]
        if not self.rows:
            raise ValueError("No selected questions")
        self.output = Path(config["output"])
        self.client = client or Client(config.get("timeout_seconds", 180), config.get("max_attempts", 5))
        self.judge_gate = threading.Semaphore(config.get("judge_concurrency", 8))
        self.stop = threading.Event()

    def conditions(self):
        return [(model, retriever, k) for model in self.config["models"]
                for retriever in self.config["retrievers"] for k in self.config["k"]]

    def jobs(self):
        return [(row, model, retriever, k) for model, retriever, k in self.conditions() for row in self.rows]

    def answer_key(self, row, model, selected):
        return digest({"format": 1, "input": self.dataset.input_identity(row, selected),
                       "model": self.config["reader_specs"][model], "system": self.config["reader_system"]})

    def grade_key(self, row, answer):
        return digest({"format": 1, "question": row["q"], "reference_answer": row["gold"]["answer"],
                       "candidate_answer": answer, "judge": self.config["judge_spec"],
                       "system": self.config["judge_system"]})

    def cell_path(self, row, model, retriever, k):
        filename = digest(row["id"]) + ".json"
        return self.output / "cells" / f"{model}_{retriever}_k{k}" / filename

    def cell_identity(self, row, model, retriever, k):
        return {"question_id": row["id"], "model": model, "retriever": retriever, "k": k,
                "task": task_of(row), "answer_status": row.get("answer_status"),
                "project": row.get("project")}

    def process(self, job, stage="run"):
        row, model, retriever, k = job
        if self.stop.is_set():
            return {**self.cell_identity(row, model, retriever, k), "status": "pending"}
        selected = self.dataset.selected(row, retriever, k)
        answer_key = self.answer_key(row, model, selected)
        cell = {**self.cell_identity(row, model, retriever, k), "answer_key": answer_key,
                "status": "pending", "retrieved_unit_ids": [unit["id"] for unit in selected]}
        path = self.cell_path(row, model, retriever, k)
        cache = self.output / "cache/answers" / (answer_key + ".json")
        try:
            with file_lock(cache.with_suffix(".lock")):
                if cache.exists():
                    response = read_json(cache)
                    if response.get("input_hash") != answer_key:
                        raise ValueError("Answer cache identity mismatch")
                elif stage == "judge":
                    cell["pending_reason"] = "answer_missing"
                    atomic_json(path, cell)
                    return cell
                else:
                    spec = self.config["reader_specs"][model]
                    body = reader_body(self.dataset, row, selected, spec, self.config["reader_system"])
                    try:
                        result = self.client.answer(spec, body)
                        response = {**result, "input_hash": answer_key, "status": "answered"}
                    except ProviderError as exc:
                        if not exc.capacity or not self.config.get("capacity_errors_as_incorrect", True):
                            raise
                        response = {"status": "unsupported", "input_hash": answer_key,
                                    "error_code": exc.code, "http_status": exc.status}
                    atomic_json(cache, response)
            if response["status"] == "unsupported" and not self.config.get("capacity_errors_as_incorrect", True):
                raise ProviderError(response["error_code"], capacity=True)
            if response["status"] == "unsupported":
                cell.update(status="unsupported", correct=False, error_code=response["error_code"])
            else:
                cell.update(answer=response["answer"], finish_reason=response.get("finish_reason"),
                            answer_usage=response.get("usage"), status="answered")
                grade_key = self.grade_key(row, response["answer"])
                grade_path = self.output / "cache/judgments" / (grade_key + ".json")
                if stage != "answer" or grade_path.exists():
                    with file_lock(grade_path.with_suffix(".lock")):
                        if grade_path.exists():
                            grade = read_json(grade_path)
                            if grade.get("input_hash") != grade_key:
                                raise ValueError("Judgment cache identity mismatch")
                        else:
                            with self.judge_gate:
                                result = self.client.judge(self.config["judge_spec"],
                                    judge_body(row, response["answer"], self.config["judge_spec"], self.config["judge_system"]))
                            grade = {**result, "input_hash": grade_key}
                            atomic_json(grade_path, grade)
                    cell.update(status="graded", correct=grade["correct"], reason=grade["reason"],
                                grade_key=grade_key, judge_usage=grade.get("usage"))
        except ProviderError as exc:
            cell["error"] = {"code": exc.code, "http_status": exc.status, "retryable": exc.retryable}
            if exc.status in {401, 403} or exc.code in {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active"}:
                self.stop.set()
        atomic_json(path, cell)
        return cell

    def current_cell(self, row, model, retriever, k):
        path = self.cell_path(row, model, retriever, k)
        pending = {**self.cell_identity(row, model, retriever, k), "status": "pending"}
        if not path.exists():
            return pending
        cell = {**read_json(path), **self.cell_identity(row, model, retriever, k)}
        selected = self.dataset.selected(row, retriever, k)
        if cell.get("answer_key") != self.answer_key(row, model, selected):
            return pending
        if cell.get("status") == "unsupported" and not self.config.get("capacity_errors_as_incorrect", True):
            return pending
        if cell.get("status") == "graded" and cell.get("grade_key") != self.grade_key(row, cell["answer"]):
            return {**cell, "status": "answered", "correct": None}
        return cell

    @staticmethod
    def summarize(cells):
        completed = [cell for cell in cells if cell["status"] in {"graded", "unsupported"}]
        correct = sum(cell.get("correct") is True for cell in completed)
        n = len(cells)
        return {"questions": n, "graded": sum(cell["status"] == "graded" for cell in cells),
                "unsupported": sum(cell["status"] == "unsupported" for cell in cells),
                "pending": n - len(completed), "correct": correct,
                "accuracy": 100 * correct / n if n and len(completed) == n else None,
                "observed_accuracy": 100 * correct / len(completed) if completed else None,
                "coverage": 100 * len(completed) / n if n else None}

    def metrics(self):
        summaries, cell_rows = [], []
        for model, retriever, k in self.conditions():
            cells = [self.current_cell(row, model, retriever, k) for row in self.rows]
            row = {"model": model, "retriever": retriever, "k": k, **self.summarize(cells),
                   **self.dataset.recall(self.rows, retriever, k)}
            row["tasks"] = {task: self.summarize([cell for cell in cells if cell["task"] == task]) for task in TASKS}
            row["answerability"] = {status: self.summarize([cell for cell in cells if cell.get("answer_status") == status])
                                    for status in ("answerable", "unanswerable")}
            summaries.append(row)
            cell_rows.extend(cells)
        atomic_json(self.output / "metrics.json", summaries)
        fields = ["model", "retriever", "k", "questions", "graded", "unsupported", "pending", "correct",
                  "accuracy", "observed_accuracy", "coverage", "evidence_recall"]
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output / "metrics.csv").open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(destination, fieldnames=fields + list(TASKS))
            writer.writeheader()
            for summary in summaries:
                writer.writerow({**{key: summary[key] for key in fields},
                                 **{task: summary["tasks"][task]["accuracy"] for task in TASKS}})
        with (self.output / "results.jsonl").open("w", encoding="utf-8") as destination:
            for cell in cell_rows:
                destination.write(json.dumps(cell, ensure_ascii=False) + "\n")
        return summaries

    def plan(self):
        for row in self.rows:
            for _, retriever, k in self.conditions():
                self.dataset.selected(row, retriever, k)
        return {"selected_questions": len(self.rows), "models": self.config["models"],
                "conditions": len(self.conditions()), "answer_cells": len(self.jobs()),
                "maximum_judge_calls_before_retries": len(self.jobs()),
                "output": str(self.output), "reader_concurrency": self.config.get("concurrency", 8),
                "judge_concurrency": self.config.get("judge_concurrency", 8)}

    def run(self, stage="run"):
        self.plan()
        if stage != "judge":
            for spec in self.config["reader_specs"].values():
                self.client.check_credentials(spec)
                if hasattr(self.client, "check_local_model"):
                    self.client.check_local_model(spec)
        if stage != "answer":
            self.client.check_credentials(self.config["judge_spec"])
        jobs = self.jobs()
        self.output.mkdir(parents=True, exist_ok=True)
        saved = {key: value for key, value in self.config.items() if key not in {"dataset", "output"}}
        manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "config": saved,
                    "question_ids": [row["id"] for row in self.rows], "stage": stage}
        atomic_json(self.output / "runs" / (digest(manifest) + ".json"), manifest)
        done, pending, unsupported = 0, 0, 0
        with ThreadPoolExecutor(max_workers=self.config.get("concurrency", 8)) as pool:
            futures = [pool.submit(self.process, job, stage) for job in jobs]
            try:
                for future in as_completed(futures):
                    result = future.result()
                    done += 1
                    pending += int(result["status"] not in {"graded", "unsupported"} if stage != "answer" else result["status"] == "pending")
                    unsupported += int(result["status"] == "unsupported")
                    if done == 1 or done % 25 == 0 or done == len(jobs):
                        print(f"{done}/{len(jobs)} processed; {pending} pending; {unsupported} unsupported", flush=True)
            except KeyboardInterrupt:
                self.stop.set()
                for future in futures:
                    future.cancel()
                raise
        summaries = self.metrics()
        return summaries, pending
