"""Service and Ollama health endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

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
