"""End-to-end localhost acceptance tests for the Phase 3 service API."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_ROOT = REPO_ROOT / "service"
API_KEY = "acceptance-test-key"
MODEL = "acceptance-model"
FIT_ANALYSIS = {
    "overall_score": 72,
    "recommendation": "maybe",
    "reasons_to_apply": ["Relevant backend fundamentals"],
    "risks": ["Production experience should be verified"],
    "matched_skills": ["C#", "SQL"],
    "missing_skills": ["Kubernetes"],
    "resume_keywords_to_include": [".NET", "API"],
    "suggested_resume_angle": "Emphasize verified backend and API work.",
    "cover_letter_angle": "Connect early-career experience to the role.",
    "questions_to_answer_before_applying": ["Confirm the expected experience level."],
}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


class OllamaHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/api/tags":
            self.send_error(404)
            return
        self.send_json({"models": [{"name": MODEL}]})

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_json(
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(FIT_ANALYSIS),
                }
            }
        )

    def send_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class FakeOllama:
    def __init__(self) -> None:
        self.port = free_port()
        self.server: ReusableThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.server = ReusableThreadingHTTPServer(
            ("127.0.0.1", self.port),
            OllamaHandler,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)
        self.server = None
        self.thread = None


@unittest.skipUnless(importlib.util.find_spec("uvicorn"), "uvicorn is not installed")
class ServiceAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(prefix="ai-job-service-acceptance-")
        cls.data_dir = Path(cls.temporary.name) / "data"
        cls.service_port = free_port()
        cls.base_url = f"http://127.0.0.1:{cls.service_port}"
        cls.ollama = FakeOllama()
        cls.ollama.start()

        environment = os.environ.copy()
        environment.update(
            {
                "APP_HOST": "127.0.0.1",
                "APP_PORT": str(cls.service_port),
                "APP_DATA_DIR": str(cls.data_dir),
                "OLLAMA_BASE_URL": cls.ollama.base_url,
                "OLLAMA_MODEL": MODEL,
                "APP_API_KEY": API_KEY,
                "ENABLE_REMOTE_MODE": "false",
                "PYTHONPATH": os.pathsep.join((str(SERVICE_ROOT), str(REPO_ROOT))),
            }
        )
        cls.log_path = Path(cls.temporary.name) / "service.log"
        cls.log_file = cls.log_path.open("w+", encoding="utf-8")
        cls.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(cls.service_port),
            ],
            cwd=REPO_ROOT,
            env=environment,
            stdout=cls.log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        cls.wait_for_service()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.ollama.stop()
        if cls.process.poll() is None:
            cls.process.terminate()
            try:
                cls.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)
        cls.log_file.close()
        cls.temporary.cleanup()

    @classmethod
    def wait_for_service(cls) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if cls.process.poll() is not None:
                break
            try:
                status, payload = cls.request("GET", "/health")
                if status == 200 and payload == {"status": "ok"}:
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        cls.log_file.flush()
        logs = cls.log_path.read_text(encoding="utf-8", errors="replace")
        raise RuntimeError(f"service did not start successfully:\n{logs}")

    @classmethod
    def request(
        cls,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        api_key: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if api_key is not None:
            headers["X-API-Key"] = api_key
        request = urllib.request.Request(
            f"{cls.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    @classmethod
    def capture_job(cls, company: str = "Acceptance Software") -> dict[str, Any]:
        status, response = cls.request(
            "POST",
            "/jobs/capture",
            api_key=API_KEY,
            payload={
                "source": "manual",
                "source_url": "https://example.test/jobs/backend-developer",
                "title": "Junior Backend Developer",
                "company": company,
                "location": "Remote",
                "description_text": "Build and maintain .NET APIs with SQL.",
                "raw_text": "Build and maintain .NET APIs with SQL.",
                "capture_method": "acceptance_test",
            },
        )
        if status != 201:
            raise AssertionError(f"job capture returned HTTP {status}: {response}")
        return response

    def test_service_starts_and_health_works(self) -> None:
        status, payload = self.request("GET", "/health")

        self.assertEqual(status, 200)
        self.assertEqual(payload, {"status": "ok"})

    def test_ollama_health_handles_reachable_and_unreachable(self) -> None:
        status, payload = self.request("GET", "/health/ollama")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ollama_reachable"])
        self.assertTrue(payload["model_installed"])

        self.ollama.stop()
        try:
            status, payload = self.request("GET", "/health/ollama")
            self.assertEqual(status, 503)
            self.assertFalse(payload["ollama_reachable"])
            self.assertEqual(payload["error_code"], "unavailable")
        finally:
            self.ollama.start()

    def test_capture_rejects_missing_and_bad_api_keys(self) -> None:
        payload = {
            "source": "manual",
            "source_url": "https://example.test/jobs/rejected",
            "title": "Rejected Job",
            "company": "No Save Inc",
            "description_text": "This request must not be saved.",
        }

        missing_status, _ = self.request("POST", "/jobs/capture", payload=payload)
        bad_status, _ = self.request(
            "POST",
            "/jobs/capture",
            payload=payload,
            api_key="wrong-key",
        )

        self.assertEqual(missing_status, 401)
        self.assertEqual(bad_status, 401)
        captured_dir = self.data_dir / "job_intake" / "captured_jobs"
        rejected_files = list(captured_dir.glob("*no-save-inc*.json")) if captured_dir.exists() else []
        self.assertEqual(rejected_files, [])

    def test_capture_list_and_get_job(self) -> None:
        captured = self.capture_job("List and Get Software")
        job_id = captured["id"]

        list_status, listing = self.request("GET", "/jobs")
        get_status, job = self.request("GET", f"/jobs/{job_id}")

        self.assertEqual(list_status, 200)
        self.assertGreaterEqual(listing["count"], 1)
        self.assertIn(job_id, {item["id"] for item in listing["jobs"]})
        self.assertEqual(get_status, 200)
        self.assertEqual(job["id"], job_id)
        saved_path = self.data_dir / captured["path"]
        self.assertTrue(saved_path.is_file())

    def test_process_returns_task_and_completes_with_fake_ollama(self) -> None:
        captured = self.capture_job("Process Software")
        job_id = captured["id"]

        missing_status, _ = self.request("POST", f"/jobs/{job_id}/process")
        bad_status, _ = self.request(
            "POST",
            f"/jobs/{job_id}/process",
            api_key="wrong-key",
        )
        status, response = self.request(
            "POST",
            f"/jobs/{job_id}/process",
            api_key=API_KEY,
        )

        self.assertEqual(missing_status, 401)
        self.assertEqual(bad_status, 401)
        self.assertEqual(status, 202)
        self.assertEqual(response["status"], "queued")
        task_id = response["task_id"]
        deadline = time.monotonic() + 10
        task: dict[str, Any] = {}
        while time.monotonic() < deadline:
            task_status, task = self.request("GET", f"/tasks/{task_id}")
            self.assertEqual(task_status, 200)
            if task["state"] in {"succeeded", "failed"}:
                break
            time.sleep(0.1)

        self.assertEqual(task.get("state"), "succeeded", task)
        self.assertTrue(task.get("application_id"))
        application_dir = self.data_dir / "applications" / str(task["application_id"])
        self.assertTrue((application_dir / "fit-analysis.json").is_file())
        self.assertTrue((application_dir / "application-checklist.md").is_file())


if __name__ == "__main__":
    unittest.main()
