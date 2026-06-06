"""Ollama-backed model provider."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any

from ai_job_search.model_provider import (
    ModelProviderConnectionError,
    ModelProviderError,
    ModelProviderInvalidResponseError,
    ModelProviderModelMissingError,
    ModelProviderTimeoutError,
    ModelRequest,
    ModelResponse,
)


DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:14b"
DEFAULT_TIMEOUT_SECONDS = 60
JSON_SYSTEM_SUFFIX = (
    "Return JSON only. Do not wrap the JSON in Markdown, do not include prose, "
    "and do not include comments."
)


class OllamaProvider:
    """Call Ollama's local chat API through the generic provider interface."""

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = self._build_payload(request)
        raw = self._post_chat(payload)
        text = self._extract_text(raw)
        return ModelResponse(text=text, raw=raw)

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        system_prompt = request.system_prompt.strip()
        if request.response_format == "json":
            system_prompt = f"{system_prompt}\n\n{JSON_SYSTEM_SUFFIX}" if system_prompt else JSON_SYSTEM_SUFFIX

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "stream": False,
        }

        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if options:
            payload["options"] = options

        if request.response_format == "json":
            payload["format"] = "json"

        return payload

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/api/chat"
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code == 404 or "not found" in detail.lower() or "pull" in detail.lower():
                raise ModelProviderModelMissingError(
                    f"Ollama model {self.model!r} is not available. Pull it with: ollama pull {self.model}"
                ) from exc
            raise ModelProviderError(f"Ollama chat request failed: HTTP {exc.code}. {detail}") from exc
        except urllib.error.URLError as exc:
            raise ModelProviderConnectionError(
                f"Ollama is not reachable at {url}. Start Ollama and retry. Details: {exc}"
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ModelProviderTimeoutError(
                f"Timed out waiting for Ollama model {self.model!r} after {self.timeout_seconds} seconds."
            ) from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ModelProviderInvalidResponseError(f"Ollama returned invalid JSON: {exc}") from exc

        if not isinstance(parsed, dict):
            raise ModelProviderInvalidResponseError("Ollama response must be a JSON object.")

        return parsed

    def _extract_text(self, raw: dict[str, Any]) -> str:
        message = raw.get("message")
        if not isinstance(message, dict):
            raise ModelProviderInvalidResponseError("Ollama response missing message object.")

        content = message.get("content")
        if not isinstance(content, str):
            raise ModelProviderInvalidResponseError("Ollama response message.content must be a string.")

        if not content.strip():
            raise ModelProviderInvalidResponseError("Ollama response message.content is empty.")

        return content
