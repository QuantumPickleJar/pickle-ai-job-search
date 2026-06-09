import tempfile
import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "service"))

from app.config import Settings
from app.services.processing import (
    ProcessingError,
    build_cover_letter_prompt,
    build_profile_context,
    validate_generated_cover_letter,
)
from app.ui.views import task_table


class ServiceGenerationRegressionTests(unittest.TestCase):
    def test_generation_task_rows_link_to_generated_file(self) -> None:
        html = task_table(
            [
                {
                    "job_id": "onestream-software-engineer",
                    "application_id": "onestream-software-engineer",
                    "task_type": "generate-cover-letter",
                    "state": "succeeded",
                    "updated_at": "2026-06-07T12:00:00+00:00",
                }
            ]
        )

        self.assertIn(
            '/ui/generated/onestream-software-engineer/cover-letter.md',
            html,
        )
        self.assertIn('Open generated file', html)

    def test_cover_letter_prompt_includes_profile_context_and_constraints(self) -> None:
        prompt = build_cover_letter_prompt(
            {"title": "Software Engineer", "company": "OneStream"},
            {
                "cover_letter_angle": "Highlight enterprise application contribution.",
                "missing_skills": ["Power Platform"],
            },
            "Notes section",
            "Profile says BizLink is enterprise application experience.",
            "Supporting docs",
        )

        self.assertIn("Profile says BizLink", prompt)
        self.assertIn("Do not say the candidate lacks enterprise application experience.", prompt)
        self.assertIn("Do not describe the candidate as architecture owner", prompt)
        self.assertIn("Vincent Morrill", prompt)
        self.assertNotIn("[Candidate Name]", prompt)
        self.assertNotIn('"missing_skills"', prompt)
        self.assertIn("Internal-only missing skill risks", prompt)

    def test_cover_letter_validator_rejects_forterra_bad_sentence(self) -> None:
        with self.assertRaises(ProcessingError):
            validate_generated_cover_letter(
                "I am actively deepening my experience with enterprise tooling and I do not meet Minimum 2-3 years."
            )

    def test_cover_letter_validator_rejects_candidate_placeholder(self) -> None:
        with self.assertRaises(ProcessingError):
            validate_generated_cover_letter("Best regards,\n[Candidate Name]")

    def test_build_profile_context_includes_enterprise_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            profile = root / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "resume_facts.md").write_text("BizLink enterprise context", encoding="utf-8")
            (profile / "project_inventory.md").write_text("Dynamic Plan Benefits Designer", encoding="utf-8")
            (profile / "experience_bullets.md").write_text("UWO Portal contribution", encoding="utf-8")
            (profile / "skills_inventory.md").write_text("C#\n.NET", encoding="utf-8")
            (profile / "disallowed_claims.md").write_text("Do not claim ownership", encoding="utf-8")

            settings = Settings(
                app_host="127.0.0.1",
                app_port=3927,
                app_data_dir=root,
                ollama_base_url="http://localhost:11434",
                ollama_model="qwen2.5:14b",
                app_api_key="test",
                enable_remote_mode=False,
            )

            context = build_profile_context(settings)
            self.assertIn("profile/resume_facts.md", context)
            self.assertIn("profile/project_inventory.md", context)
            self.assertIn("profile/experience_bullets.md", context)
            self.assertIn("profile/skills_inventory.md", context)
            self.assertIn("profile/disallowed_claims.md", context)


if __name__ == "__main__":
    unittest.main()