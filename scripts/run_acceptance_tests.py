#!/usr/bin/env python3
"""Run acceptance checks for the local-first job application workflow."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_job_search.apply_from_file import (
    ApplyFromFileError,
    application_dir_for_job,
    apply_from_file,
)
from ai_job_search.fit_scoring import FitScoringError, score_job_file
from ai_job_search.job_validation import load_json, validate_job
from ai_job_search.model_provider import ModelProviderError
from ai_job_search.providers import OllamaProvider


Status = Literal["PASS", "FAIL", "SKIP"]

REQUIRED_DIRECTORIES = [
    Path("job_intake/captured_jobs"),
    Path("job_intake/examples"),
    Path("applications"),
    Path("profile"),
]
SAMPLE_JOB = Path("job_intake/examples/sample-dotnet-developer-job.json")
REQUIRED_FIT_FIELDS = [
    "overall_score",
    "recommendation",
    "matched_skills",
    "missing_skills",
    "resume_keywords_to_include",
]
REQUIRED_APPLICATION_FILES = [
    "job.json",
    "fit-analysis.json",
    "resume-targeting.md",
    "cover-letter-notes.md",
    "application-checklist.md",
]
OLLAMA_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class TestResult:
    name: str
    status: Status
    detail: str


def result(name: str, status: Status, detail: str) -> TestResult:
    test_result = TestResult(name=name, status=status, detail=detail)
    print(f"[{status}] {name}: {detail}")
    return test_result


def check_folder_structure() -> TestResult:
    missing = [str(path) for path in REQUIRED_DIRECTORIES if not (REPO_ROOT / path).is_dir()]
    if missing:
        return result("Folder structure", "FAIL", "missing directories: " + ", ".join(missing))
    return result("Folder structure", "PASS", "required local-first directories exist")


def check_sample_job() -> tuple[TestResult, dict[str, object] | None]:
    sample_path = REPO_ROOT / SAMPLE_JOB
    data, load_errors = load_json(sample_path)
    errors = load_errors or validate_job(data)
    if errors:
        return result("Sample job validity", "FAIL", "; ".join(errors)), None
    if not isinstance(data, dict):
        return result("Sample job validity", "FAIL", "root value must be an object"), None
    return result("Sample job validity", "PASS", str(SAMPLE_JOB)), data


def fetch_ollama_models() -> tuple[TestResult, bool]:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    tags_url = f"{base_url}/api/tags"
    request = urllib.request.Request(tags_url, headers={"Accept": "application/json"})

    try:
        with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return result("Ollama availability", "FAIL", f"{tags_url} returned HTTP {exc.code}"), False
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        return (
            result(
                "Ollama availability",
                "SKIP",
                f"Ollama is not reachable at {tags_url}; start Ollama to run model-dependent checks ({exc})",
            ),
            False,
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return result("Ollama availability", "FAIL", f"invalid JSON from {tags_url}: {exc}"), False

    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return result("Ollama availability", "FAIL", "/api/tags response is missing a models list"), False

    models = []
    for item in payload["models"]:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name:
            models.append(name)

    if not models:
        return result("Ollama availability", "FAIL", "Ollama is reachable but has no installed models"), False

    return result("Ollama availability", "PASS", "installed models: " + ", ".join(models)), True


def prepare_temporary_repo(temp_root: Path) -> Path:
    sample_source = REPO_ROOT / SAMPLE_JOB
    sample_target = temp_root / SAMPLE_JOB
    sample_target.parent.mkdir(parents=True)
    sample_target.write_bytes(sample_source.read_bytes())

    (temp_root / "applications").mkdir()
    profile_dir = temp_root / "profile"
    profile_dir.mkdir()
    resume_facts = REPO_ROOT / "profile" / "resume_facts.md"
    if resume_facts.is_file():
        shutil.copy2(resume_facts, profile_dir / "resume_facts.md")

    return sample_target


def check_fit_scoring(
    temp_root: Path,
    sample_path: Path,
    provider: OllamaProvider,
    ollama_ready: bool,
) -> tuple[TestResult, Path | None]:
    script_path = REPO_ROOT / "scripts" / "score_fit.py"
    if not script_path.is_file():
        return result("Fit scoring", "SKIP", "scripts/score_fit.py does not exist"), None
    if not ollama_ready:
        return result("Fit scoring", "SKIP", "Ollama is unavailable"), None

    try:
        output_path = score_job_file(sample_path, provider=provider, repo_root=temp_root)
    except (FitScoringError, ModelProviderError) as exc:
        return result("Fit scoring", "FAIL", str(exc)), None

    analysis, load_errors = load_json(output_path)
    if load_errors:
        return result("Fit scoring", "FAIL", "; ".join(load_errors)), None
    if not isinstance(analysis, dict):
        return result("Fit scoring", "FAIL", "fit-analysis.json root must be an object"), None

    missing = [field for field in REQUIRED_FIT_FIELDS if field not in analysis]
    if missing:
        return result("Fit scoring", "FAIL", "missing output fields: " + ", ".join(missing)), None

    return result("Fit scoring", "PASS", "created valid fit-analysis.json in temporary output"), output_path


def check_apply_from_file(
    temp_root: Path,
    sample_path: Path,
    sample_job: dict[str, object] | None,
    provider: OllamaProvider,
    fit_path: Path | None,
    ollama_ready: bool,
) -> TestResult:
    script_path = REPO_ROOT / "scripts" / "apply_from_file.py"
    if not script_path.is_file():
        return result("Apply from file", "SKIP", "scripts/apply_from_file.py does not exist")
    if sample_job is None:
        return result("Apply from file", "SKIP", "sample job validation failed")
    if not ollama_ready:
        return result("Apply from file", "SKIP", "Ollama is unavailable")
    if fit_path is None:
        return result("Apply from file", "SKIP", "fit scoring prerequisite failed")

    expected_app_dir = application_dir_for_job(sample_job, temp_root)
    expected_app_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(fit_path, expected_app_dir / "fit-analysis.json")

    try:
        app_dir = apply_from_file(sample_path, provider=provider, repo_root=temp_root)
    except (ApplyFromFileError, ModelProviderError) as exc:
        return result("Apply from file", "FAIL", str(exc))

    missing = [name for name in REQUIRED_APPLICATION_FILES if not (app_dir / name).is_file()]
    if missing:
        return result("Apply from file", "FAIL", "missing generated files: " + ", ".join(missing))

    return result("Apply from file", "PASS", "created complete temporary application workspace")


def print_summary(results: list[TestResult]) -> int:
    counts = {
        status: sum(test_result.status == status for test_result in results)
        for status in ("PASS", "FAIL", "SKIP")
    }
    print()
    print(
        "Acceptance summary: "
        f"{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped"
    )
    return 1 if counts["FAIL"] else 0


def main() -> int:
    results: list[TestResult] = []
    results.append(check_folder_structure())

    sample_result, sample_job = check_sample_job()
    results.append(sample_result)

    ollama_result, ollama_ready = fetch_ollama_models()
    results.append(ollama_result)

    if sample_job is None:
        results.append(result("Fit scoring", "SKIP", "sample job validation failed"))
        results.append(result("Apply from file", "SKIP", "sample job validation failed"))
    else:
        with tempfile.TemporaryDirectory(prefix="ai-job-search-acceptance-") as temp_dir:
            temp_root = Path(temp_dir)
            sample_path = prepare_temporary_repo(temp_root)
            provider = OllamaProvider()

            fit_result, fit_path = check_fit_scoring(
                temp_root=temp_root,
                sample_path=sample_path,
                provider=provider,
                ollama_ready=ollama_ready,
            )
            results.append(fit_result)
            results.append(
                check_apply_from_file(
                    temp_root=temp_root,
                    sample_path=sample_path,
                    sample_job=sample_job,
                    provider=provider,
                    fit_path=fit_path,
                    ollama_ready=ollama_ready,
                )
            )

    return print_summary(results)


if __name__ == "__main__":
    sys.exit(main())
