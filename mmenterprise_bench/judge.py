"""LLM judge with a content-addressed judgment cache.

The judge sees exactly three JSON fields (question, reference_answer, candidate_answer) under the
system prompt in ``prompts/judge_system.txt`` and must return ``{"correct": bool, "reason": str}``.
Any OpenAI-compatible chat-completions server can act as the judge (``base_url``), so an open-source
model served by vLLM, or a mock server in tests, can replace the paper's GPT-5.6 Luna judge.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading

from .data import digest, read_json
from .pipeline import atomic_json, file_lock
from .providers import Client, ProviderError

HERE = Path(__file__).resolve().parent
DEFAULT_JUDGE_MODEL = "gpt-5.6-luna"
DEFAULT_JUDGE_BASE_URL = "https://api.openai.com/v1"
DEFAULT_JUDGE_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_JUDGE_GENERATION = {"reasoning_effort": "low", "max_completion_tokens": 4096, "store": False,
                            "response_format": {"type": "json_object"}}


def judge_system_prompt():
    return (HERE / "prompts/judge_system.txt").read_text(encoding="utf-8").strip()


class Judge:
    """Judge candidate answers; identical (question, reference, candidate, model, prompt) tuples hit the cache."""

    def __init__(self, model=DEFAULT_JUDGE_MODEL, base_url=DEFAULT_JUDGE_BASE_URL, api_key_env=DEFAULT_JUDGE_API_KEY_ENV,
                 cache_dir=None, concurrency=8, client=None, generation=None, system=None, timeout=180, attempts=5):
        if not 1 <= int(concurrency) <= 256:
            raise ValueError("Judge concurrency must be between 1 and 256")
        self.generation = dict(DEFAULT_JUDGE_GENERATION if generation is None else generation)
        self.system = system if system is not None else judge_system_prompt()
        # "local" = OpenAI-compatible endpoint that may run without a key (vLLM, mock); same wire format.
        provider = "openai" if "api.openai.com" in base_url else "local"
        self.spec = {"provider": provider, "model_id": model, "base_url": base_url.rstrip("/"),
                     "api_key_env": api_key_env, "generation": self.generation}
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.concurrency = int(concurrency)
        self.client = client or Client(timeout=timeout, attempts=attempts)
        self.http = client is None            # an injected client (tests, custom transports) needs no key
        self.calls = 0
        self.cache_hits = 0
        self._counter_lock = threading.Lock()

    # -- identity -----------------------------------------------------------------------------
    def key(self, question, reference, candidate):
        return digest({"format": 1, "question": question, "reference_answer": reference,
                       "candidate_answer": candidate, "judge_model": self.spec["model_id"],
                       "generation": self.generation, "system": self.system})

    def body(self, question, reference, candidate):
        from .providers import judge_body
        row = {"q": question, "gold": {"answer": reference}}
        return judge_body(row, candidate, self.spec, self.system)

    def check_credentials(self):
        """The hosted OpenAI endpoint needs a key; local/vLLM/mock servers may run without one."""
        variable = self.spec["api_key_env"]
        if self.http and "api.openai.com" in self.spec["base_url"] and not os.environ.get(variable):
            raise ValueError(f"Set the environment variable {variable} (or pass --judge-base-url for a local judge)")

    # -- single judgment ----------------------------------------------------------------------
    def judge(self, question, reference, candidate):
        """Return ``{"correct": bool, "reason": str, "cached": bool, "key": sha256}``."""
        key = self.key(question, reference, candidate)
        path = self.cache_dir / (key + ".json") if self.cache_dir else None
        if path is not None:
            with file_lock(path.with_suffix(".lock")):
                if path.exists():
                    cached = read_json(path)
                    if cached.get("input_hash") == key and isinstance(cached.get("correct"), bool):
                        with self._counter_lock:
                            self.cache_hits += 1
                        return {"correct": cached["correct"], "reason": cached.get("reason", ""), "cached": True, "key": key}
                result = self._call(question, reference, candidate)
                atomic_json(path, {**result, "input_hash": key})
        else:
            result = self._call(question, reference, candidate)
        return {"correct": result["correct"], "reason": result["reason"], "cached": False, "key": key}

    def _call(self, question, reference, candidate):
        with self._counter_lock:
            self.calls += 1
        result = self.client.judge(self.spec, self.body(question, reference, candidate))
        return {"correct": result["correct"], "reason": result["reason"],
                "usage": result.get("usage"), "returned_model": result.get("returned_model")}

    # -- batch --------------------------------------------------------------------------------
    def judge_many(self, items, progress=None):
        """Judge ``items`` (iterable of (question, reference, candidate)) concurrently, preserving order.

        Returns a list of dicts; a failed request yields ``{"error": code}`` instead of raising, so one
        transient failure does not lose the rest of the run. Re-running completes the failed ones.
        """
        items = list(items)
        results = [None] * len(items)
        done = 0
        lock = threading.Lock()

        def work(index):
            question, reference, candidate = items[index]
            try:
                return self.judge(question, reference, candidate)
            except ProviderError as exc:
                return {"correct": None, "reason": None, "cached": False, "error": exc.code}

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            for index, result in zip(range(len(items)), pool.map(work, range(len(items)))):
                results[index] = result
                with lock:
                    done += 1
                    if progress and (done == 1 or done % 50 == 0 or done == len(items)):
                        progress(done, len(items))
        return results
