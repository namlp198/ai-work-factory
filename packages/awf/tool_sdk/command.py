"""Command wrapper: argv only (no shell), allowlist, timeout, output limits, secret redaction."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from awf.security import redact, sanitized_env


@dataclass
class CommandResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def summary(self, max_chars: int = 6000) -> dict:
        def tail(text: str) -> str:
            return text if len(text) <= max_chars else "[...truncated...]\n" + text[-max_chars:]

        return {
            "argv": self.argv,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
            "stdout_tail": tail(self.stdout),
            "stderr_tail": tail(self.stderr),
            "output_truncated": self.truncated,
        }


def _limit(data: bytes, max_bytes: int) -> tuple[str, bool]:
    truncated = len(data) > max_bytes
    if truncated:
        data = data[-max_bytes:]
    return redact(data.decode("utf-8", errors="replace")), truncated


def run_command(argv: list[str], cwd: Path, timeout: int = 600, max_bytes: int = 200_000,
                env: dict[str, str] | None = None) -> CommandResult:
    if not argv:
        raise ValueError("empty command")
    exe = shutil.which(argv[0]) or argv[0]
    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            [exe, *argv[1:]],
            cwd=str(cwd),
            capture_output=True,
            timeout=timeout,
            env=sanitized_env({"PYTHONDONTWRITEBYTECODE": "1", **(env or {})}),
            stdin=subprocess.DEVNULL,
            shell=False,
        )
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        code, out, err = -1, exc.stdout or b"", (exc.stderr or b"") + f"\n[timeout after {timeout}s]".encode()
    except FileNotFoundError:
        code, out, err = 127, b"", f"executable not found: {argv[0]}".encode()
    stdout, t1 = _limit(out, max_bytes)
    stderr, t2 = _limit(err, max_bytes)
    return CommandResult(
        argv=list(argv),
        exit_code=code,
        stdout=stdout,
        stderr=stderr,
        duration_ms=int((time.monotonic() - start) * 1000),
        timed_out=timed_out,
        truncated=t1 or t2,
    )


def git(args: list[str], cwd: Path, timeout: int = 300, check: bool = True,
        env: dict[str, str] | None = None) -> CommandResult:
    """`env` adds variables for this call, e.g. GIT_CONFIG_* carrying the credentials of a hosted repository."""
    extra = env or {}
    env = {
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": os.environ.get("AWF_GIT_AUTHOR_NAME", "AI Work Factory"),
        "GIT_AUTHOR_EMAIL": os.environ.get("AWF_GIT_AUTHOR_EMAIL", "awf@localhost"),
        "GIT_COMMITTER_NAME": os.environ.get("AWF_GIT_AUTHOR_NAME", "AI Work Factory"),
        "GIT_COMMITTER_EMAIL": os.environ.get("AWF_GIT_AUTHOR_EMAIL", "awf@localhost"),
        **extra,
    }
    result = run_command(["git", "-c", "core.autocrlf=false", *args], cwd=cwd, timeout=timeout,
                         max_bytes=5_000_000, env=env)
    if check and not result.ok:
        raise GitError(f"git {' '.join(args)} failed ({result.exit_code}): {result.stderr.strip()[:2000]}")
    return result


class GitError(RuntimeError):
    pass
