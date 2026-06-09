"""Sanitize task errors before persisting and displaying them in the UI."""

from __future__ import annotations

import re


MAX_ERROR_LENGTH = 1500


_CUDA_OOM_PATTERNS = (
    "cudamalloc failed",
    "out of memory",
    "unable to allocate cuda",
    "failed to allocate cuda",
    "llama-server process has terminated",
)


_STACKTRACE_LINE = re.compile(r"^\s*(File \".+\", line \d+|Traceback \(most recent call last\):)")
_MODEL_RE = re.compile(r"model\s*=\s*['\"]?([^'\"\s]+)['\"]?", re.IGNORECASE)
_ALLOC_RE = re.compile(
    r"failed to allocate cuda\d* buffer of size \d+",
    re.IGNORECASE,
)

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(token\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(secret\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(password\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s,;]+)"),
    re.compile(r"(?i)(x-api-key\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(api_key=)([^&\s]+)"),
    re.compile(r"\bsk-[A-Za-z0-9]{12,}\b"),
)

_BLOB_PATH_RE = re.compile(
    r"[A-Za-z]:\\[^\n\r]*?\\blobs\\sha256-[A-Za-z0-9]+",
    re.IGNORECASE,
)


def safe_task_error(exc: Exception, task_type: str) -> str:
    """Return a UI-safe error that preserves actionable diagnostics."""
    raw = str(exc or "").strip()
    if not raw:
        return _fallback_message(task_type)

    cleaned = _strip_stacktrace(raw)
    cleaned = _mask_secrets(cleaned)
    cleaned = _scrub_local_model_paths(cleaned)

    if _is_cuda_oom(cleaned):
        cleaned = _format_cuda_oom(cleaned)

    cleaned = cleaned.strip()
    if not cleaned:
        cleaned = _fallback_message(task_type)

    return _truncate(cleaned, MAX_ERROR_LENGTH)


def _strip_stacktrace(message: str) -> str:
    lines = []
    for line in message.splitlines():
        if _STACKTRACE_LINE.match(line):
            continue
        stripped = line.strip()
        if stripped.startswith("^"):
            continue
        lines.append(line)
    merged = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", merged)


def _mask_secrets(message: str) -> str:
    redacted = message
    for index, pattern in enumerate(_SECRET_PATTERNS):
        if index == len(_SECRET_PATTERNS) - 1:
            redacted = pattern.sub("<redacted>", redacted)
            continue
        redacted = pattern.sub(r"\1<redacted>", redacted)
    return redacted


def _scrub_local_model_paths(message: str) -> str:
    return _BLOB_PATH_RE.sub("<ollama-blob-path>", message)


def _is_cuda_oom(message: str) -> bool:
    lowered = message.lower()
    return any(pattern in lowered for pattern in _CUDA_OOM_PATTERNS)


def _extract_model(message: str) -> str:
    match = _MODEL_RE.search(message)
    if not match:
        return "configured model"
    return match.group(1).strip()


def _extract_allocation_detail(message: str) -> str:
    match = _ALLOC_RE.search(message)
    if not match:
        return ""
    return match.group(0).strip()


def _format_cuda_oom(message: str) -> str:
    model = _extract_model(message)
    allocation_detail = _extract_allocation_detail(message)
    summary = (
        f"Ollama CUDA out-of-memory while loading model '{model}'. "
        "The model server could not allocate GPU memory. "
        "Try restarting the model runner, checking 'ollama ps', lowering 'OLLAMA_NUM_CTX', "
        "using a smaller model, or setting 'OLLAMA_FALLBACK_MODEL'."
    )
    if allocation_detail:
        summary += f" Allocation detail: {allocation_detail}."
    return summary


def _truncate(message: str, max_length: int) -> str:
    if len(message) <= max_length:
        return message
    suffix = " ... [truncated]"
    return message[: max_length - len(suffix)] + suffix


def _fallback_message(task_type: str) -> str:
    if task_type == "process-job":
        return "Job processing failed. Review service logs for details."
    if task_type == "generate-cv":
        return "CV generation failed. Review service logs for details."
    if task_type == "generate-cover-letter":
        return "Cover letter generation failed. Review service logs for details."
    return "Task failed. Review service logs for details."