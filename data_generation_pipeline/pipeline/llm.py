"""Schema-validated API calls with response caching and cumulative retry feedback."""
from __future__ import annotations
import base64, hashlib, json, os, time
from pathlib import Path
import tempfile
from typing import Any, Type

import openai
from pydantic import BaseModel, ValidationError

from . import config
from .prompts import render_prompt

_client = None


def client():
    global _client
    if _client is None:
        _client = openai.OpenAI(api_key=os.environ[config.API["key_env"]], base_url=config.API["base_url"], timeout=config.API["timeout_seconds"], max_retries=0)
    return _client


def _img_part(path: str | Path) -> dict:
    p = Path(path)
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    b = base64.b64encode(p.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b}"}}


class LLM:
    def __init__(self, run: config.Run, model: str | None = None):
        self.run = run
        self.model = model or config.MODEL
        self.log_path = run.dir / "llm_log.jsonl"
        self._vocab = None  # archetype vocabulary, loaded lazily (stage 02 writes it)

    # ------------------------------------------------------------------ core
    def _cache_key(self, payload: dict) -> Path:
        h = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        return self.run.cache / f"{h}.json"

    def _raw(self, messages: list, json_mode: bool, tag: str, effort: str | None, max_tokens: int | None, extra: dict | None = None) -> str:
        payload = {"base_url": config.API["base_url"] or os.environ.get("OPENAI_BASE_URL"), "model": self.model, "messages": messages, "json": json_mode, "effort": effort, "max_tokens": max_tokens}
        if extra: payload["extra"] = extra  # attempt index / salt: a retry or a soft fallback is never a cache hit of the rejected answer
        cp = self._cache_key(payload)
        if cp.exists():
            return json.loads(cp.read_text())["content"]
        kw: dict[str, Any] = dict(model=self.model, messages=messages, store=False)
        if json_mode:
            kw["response_format"] = {"type": "json_object"}
        if effort:
            kw["reasoning_effort"] = effort
        if max_tokens:
            kw["max_completion_tokens"] = max_tokens
        last = None
        for attempt in range(config.API["transport_retries"]):
            try:
                t = time.time()
                r = client().chat.completions.create(**kw)
                content = r.choices[0].message.content or ""
                u = r.usage
                with open(self.log_path, "a") as f:
                    f.write(json.dumps({"tag": tag, "prompt_tokens": getattr(u, "prompt_tokens", 0), "completion_tokens": getattr(u, "completion_tokens", 0),
                                        "seconds": round(time.time() - t, 1), "cached": False}) + "\n")
                with tempfile.NamedTemporaryFile(mode="w", dir=cp.parent, delete=False) as cached:
                    json.dump({"content": content}, cached)
                    temp_path = Path(cached.name)
                temp_path.replace(cp)
                return content
            except (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as e:
                last = e; time.sleep(3 * (attempt + 1))
            except openai.APIError as e:
                raise RuntimeError(f"API request failed ({type(e).__name__}, status={getattr(e, 'status_code', None)})") from None
        raise RuntimeError(f"LLM call failed after retries ({type(last).__name__})") from None

    # ------------------------------------------------------------------ public
    def json(self, prompt: str, variables: dict, schema: Type[BaseModel] | None = None, images: list | None = None,
             tag: str = "", effort: str | None = "medium", max_tokens: int | None = None, retries: int | None = None,
             validator=None, salt: str | None = None):
        """Render prompts/<prompt>.md, call the model in JSON mode, validate with `schema` (pydantic) and an optional
        extra `validator(obj) -> list[str]` of error strings. On failure the errors are appended and the call retried."""
        retries = retries if retries is not None else config.SETTINGS["llm_retries"]
        if self._vocab is None:
            from . import archetype
            self._vocab = archetype.prompt_vars(archetype.load(self.run))
        variables = {**self._vocab, **variables}
        system, user = render_prompt(prompt, variables)
        if schema is not None:
            user += "\n\nReturn ONLY a JSON object matching this JSON schema:\n" + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        content: list = [{"type": "text", "text": user}]
        for im in images or []:
            content.append(_img_part(im))
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        errors_prev: list[str] = []; errors_all: list[str] = []
        for attempt in range(retries + 1):
            msgs = messages
            if errors_prev:
                # every rejection so far, not just the last one: fixing the latest complaint while re-breaking an earlier one
                # is the common failure of a model that only sees the previous list
                seen = list(dict.fromkeys(errors_all))
                msgs = messages + [{"role": "user", "content": f"Your answer was rejected ({attempt} time{'s' if attempt > 1 else ''} so far). Every one of these must hold at once — the latest rejection is marked *:\n- "
                                    + "\n- ".join(("* " if e in errors_prev else "") + e for e in seen[-16:]) + "\nFix every listed problem without reintroducing an earlier one, and return the full corrected JSON object."}]
            raw = self._raw(msgs, True, tag or prompt, effort, max_tokens, extra=({"attempt": attempt} if attempt else {}) | ({"salt": salt} if salt else {}) or None)
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError as e:
                errors_prev = [f"invalid JSON: {e}"]; errors_all += errors_prev; continue
            errs: list[str] = []
            parsed = obj
            if schema is not None:
                try:
                    parsed = schema.model_validate(obj)
                except ValidationError as e:
                    errs += [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:10]]
            if not errs and validator is not None:
                errs += list(validator(parsed))
            if not errs:
                return parsed
            errors_prev = errs; errors_all += errs
            with open(self.run.dir / "rejections.jsonl", "a") as f:  # every validator rejection, for tuning rule strictness
                f.write(json.dumps({"prompt": prompt, "tag": tag, "attempt": attempt + 1, "errors": errs[:6]}, ensure_ascii=False) + "\n")
            # bust the cache for the retry by appending the errors (different messages → different key)
        raise RuntimeError(f"[{prompt}] validation failed after {retries + 1} attempts: {errors_prev[:5]}")

    def text(self, prompt: str, variables: dict, images: list | None = None, tag: str = "", effort: str | None = "medium",
             max_tokens: int | None = None) -> str:
        if self._vocab is None:
            from . import archetype
            self._vocab = archetype.prompt_vars(archetype.load(self.run))
        variables = {**self._vocab, **variables}
        system, user = render_prompt(prompt, variables)
        content: list = [{"type": "text", "text": user}]
        for im in images or []:
            content.append(_img_part(im))
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
        return self._raw(messages, False, tag or prompt, effort, max_tokens)

    def usage(self) -> dict:
        tot = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        if self.log_path.exists():
            for line in self.log_path.read_text().splitlines():
                d = json.loads(line); tot["calls"] += 1
                tot["prompt_tokens"] += d["prompt_tokens"]; tot["completion_tokens"] += d["completion_tokens"]
        return tot
