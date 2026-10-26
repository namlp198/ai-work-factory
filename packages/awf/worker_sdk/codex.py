"""Codex (OpenAI) as a coding engine (ADR-012): `codex exec --json` drives the edit loop in the job workspace.

Same division of labour as the Claude Code engine (ADR-011): the engine only edits files; the prompt and the
model come from the control plane, the worker builds and tests the result itself and the Critic judges that
evidence, and the worker commits. What differs is how the engine is contained:
- Codex runs every command in its own OS sandbox (`--sandbox workspace-write`): writes stay inside the
  workspace, network access is off, and approvals are `never` (nothing waits for a human). It does not limit
  *which* commands run inside that sandbox, unlike Claude Code's per-command rules.
- The engine gets its own empty CODEX_HOME per job and `--ignore-user-config --ignore-rules`: no saved login,
  user config, rules or history from the worker machine. It authenticates with the API key only.
- Commands it runs see only a core environment (`shell_environment_policy.inherit=core`), never the key.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable

from awf.security import redact, sanitized_env
from awf.worker_sdk.claude_code import EngineResult

ENGINE = "codex"
CAPABILITY = "codex"

ENGINE_RULES = """

## Platform rules for this job (they override anything above that conflicts)
You are running as the coding engine of the AI Work Factory, inside a fresh git checkout of the project.
- Edit files with your own tools. Ignore any mention of platform tools such as fs.read, build.run or
  submit_result: they belong to another runtime.
- Do not commit, push, create branches or modify .git. The platform commits and merges your changes.
- There is no network access. The project's commands are: {commands}.
- When you have finished, the platform runs the build and the tests itself; only those results decide
  whether the task is accepted. Do not claim results you did not observe.
- Finish with one final message in Markdown containing these sections: {sections}.
"""


def codex_bin() -> list[str] | None:
    """The `codex` executable: AWF_CODEX_BIN, else the one on PATH."""
    configured = os.environ.get("AWF_CODEX_BIN", "").strip()
    if configured:
        return [configured]
    found = shutil.which("codex")
    return [found] if found else None


def build_argv(base: list[str], *, model: str, effort: str | None) -> list[str]:
    # -c values are TOML; bare words that are not valid TOML are read as strings, so no quotes are needed (quotes
    # would not survive the Windows cmd shim of an npm-installed codex).
    argv = [*base, "exec", "--json", "--model", model, "--sandbox", "workspace-write", "--ephemeral",
            "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--color", "never",
            "-c", "approval_policy=never", "-c", "web_search=disabled",
            "-c", "sandbox_workspace_write.network_access=false", "-c", "history.persistence=none",
            "-c", "shell_environment_policy.inherit=core"]
    if effort:
        argv += ["-c", f"model_reasoning_effort={effort}"]
    return [*argv, "-"]  # the prompt comes on stdin


def engine_env(codex_home: Path) -> dict[str, str]:
    """No credentials from the worker except the OpenAI key, passed the way `codex exec` reads it."""
    env = sanitized_env()
    key = os.environ.get("CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if key:
        env["CODEX_API_KEY"] = key
    if os.environ.get("OPENAI_BASE_URL"):
        env["OPENAI_BASE_URL"] = os.environ["OPENAI_BASE_URL"]
    codex_home.mkdir(parents=True, exist_ok=True)
    env["CODEX_HOME"] = str(codex_home)
    return env


class CodexParser:
    """Folds `codex exec --json` events (JSON lines) into an EngineResult and a readable job log."""

    def __init__(self, log: Callable[[str, str], None]):
        self.log = log
        self.result = EngineResult()
        self.messages: list[str] = []
        self.completed = False
        self.failure: str | None = None

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
        if kind == "thread.started":
            self.result.session_id = event.get("thread_id")
        elif kind == "item.completed":
            self._item(event.get("item") or {})
        elif kind == "turn.completed":
            self.completed = True
            usage = event.get("usage") or {}
            self.result.input_tokens += int(usage.get("input_tokens") or 0)
            self.result.output_tokens += int(usage.get("output_tokens") or 0)
        elif kind == "turn.failed":
            self.failure = str((event.get("error") or {}).get("message") or "turn failed")
        elif kind == "error":
            self.failure = str(event.get("message") or "error")

    def _item(self, item: dict[str, Any]) -> None:
        kind = item.get("type")
        if kind == "agent_message" and item.get("text"):
            self.messages.append(item["text"])
            self.log(redact("[codex] " + item["text"].strip().splitlines()[0][:300]), "stdout")
        elif kind == "command_execution":
            self.result.turns += 1
            failed = item.get("status") == "failed" or (item.get("exit_code") not in (None, 0))
            command = str(item.get("command") or "")[:200]
            self.result.tool_calls.append({"tool": "codex.command", "status": "error" if failed else "ok",
                                           "target": command})
            self.log(redact(f"[codex] $ {command} (exit {item.get('exit_code')})"), "stdout")
        elif kind == "file_change":
            self.result.turns += 1
            paths = ", ".join(str(c.get("path")) for c in item.get("changes") or [])[:200]
            self.result.tool_calls.append({"tool": "codex.file_change", "status": "ok", "target": paths})
            self.log(redact(f"[codex] edit {paths}"), "stdout")

    def finish(self, exit_code: int, stderr_tail: str) -> EngineResult:
        res = self.result
        if self.failure or not self.completed or exit_code not in (0, None):
            reason = self.failure or (stderr_tail.strip()[-1500:] if stderr_tail.strip() else
                                      f"codex exited with code {exit_code} without finishing the turn")
            res.status = "failed"
            res.error = f"codex: {reason}"[:2000]
            lowered = reason.lower()
            res.retryable = not any(s in lowered for s in ("401", "unauthorized", "invalid api key", "incorrect api key",
                                                           "insufficient_quota", "model_not_found", "does not exist"))
            return res
        res.status, res.text = "succeeded", (self.messages[-1] if self.messages else "")
        return res
