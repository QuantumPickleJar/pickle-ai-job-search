#!/usr/bin/env python3
"""Run a tiny demo prompt through the configured model provider."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_job_search.model_provider import ModelProviderError, ModelRequest
from ai_job_search.providers import OllamaProvider


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a tiny prompt through the Ollama model provider.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Ask the model for a JSON response instead of plain text.",
    )
    args = parser.parse_args()

    provider = OllamaProvider()
    request = ModelRequest(
        system_prompt="You are a concise runtime test assistant.",
        user_prompt=(
            'Return {"status": "ok", "task": "model_provider_demo"}'
            if args.json
            else "Reply with one short sentence confirming the model provider is working."
        ),
        temperature=0,
        max_tokens=80,
        response_format="json" if args.json else "text",
    )

    try:
        response = provider.complete(request)
    except ModelProviderError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(response.text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
