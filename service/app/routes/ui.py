"""Server-rendered pages for the local-first service."""

from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import parse_qs, quote

from ai_job_search.intake import JobIntakeError
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.auth import api_key_is_valid
from app.config import Settings, get_settings
from app.dependencies import get_job_store, get_task_manager
from app.services.job_store import (
    InvalidStoredDataError,
    JobStore,
    ResourceNotFoundError,
)
from app.services.ollama_client import OllamaClient, OllamaHealthError
from app.services.task_queue import TaskManager
from app.services.task_store import InvalidTaskDataError, TaskStore
from app.ui.views import (
    application_table,
    detail_grid,
    empty_state,
    form_value,
    job_table,
    list_block,
    metric,
    page,
    render_file,
    section,
    selected,
    source_link,
    status_badge,
    task_table,
    text_block,
)


router = APIRouter(tags=["ui"])


@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui", status_code=307)


@router.get("/ui", response_class=HTMLResponse)
def dashboard(
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_job_store),
) -> HTMLResponse:
    try:
        jobs = store.list_jobs()
        applications = store.list_applications()
        tasks = TaskStore(settings.app_data_dir).list()
        error = ""
    except (InvalidStoredDataError, InvalidTaskDataError) as exc:
        jobs, applications, tasks = [], [], []
        error = str(exc)

    state_counts = {
        state: sum(1 for task in tasks if task.get("state") == state)
        for state in ("queued", "running", "succeeded", "failed")
    }
    metrics = f"""
<div class="metrics">
  {metric("Captured jobs", len(jobs), "/ui/jobs")}
  {metric("Applications", len(applications), "/ui/applications")}
  {metric("Active tasks", state_counts["queued"] + state_counts["running"], "/ui#tasks")}
  {metric("Failed tasks", state_counts["failed"], "/ui#tasks")}
</div>
"""
    body = metrics
    body += section(
        "Recent jobs",
        job_table(jobs[:5]),
        '<a href="/ui/jobs">View all</a>',
    )
    body += f'<div id="tasks">{section("Processing activity", task_table(tasks[:6]))}</div>'
    return HTMLResponse(
        page(
            title="Dashboard",
            active="dashboard",
            body=body,
            settings=settings,
            error=error,
        )
    )


@router.get("/ui/jobs/new", response_class=HTMLResponse)
def submit_job_page(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    return HTMLResponse(render_submit_form(settings, {}))


@router.post("/ui/jobs/new", response_class=HTMLResponse)
async def submit_job(
    request: Request,
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_job_store),
) -> Response:
    values = await read_form(request)
    if not api_key_is_valid(values.get("api_key"), settings):
        return HTMLResponse(
            render_submit_form(
                settings,
                values,
                error="Invalid or missing API key.",
            ),
            status_code=401,
        )

    payload: dict[str, Any] = {
        "source": values.get("source", ""),
        "source_url": values.get("source_url", ""),
        "title": values.get("title", ""),
        "company": values.get("company", ""),
        "location": values.get("location", ""),
        "remote_status": values.get("remote_status", "unknown"),
        "employment_type": values.get("employment_type", "unknown"),
        "seniority": values.get("seniority", "unknown"),
        "description_text": values.get("description_text", ""),
        "raw_text": values.get("raw_text", ""),
        "capture_method": "manual_web_ui",
        "notes": values.get("notes", ""),
    }
    try:
        job, _ = store.save_capture(payload)
    except JobIntakeError as exc:
        return HTMLResponse(
            render_submit_form(settings, values, error=str(exc)),
            status_code=422,
        )
    except (InvalidStoredDataError, OSError):
        return HTMLResponse(
            render_submit_form(
                settings,
                values,
                error="The captured job could not be saved.",
            ),
            status_code=500,
        )

    return RedirectResponse(f"/ui/jobs/{job['id']}?saved=1", status_code=303)


@router.get("/ui/jobs", response_class=HTMLResponse)
def captured_jobs(
    request: Request,
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_job_store),
) -> HTMLResponse:
    query = request.query_params.get("q", "").strip()
    try:
        jobs = store.list_jobs()
        error = ""
    except InvalidStoredDataError as exc:
        jobs, error = [], str(exc)

    if query:
        needle = query.casefold()
        jobs = [
            job
            for job in jobs
            if needle
            in " ".join(
                str(job.get(key, ""))
                for key in ("title", "company", "location")
            ).casefold()
        ]

    search_value = escape(query, quote=True)
    search = f"""
<form class="toolbar" method="get" action="/ui/jobs">
  <label class="search-field">
    <span class="sr-only">Search captured jobs</span>
    <input type="search" name="q" value="{search_value}" placeholder="Search role, company, or location">
  </label>
  <button class="button" type="submit">Search</button>
</form>
"""
    body = search + section(f"Captured jobs ({len(jobs)})", job_table(jobs))
    return HTMLResponse(
        page(
            title="Captured jobs",
            active="jobs",
            body=body,
            settings=settings,
            error=error,
        )
    )


@router.get("/ui/jobs/{job_id}", response_class=HTMLResponse)
def job_detail(
    job_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_job_store),
) -> HTMLResponse:
    try:
        job, _ = store.get_job(job_id)
    except (ResourceNotFoundError, InvalidStoredDataError) as exc:
        return HTMLResponse(
            page(
                title="Job not found",
                active="jobs",
                body=empty_state(
                    "Job unavailable",
                    str(exc),
                    '<a class="button" href="/ui/jobs">Back to jobs</a>',
                ),
                settings=settings,
            ),
            status_code=404,
        )

    try:
        tasks = [
            task
            for task in TaskStore(settings.app_data_dir).list()
            if task.get("job_id") == job_id
        ]
        task_error = ""
    except InvalidTaskDataError as exc:
        tasks, task_error = [], str(exc)

    saved = request.query_params.get("saved") == "1"
    queued_task = request.query_params.get("queued", "")
    notice = (
        "Job saved."
        if saved
        else (f"Processing task queued: {queued_task}" if queued_task else "")
    )
    details = detail_grid(
        (
            ("Company", job.get("company")),
            ("Location", job.get("location")),
            ("Workplace", job.get("remote_status")),
            ("Employment", job.get("employment_type")),
            ("Seniority", job.get("seniority")),
            ("Status", job.get("application_status")),
            ("Source", job.get("source")),
            ("Captured", job.get("captured_at")),
        )
    )
    auth_field = api_key_field(settings)
    safe_job_id = quote(job_id, safe="-._~")
    process_form = f"""
<form class="process-form" method="post" action="/ui/jobs/{safe_job_id}/process">
  {auth_field}
  <button class="button button-primary" type="submit">Queue fit analysis</button>
</form>
"""
    overview = f"""
<div class="detail-header">
  <div>
    <p class="eyebrow">{status_badge(job.get("application_status"))}</p>
    <h2>{escape(str(job.get("title") or "Untitled role"))}</h2>
    <p>{escape(str(job.get("company") or "Unknown company"))}</p>
  </div>
  <div class="detail-actions">
    {source_link(job.get("source_url"))}
    {process_form}
  </div>
</div>
{details}
"""
    content = text_block("Description", job.get("description_text"))
    content += list_block("Requirements", job.get("requirements"))
    content += list_block(
        "Preferred qualifications",
        job.get("preferred_qualifications"),
    )
    content += list_block("Technologies", job.get("technologies"))
    content += list_block("Responsibilities", job.get("responsibilities"))
    content += text_block("Notes", job.get("notes"))
    body = section("Job overview", overview)
    body += section(
        "Posting details",
        content or '<p class="muted panel-message">No additional details captured.</p>',
    )
    body += section("Processing history", task_table(tasks))
    return HTMLResponse(
        page(
            title=str(job.get("title") or "Job detail"),
            active="jobs",
            body=body,
            settings=settings,
            notice=notice,
            error=task_error,
        )
    )


@router.post("/ui/jobs/{job_id}/process", response_class=HTMLResponse)
async def process_job(
    job_id: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_job_store),
    manager: TaskManager = Depends(get_task_manager),
) -> Response:
    values = await read_form(request)
    safe_job_id = quote(job_id, safe="-._~")
    if not api_key_is_valid(values.get("api_key"), settings):
        return HTMLResponse(
            page(
                title="Authorization required",
                active="jobs",
                body=empty_state(
                    "Processing was not queued",
                    "The API key was missing or invalid.",
                    f'<a class="button" href="/ui/jobs/{safe_job_id}">Back to job</a>',
                ),
                settings=settings,
            ),
            status_code=401,
        )
    try:
        _, job_path = store.get_job(job_id)
    except (ResourceNotFoundError, InvalidStoredDataError) as exc:
        return HTMLResponse(
            page(
                title="Job unavailable",
                active="jobs",
                body=empty_state(
                    "Processing was not queued",
                    str(exc),
                    '<a class="button" href="/ui/jobs">Back to jobs</a>',
                ),
                settings=settings,
            ),
            status_code=404,
        )

    task = manager.submit(job_id, job_path)
    return RedirectResponse(
        f"/ui/jobs/{safe_job_id}?queued={task['task_id']}",
        status_code=303,
    )


@router.get("/ui/applications", response_class=HTMLResponse)
def applications(
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_job_store),
) -> HTMLResponse:
    try:
        items = store.list_applications()
        error = ""
    except InvalidStoredDataError as exc:
        items, error = [], str(exc)
    body = section(
        f"Application workspaces ({len(items)})",
        application_table(items),
    )
    return HTMLResponse(
        page(
            title="Applications",
            active="applications",
            body=body,
            settings=settings,
            error=error,
        )
    )


@router.get("/ui/applications/{application_id}", response_class=HTMLResponse)
def application_detail(
    application_id: str,
    settings: Settings = Depends(get_settings),
    store: JobStore = Depends(get_job_store),
) -> HTMLResponse:
    try:
        application = store.get_application(application_id)
    except (ResourceNotFoundError, InvalidStoredDataError) as exc:
        return HTMLResponse(
            page(
                title="Application not found",
                active="applications",
                body=empty_state(
                    "Workspace unavailable",
                    str(exc),
                    '<a class="button" href="/ui/applications">Back to applications</a>',
                ),
                settings=settings,
            ),
            status_code=404,
        )

    files = application.get("files", {})
    display_order = (
        "fit-analysis.json",
        "resume-targeting.md",
        "cover-letter-notes.md",
        "application-checklist.md",
        "job.json",
    )
    content = "".join(
        render_file(
            filename,
            files[filename],
            expanded=filename != "job.json",
        )
        for filename in display_order
        if filename in files
    )
    if not content:
        content = empty_state(
            "Workspace is empty",
            "No reviewable application files were found.",
        )
    body = section(application_id, content)
    return HTMLResponse(
        page(
            title="Application detail",
            active="applications",
            body=body,
            settings=settings,
        )
    )


@router.get("/ui/health", response_class=HTMLResponse)
def health_page(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    try:
        tags = OllamaClient(settings.ollama_base_url).fetch_tags()
        models = list(tags.models)
        installed = settings.ollama_model in models
        ollama_state = "healthy" if installed else "model missing"
        detail = (
            f"{settings.ollama_model} is installed."
            if installed
            else f"{settings.ollama_model} was not found in the installed model list."
        )
    except OllamaHealthError as exc:
        models = []
        ollama_state = "unavailable"
        detail = str(exc)

    models_html = "".join(f"<li>{escape(model)}</li>" for model in models)
    if not models_html:
        models_html = "<li>No models reported</li>"
    config = detail_grid(
        (
            ("Configured model", settings.ollama_model),
            ("Data directory", settings.app_data_dir),
            ("Mode", "Remote" if settings.enable_remote_mode else "Local"),
            ("API key", "Configured" if settings.app_api_key else "Not configured"),
        )
    )
    body = f"""
<div class="status-grid">
  <section class="status-card">
    <div class="status-card-heading"><h2>Service</h2>{status_badge("healthy")}</div>
    <p>The web service and mounted route layer are responding.</p>
  </section>
  <section class="status-card">
    <div class="status-card-heading"><h2>Ollama</h2>{status_badge(ollama_state)}</div>
    <p>{escape(detail)}</p>
  </section>
</div>
{section("Configuration", config)}
{section("Installed models", f'<ul class="model-list">{models_html}</ul>')}
"""
    return HTMLResponse(
        page(
            title="Health and status",
            active="health",
            body=body,
            settings=settings,
        )
    )


def render_submit_form(
    settings: Settings,
    values: dict[str, str],
    error: str = "",
) -> str:
    auth = api_key_field(settings)
    description = escape(values.get("description_text", ""))
    raw_text = escape(values.get("raw_text", ""))
    notes = escape(values.get("notes", ""))
    form = f"""
<form class="job-form" method="post" action="/ui/jobs/new">
  <section class="panel">
    <div class="section-heading">
      <h2>Posting</h2>
      <span class="required-note">Required fields marked *</span>
    </div>
    <div class="form-grid">
      <label><span>Source *</span>
        <select name="source" required>
          <option value="manual"{selected(values, "source", "manual", "manual")}>Manual</option>
          <option value="linkedin"{selected(values, "source", "linkedin")}>LinkedIn</option>
          <option value="indeed"{selected(values, "source", "indeed")}>Indeed</option>
          <option value="ziprecruiter"{selected(values, "source", "ziprecruiter")}>ZipRecruiter</option>
          <option value="other"{selected(values, "source", "other")}>Other</option>
        </select>
      </label>
      <label class="span-2"><span>Source URL *</span>
        <input type="url" name="source_url" value="{form_value(values, "source_url")}" required>
      </label>
      <label><span>Job title *</span>
        <input name="title" value="{form_value(values, "title")}" required>
      </label>
      <label><span>Company *</span>
        <input name="company" value="{form_value(values, "company")}" required>
      </label>
      <label><span>Location</span>
        <input name="location" value="{form_value(values, "location")}">
      </label>
      <label><span>Workplace</span>
        <select name="remote_status">
          <option value="unknown"{selected(values, "remote_status", "unknown", "unknown")}>Unknown</option>
          <option value="remote"{selected(values, "remote_status", "remote")}>Remote</option>
          <option value="hybrid"{selected(values, "remote_status", "hybrid")}>Hybrid</option>
          <option value="onsite"{selected(values, "remote_status", "onsite")}>Onsite</option>
        </select>
      </label>
      <label><span>Employment</span>
        <select name="employment_type">
          <option value="unknown"{selected(values, "employment_type", "unknown", "unknown")}>Unknown</option>
          <option value="full-time"{selected(values, "employment_type", "full-time")}>Full-time</option>
          <option value="part-time"{selected(values, "employment_type", "part-time")}>Part-time</option>
          <option value="contract"{selected(values, "employment_type", "contract")}>Contract</option>
          <option value="internship"{selected(values, "employment_type", "internship")}>Internship</option>
        </select>
      </label>
      <label><span>Seniority</span>
        <select name="seniority">
          <option value="unknown"{selected(values, "seniority", "unknown", "unknown")}>Unknown</option>
          <option value="intern"{selected(values, "seniority", "intern")}>Intern</option>
          <option value="junior"{selected(values, "seniority", "junior")}>Junior</option>
          <option value="mid"{selected(values, "seniority", "mid")}>Mid</option>
          <option value="senior"{selected(values, "seniority", "senior")}>Senior</option>
        </select>
      </label>
    </div>
  </section>
  <section class="panel">
    <div class="section-heading"><h2>Job text</h2></div>
    <div class="form-stack">
      <label><span>Description *</span>
        <textarea name="description_text" rows="14" required>{description}</textarea>
      </label>
      <label><span>Raw captured text</span>
        <textarea name="raw_text" rows="6">{raw_text}</textarea>
      </label>
      <label><span>Notes</span>
        <textarea name="notes" rows="4">{notes}</textarea>
      </label>
      {auth}
    </div>
    <div class="form-actions">
      <a class="button button-secondary" href="/ui/jobs">Cancel</a>
      <button class="button button-primary" type="submit">Save captured job</button>
    </div>
  </section>
</form>
"""
    return page(
        title="Submit job",
        active="submit",
        body=form,
        settings=settings,
        error=error,
    )


def api_key_field(settings: Settings) -> str:
    if not settings.app_api_key:
        return ""
    return """
<label class="api-key-field"><span>Service API key *</span>
  <input type="password" name="api_key" autocomplete="off" required>
</label>
"""


async def read_form(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True, strict_parsing=False)
    return {key: values[-1] for key, values in parsed.items()}
