"""Private Ollama health client using the configured base URL."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_TIMEOUT_SECONDS = 5


class OllamaHealthError(RuntimeError):
    """Base error for Ollama availability checks."""


class OllamaUnavailableError(OllamaHealthError):
    """Raised when the configured Ollama service cannot be reached."""


class OllamaEndpointError(OllamaHealthError):
    """Raised when Ollama is reachable but its tags endpoint fails."""


class OllamaInvalidResponseError(OllamaHealthError):
    """Raised when Ollama returns an unusable tags response."""


@dataclass(frozen=True)
class OllamaTags:
    models: tuple[str, ...]


class OllamaClient:
    def __init__(self, base_url: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fetch_tags(self) -> OllamaTags:
        url = f"{self.base_url}/api/tags"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise OllamaEndpointError(f"Ollama tags endpoint returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise OllamaUnavailableError("Ollama is not reachable") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise OllamaUnavailableError("Ollama health check timed out") from exc
        except UnicodeDecodeError as exc:
            raise OllamaInvalidResponseError("Ollama returned a non-UTF-8 response") from exc

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise OllamaInvalidResponseError("Ollama returned invalid JSON") from exc

        return OllamaTags(models=extract_model_names(payload))


def extract_model_names(payload: Any) -> tuple[str, ...]:
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise OllamaInvalidResponseError("Ollama response is missing a models list")

    models = []
    for item in payload["models"]:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name.strip():
            models.append(name.strip())

    return tuple(models)
