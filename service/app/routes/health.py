"""Service and Ollama health endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.services.diagnostics import smoke_test_model
from app.config import Settings, get_settings
from app.services.ollama_client import (
    OllamaClient,
    OllamaEndpointError,
    OllamaHealthError,
    OllamaInvalidResponseError,
)


router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ollama", response_model=None)
def ollama_health(settings: Settings = Depends(get_settings)) -> dict[str, Any] | JSONResponse:
    client = OllamaClient(settings.ollama_base_url)

    try:
        tags = client.fetch_tags()
    except OllamaInvalidResponseError as exc:
        return health_error("invalid_response", str(exc), reachable=True)
    except OllamaEndpointError as exc:
        return health_error("endpoint_error", str(exc), reachable=True)
    except OllamaHealthError as exc:
        return health_error("unavailable", str(exc), reachable=False)

    models = list(tags.models)
    if not models:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "ollama_reachable": True,
                "configured_model": settings.ollama_model,
                "model_installed": False,
                "installed_models": [],
                "error": "Ollama is reachable but no models are installed",
            },
        )

    model_installed = settings.ollama_model in tags.models
    response = {
        "status": "ok" if model_installed else "error",
        "ollama_reachable": True,
        "configured_model": settings.ollama_model,
        "model_installed": model_installed,
        "installed_models": models,
    }
    if model_installed:
        return response

    response["error"] = "Configured Ollama model is not installed"
    return JSONResponse(status_code=503, content=response)


def health_error(code: str, message: str, reachable: bool) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "status": "error",
            "ollama_reachable": reachable,
            "error_code": code,
            "error": message,
        },
    )


@router.get("/health/model", response_model=None)
def model_health(settings: Settings = Depends(get_settings)) -> dict[str, Any] | JSONResponse:
    """Check if the configured model is available and reachable."""
    try:
        client = OllamaClient(settings.ollama_base_url)
        tags = client.fetch_tags()
    except OllamaHealthError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "provider_reachable": False,
                "configured_model": settings.ollama_model,
                "error": str(exc),
            },
        )
    except (OllamaInvalidResponseError, OllamaEndpointError) as exc:
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "provider_reachable": True,
                "configured_model": settings.ollama_model,
                "error": str(exc),
            },
        )

    model_available = settings.ollama_model in tags.models
    if model_available:
        return {
            "status": "ok",
            "provider_reachable": True,
            "configured_model": settings.ollama_model,
            "model_available": True,
        }

    return JSONResponse(
        status_code=503,
        content={
            "status": "error",
            "provider_reachable": True,
            "configured_model": settings.ollama_model,
            "model_available": False,
            "error": f"Model '{settings.ollama_model}' is not installed",
        },
    )


@router.post("/diagnostics/model-smoke-test", response_model=None)
def model_smoke_test(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Run a smoke test of the configured Ollama provider."""
    diagnostics = smoke_test_model(settings)
    
    return {
        "status": "ok" if diagnostics.provider_reachable else "error",
        "provider_reachable": diagnostics.provider_reachable,
        "configured_model": diagnostics.configured_model,
        "fallback_model": diagnostics.fallback_model,
        "num_ctx": diagnostics.num_ctx,
        "keep_alive": diagnostics.keep_alive,
        "response_text": diagnostics.response_text,
        "elapsed_ms": round(diagnostics.elapsed_ms, 2),
        "error": diagnostics.error,
    }

