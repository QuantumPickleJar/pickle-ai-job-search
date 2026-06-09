import tempfile
import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "service"))

from app.config import Settings
from app.services.evidence_tools import ALLOWED_PROFILE_EVIDENCE_FILES
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


if __name__ == "__main__":
    unittest.main()
