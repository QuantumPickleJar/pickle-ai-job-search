#!/usr/bin/env python3
"""Smoke-test a local Ollama runtime.

Usage:
    python scripts/ollama_smoke_test.py
    OLLAMA_MODEL=qwen2.5:14b python scripts/ollama_smoke_test.py
    python scripts/ollama_smoke_test.py --generate
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "http://localhost:11434"
TAGS_TIMEOUT_SECONDS = 5
GENERATE_TIMEOUT_SECONDS = 60
RUNTIME_TEST_PROMPT = """Return JSON only:
{
  "status": "ok",
  "task": "job_search_runtime_test"
}
"""


def normalize_base_url(value: str) -> str:
    return value.rstrip("/")


def request_json(url: str, timeout: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json"}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)


def model_name(model: dict[str, Any]) -> str | None:
    name = model.get("name") or model.get("model")
    return name if isinstance(name, str) else None


def fetch_models(base_url: str) -> list[str]:
    tags_url = f"{base_url}/api/tags"
    try:
        payload = request_json(tags_url, timeout=TAGS_TIMEOUT_SECONDS)
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Ollama is not reachable at {tags_url}. Start Ollama and retry. Details: {exc}"
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Timed out connecting to Ollama at {tags_url}.") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid JSON from {tags_url}: {exc}") from exc

    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        raise RuntimeError("Ollama /api/tags response did not include a models list.")

    models = [name for item in raw_models if isinstance(item, dict) for name in [model_name(item)] if name]
    if not models:
        raise RuntimeError("Ollama is reachable, but no installed models were found.")

    return models


def run_generation_test(base_url: str, model: str) -> dict[str, Any]:
    generate_url = f"{base_url}/api/generate"
    try:
        payload = request_json(
            generate_url,
            timeout=GENERATE_TIMEOUT_SECONDS,
            payload={
                "model": model,
                "prompt": RUNTIME_TEST_PROMPT,
                "format": "json",
                "stream": False,
            },
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama generation failed for model {model!r}: HTTP {exc.code}. {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Ollama is not reachable at {generate_url}. Start Ollama and retry. Details: {exc}"
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Timed out waiting for model {model!r} to generate a response.") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Ollama returned invalid JSON from {generate_url}: {exc}") from exc

    response_text = payload.get("response")
    if not isinstance(response_text, str) or not response_text.strip():
        raise RuntimeError("Ollama generation response did not include a non-empty response string.")

    try:
        parsed_response = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Model response was not valid JSON: {response_text}") from exc

    return parsed_response


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test local Ollama availability and installed models.")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Also run a small structured generation test using OLLAMA_MODEL or the first installed model.",
    )
    args = parser.parse_args()

    base_url = normalize_base_url(os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL))
    configured_model = os.environ.get("OLLAMA_MODEL")

    try:
        models = fetch_models(base_url)
        print(f"OK: Ollama reachable at {base_url}")
        print("Installed models:")
        for model in models:
            print(f"- {model}")

        selected_model = configured_model or models[0]
        if configured_model:
            if configured_model not in models:
                installed = ", ".join(models)
                raise RuntimeError(
                    f"Configured OLLAMA_MODEL={configured_model!r} is not installed. Installed models: {installed}"
                )
            print(f"OK: configured OLLAMA_MODEL is installed: {configured_model}")

        if args.generate:
            parsed_response = run_generation_test(base_url, selected_model)
            print(f"OK: generation test completed with model: {selected_model}")
            print("Generation response JSON:")
            print(json.dumps(parsed_response, indent=2, sort_keys=True))

    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
