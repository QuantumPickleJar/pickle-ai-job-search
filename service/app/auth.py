"""API-key authentication for mutating service endpoints."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings


def api_key_is_valid(provided_key: str | None, settings: Settings) -> bool:
    configured_key = settings.app_api_key
    if not configured_key:
        return True
    return provided_key is not None and hmac.compare_digest(provided_key, configured_key)


def require_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    if not api_key_is_valid(x_api_key, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
