"""MMEnterprise-Bench Leaderboard — Gradio Space.

Three tabs: public leaderboard, submission form (uploads go to a private queue), and a password-protected admin
tab that approves / rejects / hides entries. No LLM API is called here: contributors run ``mmeb score`` locally
and upload the resulting ``metrics.json``.

Environment: STORAGE=hf|local, HF_TOKEN, QUEUE_REPO, RESULTS_REPO, ADMIN_PASSWORD (use a long random string; failed
unlocks are delayed and a client that keeps failing is locked out for a few minutes), LOCAL_STORAGE_DIR (local), ALLOW_PRE_JUDGED=1 (optional admin override: queue pre-judged files
flagged for review instead of rejecting them), MAX_PER_CONTACT_PER_DAY / MAX_PER_CLIENT_PER_HOUR / MAX_GLOBAL_PER_HOUR
(abuse limits; defaults 5 / 10 / 30).
"""
from __future__ import annotations

import hashlib
import html
import logging
import os
import re
import secrets
import threading
import time
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import gradio as gr
import pandas as pd

from rows import ALL_SETTINGS, DEFAULT_SETTING, SETTINGS, build_row, short_setting, today
from storage import get_storage, is_valid_row_id, is_valid_submission_id, normalize_contact, utc_now
from validate import (MAX_METRICS_BYTES, TASKS, ValidationError, load_metrics_bytes, validate_metrics,
                      validate_predictions)

TITLE = "MMEnterprise-Bench Leaderboard"
GITHUB = "https://github.com/mlvlab/MMEnterprise-Bench"
DATASET = "https://huggingface.co/datasets/MMEnterprise-Bench/MMEnterprise-Bench"
PROJECT = "https://mlvlab.github.io/MMEnterprise-Bench/"
BRAND = "#2b4f9e"
MAX_PER_CONTACT_PER_DAY = int(os.environ.get("MAX_PER_CONTACT_PER_DAY", "5"))
MAX_PER_CLIENT_PER_HOUR = int(os.environ.get("MAX_PER_CLIENT_PER_HOUR", "10"))
MAX_GLOBAL_PER_HOUR = int(os.environ.get("MAX_GLOBAL_PER_HOUR", "30"))
MAX_UPLOAD = "26mb"                      # HTTP-layer cap (predictions.jsonl <= 25 MB); checked again after upload
ALLOW_PRE_JUDGED = os.environ.get("ALLOW_PRE_JUDGED", "").strip() in ("1", "true", "yes")
ADMIN_FAIL_LIMIT = 10                    # failed unlocks from one client before that client is locked out
ADMIN_COOLDOWN_SECONDS = 300
ADMIN_FAIL_DELAY_SECONDS = 1.0           # delay after a failed Unlock/Refresh (the write actions return at once)
ADMIN_FAIL_TABLE_MAX = 5000              # distinct clients tracked at most
LINK_RE = re.compile(r"^https?://[^\s<>()\"']+$")

log = logging.getLogger("mmeb-leaderboard")

STORAGE = get_storage()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

MAIN_COLS = ["Rank", "Model", "Organization", "Setting", "Overall", "Evidence Reasoning", "Conflict Resolution",
             "Unanswerability", "Judge", "Date", "Source", "Link"]
# relative weights; ``_widths`` rescales the visible columns so they always sum to 100%
MAIN_WEIGHTS = {"Rank": 4.5, "Model": 13, "Organization": 10.5, "Setting": 8, "Overall": 7, "Evidence Reasoning": 9,
                "Conflict Resolution": 9, "Unanswerability": 12, "Judge": 10, "Date": 9.5, "Source": 7.5, "Link": 4.5}
TASK_COLS = ["Rank", "Model", "Setting"] + TASKS
TASK_WEIGHTS = {"Rank": 5.5, "Model": 12, "Setting": 9, "Unanswerability": 11}
PENDING_WIDTHS = ["230px", "150px", "130px", "110px", "100px", "75px", "60px", "60px", "60px", "80px", "95px", "120px",
                  "150px", "180px", "180px", "200px", "240px"]
APPROVED_WIDTHS = ["230px", "180px", "160px", "120px", "80px", "90px", "100px", "70px"]


def _widths(columns, weights, default=9.0):
    """Percent widths for the visible columns, rescaled to sum to 100%."""
    raw = [float(weights.get(c, default)) for c in columns]
    total = sum(raw) or 1.0
    return [f"{100 * w / total:.2f}%" for w in raw]

INTRO = f"""
**MMEnterprise-Bench** is a QA benchmark over simulated company workspaces (Slack, email, Notion, meeting notes,
slides, PDF pages, charts) whose records are revised and contradicted over time; it tests evidence reasoning,
conflict resolution and unanswerability for enterprise agents.
Scores are **self-reported** with the official `mmeb score` protocol (run locally with your own judge key) and
**reviewed by the maintainers** before they are listed.
[GitHub]({GITHUB}) · [Dataset]({DATASET}) · [Project page]({PROJECT})
"""

FOOTNOTE = "Accuracy over all 1,613 questions; missing predictions count as incorrect."

SUBMIT_HELP = f"""
Run the official scorer locally (it needs your own judge API key; the Space never calls an LLM), then upload the
`metrics.json` it writes:

```bash
pip install git+{GITHUB}
mmeb download
mmeb score predictions.jsonl --output outputs/my_agent      # -> upload outputs/my_agent/metrics.json
```

Attach `predictions.jsonl` as well if you can (optional, recommended for audit, at most 25 MB). Your entry is
queued and appears on the leaderboard once a maintainer approves it; the contact e-mail is kept private.
"""

CSS = f"""
:root {{ --mmeb-blue: {BRAND}; }}
.gradio-container {{ width: 100% !important; max-width: 1280px !important; margin: 0 auto !important; }}
#title h1 {{ color: var(--mmeb-blue); font-size: 1.8rem; margin-bottom: 0.2rem; }}
#intro p {{ margin: 0.25rem 0; }}
#footnote p, #footnote {{ color: #6b7280; font-size: 0.85rem; }}
.tabitem {{ padding-top: 0.5rem; }}
table.table thead th {{ background: #eef2fb !important; color: #1e3870 !important; font-weight: 600 !important; }}
.table td, .table th {{ font-size: 0.92rem; }}
#lb-table *, #task-table *, #pending-table *, #results-table * {{ font-family: var(--font) !important; }}
#lb-table th .header-button span, #task-table th .header-button span, #lb-table th .header-content,
#task-table th .header-content {{ white-space: normal !important; overflow: visible !important;
  text-overflow: clip !important; line-height: 1.25; }}
#lb-table th .header-button, #task-table th .header-button {{ white-space: normal !important; overflow: visible !important; }}
#lb-table th *, #task-table th *, #pending-table th *, #results-table th * {{ word-break: keep-all !important;
  overflow-wrap: normal !important; hyphens: manual !important; }}
#task-table th .header-content, #task-table th .header-button span {{ font-size: 0.85rem; }}
/* narrow viewports: the table wrapper scrolls, never the page (flex ancestors must not grow to the table) */
.gradio-container, .gradio-container main, .gradio-container .wrap, .gradio-container .contain,
.gradio-container .tabs, .gradio-container .tabitem, .gradio-container .block,
.gradio-container .table-container {{ min-width: 0 !important; max-width: 100%; }}
#pending-table th .header-button span, #results-table th .header-button span, #pending-table th .header-content,
#results-table th .header-content {{ white-space: normal !important; overflow: visible !important;
  text-overflow: clip !important; line-height: 1.25; }}
#lb-table td span, #task-table td span {{ white-space: normal; overflow-wrap: anywhere; }}
#lb-table table, #task-table table {{ min-width: 1150px; }}
/* admin tables: fixed pixel columns, the wrapper scrolls instead of squeezing the headers */
#pending-table table {{ min-width: 2100px; }}
#results-table table {{ min-width: 1030px; }}
#lb-table .table-wrap, #task-table .table-wrap, #pending-table .table-wrap, #results-table .table-wrap {{ overflow-x: auto; }}
#lb-table tbody tr:nth-child(even) td {{ background: #f8fafc; }}
.receipt {{ border-left: 4px solid var(--mmeb-blue); padding: 0.5rem 0.9rem; background: #eef2fb; border-radius: 4px; }}
.rejected {{ border-left: 4px solid #b91c1c; padding: 0.5rem 0.9rem; background: #fef2f2; border-radius: 4px; }}
"""

BLUE = gr.themes.Color(c50="#eef2fb", c100="#dce3f5", c200="#b9c7eb", c300="#8fa5dc", c400="#5f7fc6",
                       c500="#2b4f9e", c600="#25458a", c700="#1e3870", c800="#172b56", c900="#101f3f", c950="#0a1428")
THEME = gr.themes.Soft(primary_hue=BLUE, neutral_hue="slate", font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif",
                                                                      "system-ui", "sans-serif"])


# ----------------------------------------------------------------------------------------------------------------
# Leaderboard tab
# ----------------------------------------------------------------------------------------------------------------
def _round1(value) -> str:
    """One decimal, rounded half-up (the convention of the paper / README tables; no banker's rounding)."""
    return str(Decimal(value).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def _fmt(value):
    """Display a stored accuracy (percent) with one decimal; ``repr`` keeps the stored decimals exact (61.25 -> 61.3)."""
    return "–" if value is None else _round1(repr(float(value)))


def _fmt_count(count, fallback):
    """Display 100 * correct / n from the exact counts when the row has them, else the stored accuracy."""
    if isinstance(count, dict) and isinstance(count.get("n"), int) and count["n"] > 0 \
            and isinstance(count.get("correct"), int):
        return _round1(Decimal(100 * count["correct"]) / Decimal(count["n"]))
    return _fmt(fallback)


def _sort_overall(row: dict) -> float:
    counts = (row.get("counts") or {}).get("overall") or {}
    if isinstance(counts.get("n"), int) and counts["n"] > 0 and isinstance(counts.get("correct"), int):
        return 100.0 * counts["correct"] / counts["n"]
    return float(row.get("overall") or 0.0)


def _link_md(url: str) -> str:
    url = (url or "").strip()
    if not url or not LINK_RE.match(url):
        return ""
    return f"[link]({url})"


def leaderboard_tables(setting: str):
    """Main table + per-task table for one setting (or 'All'), sorted by Overall desc."""
    rows = STORAGE.visible_results()
    if setting and setting != ALL_SETTINGS:
        rows = [r for r in rows if r.get("setting") == setting]
    rows.sort(key=lambda r: (-_sort_overall(r), str(r.get("model", ""))))
    main, tasks = [], []
    for rank, r in enumerate(rows, 1):
        cats = r.get("categories") or {}
        counts = r.get("counts") or {}
        cat_counts = counts.get("categories") or {}
        task_counts = counts.get("tasks") or {}
        label = short_setting(r.get("setting", ""))   # "2B · k=10" in the table; the full string stays in storage
        main.append([rank, r.get("model", ""), r.get("organization", ""), label,
                     _fmt_count(counts.get("overall"), r.get("overall")),
                     _fmt_count(cat_counts.get("ER"), cats.get("ER")), _fmt_count(cat_counts.get("CR"), cats.get("CR")),
                     _fmt_count(cat_counts.get("UNS"), cats.get("UNS")),
                     r.get("judge", ""), r.get("date", ""), r.get("source", ""), _link_md(r.get("link"))])
        per_task = r.get("tasks") or {}
        tasks.append([rank, r.get("model", ""), label] + [_fmt_count(task_counts.get(t), per_task.get(t)) for t in TASKS])
    main_df = pd.DataFrame(main, columns=MAIN_COLS)
    task_df = pd.DataFrame(tasks, columns=TASK_COLS)
    if setting != ALL_SETTINGS:
        main_df = main_df.drop(columns=["Setting"])
        task_df = task_df.drop(columns=["Setting"])
    main_types = ["number"] + ["str"] * (len(main_df.columns) - 2) + ["markdown"]
    main_widths = _widths(main_df.columns, MAIN_WEIGHTS)
    task_widths = _widths(task_df.columns, TASK_WEIGHTS)
    return (gr.update(value=main_df, headers=list(main_df.columns), datatype=main_types, column_widths=main_widths),
            gr.update(value=task_df, headers=list(task_df.columns), column_widths=task_widths),
            f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'} · {FOOTNOTE}")


# ----------------------------------------------------------------------------------------------------------------
# Submit tab
# ----------------------------------------------------------------------------------------------------------------
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _rejected(message: str) -> str:
    body = html.escape(message).replace("\n", "<br>")
    return f'<div class="rejected"><strong>Not accepted.</strong><br>{body}</div>'


def _client_key(request) -> str:
    """Stable, non-reversible key for the submitting client (first X-Forwarded-For hop or the socket address)."""
    host = ""
    try:
        forwarded = request.headers.get("x-forwarded-for", "") if request is not None else ""
        host = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request and request.client else "")
    except Exception:
        host = ""
    return hashlib.sha256(f"mmeb-lb|{host}".encode("utf-8")).hexdigest()[:16] if host else ""


UPLOAD_GONE = ("The uploaded file is no longer available on the server; please attach metrics.json "
               "(and predictions.jsonl, if any) again and resubmit.")


def submit(model, organization, link, contact_email, setting, judge, notes, metrics_file, predictions_file, consent,
           request: gr.Request = None):
    # The uploads stay where Gradio put them (its upload cache is content-addressed and shared between identical
    # in-flight uploads, so the app never unlinks them); ``delete_cache`` on the Blocks removes them after an hour.
    model = (model or "").strip().replace("<", "").replace(">", "")
    organization = (organization or "").strip().replace("<", "").replace(">", "")
    link = (link or "").strip()
    contact_email = (contact_email or "").strip()
    judge = (judge or "").strip()
    notes = (notes or "").strip()
    problems = []
    if not model:
        problems.append("Model name is required.")
    if not organization:
        problems.append("Organization is required.")
    if not EMAIL_RE.match(contact_email):
        problems.append("A valid contact e-mail is required (kept private).")
    if setting not in SETTINGS:
        problems.append("Choose a setting.")
    if not judge:
        problems.append("Judge model is required (the model used by `mmeb score`).")
    if link and not LINK_RE.match(link):
        problems.append("Link must be a plain http(s) URL without spaces, quotes or brackets.")
    if not consent:
        problems.append("Please confirm that you ran the official scorer and the file is unmodified.")
    if not metrics_file:
        problems.append("metrics.json is required.")
    if len(model) > 120 or len(organization) > 120 or len(link) > 300 or len(notes) > 2000:
        problems.append("A field is too long (model/org ≤ 120, link ≤ 300, notes ≤ 2000 characters).")
    if problems:
        return _rejected("\n".join(problems))

    metrics_path = Path(metrics_file)
    try:
        if metrics_path.stat().st_size > MAX_METRICS_BYTES:
            raise ValidationError(f"metrics.json is larger than {MAX_METRICS_BYTES // 1000} KB.")
        metrics_text = metrics_path.read_bytes()
        metrics = load_metrics_bytes(metrics_text)
        summary = validate_metrics(metrics, allow_pre_judged=ALLOW_PRE_JUDGED)
    except ValidationError as exc:
        return _rejected(str(exc))
    except OSError as exc:   # path details stay in the server log
        log.warning("reading the uploaded metrics file failed: %s: %s", type(exc).__name__, exc)
        return _rejected(UPLOAD_GONE)

    predictions_info = None
    if predictions_file:
        try:
            predictions_info = validate_predictions(predictions_file)
        except ValidationError as exc:
            return _rejected(str(exc))
        except OSError as exc:
            log.warning("reading the uploaded predictions file failed: %s: %s", type(exc).__name__, exc)
            return _rejected(UPLOAD_GONE)

    client_key = _client_key(request)
    try:
        recent_contact = STORAGE.count_recent_by_contact(contact_email, hours=24)
        recent_hour = STORAGE.submissions_since(utc_now() - timedelta(hours=1))
    except Exception as exc:  # storage hiccup: fail closed with a readable message
        log.warning("queue unavailable: %s: %s", type(exc).__name__, exc)
        return _rejected(f"The submission queue is temporarily unavailable ({type(exc).__name__}). Please retry later.")
    if recent_contact >= MAX_PER_CONTACT_PER_DAY:
        return _rejected(f"Limit reached: at most {MAX_PER_CONTACT_PER_DAY} submissions per contact per 24 hours.")
    if client_key and sum(1 for m in recent_hour if m.get("client_key") == client_key) >= MAX_PER_CLIENT_PER_HOUR:
        return _rejected(f"Limit reached: at most {MAX_PER_CLIENT_PER_HOUR} submissions per hour from one client.")
    if len(recent_hour) >= MAX_GLOBAL_PER_HOUR:
        return _rejected("The queue is busy (hourly submission cap reached). Please retry in an hour.")

    warnings = list(predictions_info["warnings"]) if predictions_info else []
    if summary.get("pre_judged"):
        warnings.append("metrics.json is pre-judged (admin override enabled); flagged for review.")
    meta = {
        "model": model,
        "organization": organization,
        "link": link,
        "contact_email": contact_email,
        "contact_key": normalize_contact(contact_email),
        "client_key": client_key,
        "setting": setting,
        "judge": judge,
        "notes": notes,
        "submitted_at": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "pending",
        "admin_notes": "",
        "consent": True,
        "summary": summary,
        "predictions": predictions_info,
        "warnings": warnings,
    }
    try:
        receipt = STORAGE.create_submission(meta, metrics_text.decode("utf-8"), predictions_file or None)
    except OSError as exc:
        log.warning("storing a submission failed: %s: %s", type(exc).__name__, exc)
        return _rejected(UPLOAD_GONE)
    except Exception as exc:
        log.warning("storing a submission failed: %s: %s", type(exc).__name__, exc)
        return _rejected(f"Storing the submission failed ({type(exc).__name__}). Please retry later.")
    warn_html = ""
    if warnings:
        warn_html = "<br>Warnings: " + "; ".join(html.escape(w) for w in warnings)
    return (f'<div class="receipt"><strong>Received — pending review.</strong><br>'
            f'Receipt id: <code>{html.escape(receipt)}</code><br>'
            f'{html.escape(model)} · {html.escape(setting)} · Overall {summary["overall"]:.1f} '
            f'(ER {_fmt(summary["categories"]["ER"])} · CR {_fmt(summary["categories"]["CR"])} · '
            f'UNS {_fmt(summary["categories"]["UNS"])}) · missing {summary["missing"]}'
            f'{" · predictions.jsonl attached" if predictions_info else ""}{warn_html}<br>'
            f'A maintainer will review the entry; keep the receipt id for questions.</div>')


# ----------------------------------------------------------------------------------------------------------------
# Admin tab
# ----------------------------------------------------------------------------------------------------------------
PENDING_COLS = ["Receipt id", "Model", "Organization", "Setting", "Judge", "Overall", "ER", "CR", "UNS", "Missing",
                "Pre-judged", "predictions.jsonl", "Submitted (UTC)", "Contact", "Link", "Notes", "Warnings"]
APPROVED_COLS = ["Row id", "Model", "Organization", "Setting", "Overall", "Source", "Date", "Hidden"]


_ADMIN_LOCK = threading.Lock()
_ADMIN_FAILS: dict[str, dict] = {}       # client key -> {"count", "locked_until", "last"}
WRONG_PASSWORD = "Wrong password (repeated failures from one client lock that client out for a few minutes)."


def _is_admin(password: str, request=None, delay: bool = False) -> bool:
    """Constant-time password check with a per-client failure counter.

    Failures are counted per client (``_client_key``), so a stranger cannot lock the owner out; after
    ``ADMIN_FAIL_LIMIT`` failures that client is refused for ``ADMIN_COOLDOWN_SECONDS``. ``delay`` adds the 1 s
    brute-force delay (used by Unlock/Refresh, which run on their own queue; write actions return immediately).
    Each request counts once.
    """
    key = _client_key(request) or "unknown"
    now = time.monotonic()
    with _ADMIN_LOCK:
        for stale in [k for k, v in _ADMIN_FAILS.items()
                      if now >= v["locked_until"] and now - v["last"] > ADMIN_COOLDOWN_SECONDS]:
            del _ADMIN_FAILS[stale]
        entry = _ADMIN_FAILS.get(key)
        if entry and now < entry["locked_until"]:
            return False
    ok = bool(ADMIN_PASSWORD) and secrets.compare_digest((password or "").encode(), ADMIN_PASSWORD.encode())
    with _ADMIN_LOCK:
        if ok:
            _ADMIN_FAILS.pop(key, None)
            return True
        if key not in _ADMIN_FAILS and len(_ADMIN_FAILS) >= ADMIN_FAIL_TABLE_MAX:
            oldest = min(_ADMIN_FAILS, key=lambda k: _ADMIN_FAILS[k]["last"])
            del _ADMIN_FAILS[oldest]
        entry = _ADMIN_FAILS.setdefault(key, {"count": 0, "locked_until": 0.0, "last": now})
        entry["count"] += 1
        entry["last"] = now
        if entry["count"] >= ADMIN_FAIL_LIMIT:
            entry["count"] = 0
            entry["locked_until"] = now + ADMIN_COOLDOWN_SECONDS
            log.warning("admin: %d failed unlocks from client %s, locked out for %ds", ADMIN_FAIL_LIMIT, key,
                        ADMIN_COOLDOWN_SECONDS)
    if delay:
        time.sleep(ADMIN_FAIL_DELAY_SECONDS)
    return False


def _admin_error(action: str, ident: str, exc: Exception) -> str:
    """Short status for the admin tab; details (paths, hub responses) go to the server log only."""
    log.warning("%s %s failed: %s: %s", action, ident, type(exc).__name__, exc)
    if type(exc).__name__ in ("FileNotFoundError", "EntryNotFoundError", "RepositoryNotFoundError"):
        return f"{action} failed: no submission or result row with id {ident}."
    return f"{action} failed for {ident}: {type(exc).__name__} (see the Space logs)."


def _pending_frame():
    metas = STORAGE.list_submissions(status="pending")
    data = []
    for m in metas:
        s = m.get("summary") or {}
        cats = s.get("categories") or {}
        preds = m.get("predictions")
        data.append([m.get("id"), m.get("model"), m.get("organization"), m.get("setting"), m.get("judge"),
                     _fmt(s.get("overall")), _fmt(cats.get("ER")), _fmt(cats.get("CR")), _fmt(cats.get("UNS")),
                     s.get("missing"), "yes" if s.get("pre_judged") else "no",
                     f"yes ({preds['lines']} lines)" if preds else "no", m.get("submitted_at"),
                     m.get("contact_email"), m.get("link"), m.get("notes"), "; ".join(m.get("warnings") or [])])
    choices = [f"{m.get('id')} — {m.get('model')} ({m.get('organization')})" for m in metas]
    return pd.DataFrame(data, columns=PENDING_COLS), choices


def _results_frame():
    rows = STORAGE.read_results()
    rows.sort(key=lambda r: (r.get("source") != "community", r.get("setting", ""), -(r.get("overall") or 0.0)))
    data = [[r.get("id"), r.get("model"), r.get("organization"), r.get("setting"), _fmt(r.get("overall")),
             r.get("source"), r.get("date"), "yes" if r.get("hidden") else "no"] for r in rows]
    choices = [f"{r.get('id')} — {r.get('model')} [{r.get('setting')}]{' (hidden)' if r.get('hidden') else ''}"
               for r in rows]
    return pd.DataFrame(data, columns=APPROVED_COLS), choices


def _choice_id(choice: str) -> str:
    return (choice or "").split(" — ", 1)[0].strip()


def _admin_denied():
    """Outputs for a wrong password: panel hidden, tables empty (the password is checked once per request)."""
    return (gr.update(visible=False), WRONG_PASSWORD, pd.DataFrame(columns=PENDING_COLS),
            gr.update(choices=[], value=None), pd.DataFrame(columns=APPROVED_COLS), gr.update(choices=[], value=None))


def _admin_view(status=""):
    """Outputs for an authenticated admin: the pending list and the approved rows (no password check here)."""
    pending_df, pending_choices = _pending_frame()
    results_df, result_choices = _results_frame()
    status = status or f"Unlocked. {len(pending_choices)} pending, {len(result_choices)} result rows."
    return (gr.update(visible=True), status, pending_df,
            gr.update(choices=pending_choices, value=pending_choices[0] if pending_choices else None),
            results_df, gr.update(choices=result_choices, value=None))


def admin_refresh(password, request: gr.Request = None):
    if not _is_admin(password, request, delay=True):
        return _admin_denied()
    return _admin_view()


def admin_approve(password, choice, note, request: gr.Request = None):
    if not _is_admin(password, request):
        return _admin_denied()
    sid = _choice_id(choice)
    if not sid:
        return _admin_view("Pick a pending submission first.")
    if not is_valid_submission_id(sid):
        return _admin_view("That is not a submission id; pick one from the list.")
    try:
        meta = STORAGE.get_meta(sid)
        if meta.get("status") != "pending":
            return _admin_view(f"{sid} is not pending (status: {meta.get('status')}).")
        metrics = STORAGE.get_metrics(sid)
        summary = validate_metrics(metrics, allow_pre_judged=True)   # admin override for pre-judged files
        row = build_row(row_id=sid, model=meta["model"], organization=meta["organization"], link=meta.get("link", ""),
                        setting=meta["setting"], judge=meta.get("judge", ""), date=today(), source="community",
                        summary=summary, notes=meta.get("notes", ""))
        STORAGE.append_result(row)
        STORAGE.update_meta(sid, {"status": "approved", "admin_notes": (note or "").strip(),
                                  "decided_at": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")})
    except ValidationError as exc:
        return _admin_view(f"Cannot approve {sid}: {exc}")
    except Exception as exc:
        return _admin_view(_admin_error("Approve", sid, exc))
    return _admin_view(f"Approved {sid}; it is now on the leaderboard.")


def admin_reject(password, choice, reason, request: gr.Request = None):
    if not _is_admin(password, request):
        return _admin_denied()
    sid = _choice_id(choice)
    if not sid:
        return _admin_view("Pick a pending submission first.")
    reason = (reason or "").strip()
    if not reason:
        return _admin_view("Give a reason for the rejection (stored with the submission).")
    if not is_valid_submission_id(sid):
        return _admin_view("That is not a submission id; pick one from the list.")
    try:
        meta = STORAGE.get_meta(sid)
        if meta.get("status") != "pending":
            return _admin_view(f"{sid} is not pending (status: {meta.get('status')}).")
        STORAGE.update_meta(sid, {"status": "rejected", "admin_notes": reason,
                                  "decided_at": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")})
    except Exception as exc:
        return _admin_view(_admin_error("Reject", sid, exc))
    return _admin_view(f"Rejected {sid}.")


def _set_hidden(password, choice, hidden: bool, request=None):
    if not _is_admin(password, request):
        return _admin_denied()
    rid = _choice_id(choice)
    if not rid:
        return _admin_view("Pick a result row first.")
    if not is_valid_row_id(rid):
        return _admin_view("That is not a result row id; pick one from the list.")
    try:
        if not STORAGE.set_result_hidden(rid, hidden):
            return _admin_view(f"No result row with id {rid}.")
        if rid in STORAGE.list_submission_ids():
            STORAGE.update_meta(rid, {"status": "hidden" if hidden else "approved"})
    except Exception as exc:
        return _admin_view(_admin_error("Hide" if hidden else "Unhide", rid, exc))
    return _admin_view(f"{'Hidden' if hidden else 'Visible again'}: {rid}.")


def admin_hide(password, choice, request: gr.Request = None):
    return _set_hidden(password, choice, True, request)


def admin_unhide(password, choice, request: gr.Request = None):
    return _set_hidden(password, choice, False, request)


# ----------------------------------------------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------------------------------------------
def _initial_tables():
    """Tables for the default setting at build time, so the page never renders empty; refreshed again on load."""
    try:
        main, task, note = leaderboard_tables(DEFAULT_SETTING)
        return main["value"], task["value"], note
    except Exception as exc:  # storage unavailable at startup: render empty tables, the load event retries
        empty_main = pd.DataFrame(columns=[c for c in MAIN_COLS if c != "Setting"])
        empty_task = pd.DataFrame(columns=[c for c in TASK_COLS if c != "Setting"])
        return empty_main, empty_task, f"{FOOTNOTE} (results unavailable: {type(exc).__name__})"


# dropdown entries are (label, value): the short label fits a phone-width box, the stored value stays the full string
SETTING_CHOICES = [(short_setting(s), s) for s in SETTINGS]
SETTING_INFO = "2B / 8B: released retriever size, k: records per question; or your own retrieval / other"


def build_app() -> gr.Blocks:
    init_main, init_task, init_note = _initial_tables()
    with gr.Blocks(title=TITLE, theme=THEME, css=CSS, delete_cache=(3600, 3600)) as demo:
        gr.Markdown(f"# {TITLE}", elem_id="title")
        with gr.Tabs():
            with gr.Tab("Leaderboard"):
                gr.Markdown(INTRO, elem_id="intro")
                with gr.Row():
                    setting_filter = gr.Dropdown(choices=SETTING_CHOICES + [(ALL_SETTINGS, ALL_SETTINGS)],
                                                 value=DEFAULT_SETTING, label="Setting", info=SETTING_INFO, scale=2)
                    refresh_lb = gr.Button("Refresh", variant="secondary", scale=0, min_width=100)
                init_main_cols = [c for c in MAIN_COLS if c != "Setting"]
                init_task_cols = [c for c in TASK_COLS if c != "Setting"]
                lb_table = gr.Dataframe(value=init_main, headers=init_main_cols, interactive=False,
                                        wrap=True, elem_id="lb-table", row_count=(0, "dynamic"), max_height=640,
                                        datatype=["number"] + ["str"] * 9 + ["markdown"],
                                        column_widths=_widths(init_main_cols, MAIN_WEIGHTS))
                lb_note = gr.Markdown(init_note, elem_id="footnote")
                with gr.Accordion("Per-task accuracy (8 tasks)", open=False):
                    task_table = gr.Dataframe(value=init_task, headers=init_task_cols, interactive=False, wrap=True,
                                              elem_id="task-table", row_count=(0, "dynamic"), max_height=640,
                                              column_widths=_widths(init_task_cols, TASK_WEIGHTS))
                setting_filter.change(leaderboard_tables, [setting_filter], [lb_table, task_table, lb_note],
                                      api_name="leaderboard")
                refresh_lb.click(leaderboard_tables, [setting_filter], [lb_table, task_table, lb_note],
                                 api_name="leaderboard_refresh")
                demo.load(leaderboard_tables, [setting_filter], [lb_table, task_table, lb_note], api_name=False)

            with gr.Tab("Submit"):
                gr.Markdown(SUBMIT_HELP)
                with gr.Row():
                    with gr.Column():
                        model = gr.Textbox(label="Model name *", placeholder="e.g. MyAgent (Qwen3.5-9B + reranker)")
                        organization = gr.Textbox(label="Organization *")
                        link = gr.Textbox(label="Link (paper / code)", placeholder="https://")
                        contact = gr.Textbox(label="Contact e-mail * (kept private)")
                    with gr.Column():
                        setting = gr.Dropdown(choices=SETTING_CHOICES, value=DEFAULT_SETTING, label="Setting *",
                                              info=SETTING_INFO)
                        judge = gr.Textbox(label="Judge model *", value="gpt-5.6-luna")
                        notes = gr.Textbox(label="Notes", lines=3, placeholder="Retrieval, prompting, anything a reader should know")
                with gr.Row():
                    metrics_file = gr.File(label="metrics.json * (from `mmeb score`, ≤ 1 MB)", file_types=[".json"],
                                           type="filepath")
                    predictions_file = gr.File(label="predictions.jsonl (optional, ≤ 25 MB)", file_types=[".jsonl"],
                                               type="filepath")
                consent = gr.Checkbox(label="I ran the official scorer and the file is unmodified", value=False)
                submit_btn = gr.Button("Submit for review", variant="primary")
                receipt = gr.HTML()
                submit_btn.click(submit, [model, organization, link, contact, setting, judge, notes, metrics_file,
                                          predictions_file, consent], [receipt], api_name="submit")

            with gr.Tab("Admin"):
                with gr.Row():
                    password = gr.Textbox(label="Admin password", type="password", scale=3)
                    unlock = gr.Button("Unlock", variant="primary", scale=0, min_width=110)
                    refresh_admin = gr.Button("Refresh", scale=0, min_width=110)
                admin_status = gr.Markdown("")
                with gr.Column(visible=False) as admin_panel:
                    gr.Markdown("### Pending submissions")
                    pending_table = gr.Dataframe(headers=PENDING_COLS, interactive=False, wrap=True,
                                                 row_count=(0, "dynamic"), max_height=420, elem_id="pending-table",
                                                 column_widths=PENDING_WIDTHS)
                    with gr.Row():
                        pending_choice = gr.Dropdown(label="Submission", choices=[], scale=3, allow_custom_value=True)
                        decision_note = gr.Textbox(label="Reason / note", scale=3)
                    with gr.Row():
                        approve_btn = gr.Button("Approve", variant="primary")
                        reject_btn = gr.Button("Reject", variant="stop")
                    gr.Markdown("### Approved rows (paper + community)")
                    results_table = gr.Dataframe(headers=APPROVED_COLS, interactive=False, wrap=True,
                                                 row_count=(0, "dynamic"), max_height=420, elem_id="results-table",
                                                 column_widths=APPROVED_WIDTHS)
                    with gr.Row():
                        result_choice = gr.Dropdown(label="Result row", choices=[], scale=3, allow_custom_value=True)
                        hide_btn = gr.Button("Hide", variant="stop", scale=0, min_width=110)
                        unhide_btn = gr.Button("Unhide", scale=0, min_width=110)
                admin_outputs = [admin_panel, admin_status, pending_table, pending_choice, results_table, result_choice]
                # Unlock/Refresh (which carry the failed-password delay) run on their own queue, so wrong-password
                # spam cannot queue in front of the owner's approve/reject; admin writes are serialized on "admin"
                unlock.click(admin_refresh, [password], admin_outputs, api_name="admin_unlock", concurrency_limit=1,
                             concurrency_id="admin_login")
                refresh_admin.click(admin_refresh, [password], admin_outputs, api_name="admin_refresh",
                                    concurrency_limit=1, concurrency_id="admin_login")
                approve_btn.click(admin_approve, [password, pending_choice, decision_note], admin_outputs,
                                  api_name="admin_approve", concurrency_limit=1, concurrency_id="admin")
                reject_btn.click(admin_reject, [password, pending_choice, decision_note], admin_outputs,
                                 api_name="admin_reject", concurrency_limit=1, concurrency_id="admin")
                hide_btn.click(admin_hide, [password, result_choice], admin_outputs, api_name="admin_hide",
                               concurrency_limit=1, concurrency_id="admin")
                unhide_btn.click(admin_unhide, [password, result_choice], admin_outputs, api_name="admin_unhide",
                                 concurrency_limit=1, concurrency_id="admin")
    return demo


demo = build_app()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    demo.queue(default_concurrency_limit=4).launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=int(os.environ.get("GRADIO_SERVER_PORT", os.environ.get("PORT", "7860"))),
        max_file_size=MAX_UPLOAD,
    )
