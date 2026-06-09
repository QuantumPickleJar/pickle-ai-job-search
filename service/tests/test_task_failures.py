"""Tests for task failure sanitization and task API error visibility."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.routes.tasks import get_task, list_tasks
from app.services.processing import ProcessingError
from app.services.task_errors import safe_task_error
from app.services.task_store import TaskStore


class TaskFailureTests(unittest.TestCase):
    def test_task_api_returns_error_for_failed_task(self) -> None:
        with TemporaryDirectory(prefix="task-api-") as temporary_dir:
            store = TaskStore(Path(temporary_dir))
            task = store.create("job-abc", task_type="generate-cover-letter", application_id="app-abc")
            task_id = str(task["task_id"])
            store.transition(task_id, "running")
            store.transition(task_id, "failed", error="cover letter generation failed: model missing")

            listed = list_tasks(store)
            single = get_task(task_id, store)

        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["tasks"][0]["error"], "cover letter generation failed: model missing")
        self.assertEqual(single["error"], "cover letter generation failed: model missing")

    def test_safe_task_error_masks_obvious_secrets(self) -> None:
        message = "Ollama request failed api_key=abc123 token=xyz authorization=Bearer secret-token"
        sanitized = safe_task_error(ProcessingError(message), "generate-cover-letter")

        self.assertNotIn("abc123", sanitized)
        self.assertNotIn("xyz", sanitized)
        self.assertNotIn("secret-token", sanitized)
        self.assertIn("<redacted>", sanitized)

    def test_safe_task_error_classifies_cuda_oom_and_keeps_details(self) -> None:
        message = (
            "cover letter generation failed: Ollama chat request failed: HTTP 500 CUDA out-of-memory. "
            "Model='qwen2.5:7b'. Detail: "
            "llama-server process has terminated: exit status 1: cudaMalloc failed: out of memory "
            "failed to allocate CUDA0 buffer of size 4370558976"
        )
        sanitized = safe_task_error(ProcessingError(message), "generate-cover-letter")

        self.assertIn("Ollama CUDA out-of-memory", sanitized)
        self.assertIn("qwen2.5:7b", sanitized)
        self.assertIn("failed to allocate CUDA0 buffer of size 4370558976", sanitized)

    def test_safe_task_error_scrubs_ollama_blob_paths(self) -> None:
        message = r"error loading model from F:\AI\models\blobs\sha256-abcdef123456"
        sanitized = safe_task_error(ProcessingError(message), "generate-cover-letter")

        self.assertNotIn(r"F:\AI\models\blobs\sha256-abcdef123456", sanitized)
        self.assertIn("<ollama-blob-path>", sanitized)


if __name__ == "__main__":
    unittest.main()