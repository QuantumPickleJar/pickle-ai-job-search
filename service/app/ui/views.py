"""Escaped HTML views for the service UI."""

from __future__ import annotations

import json
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
            ("health", "Health", "/ui/health"),
        )
    )
    notice_html = alert(notice, "success") if notice else ""
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
</body>
</html>
"""


def nav_link(label: str, href: str, is_active: bool) -> str:
    current = ' aria-current="page"' if is_active else ""
    css_class = "nav-link active" if is_active else "nav-link"
    return f'<a class="{css_class}" href="{href}"{current}>{escape(label)}</a>'


def alert(message: str, kind: str) -> str:
    return f'<div class="alert alert-{kind}" role="status">{escape(message)}</div>'


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
        result = (
            f'<a href="/ui/applications/{escape(str(application_id), quote=True)}">'
            "Open workspace</a>"
            if application_id
            else escape(str(task.get("error") or "Waiting"))
        )
        rows.append(
            f"""
<tr>
  <td><a class="primary-link" href="/ui/jobs/{job_id}">{escape(str(task.get("job_id") or "Unknown job"))}</a></td>
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
  <thead><tr><th>Job</th><th>State</th><th>Result</th><th>Updated</th></tr></thead>
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
    if isinstance(value, (dict, list)):
        content = json.dumps(value, ensure_ascii=False, indent=2)
        language = "json"
    else:
        content = str(value)
        language = "markdown" if filename.endswith(".md") else "text"
    open_attribute = " open" if expanded else ""
    return f"""
<details class="file-view"{open_attribute}>
  <summary><span>{escape(filename)}</span><small>{language}</small></summary>
  <pre>{escape(content)}</pre>
</details>
"""


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
