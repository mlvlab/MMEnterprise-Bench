"""Provider requests, bounded retries, and response parsing."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import random
import re
import time
import urllib.error
import urllib.request


class ProviderError(Exception):
    def __init__(self, code, *, retryable=False, capacity=False, status=None, retry_after=0):
        self.code = str(code) if code is not None and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", str(code)) else "provider_error"
        self.retryable, self.capacity, self.status = retryable, capacity, status
        self.retry_after = retry_after
        super().__init__(self.code)


def parts(dataset, row, selected, provider, image_detail):
    result = []
    for unit in selected:
        if unit.get("text"):
            result.append({"text": unit["text"]} if provider == "gemini" else
                          {"type": "text", "text": unit["text"]})
        if unit.get("image"):
            path = dataset.image_path(unit)
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            mime = mimetypes.guess_type(path)[0] or "image/png"
            if provider == "gemini":
                result.append({"inlineData": {"mimeType": mime, "data": encoded}})
            else:
                result.append({"type": "image_url", "image_url": {
                    "url": f"data:{mime};base64,{encoded}", "detail": image_detail}})
    question = "Question: " + row["q"]
    result.append({"text": question} if provider == "gemini" else {"type": "text", "text": question})
    return result


def reader_body(dataset, row, selected, spec, system):
    content = parts(dataset, row, selected, spec["provider"], spec.get("image_detail", "high"))
    if spec["provider"] == "gemini":
        return {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": content}],
                "generationConfig": spec["generation"]}
    return {"model": spec["model_id"], "messages": [{"role": "system", "content": system},
            {"role": "user", "content": content}], **spec["generation"]}


def judge_body(row, answer, spec, system):
    fields = {"question": row["q"], "reference_answer": row["gold"]["answer"],
              "candidate_answer": answer}
    return {"model": spec["model_id"], **spec["generation"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(fields, ensure_ascii=False)}]}


def _capacity_error(code, message):
    if code in {"context_length_exceeded", "context_capacity_exceeded", "too_many_images", "image_limit_exceeded"}:
        return True
    patterns = (
        r"maximum context length.*(?:requested|resulted|tokens)",
        r"(?:prompt|input)(?: token)? (?:length|tokens).*exceeds.*(?:context|maximum|model)",
        r"at most \d+ images.*(?:request|prompt)",
        r"number of (?:image|images).*exceeds.*(?:limit|maximum)",
    )
    return any(re.search(pattern, message, re.I | re.S) for pattern in patterns)


class Client:
    def __init__(self, timeout=180, attempts=5, backoff=1.0):
        self.timeout, self.attempts, self.backoff = timeout, attempts, backoff

    @staticmethod
    def check_credentials(spec):
        variable = spec.get("api_key_env")
        if variable and not os.environ.get(variable) and spec["provider"] != "local":
            raise ValueError("Set the environment variable " + variable)

    def request(self, spec, body):
        self.check_credentials(spec)
        base = spec["base_url"].rstrip("/")
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(spec.get("api_key_env", ""), "")
        if spec["provider"] == "gemini":
            url = base + "/models/" + spec["model_id"] + ":generateContent"
            headers["x-goog-api-key"] = key
        else:
            url = base + "/chat/completions"
            if key:
                headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(url, json.dumps(body).encode("utf-8"), headers)
        for attempt in range(self.attempts):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                try:
                    error = json.loads(exc.read()).get("error", {})
                    if not isinstance(error, dict):
                        error = {}
                except (ValueError, UnicodeDecodeError):
                    error = {}
                code = error.get("code") or error.get("type") or "http_error"
                status = exc.code
                capacity = status == 400 and _capacity_error(str(code), str(error.get("message", "")))
                retryable = status in {408, 409, 429, 500, 502, 503, 504}
                if str(code) in {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active"}:
                    retryable = False
                try:
                    retry_after = float(exc.headers.get("Retry-After", 0))
                except (TypeError, ValueError):
                    retry_after = 0
                failure = ProviderError(code, retryable=retryable, capacity=capacity,
                                        status=status, retry_after=retry_after)
            except (urllib.error.URLError, TimeoutError, OSError):
                failure = ProviderError("transport_error", retryable=True)
            except (ValueError, UnicodeDecodeError):
                failure = ProviderError("invalid_provider_json", retryable=True)
            if not failure.retryable or attempt + 1 == self.attempts:
                raise failure
            time.sleep(min(60, max(failure.retry_after, self.backoff * 2 ** attempt)) + random.uniform(0, 0.25))
        raise ProviderError("request_failed")

    def _answer_once(self, spec, body):
        data = self.request(spec, body)
        try:
            if spec["provider"] == "gemini":
                candidate = data["candidates"][0]
                finish = candidate.get("finishReason", "")
                if finish not in {"STOP", "MAX_TOKENS"}:
                    raise ProviderError("blocked_or_incomplete_response", retryable=False)
                chunks = candidate.get("content", {}).get("parts", [])
                answer = "".join(part.get("text", "") for part in chunks if not part.get("thought"))
                return {"answer": answer, "finish_reason": finish,
                        "usage": data.get("usageMetadata", {}), "returned_model": data.get("modelVersion")}
            choice = data["choices"][0]
            finish = choice.get("finish_reason")
            if finish not in {"stop", "length"}:
                raise ProviderError("blocked_or_incomplete_response", retryable=False)
            answer = choice["message"].get("content")
            if answer is None:
                answer = ""
            if not isinstance(answer, str):
                raise ProviderError("invalid_answer_format", retryable=True)
            return {"answer": answer, "finish_reason": finish,
                    "usage": data.get("usage", {}), "returned_model": data.get("model")}
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("invalid_answer_response", retryable=True) from exc

    def answer(self, spec, body):
        for attempt in range(self.attempts):
            try:
                return self._answer_once(spec, body)
            except ProviderError as exc:
                if not exc.retryable or exc.code not in {"invalid_answer_response", "invalid_answer_format"} or attempt + 1 == self.attempts:
                    raise
        raise ProviderError("invalid_answer_response", retryable=True)

    def check_local_model(self, spec):
        if spec["provider"] != "local":
            return
        headers = {}
        key = os.environ.get(spec.get("api_key_env", ""), "")
        if key:
            headers["Authorization"] = "Bearer " + key
        request = urllib.request.Request(spec["base_url"].rstrip("/") + "/models", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                ids = {model["id"] for model in json.load(response)["data"]}
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
            raise ValueError("Cannot read the local server model list for " + spec["model_id"])
        if spec["model_id"] not in ids:
            raise ValueError("The local endpoint is not serving " + spec["model_id"])

    def judge(self, spec, body):
        for attempt in range(self.attempts):
            try:
                response = self.answer(spec, body)
                value = json.loads(response["answer"])
                if set(value) != {"correct", "reason"} or type(value["correct"]) is not bool or not isinstance(value["reason"], str):
                    raise ValueError("Invalid judgment schema")
                return {**value, "usage": response["usage"], "returned_model": response["returned_model"]}
            except (ValueError, TypeError):
                if attempt + 1 == self.attempts:
                    raise ProviderError("invalid_judgment", retryable=True)
        raise ProviderError("invalid_judgment", retryable=True)
