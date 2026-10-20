"""MMEnterprise-Bench Leaderboard — Gradio Space.

Three tabs: public leaderboard, submission form (uploads go to a private queue), and a password-protected admin
tab that approves / rejects / hides entries. No LLM API is called here: contributors run ``mmeb score`` locally
and upload the resulting ``metrics.json``.

Environment: STORAGE=hf|local, HF_TOKEN, QUEUE_REPO, RESULTS_REPO, ADMIN_PASSWORD, LOCAL_STORAGE_DIR (local),
ALLOW_PRE_JUDGED=1 (optional admin override: queue pre-judged files flagged for review instead of rejecting them).
"""
from __future__ import annotations

import html
import os
import re
import secrets
from pathlib import Path

import gradio as gr
import pandas as pd

from rows import ALL_SETTINGS, DEFAULT_SETTING, SETTINGS, build_row, today
from storage import get_storage, utc_now
from validate import (MAX_METRICS_BYTES, TASKS, ValidationError, load_metrics_bytes, validate_metrics,
                      validate_predictions)

TITLE = "MMEnterprise-Bench Leaderboard"
GITHUB = "https://github.com/mlvlab/MMEnterprise-Bench"
DATASET = "https://huggingface.co/datasets/MMEnterprise-Bench/MMEnterprise-Bench"
PROJECT = "https://mlvlab.github.io/MMEnterprise-Bench/"
BRAND = "#2b4f9e"
MAX_PER_CONTACT_PER_DAY = 5
ALLOW_PRE_JUDGED = os.environ.get("ALLOW_PRE_JUDGED", "").strip() in ("1", "true", "yes")

STORAGE = get_storage()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

MAIN_COLS = ["Rank", "Model", "Organization", "Setting", "Overall", "Evidence Reasoning", "Conflict Resolution",
             "Unanswerability", "Judge", "Date", "Source", "Link"]
MAIN_WIDTHS = {"Rank": "5%", "Model": "15%", "Organization": "12%", "Setting": "13%", "Overall": "7%",
               "Evidence Reasoning": "9%", "Conflict Resolution": "9%", "Unanswerability": "11%", "Judge": "9%",
               "Date": "9%", "Source": "9%", "Link": "5%"}
TASK_WIDTHS = {"Rank": "5%", "Model": "17%", "Setting": "14%", "Interpretation": "10%", "Unanswerability": "10%"}
TASK_COLS = ["Rank", "Model", "Setting"] + TASKS

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
.gradio-container {{ max-width: 1280px !important; margin: 0 auto !important; }}
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
#lb-table td span, #task-table td span {{ white-space: nowrap; }}
#lb-table td:nth-child(3) span {{ white-space: normal; }}
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
def _fmt(value):
    return "–" if value is None else f"{float(value):.1f}"


def _link_md(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    return f"[link]({url})"


def leaderboard_tables(setting: str):
    """Main table + per-task table for one setting (or 'All'), sorted by Overall desc."""
    rows = STORAGE.visible_results()
    if setting and setting != ALL_SETTINGS:
        rows = [r for r in rows if r.get("setting") == setting]
    rows.sort(key=lambda r: (-(r.get("overall") or 0.0), str(r.get("model", ""))))
    main, tasks = [], []
    for rank, r in enumerate(rows, 1):
        cats = r.get("categories") or {}
        main.append([rank, r.get("model", ""), r.get("organization", ""), r.get("setting", ""),
                     _fmt(r.get("overall")), _fmt(cats.get("ER")), _fmt(cats.get("CR")), _fmt(cats.get("UNS")),
                     r.get("judge", ""), r.get("date", ""), r.get("source", ""), _link_md(r.get("link"))])
        per_task = r.get("tasks") or {}
        tasks.append([rank, r.get("model", ""), r.get("setting", "")] + [_fmt(per_task.get(t)) for t in TASKS])
    main_df = pd.DataFrame(main, columns=MAIN_COLS)
    task_df = pd.DataFrame(tasks, columns=TASK_COLS)
    if setting != ALL_SETTINGS:
        main_df = main_df.drop(columns=["Setting"])
        task_df = task_df.drop(columns=["Setting"])
    main_types = ["number"] + ["str"] * (len(main_df.columns) - 2) + ["markdown"]
    main_widths = [MAIN_WIDTHS[c] for c in main_df.columns]
    task_widths = [TASK_WIDTHS.get(c, "9%") for c in task_df.columns]
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


def submit(model, organization, link, contact_email, setting, judge, notes, metrics_file, predictions_file, consent):
    model = (model or "").strip()
    organization = (organization or "").strip()
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
    if link and not re.match(r"^https?://", link):
        problems.append("Link must start with http:// or https://.")
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
    except OSError as exc:
        return _rejected(f"Could not read the uploaded file: {exc}")

    predictions_info = None
    if predictions_file:
        try:
            predictions_info = validate_predictions(predictions_file)
        except ValidationError as exc:
            return _rejected(str(exc))

    try:
        recent = STORAGE.count_recent_by_contact(contact_email, hours=24)
    except Exception as exc:  # storage hiccup: fail closed with a readable message
        return _rejected(f"The submission queue is temporarily unavailable ({type(exc).__name__}). Please retry later.")
    if recent >= MAX_PER_CONTACT_PER_DAY:
        return _rejected(f"Limit reached: at most {MAX_PER_CONTACT_PER_DAY} submissions per contact per 24 hours.")

    warnings = list(predictions_info["warnings"]) if predictions_info else []
    if summary.get("pre_judged"):
        warnings.append("metrics.json is pre-judged (admin override enabled); flagged for review.")
    meta = {
        "model": model,
        "organization": organization,
        "link": link,
        "contact_email": contact_email,
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
    except Exception as exc:
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


def _is_admin(password: str) -> bool:
    return bool(ADMIN_PASSWORD) and secrets.compare_digest((password or "").encode(), ADMIN_PASSWORD.encode())


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


def admin_refresh(password, status=""):
    if not _is_admin(password):
        empty = pd.DataFrame(columns=PENDING_COLS), pd.DataFrame(columns=APPROVED_COLS)
        return (gr.update(visible=False), "Wrong password.", empty[0], gr.update(choices=[], value=None),
                empty[1], gr.update(choices=[], value=None))
    pending_df, pending_choices = _pending_frame()
    results_df, result_choices = _results_frame()
    status = status or f"Unlocked. {len(pending_choices)} pending, {len(result_choices)} result rows."
    return (gr.update(visible=True), status, pending_df,
            gr.update(choices=pending_choices, value=pending_choices[0] if pending_choices else None),
            results_df, gr.update(choices=result_choices, value=None))


def admin_approve(password, choice, note):
    if not _is_admin(password):
        return admin_refresh(password)
    sid = _choice_id(choice)
    if not sid:
        return admin_refresh(password, "Pick a pending submission first.")
    try:
        meta = STORAGE.get_meta(sid)
        if meta.get("status") != "pending":
            return admin_refresh(password, f"{sid} is not pending (status: {meta.get('status')}).")
        metrics = STORAGE.get_metrics(sid)
        summary = validate_metrics(metrics, allow_pre_judged=True)   # admin override for pre-judged files
        row = build_row(row_id=sid, model=meta["model"], organization=meta["organization"], link=meta.get("link", ""),
                        setting=meta["setting"], judge=meta.get("judge", ""), date=today(), source="community",
                        summary=summary, notes=meta.get("notes", ""))
        STORAGE.append_result(row)
        STORAGE.update_meta(sid, {"status": "approved", "admin_notes": (note or "").strip(),
                                  "decided_at": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")})
    except ValidationError as exc:
        return admin_refresh(password, f"Cannot approve {sid}: {exc}")
    except Exception as exc:
        return admin_refresh(password, f"Approve failed for {sid}: {type(exc).__name__}: {exc}")
    return admin_refresh(password, f"Approved {sid}; it is now on the leaderboard.")


def admin_reject(password, choice, reason):
    if not _is_admin(password):
        return admin_refresh(password)
    sid = _choice_id(choice)
    if not sid:
        return admin_refresh(password, "Pick a pending submission first.")
    reason = (reason or "").strip()
    if not reason:
        return admin_refresh(password, "Give a reason for the rejection (stored with the submission).")
    try:
        meta = STORAGE.get_meta(sid)
        if meta.get("status") != "pending":
            return admin_refresh(password, f"{sid} is not pending (status: {meta.get('status')}).")
        STORAGE.update_meta(sid, {"status": "rejected", "admin_notes": reason,
                                  "decided_at": utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")})
    except Exception as exc:
        return admin_refresh(password, f"Reject failed for {sid}: {type(exc).__name__}: {exc}")
    return admin_refresh(password, f"Rejected {sid}.")


def _set_hidden(password, choice, hidden: bool):
    if not _is_admin(password):
        return admin_refresh(password)
    rid = _choice_id(choice)
    if not rid:
        return admin_refresh(password, "Pick a result row first.")
    try:
        if not STORAGE.set_result_hidden(rid, hidden):
            return admin_refresh(password, f"No result row with id {rid}.")
        if rid in STORAGE.list_submission_ids():
            STORAGE.update_meta(rid, {"status": "hidden" if hidden else "approved"})
    except Exception as exc:
        return admin_refresh(password, f"{'Hide' if hidden else 'Unhide'} failed for {rid}: {type(exc).__name__}: {exc}")
    return admin_refresh(password, f"{'Hidden' if hidden else 'Visible again'}: {rid}.")


def admin_hide(password, choice):
    return _set_hidden(password, choice, True)


def admin_unhide(password, choice):
    return _set_hidden(password, choice, False)


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


def build_app() -> gr.Blocks:
    init_main, init_task, init_note = _initial_tables()
    with gr.Blocks(title=TITLE, theme=THEME, css=CSS) as demo:
        gr.Markdown(f"# {TITLE}", elem_id="title")
        with gr.Tabs():
            with gr.Tab("Leaderboard"):
                gr.Markdown(INTRO, elem_id="intro")
                with gr.Row():
                    setting_filter = gr.Dropdown(choices=SETTINGS + [ALL_SETTINGS], value=DEFAULT_SETTING,
                                                 label="Setting", scale=2)
                    refresh_lb = gr.Button("Refresh", variant="secondary", scale=0, min_width=100)
                lb_table = gr.Dataframe(value=init_main, headers=[c for c in MAIN_COLS if c != "Setting"], interactive=False,
                                        wrap=True, elem_id="lb-table", row_count=(0, "dynamic"), max_height=640,
                                        datatype=["number"] + ["str"] * 9 + ["markdown"],
                                        column_widths=[MAIN_WIDTHS[c] for c in MAIN_COLS if c != "Setting"])
                lb_note = gr.Markdown(init_note, elem_id="footnote")
                with gr.Accordion("Per-task accuracy (8 tasks)", open=False):
                    task_table = gr.Dataframe(value=init_task, headers=[c for c in TASK_COLS if c != "Setting"],
                                              interactive=False, wrap=True, elem_id="task-table", row_count=(0, "dynamic"), max_height=640)
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
                        setting = gr.Dropdown(choices=SETTINGS, value=DEFAULT_SETTING, label="Setting *")
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
                                                 row_count=(0, "dynamic"), max_height=420, elem_id="pending-table")
                    with gr.Row():
                        pending_choice = gr.Dropdown(label="Submission", choices=[], scale=3, allow_custom_value=True)
                        decision_note = gr.Textbox(label="Reason / note", scale=3)
                    with gr.Row():
                        approve_btn = gr.Button("Approve", variant="primary")
                        reject_btn = gr.Button("Reject", variant="stop")
                    gr.Markdown("### Approved rows (paper + community)")
                    results_table = gr.Dataframe(headers=APPROVED_COLS, interactive=False, wrap=True,
                                                 row_count=(0, "dynamic"), max_height=420, elem_id="results-table")
                    with gr.Row():
                        result_choice = gr.Dropdown(label="Result row", choices=[], scale=3, allow_custom_value=True)
                        hide_btn = gr.Button("Hide", variant="stop", scale=0, min_width=110)
                        unhide_btn = gr.Button("Unhide", scale=0, min_width=110)
                admin_outputs = [admin_panel, admin_status, pending_table, pending_choice, results_table, result_choice]
                unlock.click(admin_refresh, [password], admin_outputs, api_name="admin_unlock")
                refresh_admin.click(admin_refresh, [password], admin_outputs, api_name="admin_refresh")
                approve_btn.click(admin_approve, [password, pending_choice, decision_note], admin_outputs,
                                  api_name="admin_approve")
                reject_btn.click(admin_reject, [password, pending_choice, decision_note], admin_outputs,
                                 api_name="admin_reject")
                hide_btn.click(admin_hide, [password, result_choice], admin_outputs, api_name="admin_hide")
                unhide_btn.click(admin_unhide, [password, result_choice], admin_outputs, api_name="admin_unhide")
    return demo


demo = build_app()

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=4).launch(
        server_name=os.environ.get("GRADIO_SERVER_NAME", "0.0.0.0"),
        server_port=int(os.environ.get("GRADIO_SERVER_PORT", os.environ.get("PORT", "7860"))),
    )
