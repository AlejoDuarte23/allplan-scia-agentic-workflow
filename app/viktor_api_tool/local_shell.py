from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

try:
    from .client import get_token
except ImportError:
    from dotenv import load_dotenv

    def get_token() -> str:
        load_dotenv()
        token = (os.getenv("TOKEN_VK_APP") or os.getenv("VIKTOR_TOKEN") or "").strip()
        if not token:
            raise ValueError("Missing VIKTOR token. Set TOKEN_VK_APP or VIKTOR_TOKEN.")
        return token


SHELL_SKILL_DIR = Path(__file__).resolve().parent / "local_shell_skill"
DEFAULT_ALLOWED_DOMAINS = ("demo.viktor.ai",)
BLOCKED_EXECUTABLES = {
    "awk",
    "cat",
    "chmod",
    "chown",
    "cp",
    "env",
    "find",
    "git",
    "grep",
    "head",
    "less",
    "more",
    "mv",
    "nc",
    "ncat",
    "open",
    "pip",
    "printenv",
    "rm",
    "scp",
    "sed",
    "set",
    "ssh",
    "sudo",
    "tail",
    "uv",
}
SHELL_OPERATORS = {"&&", "||", "|", ">", ">>", "<", "<<", ";"}


def domains_from_urls(app_urls: list[str], extra_domains: list[str] | None = None) -> list[str]:
    domains: set[str] = set(DEFAULT_ALLOWED_DOMAINS)
    for app_url in app_urls:
        parsed = urlparse(app_url)
        if parsed.netloc:
            domains.add(parsed.netloc)
    for domain in extra_domains or []:
        cleaned = domain.replace("https://", "").replace("http://", "").split("/")[0].strip()
        if cleaned:
            domains.add(cleaned)
    api_base = os.getenv("VIKTOR_API_BASE")
    if api_base:
        parsed = urlparse(api_base)
        if parsed.netloc:
            domains.add(parsed.netloc)
    return sorted(domains)


def redact_text(text: str, secrets: list[str]) -> str:
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED_TOKEN]")
    redacted = re.sub(
        r"Bearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [REDACTED_TOKEN]",
        redacted,
        flags=re.IGNORECASE,
    )
    return redacted


def _host_allowed(host: str, allowed_domains: list[str]) -> bool:
    cleaned = host.lower().strip("[]")
    return cleaned in allowed_domains or any(
        cleaned.endswith("." + domain) for domain in allowed_domains
    )


def _write_python_guard(guard_dir: Path) -> None:
    guard_dir.mkdir(parents=True, exist_ok=True)
    (guard_dir / "sitecustomize.py").write_text(
        """
from __future__ import annotations

import os
import socket

_allowed = {
    d.strip().lower()
    for d in os.environ.get("VIKTOR_ALLOWED_DOMAINS", "").split(",")
    if d.strip()
}
_original_getaddrinfo = socket.getaddrinfo


def _host_allowed(host):
    if not host:
        return False
    host = str(host).lower().strip("[]")
    return host in _allowed or any(host.endswith("." + domain) for domain in _allowed)


def guarded_getaddrinfo(host, *args, **kwargs):
    if not _host_allowed(host):
        raise PermissionError(f"Network host is not allowed by SafeViktorShellExecutor: {host}")
    return _original_getaddrinfo(host, *args, **kwargs)


socket.getaddrinfo = guarded_getaddrinfo
""".lstrip(),
        encoding="utf-8",
    )


@dataclass
class SafeViktorShellExecutor:
    token: str
    allowed_domains: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_DOMAINS))
    cwd: Path | None = None
    max_timeout_ms: int = 120_000
    max_output_chars: int = 24_000
    command_log: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.cwd = Path(
            self.cwd
            or Path(tempfile.gettempdir())
            / "viktor-local-shell"
            / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        )
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.guard_dir = self.cwd / "_python_guard"
        _write_python_guard(self.guard_dir)

    def _redact(self, value: str) -> str:
        return redact_text(value, [self.token])

    def _safe_env(self) -> dict[str, str]:
        env: dict[str, str] = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.cwd),
            "TMPDIR": str(self.cwd),
            "PYTHONUNBUFFERED": "1",
            "PYTHONPATH": str(self.guard_dir),
            "TOKEN_VK_APP": self.token,
            "VIKTOR_ALLOWED_DOMAINS": ",".join(self.allowed_domains),
        }
        if os.getenv("SSL_CERT_FILE"):
            env["SSL_CERT_FILE"] = os.environ["SSL_CERT_FILE"]
        if os.getenv("REQUESTS_CA_BUNDLE"):
            env["REQUESTS_CA_BUNDLE"] = os.environ["REQUESTS_CA_BUNDLE"]
        return env

    def _validate_and_build_argv(self, command: str) -> tuple[list[str] | None, str | None]:
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return None, f"Rejected command: could not parse shell tokens ({exc})."

        if not argv:
            return None, "Rejected command: empty command."

        executable = Path(argv[0]).name
        if executable in BLOCKED_EXECUTABLES:
            return None, f"Rejected command: executable '{executable}' is not allowed."
        if executable not in {"python", "python3", Path(sys.executable).name, "curl"}:
            return None, "Rejected command: only python/python3 and curl are allowed."
        if any(token in SHELL_OPERATORS for token in argv):
            return None, "Rejected command: shell operators, pipes, and redirects are not allowed."
        if any(".env" in token for token in argv):
            return None, "Rejected command: reading .env is not allowed."
        if executable in {"python", "python3", Path(sys.executable).name}:
            if len(argv) >= 3 and argv[1] == "-m" and argv[2] in {"pip", "venv", "ensurepip"}:
                return None, f"Rejected command: python -m {argv[2]} is not allowed."
            return [sys.executable if executable in {"python", "python3"} else argv[0], *argv[1:]], None

        for token in argv[1:]:
            if token.startswith("http://") or token.startswith("https://"):
                host = urlparse(token).netloc
                if not _host_allowed(host, self.allowed_domains):
                    return None, f"Rejected curl URL: host '{host}' is not allowed."
            if token.startswith("file://"):
                return None, "Rejected curl URL: file:// is not allowed."
        expanded = [self.token if token == "$TOKEN_VK_APP" else token for token in argv]
        return expanded, None

    async def __call__(self, request: Any) -> Any:
        from agents import ShellCallOutcome, ShellCommandOutput, ShellResult

        action = request.data.action
        outputs: list[Any] = []
        for command in action.commands:
            argv, rejection = self._validate_and_build_argv(command)
            started_at = datetime.now(timezone.utc).isoformat()
            if rejection:
                stderr = rejection
                outputs.append(
                    ShellCommandOutput(
                        command=self._redact(command),
                        stdout="",
                        stderr=stderr,
                        outcome=ShellCallOutcome(type="exit", exit_code=126),
                    )
                )
                self.command_log.append(
                    {
                        "command": self._redact(command),
                        "started_at": started_at,
                        "exit_code": 126,
                        "rejected": True,
                        "stderr": stderr,
                    }
                )
                break

            timeout = min(action.timeout_ms or self.max_timeout_ms, self.max_timeout_ms) / 1000
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self.cwd,
                env=self._safe_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            timed_out = False
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                stdout_bytes, stderr_bytes = await proc.communicate()
                timed_out = True

            stdout = self._redact(stdout_bytes.decode("utf-8", errors="replace"))
            stderr = self._redact(stderr_bytes.decode("utf-8", errors="replace"))
            if len(stdout) > self.max_output_chars:
                stdout = stdout[: self.max_output_chars] + "\n[stdout truncated]"
            if len(stderr) > self.max_output_chars:
                stderr = stderr[: self.max_output_chars] + "\n[stderr truncated]"

            exit_code = getattr(proc, "returncode", None)
            outputs.append(
                ShellCommandOutput(
                    command=self._redact(command),
                    stdout=stdout,
                    stderr=stderr,
                    outcome=ShellCallOutcome(
                        type="timeout" if timed_out else "exit",
                        exit_code=exit_code,
                    ),
                )
            )
            self.command_log.append(
                {
                    "command": self._redact(command),
                    "started_at": started_at,
                    "exit_code": exit_code,
                    "timed_out": timed_out,
                    "stdout": stdout,
                    "stderr": stderr,
                }
            )
            if timed_out:
                break

        log_path = self.cwd / "shell_log.json"
        log_path.write_text(json.dumps(self.command_log, indent=2), encoding="utf-8")
        return ShellResult(
            output=outputs,
            provider_data={
                "working_directory": str(self.cwd),
                "allowed_domains": self.allowed_domains,
                "log_path": str(log_path),
            },
        )


async def run_local_viktor_shell_commands(
    *,
    commands: list[str],
    token: str | None = None,
    app_urls: list[str] | None = None,
    allowed_domains: list[str] | None = None,
    timeout_ms: int = 120_000,
) -> dict[str, Any]:
    executor = SafeViktorShellExecutor(
        token=token or get_token(),
        allowed_domains=domains_from_urls(app_urls or [], allowed_domains),
    )
    request = SimpleNamespace(
        data=SimpleNamespace(
            action=SimpleNamespace(commands=commands, timeout_ms=timeout_ms)
        )
    )
    result = await executor(request)
    return {
        "working_directory": str(executor.cwd),
        "allowed_domains": executor.allowed_domains,
        "commands": executor.command_log,
        "provider_data": getattr(result, "provider_data", None),
    }


def create_viktor_local_shell_tool() -> Any:
    from agents import ShellTool

    domains = domains_from_urls([])
    executor = SafeViktorShellExecutor(token=get_token(), allowed_domains=domains)
    skill = {
        "name": "viktor-local-shell",
        "description": "Safely inspect VIKTOR apps and draft bridge code with local python/curl commands.",
        "path": str(SHELL_SKILL_DIR),
    }
    return ShellTool(
        name="viktor_local_shell",
        environment={
            "type": "local",
            "skills": [skill],
        },
        executor=executor,
        needs_approval=False,
    )
