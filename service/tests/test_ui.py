"""Focused tests for UI escaping and form authentication."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.auth import api_key_is_valid
from app.config import Settings
from app.routes.ui import task_detail
from app.services.task_store import TaskStore
from app.ui.views import page, render_file, source_link
from app.ui.views import task_table


def settings(api_key: str = "") -> Settings:
    return Settings(
        app_host="127.0.0.1",
        app_port=3927,
        app_data_dir=Path("data"),
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="test-model",
        app_api_key=api_key,
        enable_remote_mode=False,
    )


class UiSafetyTests(unittest.TestCase):
    def test_page_escapes_title(self) -> None:
        rendered = page(
            title="<script>alert(1)</script>",
            active="dashboard",
            body="<p>trusted layout</p>",
            settings=settings(),
        )

        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)

    def test_application_file_content_is_escaped(self) -> None:
        rendered = render_file("notes.md", "<img src=x onerror=alert(1)>")

        self.assertNotIn("<img", rendered)
        self.assertIn("&lt;img", rendered)

    def test_application_file_can_render_collapsed(self) -> None:
        rendered = render_file("job.json", {"title": "Example"}, expanded=False)

        self.assertIn('<details class="file-view"', rendered)
        self.assertNotIn('<details class="file-view" open>', rendered)

    def test_source_link_rejects_non_http_scheme(self) -> None:
        rendered = source_link("javascript:alert(1)")

        self.assertNotIn("href=", rendered)
        self.assertIn("No valid source URL", rendered)

    def test_api_key_validation_is_optional_and_exact(self) -> None:
        self.assertTrue(api_key_is_valid(None, settings()))
        self.assertTrue(api_key_is_valid("correct", settings("correct")))
        self.assertFalse(api_key_is_valid("wrong", settings("correct")))
        self.assertFalse(api_key_is_valid(None, settings("correct")))

    def test_failed_task_row_shows_error_even_with_workspace_link(self) -> None:
        rendered = task_table(
            [
                {
                    "task_id": "a" * 32,
                    "job_id": "job-123",
                    "application_id": "app-123",
                    "task_type": "generate-cover-letter",
                    "state": "failed",
                    "error": "Ollama CUDA out-of-memory while loading model 'qwen2.5:7b'.",
                    "updated_at": "2026-06-09T10:00:00+00:00",
                }
            ]
        )

        self.assertIn("Ollama CUDA out-of-memory", rendered)
        self.assertIn("/ui/applications/app-123", rendered)
        self.assertIn("/ui/tasks/", rendered)

    def test_task_detail_page_renders_error_text(self) -> None:
        with TemporaryDirectory(prefix="ui-task-detail-") as temporary_dir:
            data_dir = Path(temporary_dir)
            store = TaskStore(data_dir)
            task = store.create(
                "job-123",
                task_type="generate-cover-letter",
                application_id="app-789",
            )
            store.transition(
                str(task["task_id"]),
                "running",
            )
            store.record_model_query(str(task["task_id"]), "draft-pass")
            store.record_model_query(str(task["task_id"]), "critique-pass")
            error_text = "cover letter generation failed: model missing"
            store.transition(
                str(task["task_id"]),
                "failed",
                error=error_text,
            )

            response = task_detail(
                str(task["task_id"]),
                settings=Settings(
                    app_host="127.0.0.1",
                    app_port=3927,
                    app_data_dir=data_dir,
                    ollama_base_url="http://127.0.0.1:11434",
                    ollama_model="test-model",
                    app_api_key="",
                    enable_remote_mode=False,
                ),
            )

        self.assertEqual(response.status_code, 200)
        body = response.body.decode("utf-8")
        self.assertIn(error_text, body)
        self.assertIn("Open workspace", body)
        self.assertIn("Model query progress", body)
        self.assertIn("Observed model queries during this task: <strong>2</strong>", body)


if __name__ == "__main__":
    unittest.main()
