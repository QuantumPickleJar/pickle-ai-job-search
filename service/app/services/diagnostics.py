"""Test and report on service provider connectivity and configuration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ai_job_search.model_provider import ModelRequest
from ai_job_search.providers.ollama import OllamaProvider

from app.config import Settings
from app.services.task_errors import safe_task_error


@dataclass(frozen=True)
class ModelDiagnostics:
    provider_reachable: bool
    configured_model: str
    fallback_model: str | None
    num_ctx: int
    keep_alive: str
    response_text: str
    elapsed_ms: float
    error: str | None


def smoke_test_model(settings: Settings) -> ModelDiagnostics:
    """Test if the configured Ollama provider can respond to a simple request."""
    configured_model = settings.ollama_model
    fallback_model = getattr(settings, "ollama_fallback_model", None)
    
    provider = OllamaProvider(
        model=configured_model,
        base_url=settings.ollama_base_url,
        timeout_seconds=10,
    )
    
    num_ctx = getattr(provider, "_num_ctx", 2048)
    keep_alive = getattr(provider, "_keep_alive", "5m")
    
    start_time = time.monotonic()
    response_text = ""
    error = None
    
    try:
        request = ModelRequest(
            system_prompt="You are a diagnostic test helper.",
            user_prompt="Return the word OK only.",
            temperature=0.1,
            max_tokens=10,
            response_format="text",
        )
        response = provider.complete(request)
        response_text = response.text.strip()
    except Exception as exc:
        sanitized = safe_task_error(exc, "smoke-test")
        error = sanitized
    
    elapsed_ms = (time.monotonic() - start_time) * 1000
    
    return ModelDiagnostics(
        provider_reachable=error is None,
        configured_model=configured_model,
        fallback_model=fallback_model,
        num_ctx=num_ctx,
        keep_alive=keep_alive,
        response_text=response_text,
        elapsed_ms=elapsed_ms,
        error=error,
    )
