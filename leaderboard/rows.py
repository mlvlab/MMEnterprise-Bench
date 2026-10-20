"""Leaderboard row schema and the settings vocabulary shared by the app, the seeder and the tests."""
from __future__ import annotations

from datetime import datetime, timezone

from validate import TASKS

SETTINGS = [
    "Released retrieval 2B · k=10",
    "Released retrieval 2B · k=50",
    "Released retrieval 2B · k=100",
    "Released retrieval 8B · k=10",
    "Released retrieval 8B · k=50",
    "Released retrieval 8B · k=100",
    "Own retrieval",
    "Other / full workspace",
]
DEFAULT_SETTING = SETTINGS[0]
ALL_SETTINGS = "All"
PAPER_LINK = "https://github.com/mlvlab/MMEnterprise-Bench"
PAPER_ORG = "MMEnterprise-Bench authors"
PAPER_JUDGE = "gpt-5.6-luna"
PAPER_DATE = "2026-10-20"      # release date of the paper baselines; keeps seeded rows stable across reseeds


def setting_for(retriever: str, k: int) -> str:
    return f"Released retrieval {retriever.upper()} · k={k}"


def short_setting(setting: str) -> str:
    """Compact table label: 'Released retrieval 2B · k=10' -> '2B · k=10'; other settings unchanged."""
    prefix = "Released retrieval "
    return setting[len(prefix):] if (setting or "").startswith(prefix) else (setting or "")


def build_row(*, row_id: str, model: str, organization: str, link: str, setting: str, judge: str, date: str,
              source: str, summary: dict, notes: str = "") -> dict:
    """One approved leaderboard entry (one line of results.jsonl)."""
    assert source in ("paper", "community")
    return {
        "id": row_id,
        "model": model,
        "organization": organization,
        "link": link or "",
        "setting": setting,
        "judge": judge,
        "date": date,
        "source": source,
        "overall": summary["overall"],
        "categories": summary["categories"],
        "tasks": {t: summary["tasks"].get(t) for t in TASKS},
        "missing": summary.get("missing", 0),
        "answerability": summary.get("answerability"),
        "evidence_modality": summary.get("evidence_modality"),
        "evidence_count": summary.get("evidence_count"),
        "retrieval_recall": summary.get("retrieval_recall"),
        # exact correct/n per group: the tables round these half-up to one decimal (matches the paper tables)
        "counts": summary.get("counts"),
        "notes": notes or "",
        "hidden": False,
    }


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
