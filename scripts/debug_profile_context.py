#!/usr/bin/env python3
"""Print profile context loading diagnostics for fit scoring."""

from __future__ import annotations

import argparse
from pathlib import Path

from ai_job_search.fit_scoring import load_profile_context, profile_context_status


def main() -> int:
    parser = argparse.ArgumentParser(description="Debug profile context loading.")
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root to inspect (for example: . or data)",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    status = profile_context_status(root)
    context = load_profile_context(root)

    print(f"Root: {status['repo_root']}")
    print(f"Selected profile dir: {status['selected_profile_dir']}")
    print(f"Selected source: {status['selected_profile_source']}")

    print("\nCandidate profile dirs:")
    for item in status["candidate_profile_dirs"]:
        print(f"- {item['source']}: {item['path']}")

    print("\nLoaded files:")
    loaded = status["loaded_files"]
    if loaded:
        for name in loaded:
            print(f"- {name}")
    else:
        print("- (none)")

    print("\nMissing or empty files:")
    missing = status["missing_files"]
    if missing:
        for name in missing:
            print(f"- {name}")
    else:
        print("- (none)")

    print("\nFile details:")
    for item in status["files"]:
        print(
            "- {filename}: exists={exists}, loaded={loaded}, chars={chars}, path={path}".format(
                filename=item["filename"],
                exists=item["exists"],
                loaded=item["loaded"],
                chars=item["chars"],
                path=item["path"],
            )
        )

    print(f"\nCombined context length: {len(context)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
