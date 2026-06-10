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
DEFAULT_MODEL = "qwen2.5:7b"
DEFAULT_NUM_CTX = 2048
DEFAULT_KEEP_ALIVE = "0"
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
        fallback_model: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        self.fallback_model = fallback_model or os.environ.get("OLLAMA_FALLBACK_MODEL") or None
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.num_ctx = int(os.environ.get("OLLAMA_NUM_CTX", str(DEFAULT_NUM_CTX)))
        self.keep_alive = os.environ.get("OLLAMA_KEEP_ALIVE", DEFAULT_KEEP_ALIVE)
        self.timeout_seconds = timeout_seconds

    def complete(self, request: ModelRequest) -> ModelResponse:
        payload = self._build_payload(request, model=self.model)
        try:
            raw = self._post_chat(payload)
            text = self._extract_text(raw)
            return ModelResponse(text=text, raw=raw)
        except ModelProviderError as exc:
            if self._should_retry_with_fallback(exc):
                fallback_payload = self._build_payload(request, model=self.fallback_model)
                raw = self._post_chat(fallback_payload)
                text = self._extract_text(raw)
                return ModelResponse(text=text, raw=raw)
            raise

    def _build_payload(self, request: ModelRequest, model: str | None = None) -> dict[str, Any]:
        system_prompt = request.system_prompt.strip()
        if request.response_format == "json":
            system_prompt = f"{system_prompt}\n\n{JSON_SYSTEM_SUFFIX}" if system_prompt else JSON_SYSTEM_SUFFIX

        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "stream": False,
            "keep_alive": self.keep_alive,
        }

        options: dict[str, Any] = {"num_ctx": self.num_ctx}
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
            if exc.code == 500 and self._is_cuda_oom_message(detail):
                raise ModelProviderError(
                    "Ollama chat request failed: HTTP 500 CUDA out-of-memory. "
                    f"Model={payload.get('model')!r}. Detail: {detail}"
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

    @staticmethod
    def _is_cuda_oom_message(detail: str) -> bool:
        lowered = detail.lower()
        return (
            "cudamalloc failed" in lowered
            or "out of memory" in lowered
            or "unable to allocate cuda" in lowered
            or "failed to allocate cuda" in lowered
        )

    def _should_retry_with_fallback(self, exc: ModelProviderError) -> bool:
        if not self.fallback_model or self.fallback_model == self.model:
            return False
        message = str(exc).lower()
        return "http 500" in message and "cuda out-of-memory" in message

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
