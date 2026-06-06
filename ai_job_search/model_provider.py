"""Generic model-provider interfaces for local-first workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


ResponseFormat = Literal["text", "json"]


@dataclass(frozen=True)
class ModelRequest:
    system_prompt: str
    user_prompt: str
    temperature: float | None = None
    max_tokens: int | None = None
    response_format: ResponseFormat = "text"


@dataclass(frozen=True)
class ModelResponse:
    text: str
    raw: Any | None = None


class ModelProvider(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return a model completion for the given request."""


class ModelProviderError(RuntimeError):
    """Base error raised by model providers."""


class ModelProviderConnectionError(ModelProviderError):
    """Raised when the provider endpoint is unreachable."""


class ModelProviderModelMissingError(ModelProviderError):
    """Raised when the configured model is not available."""


class ModelProviderTimeoutError(ModelProviderError):
    """Raised when the provider does not respond in time."""


class ModelProviderInvalidResponseError(ModelProviderError):
    """Raised when the provider returns an unexpected response shape."""

