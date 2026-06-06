"""Focused tests for UI escaping and form authentication."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.auth import api_key_is_valid
from app.config import Settings
from app.ui.views import page, render_file, source_link


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

        self.assertIn('<details class="file-view">', rendered)
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


if __name__ == "__main__":
    unittest.main()
