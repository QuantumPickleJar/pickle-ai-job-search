#!/usr/bin/env python3
"""Validate a captured job posting JSON file.

Usage:
    python scripts/validate_job.py job_intake/examples/sample-dotnet-developer-job.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_job_search.job_validation import load_json, validate_job


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a captured job posting JSON file.")
    parser.add_argument("path", help="Path to a captured job JSON file.")
    args = parser.parse_args()

    path = Path(args.path)
    data, load_errors = load_json(path)
    errors = load_errors or validate_job(data)

    if errors:
        print(f"INVALID: {path}")
        for error in errors:
            print(f"- {error}")
        return 1

    print(f"VALID: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
