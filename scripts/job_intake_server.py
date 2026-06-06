#!/usr/bin/env python3
"""Run a localhost-only captured job intake server."""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_job_search.intake import JobIntakeError, save_captured_job


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3927
CAPTURE_PATH = "/jobs/capture"
ALLOWED_ORIGIN_PREFIXES = (
    "chrome-extension://",
    "moz-extension://",
    "edge-extension://",
)
ALLOWED_LOCAL_DEV_ORIGINS = {
    "http://localhost:3927",
    "http://127.0.0.1:3927",
}


def allowed_origin(origin: str | None) -> str | None:
    if not origin:
        return None
    if origin in ALLOWED_LOCAL_DEV_ORIGINS:
        return origin
    if origin.startswith(ALLOWED_ORIGIN_PREFIXES):
        return origin
    return None


class JobIntakeHandler(BaseHTTPRequestHandler):
    server_version = "JobIntakeServer/0.1"

    def end_headers(self) -> None:
        origin = allowed_origin(self.headers.get("Origin"))
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        if self.path != CAPTURE_PATH:
            self.send_json({"error": "not found"}, status=404)
            return
        self.send_response(204)
        self.end_headers()

    def do_POST(self) -> None:
        if self.path != CAPTURE_PATH:
            self.send_json({"error": "not found"}, status=404)
            return

        try:
            payload = self.read_json_body()
            job, path = save_captured_job(payload, REPO_ROOT)
        except JobIntakeError as exc:
            self.send_json({"error": str(exc)}, status=400)
            return
        except json.JSONDecodeError as exc:
            self.send_json({"error": f"invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}"}, status=400)
            return
        except OSError as exc:
            self.send_json({"error": f"failed to save captured job: {exc}"}, status=500)
            return

        response = {
            "status": "saved",
            "path": str(path.relative_to(REPO_ROOT)),
            "id": job["id"],
        }
        self.send_json(response, status=201)

    def read_json_body(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type.lower():
            raise JobIntakeError("Content-Type must be application/json")

        length = self.headers.get("Content-Length")
        if length is None:
            raise JobIntakeError("Content-Length header is required")

        try:
            body_length = int(length)
        except ValueError as exc:
            raise JobIntakeError("Content-Length must be an integer") from exc

        if body_length <= 0:
            raise JobIntakeError("request body must not be empty")
        if body_length > 1_000_000:
            raise JobIntakeError("request body is too large")

        raw = self.rfile.read(body_length).decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise JobIntakeError("request body must be a JSON object")
        return payload

    def send_json(self, payload: dict[str, Any], status: int) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the localhost-only job intake server.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host. Must be 127.0.0.1 or localhost.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port.")
    args = parser.parse_args()

    if args.host not in {"127.0.0.1", "localhost"}:
        print("ERROR: refusing to bind to a non-localhost address. Use 127.0.0.1 or localhost.", file=sys.stderr)
        return 1

    server = ThreadingHTTPServer((args.host, args.port), JobIntakeHandler)
    print(f"Listening on http://{args.host}:{args.port}")
    print(f"POST {CAPTURE_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
