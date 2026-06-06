"""Adapter from the service API to the Phase 2 apply-from-file workflow."""

from __future__ import annotations

from pathlib import Path

from ai_job_search.apply_from_file import ApplyFromFileError, apply_from_file
from ai_job_search.model_provider import ModelProviderError
from ai_job_search.providers import OllamaProvider

from app.config import Settings


class ProcessingError(RuntimeError):
    """Raised when a job cannot be processed into an application workspace."""


def process_job(job_path: Path, settings: Settings) -> Path:
    provider = OllamaProvider(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
    )
    try:
        return apply_from_file(
            job_path,
            provider=provider,
            repo_root=settings.app_data_dir,
        )
    except (ApplyFromFileError, ModelProviderError, OSError) as exc:
        raise ProcessingError(str(exc)) from exc
