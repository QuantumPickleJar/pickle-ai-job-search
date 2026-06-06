"""Environment-based service configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse


SERVICE_NAME = "AI Job Search Service"
SERVICE_VERSION = "0.1.0"

DEFAULT_APP_HOST = "0.0.0.0"
DEFAULT_APP_PORT = 3927
DEFAULT_APP_DATA_DIR = "/app/data"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:14b"

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    """Raised when service environment configuration is invalid."""


def parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise ConfigError("APP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ConfigError("APP_PORT must be between 1 and 65535")
    return port


def parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    allowed = ", ".join(sorted(TRUE_VALUES | FALSE_VALUES))
    raise ConfigError(f"{name} must be one of: {allowed}")


def required_text(name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ConfigError(f"{name} must not be empty")
    return normalized


def parse_base_url(value: str) -> str:
    base_url = required_text("OLLAMA_BASE_URL", value).rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("OLLAMA_BASE_URL must be an absolute http or https URL")
    if parsed.username or parsed.password:
        raise ConfigError("OLLAMA_BASE_URL must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ConfigError("OLLAMA_BASE_URL must not contain a path, query, or fragment")
    return base_url


@dataclass(frozen=True)
class Settings:
    app_host: str
    app_port: int
    app_data_dir: Path
    ollama_base_url: str
    ollama_model: str
    app_api_key: str = field(repr=False)
    enable_remote_mode: bool

    @classmethod
    def from_env(cls) -> "Settings":
        app_host = required_text("APP_HOST", os.environ.get("APP_HOST", DEFAULT_APP_HOST))
        app_port = parse_port(os.environ.get("APP_PORT", str(DEFAULT_APP_PORT)))
        app_data_dir = Path(
            required_text("APP_DATA_DIR", os.environ.get("APP_DATA_DIR", DEFAULT_APP_DATA_DIR))
        )
        ollama_base_url = parse_base_url(
            os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL)
        )
        ollama_model = required_text(
            "OLLAMA_MODEL",
            os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
        )
        app_api_key = os.environ.get("APP_API_KEY", "").strip()
        enable_remote_mode = parse_bool(
            "ENABLE_REMOTE_MODE",
            os.environ.get("ENABLE_REMOTE_MODE", "false"),
        )

        if enable_remote_mode and not app_api_key.strip():
            raise ConfigError("APP_API_KEY must be set when ENABLE_REMOTE_MODE is true")

        return cls(
            app_host=app_host,
            app_port=app_port,
            app_data_dir=app_data_dir,
            ollama_base_url=ollama_base_url,
            ollama_model=ollama_model,
            app_api_key=app_api_key,
            enable_remote_mode=enable_remote_mode,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
