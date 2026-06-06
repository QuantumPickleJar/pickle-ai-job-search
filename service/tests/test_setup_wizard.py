"""Tests for the standard-library master setup wizard."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "setup_wizard.py"
REPO_ROOT = SCRIPT_PATH.parents[1]
COMPOSE_PATH = REPO_ROOT / "docker-compose.service.yml"
SPEC = importlib.util.spec_from_file_location("setup_wizard", SCRIPT_PATH)
assert SPEC and SPEC.loader
setup_wizard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = setup_wizard
SPEC.loader.exec_module(setup_wizard)


EXAMPLE_ENV = """# Example
PUID=1000
PGID=1000
APP_HOST=127.0.0.1
APP_PORT=3927
APP_DATA_DIR=/app/data
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:14b
APP_API_KEY=replace-with-a-strong-random-secret
ENABLE_REMOTE_MODE=false
CUSTOM_VALUE=preserve-me
"""


def args(**overrides: object) -> SimpleNamespace:
    defaults = {
        "ollama_base_url": None,
        "model": None,
        "app_host": None,
        "role": "all-in-one",
        "yes": False,
        "dry_run": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class SetupWizardTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / ".env.example").write_text(EXAMPLE_ENV, encoding="utf-8")
        return temporary, root

    def test_update_env_preserves_unknown_values_and_comments(self) -> None:
        rendered = setup_wizard.update_env_text(
            EXAMPLE_ENV,
            {"APP_HOST": "192.168.1.20", "PUID": "1234"},
        )

        self.assertIn("# Example", rendered)
        self.assertIn("CUSTOM_VALUE=preserve-me", rendered)
        self.assertIn("APP_HOST=192.168.1.20", rendered)
        self.assertIn("PUID=1234", rendered)

    def test_service_host_dry_run_changes_nothing(self) -> None:
        temporary, root = self.make_root()
        self.addCleanup(temporary.cleanup)
        result = setup_wizard.SetupResult("service-host")

        setup_wizard.configure_env(
            root,
            {"APP_HOST": "127.0.0.1"},
            result,
            dry_run=True,
        )
        setup_wizard.create_data_directories(root, result, dry_run=True)
        setup_wizard.write_report(root, result, dry_run=True)

        self.assertFalse((root / ".env").exists())
        self.assertFalse((root / "data").exists())
        self.assertFalse((root / "docs" / "setup-wizard-report.md").exists())

    def test_all_in_one_writes_env_directories_and_masked_report(self) -> None:
        temporary, root = self.make_root()
        self.addCleanup(temporary.cleanup)
        (root / ".env").write_text(
            EXAMPLE_ENV.replace(
                "APP_API_KEY=replace-with-a-strong-random-secret",
                "APP_API_KEY=super-secret-key",
            ),
            encoding="utf-8",
        )
        current = setup_wizard.read_env(root / ".env")

        with (
            patch.object(setup_wizard, "command_available", return_value=False),
            patch.object(
                setup_wizard,
                "docker_compose_available",
                return_value=(False, "not installed"),
            ),
            patch.object(
                setup_wizard,
                "fetch_models",
                return_value=(False, [], "offline"),
            ),
            patch.object(
                setup_wizard,
                "detect_uid_gid",
                return_value=("1234", "5678", "test IDs"),
            ),
        ):
            result = setup_wizard.run_service_role(
                root,
                args(),
                current,
                all_in_one=True,
            )
        setup_wizard.write_report(root, result, dry_run=False)

        env = setup_wizard.read_env(root / ".env")
        self.assertEqual(env["PUID"], "1234")
        self.assertEqual(env["PGID"], "5678")
        self.assertEqual(env["APP_HOST"], "127.0.0.1")
        self.assertEqual(env["OLLAMA_BASE_URL"], "http://localhost:11434")
        self.assertEqual(env["CUSTOM_VALUE"], "preserve-me")
        self.assertEqual(env["APP_API_KEY"], "super-secret-key")
        for relative in setup_wizard.DATA_DIRECTORIES:
            self.assertTrue((root / relative).is_dir())
        report = (root / "docs" / "setup-wizard-report.md").read_text(encoding="utf-8")
        self.assertNotIn("super-secret-key", report)

    def test_diagnostics_does_not_modify_repository(self) -> None:
        temporary, root = self.make_root()
        self.addCleanup(temporary.cleanup)
        before = sorted(path.relative_to(root) for path in root.rglob("*"))

        with (
            patch.object(setup_wizard, "command_available", return_value=False),
            patch.object(
                setup_wizard,
                "docker_compose_available",
                return_value=(False, "not installed"),
            ),
            patch.object(
                setup_wizard,
                "fetch_models",
                return_value=(False, [], "offline"),
            ),
            patch.object(
                setup_wizard,
                "service_health",
                return_value=(False, "not running"),
            ),
        ):
            setup_wizard.run_diagnostics(root)

        after = sorted(path.relative_to(root) for path in root.rglob("*"))
        self.assertEqual(before, after)

    def test_non_loopback_requires_explicit_confirmation(self) -> None:
        with self.assertRaises(RuntimeError):
            setup_wizard.confirm_non_loopback(
                "0.0.0.0",
                args(yes=False),
            )

        setup_wizard.confirm_non_loopback("0.0.0.0", args(yes=True))

    def test_report_masks_api_key(self) -> None:
        temporary, root = self.make_root()
        self.addCleanup(temporary.cleanup)
        result = setup_wizard.SetupResult(
            "service-host",
            values_written={"APP_API_KEY": "super-secret-key"},
        )

        setup_wizard.write_report(root, result, dry_run=False)

        report = (root / "docs" / "setup-wizard-report.md").read_text(encoding="utf-8")
        self.assertNotIn("super-secret-key", report)
        self.assertIn("su***ey", report)

    def test_uid_gid_detection_uses_platform_values(self) -> None:
        with (
            patch.object(setup_wizard.os, "getuid", return_value=1234, create=True),
            patch.object(setup_wizard.os, "getgid", return_value=5678, create=True),
        ):
            uid, gid, detail = setup_wizard.detect_uid_gid()

        self.assertEqual((uid, gid), ("1234", "5678"))
        self.assertIn("getuid", detail)

    def test_compose_uses_configured_uid_gid(self) -> None:
        compose = COMPOSE_PATH.read_text(encoding="utf-8")

        self.assertIn('user: "${PUID:-1000}:${PGID:-1000}"', compose)


class SetupWizardCliTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / ".env.example").write_text(EXAMPLE_ENV, encoding="utf-8")
        (root / "service").mkdir()
        return temporary, root

    def run_cli(
        self,
        root: Path | None,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(SCRIPT_PATH), *arguments]
        if root is not None:
            command.extend(["--repo-root", str(root)])
        environment = os.environ.copy()
        for key in (
            "APP_HOST",
            "APP_PORT",
            "APP_DATA_DIR",
            "OLLAMA_BASE_URL",
            "OLLAMA_MODEL",
            "APP_API_KEY",
            "ENABLE_REMOTE_MODE",
        ):
            environment.pop(key, None)
        return subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_help_output_lists_roles_and_diagnostics(self) -> None:
        completed = self.run_cli(None, "--help")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--role", completed.stdout)
        self.assertIn("model-runner", completed.stdout)
        self.assertIn("service-host", completed.stdout)
        self.assertIn("all-in-one", completed.stdout)
        self.assertIn("--diagnostics", completed.stdout)
        self.assertIn("--dry-run", completed.stdout)

    def test_diagnostics_reports_required_checks_without_changes(self) -> None:
        temporary, root = self.make_root()
        self.addCleanup(temporary.cleanup)
        before = self.snapshot(root)

        completed = self.run_cli(root, "--diagnostics")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        for label in (
            "Operating system",
            ".env exists",
            "Docker",
            "Ollama reachable",
            "Data directories",
        ):
            self.assertIn(label, completed.stdout)
        self.assertEqual(before, self.snapshot(root))

    def test_service_host_dry_run_prints_complete_default_contract(self) -> None:
        temporary, root = self.make_root()
        self.addCleanup(temporary.cleanup)

        completed = self.run_cli(root, "--role", "service-host", "--dry-run")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        for expected in (
            "APP_HOST=127.0.0.1",
            "APP_PORT=3927",
            "APP_DATA_DIR=/app/data",
            "OLLAMA_BASE_URL=http://localhost:11434",
            "OLLAMA_MODEL=qwen2.5:14b",
            "PUID=",
            "PGID=",
        ):
            self.assertIn(expected, completed.stdout)
        self.assertFalse((root / ".env").exists())
        self.assertFalse((root / "data").exists())
        self.assertFalse((root / "docs" / "setup-wizard-report.md").exists())

    def test_all_in_one_dry_run_uses_localhost_defaults(self) -> None:
        temporary, root = self.make_root()
        self.addCleanup(temporary.cleanup)

        completed = self.run_cli(root, "--role", "all-in-one", "--dry-run")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("APP_HOST=127.0.0.1", completed.stdout)
        self.assertIn("OLLAMA_BASE_URL=http://localhost:11434", completed.stdout)
        self.assertNotIn("APP_HOST=0.0.0.0", completed.stdout)

    def test_lan_bind_requires_explicit_confirmation(self) -> None:
        temporary, root = self.make_root()
        self.addCleanup(temporary.cleanup)

        rejected = self.run_cli(
            root,
            "--role",
            "service-host",
            "--dry-run",
            "--app-host",
            "0.0.0.0",
        )
        accepted = self.run_cli(
            root,
            "--role",
            "service-host",
            "--dry-run",
            "--app-host",
            "0.0.0.0",
            "--yes",
        )

        self.assertEqual(rejected.returncode, 2)
        self.assertIn("requires --yes", rejected.stderr)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("APP_HOST=0.0.0.0", accepted.stdout)
        self.assertFalse((root / ".env").exists())

    @staticmethod
    def snapshot(root: Path) -> list[tuple[str, int]]:
        return sorted(
            (path.relative_to(root).as_posix(), path.stat().st_size)
            for path in root.rglob("*")
        )


if __name__ == "__main__":
    unittest.main()
