#!/usr/bin/env python3
"""Score local fit for a captured job JSON file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_job_search.fit_scoring import FitScoringError, score_job_file
from ai_job_search.model_provider import ModelProviderError
from ai_job_search.providers import OllamaProvider


def main() -> int:
    parser = argparse.ArgumentParser(description="Score local job fit using the configured model provider.")
    parser.add_argument("job_json", help="Path to a captured job JSON file.")
    args = parser.parse_args()

    job_path = Path(args.job_json)
    provider = OllamaProvider()

    try:
        output_path = score_job_file(job_path, provider=provider, repo_root=REPO_ROOT)
    except (FitScoringError, ModelProviderError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"WROTE: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
