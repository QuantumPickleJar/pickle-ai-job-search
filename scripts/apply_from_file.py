#!/usr/bin/env python3
"""Create an application workspace from a captured job JSON file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_job_search.apply_from_file import ApplyFromFileError, apply_from_file, latest_captured_job
from ai_job_search.model_provider import ModelProviderError
from ai_job_search.providers import OllamaProvider


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a local application workspace from a captured job JSON file.")
    parser.add_argument("job_json", nargs="?", help="Path to a captured job JSON file.")
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Use the newest JSON file in job_intake/captured_jobs/.",
    )
    args = parser.parse_args()

    if args.latest and args.job_json:
        parser.error("provide either a job JSON path or --latest, not both")
    if not args.latest and not args.job_json:
        parser.error("provide a job JSON path or --latest")

    try:
        job_path = latest_captured_job(REPO_ROOT) if args.latest else Path(args.job_json)
        app_dir = apply_from_file(job_path, provider=OllamaProvider(), repo_root=REPO_ROOT)
    except (ApplyFromFileError, ModelProviderError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"WROTE: {app_dir}")
    print(f"- {app_dir / 'job.json'}")
    print(f"- {app_dir / 'fit-analysis.json'}")
    print(f"- {app_dir / 'resume-targeting.md'}")
    print(f"- {app_dir / 'cover-letter-notes.md'}")
    print(f"- {app_dir / 'application-checklist.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
