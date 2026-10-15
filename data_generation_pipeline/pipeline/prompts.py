"""Strict prompt rendering with required runtime variables."""
from __future__ import annotations
import re
from functools import lru_cache
from pathlib import Path
from . import config

_VAR = re.compile(r"\{\{(\w+)\}\}")


@lru_cache(maxsize=None)
def _load(name: str) -> tuple[str, str]:
    p = config.PROMPT_DIR / (name + ".md")
    if not p.exists():
        raise FileNotFoundError(f"prompt not found: {p}")
    txt = p.read_text(encoding="utf-8")
    if "---USER---" in txt:
        system, user = txt.split("---USER---", 1)
    else:
        system, user = "", txt
    return system.strip(), user.strip()


def render_prompt(name: str, variables: dict) -> tuple[str, str]:
    system, user = _load(name)
    shared = (config.PROMPT_DIR / "_shared" / "style.md")
    common = shared.read_text(encoding="utf-8").strip() if shared.exists() and not name.startswith("07_validate/") else ""

    def sub(s: str) -> str:
        def rep(m):
            k = m.group(1)
            if k not in variables:
                raise KeyError(f"prompt {name}: missing variable {{{{{k}}}}}")
            v = variables[k]
            return v if isinstance(v, str) else _to_text(v)
        return _VAR.sub(rep, s)

    sys_full = (sub(common) + "\n\n" + sub(system)).strip() if common else sub(system)
    usr_full = sub(user)
    return sys_full, usr_full


def _to_text(v) -> str:
    import json
    return json.dumps(v, ensure_ascii=False, indent=1)


def list_prompts() -> list[str]:
    return sorted(str(p.relative_to(config.PROMPT_DIR))[:-3] for p in config.PROMPT_DIR.rglob("*.md"))
