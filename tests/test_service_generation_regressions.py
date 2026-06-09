import tempfile
import unittest
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "service"))

from ai_job_search.model_provider import ModelResponse

from app.config import Settings
from app.services.processing import (
    ProcessingError,
    build_cover_letter_brief,
    build_fallback_cover_letter,
    build_cover_letter_prompt,
    generate_cover_letter,
    generate_cover_letter_with_review,
    is_internal_only_requirement,
    sanitize_cover_letter_reason,
    build_profile_context,
    sanitize_generated_cover_letter,
    validate_generated_cover_letter,
)
from app.ui.views import task_table
import app.services.processing as processing_module


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def complete(self, request: Any) -> ModelResponse:
        self.calls.append(
            {
                "system_prompt": request.system_prompt,
                "user_prompt": request.user_prompt,
                "temperature": request.temperature,
                "max_tokens": request.max_tokens,
            }
        )
        text = self.responses[len(self.calls) - 1] if len(self.calls) - 1 < len(self.responses) else ""
        return ModelResponse(text=text)


class FakeFailingProvider:
    def complete(self, request: Any) -> ModelResponse:
        from ai_job_search.model_provider import ModelProviderError

        raise ModelProviderError("request failed with X-API-Key=super-secret-token")


class FakeOllamaProvider:
    responses: list[str] = []
    index: int = 0

    def __init__(self, model: str, base_url: str, timeout_seconds: int) -> None:
        self.model = model
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    @classmethod
    def reset(cls, responses: list[str]) -> None:
        cls.responses = responses
        cls.index = 0

    def complete(self, request: Any) -> ModelResponse:
        if FakeOllamaProvider.index >= len(FakeOllamaProvider.responses):
            return ModelResponse(text="")
        text = FakeOllamaProvider.responses[FakeOllamaProvider.index]
        FakeOllamaProvider.index += 1
        return ModelResponse(text=text)


def polished_letter() -> str:
    return (
        "Dear Hiring Manager,\n\n"
        "I am excited to apply for the Application Services Software Engineer position at Forterra. "
        "My background in C#, .NET, SQL, and enterprise application development aligns with the role's focus "
        "on supporting business-critical internal platforms and dependable software delivery across changing business priorities.\n\n"
        "In recent work, I have contributed to internal and enterprise-grade systems by implementing features, "
        "writing unit tests, improving workflow behavior, and collaborating through pull request based delivery. "
        "My experience includes internal university IT applications, benefits and insurance software, and insurance "
        "quoting workflows that require practical communication and attention to operational reliability under real deadlines.\n\n"
        "I am particularly interested in this opportunity because it combines engineering execution with stakeholder "
        "support and operational problem solving. I would bring a grounded and maintainable development approach, "
        "clear communication with technical and non technical partners, and a strong commitment to steady improvement "
        "in a collaborative team environment where shipping quality software consistently matters.\n\n"
        "Thank you for your time and consideration. I would welcome the opportunity to discuss how my experience can "
        "support your team and contribute to dependable application services.\n\n"
        "Best regards,\n\n"
        "Vincent Morrill"
    )


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

    def test_cover_letter_prompt_uses_brief_only(self) -> None:
        brief = {
            "role_title": "Software Engineer",
            "company": "OneStream",
            "matched_skills": ["C#", ".NET"],
            "evidence_cards": [{"theme": "Enterprise", "text": "BizLink evidence", "source_file": "cover_letter_evidence.md", "claim_boundary": "Do not overclaim."}],
        }
        prompt = build_cover_letter_prompt(brief)

        self.assertIn("Letter Brief JSON", prompt)
        self.assertIn("BizLink evidence", prompt)
        self.assertIn("Vincent Morrill", prompt)
        self.assertNotIn("[Candidate Name]", prompt)
        self.assertNotIn('"missing_skills"', prompt)
        self.assertNotIn("Minimum 2-3 years", prompt)
        self.assertNotIn("Internal-only missing skill risks", prompt)
        self.assertIn("Do not mention source filenames", prompt)
        self.assertNotIn("profile/resume_facts.md", prompt)

    def test_cover_letter_brief_adds_evidence_bullets(self) -> None:
        brief = build_cover_letter_brief(
            job={"title": "Application Engineer", "company": "Forterra"},
            fit={"matched_skills": ["C#", ".NET", "SQL"]},
            profile_context="BizLink UWO portal Applied Benefits C# .NET SQL",
            documents_context="doc inventory",
            identity={"name": "Vincent Morrill", "email": "vince.codefactory@outlook.com"},
        )
        evidence_cards = brief.get("evidence_cards") or []
        evidence = [str(card.get("text") or "") for card in evidence_cards if isinstance(card, dict)]
        self.assertGreaterEqual(len(evidence), 3)
        self.assertIn("BizLink", " ".join(evidence))

    def test_cover_letter_brief_excludes_internal_fit_fields(self) -> None:
        brief = build_cover_letter_brief(
            job={"title": "Application Engineer", "company": "Forterra", "location": "Remote"},
            fit={
                "matched_skills": ["C#", ".NET", "working knowledge of Azure"],
                "resume_keywords_to_include": ["unit testing", "Minimum 2-3 years"],
                "reasons_to_apply": ["enterprise apps", "must have Microsoft 365"],
                "missing_skills": ["Power Platform"],
                "risks": ["Minimum 2-3 years"],
                "questions_to_answer_before_applying": ["Do I meet years required?"],
                "cover_letter_angle": "Grounded engineering tone",
            },
            profile_context="verified profile facts",
            documents_context="doc inventory",
            identity={"name": "Vincent Morrill", "email": "vince.codefactory@outlook.com"},
        )

        brief_text = json_dumps(brief)
        self.assertNotIn("missing_skills", brief_text)
        self.assertNotIn('"risks"', brief_text)
        self.assertNotIn("Minimum 2-3 years", brief_text)
        self.assertNotIn("Power Platform", brief_text)
        self.assertEqual(brief.get("location"), "")

    def test_internal_requirement_detector(self) -> None:
        self.assertTrue(is_internal_only_requirement("Minimum 2-3 years of software engineering experience"))
        self.assertTrue(is_internal_only_requirement("Must have Azure and Power Platform"))
        self.assertFalse(is_internal_only_requirement("Unit-tested feature work in C# services"))

    def test_cover_letter_validator_rejects_forterra_bad_sentence(self) -> None:
        with self.assertRaises(ProcessingError):
            validate_generated_cover_letter(
                "I am actively deepening my experience with enterprise tooling and I do not meet Minimum 2-3 years."
            )

    def test_cover_letter_validator_rejects_in_remote_phrase(self) -> None:
        with self.assertRaises(ProcessingError):
            validate_generated_cover_letter(
                "Dear Hiring Manager,\n\nI am excited to apply in Remote for this role.\n\nBest regards,\n\nVincent Morrill"
            )

    def test_cover_letter_validator_rejects_generic_filler(self) -> None:
        with self.assertRaises(ProcessingError):
            validate_generated_cover_letter(
                "Dear Hiring Manager,\n\nI build robust solutions that seamlessly integrate systems as the ideal candidate.\n\nBest regards,\n\nVincent Morrill"
            )

    def test_sanitizer_replaces_your_name_placeholder(self) -> None:
        result = sanitize_generated_cover_letter(
            "Dear Hiring Manager,\n\nSincerely,\n[Your Name]"
        )
        self.assertIn("Vincent Morrill", result)
        self.assertNotIn("[Your Name]", result)

    def test_sanitizer_replaces_candidate_name_placeholder(self) -> None:
        result = sanitize_generated_cover_letter(
            "Best regards,\n[Candidate Name]"
        )
        self.assertIn("Vincent Morrill", result)
        self.assertNotIn("[Candidate Name]", result)

    def test_sanitizer_replaces_lowercase_variants(self) -> None:
        result = sanitize_generated_cover_letter(
            "[your name]\n[candidate name]\n[email]\n[your email]"
        )
        self.assertIn("Vincent Morrill", result)
        self.assertIn("vince.codefactory@outlook.com", result)
        self.assertNotIn("[", result)

    def test_sanitizer_strips_code_fences(self) -> None:
        result = sanitize_generated_cover_letter(
            "```markdown\nDear Hiring Manager,\n\nText.\n```"
        )
        self.assertNotIn("```", result)
        self.assertIn("Dear Hiring Manager", result)

    def test_validator_still_rejects_i_lack(self) -> None:
        with self.assertRaises(ProcessingError):
            validate_generated_cover_letter(
                "I lack the required senior architecture experience."
            )

    def test_validator_still_rejects_minimum_years(self) -> None:
        with self.assertRaises(ProcessingError):
            validate_generated_cover_letter(
                "The role requires minimum 2-3 years of .NET experience."
            )

    def test_validator_rejects_certification_language(self) -> None:
        with self.assertRaises(ProcessingError):
            validate_generated_cover_letter(
                "Dear Hiring Manager,\n\nI meet all certification requirements listed in the posting.\n\nBest regards,\n\nVincent Morrill"
            )

    def test_sanitized_content_passes_validator_when_clean(self) -> None:
        content = polished_letter()
        sanitized = sanitize_generated_cover_letter(content)
        validate_generated_cover_letter(sanitized)
        self.assertIn("Vincent Morrill", sanitized)

    def test_fallback_cover_letter_is_safe_and_polished(self) -> None:
        brief = {
            "role_title": "Application Services Software Engineer",
            "company": "Forterra",
            "matched_skills": ["C#", ".NET Core", "SQL", "enterprise application development"],
            "evidence_bullets": [
                "Contributed feature work and unit-tested business-rule changes in BizLink, an enterprise-grade insurance quoting workflow.",
            ],
        }
        fallback = build_fallback_cover_letter(brief)
        validate_generated_cover_letter(fallback)
        lowered = fallback.lower()
        self.assertIn("Best regards", fallback)
        self.assertIn("Vincent Morrill", fallback)
        self.assertIn("enterprise", lowered)
        self.assertIn("C#, .NET Core, and SQL", fallback)
        self.assertNotIn("role's focus and scope", lowered)
        self.assertLessEqual(lowered.count("business-critical"), 1)
        self.assertNotIn("transparent communication, measurable outcomes", lowered)
        self.assertNotIn("i am excited", lowered)
        self.assertNotIn("minimum 2-3 years", lowered)
        self.assertNotIn("power platform", lowered)
        self.assertNotIn("microsoft 365", lowered)
        self.assertNotIn("azure", lowered)
        self.assertNotIn("i lack", lowered)

    def test_multi_pass_generation_calls_model_three_times(self) -> None:
        provider = FakeProvider(
            responses=[
                polished_letter(),
                "- tighten the third paragraph",
                polished_letter(),
            ]
        )

        result = generate_cover_letter_with_review(
            job={"title": "Application Services Software Engineer", "company": "Forterra", "location": "Remote"},
            fit={"matched_skills": ["C#", ".NET", "SQL"], "cover_letter_angle": "Grounded engineering tone"},
            profile_context="verified profile facts",
            documents_context="document inventory",
            identity={"name": "Vincent Morrill", "email": "vince.codefactory@outlook.com"},
            settings=None,
            provider=provider,
            review_passes=True,
        )

        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(result.source, "model-final")
        self.assertEqual(result.model_query_count_actual, 3)
        self.assertIsNone(result.fallback_reason)
        self.assertIn("Dear Hiring Manager", result.content)
        self.assertIn("Vincent Morrill", result.content)

    def test_multi_pass_uses_fallback_when_final_is_unsafe(self) -> None:
        provider = FakeProvider(
            responses=[
                '{"queries":["insurance workflow evidence"]}',
                polished_letter(),
                "- remove unsafe phrases",
                "Dear Hiring Manager,\n\nI am the ideal candidate and I have minimum 2-3 years.\n\nBest regards,\n\nVincent Morrill",
            ]
        )

        result = generate_cover_letter_with_review(
            job={"title": "Application Services Software Engineer", "company": "Forterra"},
            fit={"matched_skills": ["C#", ".NET", "SQL"]},
            profile_context="verified profile facts",
            documents_context="document inventory",
            identity={"name": "Vincent Morrill", "email": "vince.codefactory@outlook.com"},
            settings=None,
            provider=provider,
            review_passes=True,
        )

        self.assertEqual(result.source, "deterministic-fallback")
        self.assertEqual(result.model_query_count_actual, 3)
        self.assertIsNotNone(result.fallback_reason)
        self.assertIn("failed validation", (result.fallback_reason or "").lower())
        self.assertIn("I am applying for", result.content)
        self.assertNotIn("ideal candidate", result.content.lower())

    def test_fallback_reason_is_sanitized(self) -> None:
        result = generate_cover_letter_with_review(
            job={"title": "Application Services Software Engineer", "company": "Forterra"},
            fit={"matched_skills": ["C#", ".NET", "SQL"]},
            profile_context="verified profile facts",
            documents_context="document inventory",
            identity={"name": "Vincent Morrill", "email": "vince.codefactory@outlook.com"},
            settings=None,
            provider=FakeFailingProvider(),
            review_passes=True,
        )
        reason = result.fallback_reason or ""
        self.assertEqual(result.source, "deterministic-fallback")
        self.assertIn("[redacted]", reason)
        self.assertNotIn("super-secret-token", reason)

    def test_generate_cover_letter_writes_meta_with_fallback_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            app_id = "forterra-application-services"
            app_dir = root / "applications" / app_id
            app_dir.mkdir(parents=True, exist_ok=True)
            (app_dir / "job.json").write_text(
                json.dumps({"title": "Application Services Software Engineer", "company": "Forterra", "location": "Remote"}),
                encoding="utf-8",
            )
            (app_dir / "fit-analysis.json").write_text(
                json.dumps({"matched_skills": ["C#", ".NET", "SQL"]}),
                encoding="utf-8",
            )

            settings = Settings(
                app_host="127.0.0.1",
                app_port=3927,
                app_data_dir=root,
                ollama_base_url="http://localhost:11434",
                ollama_model="qwen2.5:7b",
                app_api_key="test",
                enable_remote_mode=False,
                cover_letter_review_passes=True,
            )

            original_provider = processing_module.OllamaProvider
            processing_module.OllamaProvider = FakeOllamaProvider
            FakeOllamaProvider.reset([
                '{"queries":["C# .NET SQL evidence"]}',
                polished_letter(),
                "- remove unsafe claims",
                "Dear Hiring Manager,\n\nI am the ideal candidate and I have minimum 2-3 years.\n\nBest regards,\n\nVincent Morrill",
            ])
            try:
                output = generate_cover_letter(app_id, settings)
            finally:
                processing_module.OllamaProvider = original_provider

            self.assertTrue(output.is_file())
            meta_path = app_dir / "cover-letter.meta.json"
            self.assertTrue(meta_path.is_file())
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(meta.get("source"), "deterministic-fallback")
            self.assertTrue(bool(meta.get("fallback_reason")))
            self.assertTrue(meta.get("review_passes_enabled"))
            self.assertTrue(meta.get("tool_access", {}).get("enabled"))
            self.assertIsInstance(meta.get("evidence_cards_used"), list)

    def test_generate_cover_letter_writes_meta_with_model_final_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            app_id = "forterra-application-services"
            app_dir = root / "applications" / app_id
            app_dir.mkdir(parents=True, exist_ok=True)
            (app_dir / "job.json").write_text(
                json.dumps({"title": "Application Services Software Engineer", "company": "Forterra", "location": "Remote"}),
                encoding="utf-8",
            )
            (app_dir / "fit-analysis.json").write_text(
                json.dumps({"matched_skills": ["C#", ".NET", "SQL"]}),
                encoding="utf-8",
            )

            settings = Settings(
                app_host="127.0.0.1",
                app_port=3927,
                app_data_dir=root,
                ollama_base_url="http://localhost:11434",
                ollama_model="qwen2.5:7b",
                app_api_key="test",
                enable_remote_mode=False,
                cover_letter_review_passes=True,
            )

            original_provider = processing_module.OllamaProvider
            processing_module.OllamaProvider = FakeOllamaProvider
            FakeOllamaProvider.reset([
                '{"queries":["enterprise workflow evidence"]}',
                polished_letter(),
                "- keep tone direct",
                polished_letter(),
            ])
            try:
                output = generate_cover_letter(app_id, settings)
            finally:
                processing_module.OllamaProvider = original_provider

            self.assertTrue(output.is_file())
            meta = json.loads((app_dir / "cover-letter.meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta.get("source"), "model-final")
            self.assertIsNone(meta.get("fallback_reason"))
            self.assertIsInstance(meta.get("evidence_cards_used"), list)

    def test_reason_sanitizer_redacts_keys(self) -> None:
        raw = "failed with X-API-Key=secret-token and api_key=abc123"
        cleaned = sanitize_cover_letter_reason(raw)
        self.assertIn("[redacted]", cleaned)
        self.assertNotIn("secret-token", cleaned)
        self.assertNotIn("abc123", cleaned)

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
            (profile / "experience_timeline.md").write_text("Timeline", encoding="utf-8")
            (profile / "job_preferences.md").write_text("Preferences", encoding="utf-8")
            (profile / "generation-constraints.md").write_text("Constraints", encoding="utf-8")

            settings = Settings(
                app_host="127.0.0.1",
                app_port=3927,
                app_data_dir=root,
                ollama_base_url="http://localhost:11434",
                ollama_model="qwen2.5:7b",
                app_api_key="test",
                enable_remote_mode=False,
            )

            context = build_profile_context(settings)
            self.assertIn("profile/resume_facts.md", context)
            self.assertIn("profile/project_inventory.md", context)
            self.assertIn("profile/experience_bullets.md", context)
            self.assertIn("profile/skills_inventory.md", context)
            self.assertIn("profile/disallowed_claims.md", context)
            self.assertIn("profile/experience_timeline.md", context)
            self.assertIn("profile/job_preferences.md", context)
            self.assertIn("profile/generation-constraints.md", context)


def json_dumps(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()