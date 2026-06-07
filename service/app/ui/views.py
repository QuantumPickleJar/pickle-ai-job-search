"""Escaped HTML views for the service UI."""

from __future__ import annotations

import json
import re
from html import escape
from typing import Any, Iterable
from urllib.parse import urlparse

from app.config import Settings


def page(
    *,
    title: str,
    active: str,
    body: str,
    settings: Settings,
    notice: str = "",
    error: str = "",
) -> str:
    nav = "".join(
        nav_link(label, href, active == key)
        for key, label, href in (
            ("dashboard", "Dashboard", "/ui"),
            ("submit", "Submit job", "/ui/jobs/new"),
            ("jobs", "Captured jobs", "/ui/jobs"),
            ("applications", "Applications", "/ui/applications"),
            ("generated", "Generated", "/ui/generated"),
            ("health", "Health", "/ui/health"),
        )
    )
    notice_html = alert(notice, "success", allow_html=True) if notice else ""
    error_html = alert(error, "error") if error else ""
    remote_label = "Remote mode" if settings.enable_remote_mode else "Local mode"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{escape(title)} | Job Search Service</title>
  <link rel="icon" href="/static/mark.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/static/app.css">
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <a class="brand" href="/ui" aria-label="Job Search Service dashboard">
        <img src="/static/mark.svg" alt="" width="34" height="34">
        <span><strong>Job Search</strong><small>Local service</small></span>
      </a>
      <nav aria-label="Primary">{nav}</nav>
      <div class="sidebar-status">
        <span class="status-dot"></span>
        <span>{escape(remote_label)}</span>
      </div>
    </aside>
    <div class="main-column">
      <header class="topbar">
        <div>
          <p class="eyebrow">AI Job Search Service</p>
          <h1>{escape(title)}</h1>
        </div>
        <a class="button button-primary" href="/ui/jobs/new">Submit job</a>
      </header>
      <main>
        {notice_html}
        {error_html}
        {body}
      </main>
      <footer>Local-first workflow. Review every generated claim before use.</footer>
    </div>
  </div>
    <script src="/static/live-refresh.js" defer></script>
</body>
</html>
"""


def nav_link(label: str, href: str, is_active: bool) -> str:
    current = ' aria-current="page"' if is_active else ""
    css_class = "nav-link active" if is_active else "nav-link"
    return f'<a class="{css_class}" href="{href}"{current}>{escape(label)}</a>'


def alert(message: str, kind: str, *, allow_html: bool = False) -> str:
    content = message if allow_html else escape(message)
    return f'<div class="alert alert-{kind}" role="status">{content}</div>'


def status_badge(value: Any) -> str:
    text = str(value or "unknown")
    css_value = "".join(
        character for character in text.lower() if character.isalnum() or character == "-"
    )
    return (
        f'<span class="badge badge-{css_value}">'
        f'{escape(text.replace("_", " "))}</span>'
    )


def metric(label: str, value: int, href: str) -> str:
    return f"""
<a class="metric" href="{href}">
  <span>{escape(label)}</span>
  <strong>{value}</strong>
</a>
"""


def section(title: str, content: str, action: str = "") -> str:
    action_html = f'<div class="section-action">{action}</div>' if action else ""
    return f"""
<section class="panel">
  <div class="section-heading"><h2>{escape(title)}</h2>{action_html}</div>
  {content}
</section>
"""


def empty_state(title: str, message: str, action: str = "") -> str:
    action_html = f'<div class="empty-action">{action}</div>' if action else ""
    return f"""
<div class="empty-state">
  <strong>{escape(title)}</strong>
  <p>{escape(message)}</p>
  {action_html}
</div>
"""


def job_table(jobs: Iterable[dict[str, Any]]) -> str:
    rows = []
    for job in jobs:
        job_id = escape(str(job.get("id", "")), quote=True)
        rows.append(
            f"""
<tr>
  <td><a class="primary-link" href="/ui/jobs/{job_id}">{escape(str(job.get("title") or "Untitled role"))}</a>
      <span class="cell-note">{escape(str(job.get("company") or "Unknown company"))}</span></td>
  <td>{escape(str(job.get("location") or "Unknown"))}</td>
  <td>{status_badge(job.get("application_status"))}</td>
  <td class="mono">{escape(short_date(job.get("captured_at")))}</td>
</tr>
"""
        )
    if not rows:
        return empty_state(
            "No captured jobs",
            "Submit a job to begin the local review workflow.",
            '<a class="button button-primary" href="/ui/jobs/new">Submit job</a>',
        )
    return f"""
<div class="table-wrap">
<table>
  <thead><tr><th>Role</th><th>Location</th><th>Status</th><th>Captured</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
</div>
"""


def application_table(applications: Iterable[dict[str, Any]]) -> str:
    rows = []
    for application in applications:
        application_id = escape(
            str(application.get("application_id", "")),
            quote=True,
        )
        files = application.get("available_files") or []
        rows.append(
            f"""
<tr>
  <td><a class="primary-link" href="/ui/applications/{application_id}">{escape(application_id)}</a></td>
  <td>{len(files)} workspace files</td>
  <td>{status_badge("ready" if files else "empty")}</td>
</tr>
"""
        )
    if not rows:
        return empty_state(
            "No application workspaces",
            "Process a captured job to create a reviewable workspace.",
            '<a class="button" href="/ui/jobs">View captured jobs</a>',
        )
    return f"""
<div class="table-wrap">
<table>
  <thead><tr><th>Workspace</th><th>Contents</th><th>Status</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
</div>
"""


def task_table(tasks: Iterable[dict[str, Any]]) -> str:
    rows = []
    for task in tasks:
        job_id = escape(str(task.get("job_id", "")), quote=True)
        application_id = task.get("application_id")
        task_type = str(task.get("task_type") or "process-job")
        task_label = {
            "process-job": "Fit analysis",
            "generate-cv": "Generate CV",
            "generate-cover-letter": "Generate cover letter",
        }.get(task_type, task_type.replace("-", " "))
        safe_application_id = escape(str(application_id), quote=True) if application_id else ""
        generated_target = ""
        if application_id and str(task.get("state")) == "succeeded":
            if task_type == "generate-cover-letter":
                generated_target = f"/ui/generated/{safe_application_id}/cover-letter.md"
            elif task_type == "generate-cv":
                generated_target = f"/ui/generated/{safe_application_id}/cv-draft.md"

        result = (
            f'<a href="{generated_target}">Open generated file</a>'
            if generated_target
            else (
                f'<a href="/ui/applications/{safe_application_id}">Open workspace</a>'
                if application_id
                else escape(str(task.get("error") or "Waiting"))
            )
        )
        target_href = (
            f"/ui/applications/{escape(str(application_id), quote=True)}"
            if application_id
            else f"/ui/jobs/{job_id}"
        )
        target_label = (
            escape(str(application_id)) if application_id else escape(str(task.get("job_id") or "Unknown job"))
        )
        rows.append(
            f"""
<tr>
  <td><a class="primary-link" href="{target_href}">{target_label}</a></td>
    <td>{escape(task_label)}</td>
  <td>{status_badge(task.get("state"))}</td>
  <td>{result}</td>
  <td class="mono">{escape(short_date(task.get("updated_at")))}</td>
</tr>
"""
        )
    if not rows:
        return empty_state("No processing tasks", "Queued fit-scoring tasks will appear here.")
    return f"""
<div class="table-wrap">
<table>
    <thead><tr><th>Job</th><th>Task</th><th>State</th><th>Result</th><th>Updated</th></tr></thead>
  <tbody>{''.join(rows)}</tbody>
</table>
</div>
"""


def detail_grid(items: Iterable[tuple[str, Any]]) -> str:
    cells = []
    for label, value in items:
        cells.append(
            '<div class="detail-item">'
            f"<dt>{escape(label)}</dt>"
            f"<dd>{escape(str(value or 'Not provided'))}</dd>"
            "</div>"
        )
    return f'<dl class="detail-grid">{"".join(cells)}</dl>'


def list_block(title: str, values: Any) -> str:
    if not isinstance(values, list) or not values:
        return ""
    items = "".join(f"<li>{escape(str(value))}</li>" for value in values)
    return (
        f'<section class="content-block"><h3>{escape(title)}</h3>'
        f"<ul>{items}</ul></section>"
    )


def text_block(title: str, value: Any) -> str:
    if not value:
        return ""
    return (
        f'<section class="content-block"><h3>{escape(title)}</h3>'
        f'<div class="prose">{escape(str(value))}</div></section>'
    )


def render_file(filename: str, value: Any, *, expanded: bool = True) -> str:
    polished = ""
    if isinstance(value, dict):
        polished = render_json_file(filename, value)
        content = json.dumps(value, ensure_ascii=False, indent=2)
        language = "json"
    elif isinstance(value, list):
        polished = render_json_list(value)
        content = json.dumps(value, ensure_ascii=False, indent=2)
        language = "json"
    else:
        content = str(value)
        language = "markdown" if filename.endswith(".md") else "text"
        if filename.endswith(".md"):
            polished = render_markdown_block(content)

    open_attribute = " open" if expanded else ""
    # Create a URL-safe ID from the filename for anchor links
    file_id = filename.replace(".", "-").replace("_", "-").lower()
    
    if polished:
        # When polished rendering exists, show it and hide raw content in a nested toggle
        raw_section = f"""
  <details class="raw-toggle">
    <summary>View raw {language}</summary>
    <pre>{escape(content)}</pre>
  </details>"""
        return f"""
<details class="file-view" id="{file_id}"{open_attribute}>
  <summary><span>{escape(filename)}</span><small>{language}</small></summary>
  <div class="file-polished">{polished}</div>
  {raw_section}
</details>
"""
    else:
        # No polished rendering, show raw content directly
        return f"""
<details class="file-view" id="{file_id}"{open_attribute}>
  <summary><span>{escape(filename)}</span><small>{language}</small></summary>
  <pre>{escape(content)}</pre>
</details>
"""


def render_json_file(filename: str, value: dict[str, Any]) -> str:
    if filename == "fit-analysis.json":
        return render_fit_analysis(value)
    if filename == "job.json":
        return render_job_json(value)
    return ""


def render_json_list(value: list[Any]) -> str:
    if not value:
        return '<p class="muted">No items.</p>'
    items = "".join(f"<li>{escape(str(item))}</li>" for item in value)
    return f'<ul class="rendered-list">{items}</ul>'


def render_fit_analysis(data: dict[str, Any]) -> str:
    headline = detail_grid(
        (
            ("Overall score", data.get("overall_score")),
            ("Recommendation", data.get("recommendation")),
            ("Confidence", data.get("confidence", "Not provided")),
        )
    )
    body = headline
    body += list_block("Reasons to apply", data.get("reasons_to_apply"))
    body += list_block("Matched skills", data.get("matched_skills"))
    body += list_block("Missing skills", data.get("missing_skills"))
    body += list_block("Risks", data.get("risks"))
    body += list_block("Resume keywords", data.get("resume_keywords_to_include"))
    body += text_block("Suggested resume angle", data.get("suggested_resume_angle"))
    body += text_block("Cover letter angle", data.get("cover_letter_angle"))
    body += list_block(
        "Questions before applying",
        data.get("questions_to_answer_before_applying"),
    )
    return body


def render_job_json(data: dict[str, Any]) -> str:
    headline = detail_grid(
        (
            ("Title", data.get("title")),
            ("Company", data.get("company")),
            ("Location", data.get("location")),
            ("Employment", data.get("employment_type")),
            ("Seniority", data.get("seniority")),
            ("Workplace", data.get("remote_status")),
        )
    )
    body = headline
    body += text_block("Description", data.get("description_text"))
    body += list_block("Requirements", data.get("requirements"))
    body += list_block("Preferred qualifications", data.get("preferred_qualifications"))
    body += list_block("Technologies", data.get("technologies"))
    body += list_block("Responsibilities", data.get("responsibilities"))
    return body


def render_markdown_block(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    html: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.strip()
        if not line:
            close_list()
            continue
        if line.startswith("### "):
            close_list()
            html.append(f"<h4>{escape(line[4:])}</h4>")
            continue
        if line.startswith("## "):
            close_list()
            html.append(f"<h3>{escape(line[3:])}</h3>")
            continue
        if line.startswith("# "):
            close_list()
            html.append(f"<h2>{escape(line[2:])}</h2>")
            continue

        task = re.match(r"^- \[( |x|X)\] (.+)$", line)
        if task:
            if not in_list:
                html.append('<ul class="rendered-list checklist">')
                in_list = True
            done = task.group(1).lower() == "x"
            label = escape(task.group(2))
            state = "done" if done else "todo"
            marker = "&#10003;" if done else "&#9711;"
            html.append(f'<li class="{state}"><span class="check">{marker}</span>{label}</li>')
            continue

        bullet = re.match(r"^- (.+)$", line)
        if bullet:
            if not in_list:
                html.append('<ul class="rendered-list">')
                in_list = True
            html.append(f"<li>{escape(bullet.group(1))}</li>")
            continue

        close_list()
        html.append(f"<p>{escape(line)}</p>")

    close_list()
    return "".join(html)


def source_link(url: Any) -> str:
    value = str(url or "")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return '<span class="muted">No valid source URL</span>'
    escaped = escape(value, quote=True)
    return (
        f'<a href="{escaped}" target="_blank" rel="noopener noreferrer">'
        "Open source posting</a>"
    )


def form_value(values: dict[str, str], key: str) -> str:
    return escape(values.get(key, ""), quote=True)


def selected(
    values: dict[str, str],
    key: str,
    option: str,
    default: str = "",
) -> str:
    value = values.get(key, default)
    return " selected" if value == option else ""


def short_date(value: Any) -> str:
    text = str(value or "")
    if len(text) >= 16:
        return text[:10] + " " + text[11:16]
    return text or "Unknown"
