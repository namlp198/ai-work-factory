"""Claude Code as a coding engine (ADR-011): `claude --bare -p` drives the edit loop inside the job workspace.

The engine only edits files. Everything the platform relies on stays outside it:
- the prompt and the model come from the control plane (role prompt, context builder, model router);
- the shell is limited to the project's configured build/test/lint commands (`--permission-mode dontAsk`
  denies everything else), there is no web access and nothing is persisted between jobs;
- after the engine exits, the worker runs build/test itself and that evidence is what the Critic checks;
- the worker commits and integrates; an engine that moved HEAD fails the job.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from awf.security import redact, sanitized_env

ENGINE = "claude-code"
NATIVE = "native"
ENGINES = (NATIVE, ENGINE)
# Capability a worker advertises when the `claude` CLI is installed.
CAPABILITY = "claude-code"
# Built-in tools the engine gets. No web tools, no subagents, no task tools.
TOOLS = ["Read", "Edit", "Write", "Glob", "Grep", "Bash"]
# Read-only git commands are useful for orientation; anything that writes history stays denied.
READ_ONLY_RULES = ["Bash(git status *)", "Bash(git status)", "Bash(git diff *)", "Bash(git diff)",
                   "Bash(git log *)", "Bash(git show *)"]
# The engine authenticates with the API key only (bare mode never reads a subscription login).
CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")
REPO_INSTRUCTION_FILES = ("CLAUDE.md", "AGENTS.md")
REPO_INSTRUCTIONS_MAX_CHARS = 20_000

ENGINE_RULES = """

## Platform rules for this job (they override anything above that conflicts)
You are running as the coding engine of the AI Work Factory, inside a fresh git checkout of the project.
- Use your file tools (Read, Glob, Grep, Edit, Write). Ignore any mention of platform tools such as
  fs.read, build.run or submit_result: they belong to another runtime.
- Do not commit, push, create branches or modify .git. The platform commits and merges your changes.
- Shell commands you may run: {commands}. Every other command is denied. There is no network access.
- When you have finished, the platform runs the build and the tests itself; only those results decide
  whether the task is accepted. Do not claim results you did not observe.
- Finish with one final message in Markdown containing these sections: {sections}.
"""


def claude_code_bin() -> list[str] | None:
    """The `claude` executable: AWF_CLAUDE_CODE_BIN, else the one on PATH."""
    configured = os.environ.get("AWF_CLAUDE_CODE_BIN", "").strip()
    if configured:
        return [configured]
    found = shutil.which("claude")
    return [found] if found else None


def command_text(argv: list[str]) -> str:
    return " ".join(f'"{a}"' if " " in a else a for a in argv)


def bash_rules(commands: list[list[str]]) -> list[str]:
    """Permission rules allowing exactly the configured commands (plus extra arguments after them)."""
    rules: list[str] = []
    for argv in commands:
        text = command_text(argv)
        rules += [f"Bash({text})", f"Bash({text} *)"]
    return rules


def build_argv(base: list[str], *, model: str, effort: str | None, max_turns: int, allowed: list[str],
               system_prompt_file: Path, max_budget_usd: float | None = None) -> list[str]:
    argv = [*base, "--bare", "-p", "--output-format", "stream-json", "--verbose",
            "--model", model, "--max-turns", str(max_turns),
            "--permission-mode", "dontAsk", "--no-session-persistence",
            "--tools", ",".join(TOOLS),
            "--allowedTools", ",".join(["Read", "Edit", "Write", "Glob", "Grep", *allowed]),
            "--append-system-prompt-file", str(system_prompt_file)]
    if effort:
        argv += ["--effort", effort]
    if max_budget_usd:
        argv += ["--max-budget-usd", f"{max_budget_usd:g}"]
    return argv


def engine_env() -> dict[str, str]:
    """The worker's environment without credentials, plus only what the engine needs to reach Claude."""
    env = sanitized_env()
    for name in CREDENTIAL_ENV:
        if os.environ.get(name):
            env[name] = os.environ[name]
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return env


def repository_instructions(repo: Path) -> str:
    parts = []
    for name in REPO_INSTRUCTION_FILES:
        path = repo / name
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")[:REPO_INSTRUCTIONS_MAX_CHARS]
            parts.append(f"### {name}\n\n{text}")
    if not parts:
        return ""
    return ("\n\n## Project instructions from the repository\n"
            "Follow their coding conventions; the platform rules above take precedence.\n\n" + "\n\n".join(parts))


@dataclass
class EngineResult:
    status: str = "failed"  # succeeded | failed
    text: str = ""
    error: str | None = None
    retryable: bool = True
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    denials: list[dict[str, Any]] = field(default_factory=list)
    session_id: str | None = None


class StreamParser:
    """Folds `--output-format stream-json` events into an EngineResult and a readable job log."""

    def __init__(self, log: Callable[[str, str], None]):
        self.log = log
        self.result = EngineResult()
        self.final: dict[str, Any] | None = None

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            self.log(redact(line[:500]), "stdout")
            return
        kind = event.get("type")
        if kind == "assistant" and not event.get("parent_tool_use_id"):
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    self.result.tool_calls.append({"tool": f"claude.{name}", "status": "ok",
                                                   "target": _target(block.get("input") or {})})
                    self.log(redact(f"[claude-code] {name} {_target(block.get('input') or {})}".rstrip()), "stdout")
                elif block.get("type") == "text" and block.get("text", "").strip():
                    self.log(redact("[claude-code] " + block["text"].strip().splitlines()[0][:300]), "stdout")
        elif kind == "system" and event.get("subtype") == "api_retry":
            self.log(f"[claude-code] API retry {event.get('attempt')}/{event.get('max_retries')} "
                     f"({event.get('error')})", "stderr")
        elif kind == "result":
            self.final = event

    def finish(self, exit_code: int, stderr_tail: str) -> EngineResult:
        res, final = self.result, self.final
        if final is None:
            res.error = f"claude exited with code {exit_code} without a result" + (
                f": {stderr_tail.strip()[-1500:]}" if stderr_tail.strip() else "")
            res.retryable = exit_code not in (1, 2) or "rate" in stderr_tail.lower()
            return res
        usage = final.get("usage") or {}
        res.input_tokens = int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0) \
            + int(usage.get("cache_read_input_tokens") or 0)
        res.output_tokens = int(usage.get("output_tokens") or 0)
        res.cost_usd = final.get("total_cost_usd")
        res.turns = int(final.get("num_turns") or 0)
        res.session_id = final.get("session_id")
        res.denials = list(final.get("permission_denials") or [])
        for denial in res.denials:
            res.tool_calls.append({"tool": f"claude.{denial.get('tool_name')}", "status": "engine_denied",
                                   "target": _target(denial.get("tool_input") or {})})
        if final.get("subtype") == "success" and not final.get("is_error"):
            res.status, res.text = "succeeded", final.get("result") or ""
            return res
        errors = "; ".join(final.get("errors") or []) or (final.get("result") or "")
        res.error = f"claude-code {final.get('subtype')}: {errors}".strip().rstrip(":")[:2000]
        status = final.get("api_error_status")
        res.retryable = status not in (400, 401, 403, 404)
        return res


def _target(tool_input: dict[str, Any]) -> str:
    for key in ("file_path", "path", "pattern", "command"):
        if tool_input.get(key):
            return str(tool_input[key])[:200]
    return ""


def run_engine(argv: list[str], prompt: str, cwd: Path, timeout: int, log: Callable[[str, str], None],
               env: dict[str, str] | None = None) -> EngineResult:
    parser = StreamParser(log)
    stderr_lines: list[str] = []
    try:
        proc = subprocess.Popen(argv, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env if env is not None else engine_env(),
                                text=True, encoding="utf-8", errors="replace",
                                start_new_session=sys.platform != "win32")
    except FileNotFoundError:
        return EngineResult(error=f"claude-code executable not found: {argv[0]}", retryable=False)

    def read_stdout() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            parser.feed(line)

    def read_stderr() -> None:
        for line in proc.stderr:  # type: ignore[union-attr]
            stderr_lines.append(line)
            del stderr_lines[:-200]

    readers = [threading.Thread(target=read_stdout, daemon=True), threading.Thread(target=read_stderr, daemon=True)]
    for t in readers:
        t.start()
    try:
        proc.stdin.write(prompt)  # type: ignore[union-attr]
        proc.stdin.close()  # type: ignore[union-attr]
    except OSError:
        pass
    started = time.monotonic()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        proc.wait()
        for t in readers:
            t.join(5)
        res = parser.finish(-1, "")
        res.status, res.retryable = "failed", True
        res.error = f"claude-code timed out after {timeout}s"
        return res
    for t in readers:
        t.join(10)
    res = parser.finish(code, redact("".join(stderr_lines)))
    log(f"[claude-code] finished in {int(time.monotonic() - started)}s: {res.status}, {res.turns} turns"
        + (f", ${res.cost_usd:.4f}" if res.cost_usd is not None else ""), "system")
    return res


def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
