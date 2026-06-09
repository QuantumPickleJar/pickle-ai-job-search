import tempfile
import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "service"))

from app.config import Settings
from app.services.evidence_tools import ALLOWED_PROFILE_EVIDENCE_FILES
from app.services.evidence_tools import build_evidence_queries
from app.services.evidence_tools import is_dirty_cover_letter_text
from app.services.evidence_tools import is_applicant_facing_evidence
from app.services.evidence_tools import list_profile_evidence_sources
from app.services.evidence_tools import search_profile_evidence


class CoverLetterEvidenceToolTests(unittest.TestCase):
    def make_settings(self, root: Path) -> Settings:
        return Settings(
            app_host="127.0.0.1",
            app_port=3927,
            app_data_dir=root,
            ollama_base_url="http://localhost:11434",
            ollama_model="qwen2.5:7b",
            app_api_key="",
            enable_remote_mode=False,
        )

    def test_list_sources_only_allowlisted_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            profile = root / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / ".env").write_text("SECRET=abc", encoding="utf-8")
            (profile / "internal.py").write_text("print('secret')", encoding="utf-8")
            (profile / "resume_facts.md").write_text("# Facts\n- C#", encoding="utf-8")

            sources = list_profile_evidence_sources(self.make_settings(root))
            names = [item["source_file"] for item in sources]
            self.assertEqual(tuple(names), ALLOWED_PROFILE_EVIDENCE_FILES)
            self.assertNotIn(".env", names)
            self.assertNotIn("internal.py", names)

    def test_search_rejects_path_traversal_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            (root / "profile").mkdir(parents=True, exist_ok=True)
            with self.assertRaises(ValueError):
                search_profile_evidence(self.make_settings(root), "../secret", ["C#"])

    def test_cover_letter_evidence_is_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            profile = root / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "cover_letter_evidence.md").write_text(
                "- Contributed feature work and unit-tested changes in BizLink workflow.",
                encoding="utf-8",
            )
            (profile / "resume_facts.md").write_text(
                "- Worked in enterprise application environments.",
                encoding="utf-8",
            )
            (profile / "disallowed_claims.md").write_text(
                "- Do not claim architecture ownership.",
                encoding="utf-8",
            )

            cards = search_profile_evidence(
                self.make_settings(root),
                "BizLink unit-tested workflow evidence",
                ["Enterprise insurance workflow"],
                max_results=4,
            )
            self.assertGreaterEqual(len(cards), 1)
            self.assertEqual(cards[0]["source_file"], "cover_letter_evidence.md")
            self.assertIn("source_file", cards[0])
            self.assertLessEqual(len(cards[0]["text"]), 300)

    def test_scaffold_snippets_are_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            profile = root / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "resume_facts.md").write_text(
                "- C#, .NET, ASP.NET, or .NET Core work where supported by actual projects.\n"
                "- SQL or relational database work where supported by actual projects.",
                encoding="utf-8",
            )
            (profile / "cover_letter_evidence.md").write_text(
                "- Contributed feature work and unit-tested business-rule changes in BizLink.",
                encoding="utf-8",
            )

            cards = search_profile_evidence(
                self.make_settings(root),
                "BizLink feature unit-tested evidence",
                ["Enterprise workflow"],
                max_results=5,
            )
            self.assertGreaterEqual(len(cards), 1)
            combined = " ".join(card["text"].lower() for card in cards)
            self.assertNotIn("where supported by actual projects", combined)
            self.assertTrue(is_applicant_facing_evidence("Contributed feature work in BizLink."))
            self.assertFalse(
                is_applicant_facing_evidence("C#, .NET Core, and SQL where supported by actual projects")
            )

    def test_cover_letter_evidence_preferred_over_scaffold_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            profile = root / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "cover_letter_evidence.md").write_text(
                "- Improved workflow behavior by adjusting validation flow in BizLink.",
                encoding="utf-8",
            )
            (profile / "resume_facts.md").write_text(
                "- Safe themes to verify and expand: C#, .NET Core, SQL.",
                encoding="utf-8",
            )

            cards = search_profile_evidence(
                self.make_settings(root),
                "workflow validation BizLink C#",
                ["Workflow improvements"],
                max_results=4,
            )
            self.assertGreaterEqual(len(cards), 1)
            self.assertEqual(cards[0]["source_file"], "cover_letter_evidence.md")
            self.assertNotIn("safe themes to verify", cards[0]["text"].lower())

    def test_search_does_not_return_add_verified_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            profile = root / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "project_inventory.md").write_text(
                "Add verified academic, internship, professional, and personal projects here.",
                encoding="utf-8",
            )
            (profile / "cover_letter_evidence.md").write_text(
                "- Contributed feature work and unit-tested business-rule changes in BizLink.",
                encoding="utf-8",
            )

            cards = search_profile_evidence(
                self.make_settings(root),
                "verified projects BizLink",
                ["Enterprise workflow"],
                max_results=5,
            )
            joined = " ".join(card["text"].lower() for card in cards)
            self.assertNotIn("add verified academic", joined)

    def test_search_rejects_todo_scaffold_snippets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            profile = root / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "project_inventory.md").write_text(
                "- Relevant coursework or projects: TODO",
                encoding="utf-8",
            )
            (profile / "cover_letter_evidence.md").write_text(
                "- Contributed feature work and unit-tested business-rule changes in BizLink.",
                encoding="utf-8",
            )

            cards = search_profile_evidence(
                self.make_settings(root),
                "relevant coursework projects BizLink",
                ["Enterprise workflow"],
                max_results=5,
            )
            joined = " ".join(card["text"].lower() for card in cards)
            self.assertNotIn("relevant coursework or projects: todo", joined)
            self.assertTrue(is_dirty_cover_letter_text("Relevant coursework or projects: TODO"))

    def test_search_rejects_tags_label_snippets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            profile = root / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "resume_facts.md").write_text(
                "- Tags: C#, .NET, ASP.NET, Entity-Framework",
                encoding="utf-8",
            )
            (profile / "cover_letter_evidence.md").write_text(
                "- Contributed feature work and unit-tested business-rule changes in BizLink.",
                encoding="utf-8",
            )

            cards = search_profile_evidence(
                self.make_settings(root),
                "C# .NET BizLink evidence",
                ["Enterprise workflow"],
                max_results=5,
            )
            joined = " ".join(card["text"].lower() for card in cards)
            self.assertNotIn("tags:", joined)
            self.assertTrue(is_dirty_cover_letter_text("Tags: C#, .NET, ASP.NET, Entity-Framework"))

    def test_build_evidence_queries_supports_budget_up_to_10(self) -> None:
        queries = build_evidence_queries(
            {"title": "Application Services Software Engineer", "company": "Forterra"},
            {"matched_skills": ["C#", ".NET", "SQL", "APIs"]},
        )
        self.assertGreaterEqual(len(queries), 5)
        self.assertLessEqual(len(queries), 10)

    def test_search_does_not_return_project_template_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            profile = root / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "project_inventory.md").write_text(
                "## Project Template\n"
                "### Project Name\n"
                "- Context:\n"
                "- Purpose:\n"
                "- Role:\n"
                "- Technologies:\n"
                "- What was built:\n"
                "- Outcome:\n",
                encoding="utf-8",
            )
            (profile / "cover_letter_evidence.md").write_text(
                "- Improved workflow behavior by adjusting validation flow in BizLink.",
                encoding="utf-8",
            )

            cards = search_profile_evidence(
                self.make_settings(root),
                "workflow BizLink evidence",
                ["Workflow improvements"],
                max_results=5,
            )
            joined = " ".join(card["text"].lower() for card in cards)
            self.assertNotIn("context:", joined)
            self.assertNotIn("purpose:", joined)
            self.assertNotIn("role:", joined)
            self.assertNotIn("project name", joined)

    def test_disallowed_claims_not_positive_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            profile = root / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "disallowed_claims.md").write_text(
                "- Do not claim Azure ownership.",
                encoding="utf-8",
            )

            cards = search_profile_evidence(
                self.make_settings(root),
                "Azure ownership evidence",
                ["Cloud"],
                max_results=3,
            )
            self.assertEqual(cards, [])

    def test_claim_boundary_sanitizes_disallowed_claim_headings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            profile = root / "profile"
            profile.mkdir(parents=True, exist_ok=True)
            (profile / "cover_letter_evidence.md").write_text(
                "- Used Docker in hands-on development workflows for local application work.",
                encoding="utf-8",
            )
            (profile / "disallowed_claims.md").write_text(
                "# Disallowed Claims\n\n"
                "## Explicitly Disallowed Unless Verified\n\n"
                "- Docker may be described only at the level supported by actual use.\n",
                encoding="utf-8",
            )

            cards = search_profile_evidence(
                self.make_settings(root),
                "Docker application workflow evidence",
                ["Docker"],
                max_results=3,
            )

            self.assertGreaterEqual(len(cards), 1)
            claim_boundary = cards[0].get("claim_boundary") or ""
            self.assertIn("Keep Docker references conservative", claim_boundary)
            self.assertNotIn("## Explicitly Disallowed Unless Verified", claim_boundary)
            self.assertNotIn("supported by actual use", claim_boundary)


if __name__ == "__main__":
    unittest.main()
