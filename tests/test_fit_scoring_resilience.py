import json
import tempfile
import unittest
from pathlib import Path

from ai_job_search.fit_scoring import (
    build_fit_analysis_repair_prompt,
    empty_fit_analysis,
    extract_fit_analysis_candidate,
    load_fit_profile_context,
    normalize_fit_analysis_shape,
    parse_analysis,
    score_job_file,
    validate_fit_analysis,
)
from ai_job_search.model_provider import ModelRequest, ModelResponse


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise RuntimeError("no fake responses remaining")
        return ModelResponse(text=self._responses.pop(0))


class FitScoringResilienceTests(unittest.TestCase):
    def _valid_analysis(self) -> dict:
        data = empty_fit_analysis()
        data.update(
            {
                "overall_score": 72,
                "recommendation": "maybe",
                "reasons_to_apply": ["Direct .NET overlap"],
                "risks": ["Azure depth unclear"],
                "matched_skills": ["C#", ".NET"],
                "missing_skills": ["Azure"],
                "resume_keywords_to_include": ["ASP.NET Core"],
                "suggested_resume_angle": "Emphasize backend delivery outcomes.",
                "cover_letter_angle": "Conservative enterprise-services angle.",
                "questions_to_answer_before_applying": ["Is Azure mandatory day one?"],
            }
        )
        return data

    def _valid_job(self) -> dict:
        return {
            "id": "job-1",
            "source": "manual",
            "source_url": "https://example.com/job-1",
            "captured_at": "2026-06-10T10:00:00Z",
            "title": ".NET Developer",
            "company": "Infosys",
            "location": "Copenhagen",
            "remote_status": "hybrid",
            "employment_type": "full-time",
            "seniority": "mid",
            "description_text": "Build .NET services with SQL and APIs.",
            "requirements": ["C#", ".NET", "SQL"],
            "preferred_qualifications": ["Azure"],
            "technologies": ["C#", ".NET", "SQL"],
            "responsibilities": ["Develop backend services"],
            "compensation": {"min": None, "max": None, "currency": "DKK", "raw": "Not listed"},
            "application_status": "captured",
        }

    def _write_minimal_repo(self, root: Path) -> None:
        profile_dir = root / "profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "base_profile.md").write_text("Candidate profile summary", encoding="utf-8")
        (profile_dir / "resume_facts.md").write_text("Built enterprise .NET applications", encoding="utf-8")
        (profile_dir / "skills_inventory.md").write_text("C#, .NET, SQL", encoding="utf-8")
        (profile_dir / "disallowed_claims.md").write_text("Do not invent facts", encoding="utf-8")

    def test_exact_valid_schema_passes(self) -> None:
        analysis = self._valid_analysis()
        self.assertEqual(validate_fit_analysis(analysis), [])

    def test_nested_fit_analysis_wrapper_unwraps(self) -> None:
        wrapped = {"fit_analysis": self._valid_analysis()}
        candidate = extract_fit_analysis_candidate(wrapped)
        self.assertIsInstance(candidate, dict)
        assert candidate is not None
        self.assertEqual(candidate["overall_score"], 72)

    def test_nested_analysis_wrapper_unwraps(self) -> None:
        wrapped = {"analysis": self._valid_analysis()}
        candidate = extract_fit_analysis_candidate(wrapped)
        self.assertIsInstance(candidate, dict)
        assert candidate is not None
        self.assertEqual(candidate["recommendation"], "maybe")

    def test_single_key_wrapper_unwraps(self) -> None:
        wrapped = {"payload": self._valid_analysis()}
        candidate = extract_fit_analysis_candidate(wrapped)
        self.assertIsInstance(candidate, dict)
        assert candidate is not None
        self.assertIn("matched_skills", candidate)

    def test_aliases_and_type_coercion_normalize(self) -> None:
        normalized = normalize_fit_analysis_shape(
            {
                "score": "72",
                "decision": "maybe",
                "strengths": "C#, .NET",
                "concerns": "Azure",
                "skills_matched": "C#",
                "gaps": "Azure",
                "keywords": "ASP.NET Core,SQL",
                "resume_angle": "Focus on backend",
                "letter_angle": "Conservative",
                "questions": "Is Azure mandatory?",
            }
        )
        self.assertEqual(normalized["overall_score"], 72)
        self.assertEqual(normalized["reasons_to_apply"], ["C#", ".NET"])
        self.assertEqual(normalized["questions_to_answer_before_applying"], ["Is Azure mandatory?"])

    def test_invalid_recommendation_fails_unless_obvious(self) -> None:
        obvious = normalize_fit_analysis_shape({**self._valid_analysis(), "recommendation": "strong apply"})
        self.assertEqual(obvious["recommendation"], "apply")
        self.assertEqual(validate_fit_analysis(obvious), [])

        invalid = normalize_fit_analysis_shape({**self._valid_analysis(), "recommendation": "strongly recommended"})
        self.assertIn("invalid enum: recommendation must be one of: apply, maybe, skip", validate_fit_analysis(invalid))

    def test_raw_prose_or_malformed_json_fails_parse(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            parse_analysis("This is not JSON")

    def test_repair_flow_writes_repaired_fit_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            self._write_minimal_repo(repo_root)
            job_path = repo_root / "job.json"
            output_dir = repo_root / "applications" / "infosys-net-developer"
            job_path.write_text(json.dumps(self._valid_job()), encoding="utf-8")

            repaired = self._valid_analysis()
            provider = FakeProvider([
                "Model output with prose not json",
                json.dumps(repaired),
            ])

            output_path = score_job_file(job_path, provider=provider, repo_root=repo_root, output_dir=output_dir)
            self.assertTrue(output_path.exists())

            fit_data = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(fit_data["overall_score"], 72)

            metadata = json.loads((output_dir / "fit-analysis.meta.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["source"], "model-repaired")
            self.assertTrue(metadata["repair_attempted"])
            self.assertTrue(metadata["repair_successful"])
            self.assertEqual(metadata["model_query_count_actual"], 2)

    def test_fallback_flow_writes_deterministic_fit_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            self._write_minimal_repo(repo_root)
            job_path = repo_root / "job.json"
            output_dir = repo_root / "applications" / "infosys-net-developer"
            job_path.write_text(json.dumps(self._valid_job()), encoding="utf-8")

            provider = FakeProvider([
                "not json",
                "still not json",
            ])

            output_path = score_job_file(job_path, provider=provider, repo_root=repo_root, output_dir=output_dir)
            self.assertTrue(output_path.exists())

            fit_data = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(fit_data["recommendation"], "maybe")
            self.assertEqual(fit_data["overall_score"], 65)

            metadata = json.loads((output_dir / "fit-analysis.meta.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["source"], "deterministic-fallback")
            self.assertIsNotNone(metadata["fallback_reason"])

    def test_compact_fit_profile_context_excludes_scaffold_heavy_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            profile_dir = repo_root / "profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "base_profile.md").write_text("Core profile", encoding="utf-8")
            (profile_dir / "resume_facts.md").write_text("Verified facts", encoding="utf-8")
            (profile_dir / "skills_inventory.md").write_text("C#, .NET", encoding="utf-8")
            (profile_dir / "generation-constraints.md").write_text("TODO TODO TODO scaffold", encoding="utf-8")

            context = load_fit_profile_context(repo_root, max_chars=12000)
            self.assertIn("Core profile", context)
            self.assertNotIn("TODO TODO TODO scaffold", context)

    def test_compact_fit_profile_context_respects_max_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp)
            profile_dir = repo_root / "profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            (profile_dir / "base_profile.md").write_text("A" * 5000, encoding="utf-8")

            context = load_fit_profile_context(repo_root, max_chars=200)
            self.assertLessEqual(len(context), 200)

    def test_repair_prompt_contains_required_constraints(self) -> None:
        prompt = build_fit_analysis_repair_prompt("bad", ["missing field: overall_score"])
        self.assertIn("Return JSON only.", prompt)
        self.assertIn("recommendation must be one of: apply, maybe, skip", prompt)


if __name__ == "__main__":
    unittest.main()
