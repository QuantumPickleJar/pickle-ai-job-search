#!/usr/bin/env python3
"""Configure or diagnose the local-first service by machine role."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


DEFAULT_MODEL = "qwen2.5:14b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_APP_HOST = "127.0.0.1"
DEFAULT_APP_PORT = "3927"
OLLAMA_TIMEOUT = 5
SERVICE_TIMEOUT = 3
SENSITIVE_KEYS = {"APP_API_KEY"}
DATA_DIRECTORIES = (
    "data/job_intake/captured_jobs",
    "data/job_intake/processed_jobs",
    "data/job_intake/rejected_jobs",
    "data/applications",
    "data/tasks",
    "data/profile",
    "data/logs",
)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class SetupResult:
    role: str
    values_written: dict[str, str] = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    next_commands: list[str] = field(default_factory=list)

    def check(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(Check(name, passed, detail))
        marker = "PASS" if passed else "FAIL"
        print(f"[{marker}] {name}: {detail}")

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"[WARN] {message}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure the AI Job Search service by machine role.",
    )
    parser.add_argument(
        "--role",
        choices=("model-runner", "service-host", "all-in-one", "diagnostics"),
        help="Machine role to configure.",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Check configuration without changing files.",
    )
    parser.add_argument("--ollama-base-url", help="Private Ollama base URL.")
    parser.add_argument("--model", help=f"Ollama model name (default: {DEFAULT_MODEL}).")
    parser.add_argument(
        "--app-host",
        help="Host interface for the published service port.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Accept explicit non-loopback bind warnings.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing files or directories.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def repo_root_from(args: argparse.Namespace) -> Path:
    root = args.repo_root.resolve() if args.repo_root else Path(__file__).resolve().parents[1]
    if not (root / ".env.example").is_file():
        raise RuntimeError(f"Repository root is missing .env.example: {root}")
    return root


def select_role(args: argparse.Namespace) -> str:
    if args.diagnostics:
        if args.role and args.role != "diagnostics":
            raise RuntimeError("--diagnostics cannot be combined with another role")
        return "diagnostics"
    if args.role:
        return args.role
    if not sys.stdin.isatty():
        raise RuntimeError("Interactive mode requires a terminal; supply --role")

    choices = {
        "1": "model-runner",
        "2": "service-host",
        "3": "all-in-one",
        "4": "diagnostics",
    }
    print("Select this machine's role:")
    print("1. model-runner (Windows/Ollama/GPU)")
    print("2. service-host (Raspberry Pi/Linux/Docker)")
    print("3. all-in-one (local development)")
    print("4. diagnostics (no changes)")
    while True:
        selected = input("Role [1-4]: ").strip()
        if selected in choices:
            return choices[selected]
        print("Enter 1, 2, 3, or 4.")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def update_env_text(original: str, updates: dict[str, str]) -> str:
    remaining = dict(updates)
    output: list[str] = []
    for raw_line in original.splitlines():
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                output.append(f"{key}={remaining.pop(key)}")
                continue
        output.append(raw_line)
    if remaining:
        if output and output[-1]:
            output.append("")
        output.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(output).rstrip() + "\n"


def configure_env(
    root: Path,
    updates: dict[str, str],
    result: SetupResult,
    dry_run: bool,
) -> None:
    env_path = root / ".env"
    example_path = root / ".env.example"
    if env_path.is_file():
        original = env_path.read_text(encoding="utf-8")
    else:
        original = example_path.read_text(encoding="utf-8")
        result.check(".env", True, "would create from .env.example" if dry_run else "created from .env.example")
    rendered = update_env_text(original, updates)
    if not dry_run:
        env_path.write_text(rendered, encoding="utf-8")
    result.values_written.update(updates)


def command_available(command: str) -> bool:
    return shutil.which(command) is not None


def run_command(command: list[str]) -> tuple[bool, str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = (completed.stdout or completed.stderr).strip()
    return completed.returncode == 0, output


def docker_compose_available() -> tuple[bool, str]:
    if not command_available("docker"):
        return False, "docker command not found"
    passed, output = run_command(["docker", "compose", "version"])
    return passed, output or "docker compose unavailable"


def fetch_json(url: str, timeout: int) -> dict[str, object]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_models(base_url: str) -> tuple[bool, list[str], str]:
    tags_url = f"{base_url.rstrip('/')}/api/tags"
    try:
        payload = fetch_json(tags_url, OLLAMA_TIMEOUT)
    except urllib.error.HTTPError as exc:
        return False, [], f"{tags_url} returned HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        return False, [], f"Ollama is not reachable at {tags_url}: {exc}"
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return False, [], f"Ollama returned invalid JSON: {exc}"

    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return False, [], "Ollama response is missing a models list"
    models = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name.strip():
            models.append(name.strip())
    if not models:
        return False, [], "Ollama is reachable but no models are installed"
    return True, models, "installed models: " + ", ".join(models)


def validate_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("OLLAMA_BASE_URL must be an absolute HTTP or HTTPS URL")
    if parsed.username or parsed.password:
        raise RuntimeError("OLLAMA_BASE_URL must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise RuntimeError("OLLAMA_BASE_URL must not contain a path, query, or fragment")
    return base_url


def prompt_value(label: str, current: str) -> str:
    if not sys.stdin.isatty():
        return current
    entered = input(f"{label} [{current}]: ").strip()
    return entered or current


def confirm_non_loopback(host: str, args: argparse.Namespace) -> None:
    if host in {"127.0.0.1", "localhost", "::1"}:
        return
    warning = (
        f"APP_HOST={host} exposes the service beyond Pi localhost. "
        "Use only a trusted LAN/private interface and never router port forwarding."
    )
    print(f"[WARN] {warning}")
    if args.yes:
        return
    if not sys.stdin.isatty():
        raise RuntimeError("Non-loopback APP_HOST requires --yes in non-interactive mode")
    if input("Continue with this bind host? [y/N]: ").strip().lower() not in {"y", "yes"}:
        raise RuntimeError("Setup cancelled before changing APP_HOST")


def detect_uid_gid() -> tuple[str, str, str]:
    if hasattr(os, "getuid") and hasattr(os, "getgid"):
        return str(os.getuid()), str(os.getgid()), "detected with os.getuid/os.getgid"
    uid_ok, uid = run_command(["id", "-u"])
    gid_ok, gid = run_command(["id", "-g"])
    if uid_ok and gid_ok and uid.isdigit() and gid.isdigit():
        return uid, gid, "detected with id -u/id -g"
    return "1000", "1000", "could not detect UID/GID; using documented defaults"


def create_data_directories(root: Path, result: SetupResult, dry_run: bool) -> None:
    for relative in DATA_DIRECTORIES:
        path = root / relative
        if not dry_run:
            path.mkdir(parents=True, exist_ok=True)
    result.check(
        "Data directories",
        True,
        "would create required directories" if dry_run else "required directories exist",
    )


def configured_model(env: dict[str, str], args: argparse.Namespace) -> str:
    return (args.model or env.get("OLLAMA_MODEL") or DEFAULT_MODEL).strip()


def check_ollama(base_url: str, model: str, result: SetupResult) -> None:
    passed, models, detail = fetch_models(base_url)
    result.check("Ollama reachable", passed, detail)
    if passed:
        print("Installed models:")
        for installed in models:
            print(f"- {installed}")
        result.check(
            "Configured model",
            model in models,
            f"{model} is installed" if model in models else f"{model} is not installed",
        )
    else:
        result.check("Configured model", False, "cannot verify while Ollama is unavailable")


def likely_private_ip() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 80))
        address = sock.getsockname()[0]
        return address if not address.startswith("127.") else None
    except OSError:
        return None
    finally:
        sock.close()


def run_model_runner(
    root: Path,
    args: argparse.Namespace,
    env: dict[str, str],
) -> SetupResult:
    result = SetupResult("model-runner")
    result.check("Operating system", platform.system() == "Windows", platform.platform())
    result.check(
        "Ollama command",
        command_available("ollama"),
        "ollama command found" if command_available("ollama") else "ollama command not found",
    )
    model = configured_model(env, args)
    if not env.get("OLLAMA_MODEL") and not args.model:
        result.warn(f"No model configured; recommended default is {DEFAULT_MODEL}")
    check_ollama(DEFAULT_OLLAMA_URL, model, result)
    address = likely_private_ip()
    if address:
        print(f"Likely service-host value: OLLAMA_BASE_URL=http://{address}:11434")
    else:
        print("Set OLLAMA_BASE_URL to this workstation's private LAN or Tailscale address.")
    result.warn("Do not expose Ollama directly to the public internet.")
    result.warn("The wizard does not change Windows Firewall or router settings.")
    result.next_commands = [
        f"ollama pull {model}",
        "python scripts/setup_wizard.py --diagnostics",
    ]
    return result


def service_updates(
    root: Path,
    args: argparse.Namespace,
    env: dict[str, str],
    *,
    all_in_one: bool,
) -> tuple[dict[str, str], list[str]]:
    uid, gid, uid_detail = detect_uid_gid()
    interactive = (
        not all_in_one
        and sys.stdin.isatty()
        and not args.dry_run
        and args.ollama_base_url is None
    )
    current_url = (
        DEFAULT_OLLAMA_URL
        if all_in_one
        else env.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL)
    )
    base_url = args.ollama_base_url or (
        prompt_value("OLLAMA_BASE_URL", current_url)
        if interactive
        else current_url
    )
    base_url = validate_base_url(base_url)
    current_host = DEFAULT_APP_HOST if all_in_one else env.get("APP_HOST", DEFAULT_APP_HOST)
    host = args.app_host or current_host
    confirm_non_loopback(host, args)
    updates = {
        "PUID": uid,
        "PGID": gid,
        "APP_HOST": host,
        "APP_PORT": env.get("APP_PORT", DEFAULT_APP_PORT),
        "APP_DATA_DIR": "/app/data",
        "OLLAMA_BASE_URL": base_url,
        "OLLAMA_MODEL": configured_model(env, args),
        "ENABLE_REMOTE_MODE": env.get("ENABLE_REMOTE_MODE", "false"),
    }
    return updates, [uid_detail]


def run_service_role(
    root: Path,
    args: argparse.Namespace,
    env: dict[str, str],
    *,
    all_in_one: bool,
) -> SetupResult:
    role = "all-in-one" if all_in_one else "service-host"
    result = SetupResult(role)
    system = platform.system()
    machine = platform.machine()
    result.check(
        "Operating system",
        system in {"Linux", "Windows", "Darwin"} if all_in_one else system == "Linux",
        f"{platform.platform()} ({machine})",
    )
    if not all_in_one:
        pi_detected = Path("/proc/device-tree/model").is_file() or "arm" in machine.lower() or "aarch64" in machine.lower()
        result.check(
            "Raspberry Pi environment",
            pi_detected,
            "Pi/ARM indicators detected" if pi_detected else "generic Linux host or Pi not detected",
        )
    docker = command_available("docker")
    result.check("Docker", docker, "docker command found" if docker else "docker command not found")
    compose, compose_detail = docker_compose_available()
    result.check("Docker Compose", compose, compose_detail)

    updates, uid_notes = service_updates(root, args, env, all_in_one=all_in_one)
    for note in uid_notes:
        if "could not" in note:
            result.warn(note)
        else:
            print(f"[INFO] UID/GID: {note}")
    configure_env(root, updates, result, args.dry_run)
    create_data_directories(root, result, args.dry_run)
    check_ollama(updates["OLLAMA_BASE_URL"], updates["OLLAMA_MODEL"], result)

    api_key = env.get("APP_API_KEY", "")
    if not api_key or api_key == "replace-with-a-strong-random-secret":
        result.warn("APP_API_KEY is still empty or a placeholder; replace it before remote use.")
    if updates["APP_HOST"] != DEFAULT_APP_HOST:
        result.warn(f"Selected exposure mode binds the service host port to {updates['APP_HOST']}.")
    else:
        result.warn("Service host port remains loopback-only for a private proxy or local development.")
    result.warn("Do not expose Ollama directly, open router ports, or install tunnel credentials through this wizard.")
    result.next_commands = [
        "docker compose -f docker-compose.service.yml --env-file .env config",
        "docker compose -f docker-compose.service.yml --env-file .env up -d --build",
        f"curl --fail http://{updates['APP_HOST']}:{updates['APP_PORT']}/health",
    ]
    return result


def service_health(host: str, port: str) -> tuple[bool, str]:
    probe_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{probe_host}:{port}/health"
    try:
        payload = fetch_json(url, SERVICE_TIMEOUT)
    except Exception as exc:
        return False, f"{url} unavailable: {exc}"
    return payload.get("status") == "ok", f"{url} returned {payload}"


def run_diagnostics(root: Path) -> SetupResult:
    result = SetupResult("diagnostics")
    env_path = root / ".env"
    env = read_env(env_path)
    result.check("Operating system", True, platform.platform())
    result.check("Repository root", (root / "service").is_dir(), str(root))
    result.check(".env exists", env_path.is_file(), str(env_path))

    for key in ("APP_HOST", "APP_PORT", "OLLAMA_BASE_URL", "OLLAMA_MODEL"):
        value = env.get(key, "<not set>")
        result.check(key, key in env, value)

    docker = command_available("docker")
    result.check("Docker", docker, "docker command found" if docker else "docker command not found")
    compose, compose_detail = docker_compose_available()
    result.check("Docker Compose", compose, compose_detail)

    base_url = env.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL)
    model = env.get("OLLAMA_MODEL", DEFAULT_MODEL)
    check_ollama(base_url, model, result)

    missing = [relative for relative in DATA_DIRECTORIES if not (root / relative).is_dir()]
    result.check(
        "Data directories",
        not missing,
        "all present" if not missing else "missing: " + ", ".join(missing),
    )
    data_dir = root / "data"
    writable = data_dir.is_dir() and os.access(data_dir, os.W_OK)
    result.check("Data directory writable", writable, str(data_dir))

    host = env.get("APP_HOST", DEFAULT_APP_HOST)
    port = env.get("APP_PORT", DEFAULT_APP_PORT)
    healthy, health_detail = service_health(host, port)
    result.check("Service health", healthy, health_detail)
    return result


def masked_value(key: str, value: str) -> str:
    if key not in SENSITIVE_KEYS:
        return value
    if not value:
        return "<empty>"
    return value[:2] + "***" + value[-2:] if len(value) >= 6 else "***"


def write_report(root: Path, result: SetupResult, dry_run: bool) -> None:
    if dry_run:
        print("[DRY-RUN] Setup report was not written.")
        return
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    written = "\n".join(
        f"- `{key}` = `{masked_value(key, value)}`"
        for key, value in sorted(result.values_written.items())
    ) or "- No environment values were written."
    passed = "\n".join(
        f"- {check.name}: {check.detail}" for check in result.checks if check.passed
    ) or "- None."
    failed = "\n".join(
        f"- {check.name}: {check.detail}" for check in result.checks if not check.passed
    ) or "- None."
    warnings = "\n".join(f"- {warning}" for warning in result.warnings) or "- None."
    commands = "\n".join(f"- `{command}`" for command in result.next_commands) or "- None."
    content = f"""# Setup Wizard Report

## Run

- Timestamp: `{timestamp}`
- Selected role: `{result.role}`

## Values Written

{written}

## Checks Passed

{passed}

## Checks Failed

{failed}

## Warnings

{warnings}

## Next Commands

{commands}
"""
    report_path = root / "docs" / "setup-wizard-report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(content, encoding="utf-8")
    print("Setup report: docs/setup-wizard-report.md")


def print_summary(result: SetupResult) -> None:
    passed = sum(check.passed for check in result.checks)
    failed = len(result.checks) - passed
    print(f"\nSummary: {passed} passed, {failed} failed, {len(result.warnings)} warnings")
    if result.next_commands:
        print("Next commands:")
        for command in result.next_commands:
            print(f"- {command}")


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        root = repo_root_from(args)
        role = select_role(args)
        env = read_env(root / ".env")
        for key in (
            "APP_HOST",
            "APP_PORT",
            "OLLAMA_BASE_URL",
            "OLLAMA_MODEL",
            "APP_API_KEY",
            "ENABLE_REMOTE_MODE",
        ):
            if key in os.environ:
                env[key] = os.environ[key]
        print(f"Repository root: {root}")
        print(f"Selected role: {role}")
        if args.dry_run:
            print("[DRY-RUN] No files or directories will be changed.")

        if role == "diagnostics":
            result = run_diagnostics(root)
        elif role == "model-runner":
            result = run_model_runner(root, args, env)
            write_report(root, result, args.dry_run)
        elif role == "service-host":
            result = run_service_role(root, args, env, all_in_one=False)
            write_report(root, result, args.dry_run)
        else:
            result = run_service_role(root, args, env, all_in_one=True)
            write_report(root, result, args.dry_run)

        print_summary(result)
        return 0
    except (RuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
