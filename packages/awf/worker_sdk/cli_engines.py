"""Antigravity (Gemini), Qwen Code, Kimi Code, DeepSeek Harness and MiniMax Code as coding engines (ADR-013).

The platform side is the same as for Claude Code and Codex: the engine edits the job workspace, the worker then
builds and tests it, the Critic judges that evidence, and the worker commits (an engine that moves HEAD fails the
job). The prompt never goes on a command line (Windows cmd shims mangle long text): DeepSeek Harness and MiniMax
Code read it from stdin; the others are told to read a task file in the workspace (excluded from git, removed before
the commit).

Containment is weaker than Claude Code's per-command rules or Codex's OS sandbox: these CLIs approve every tool
call in headless mode (Antigravity with --dangerously-skip-permissions, Qwen Code with --approval-mode yolo, Kimi
Code's print mode, MiniMax Code with --permission full); DeepSeek Harness runs its experimental auto mode, where an
automatic reviewer approves or denies each call. What bounds them is the throwaway workspace, a credential-free environment except their own
key, the turn/time limits, and the platform's own verification. Qwen Code can add its sandbox
(AWF_QWEN_CODE_SANDBOX=1, needs Docker or Podman).
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable

from awf.agent_runtime.providers import PROVIDERS
from awf.security import redact, sanitized_env
from awf.worker_sdk.claude_code import EngineResult, StreamParser

TASK_FILE = ".awf-task.md"
POINTER = f"Read the file {TASK_FILE} in the current directory and carry out the task it describes."

ENGINE_RULES = """

## Platform rules for this job (they override anything above that conflicts)
You are running as the coding engine of the AI Work Factory, inside a fresh git checkout of the project.
- Edit files with your own tools. Ignore any mention of platform tools such as fs.read, build.run or
  submit_result: they belong to another runtime.
- Do not commit, push, create branches or modify .git, and do not edit or delete {task_file}. The platform
  commits and merges your changes.
- The project's commands are: {commands}. Do not install packages or use the network unless the task needs it.
- When you have finished, the platform runs the build and the tests itself; only those results decide
  whether the task is accepted. Do not claim results you did not observe.
- Finish with one final message in Markdown containing these sections: {sections}.
"""

# engine -> (executable, override variable, model variable, install hint, capability)
SPECS: dict[str, dict[str, str]] = {
    "antigravity": {"exe": "agy", "bin_env": "AWF_ANTIGRAVITY_BIN", "model_env": "AWF_ANTIGRAVITY_MODEL",
                    "install": "Antigravity CLI from antigravity.google/docs/getting-started, then sign in once with `agy`"},
    "qwen-code": {"exe": "qwen", "bin_env": "AWF_QWEN_CODE_BIN", "model_env": "",
                  "install": "Qwen Code (npm i -g @qwen-code/qwen-code)"},
    "kimi-code": {"exe": "kimi", "bin_env": "AWF_KIMI_CODE_BIN", "model_env": "AWF_KIMI_CODE_MODEL",
                  "install": "Kimi Code CLI from code.kimi.com, then `kimi` → /login once"},
    "deepseek-harness": {"exe": "dsh", "bin_env": "AWF_DEEPSEEK_HARNESS_BIN", "model_env": "", "stdin": "1",
                         "install": "DeepSeek Harness (npm i -g @deepseek-ai/dsh)"},
    "minimax-code": {"exe": "mcode", "bin_env": "AWF_MINIMAX_CODE_BIN", "model_env": "AWF_MINIMAX_CODE_MODEL",
                     "stdin": "1",
                     "install": "MiniMax Code (npm i -g @minimax-ai/code), then `mcode login` or `mcode provider set-minimax-key`"},
}
ENGINES = tuple(SPECS)


def engine_bin(engine: str) -> list[str] | None:
    spec = SPECS[engine]
    configured = os.environ.get(spec["bin_env"], "").strip()
    if configured:
        return [configured]
    found = shutil.which(spec["exe"])
    return [found] if found else None


def write_task(repo: Path, text: str, is_git: bool) -> Path:
    path = repo / TASK_FILE
    path.write_text(text, encoding="utf-8")
    if is_git:
        with (repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as fh:
            fh.write(f"\n/{TASK_FILE}\n")
    return path


def uses_stdin(engine: str) -> bool:
    return SPECS[engine].get("stdin") == "1"


def build_argv(engine: str, base: list[str], *, model: str, max_turns: int, timeout: int) -> list[str]:
    if engine == "deepseek-harness":
        # Launcher options first; the first token it does not know starts the headless app's arguments.
        # "-" = the task comes on stdin. The model is the Harness' own agentDefaultModel setting.
        return [*base, "--profile", "headless", "--json", "--permission-mode", "auto", "--approval-policy", "never", "-"]
    if engine == "minimax-code":
        chosen = os.environ.get(SPECS[engine]["model_env"], "").strip()
        argv = [*base, "exec", "--output-format", "json", "--permission", "full", "--timeout", f"{timeout}s",
                "--max-steps", str(max_turns), "--input", "-"]
        return argv + (["--model", chosen] if chosen else [])
    if engine == "antigravity":
        argv = [*base, "-p", POINTER, "--output-format", "stream-json", "--dangerously-skip-permissions",
                "--print-timeout", f"{timeout}s"]
        chosen = os.environ.get(SPECS[engine]["model_env"], "").strip()
        return argv + (["--model", chosen] if chosen else [])
    if engine == "qwen-code":
        argv = [*base, "-p", POINTER, "--output-format", "stream-json", "--approval-mode", "yolo",
                "--max-session-turns", str(max_turns), "--max-wall-time", f"{timeout}s", "--model", model]
        return argv + (["--sandbox"] if os.environ.get("AWF_QWEN_CODE_SANDBOX") == "1" else [])
    chosen = os.environ.get(SPECS[engine]["model_env"], "").strip()
    return [*base, "-p", POINTER, "--output-format", "stream-json"] + (["--model", chosen] if chosen else [])


def engine_env(engine: str, model: str) -> dict[str, str]:
    """The worker's environment without credentials, plus only what this engine needs."""
    env = sanitized_env()
    if engine == "antigravity":
        for name in PROVIDERS["google"].key_envs:  # used only when agy is set to modelProvider "gemini"
            if os.environ.get(name):
                env[name] = os.environ[name]
    elif engine == "qwen-code":
        qwen = PROVIDERS["qwen"]
        env.update({"OPENAI_API_KEY": qwen.key(), "OPENAI_BASE_URL": qwen.endpoint(), "OPENAI_MODEL": model})
    elif engine in ("kimi-code", "deepseek-harness", "minimax-code"):
        provider = {"kimi-code": "kimi", "deepseek-harness": "deepseek", "minimax-code": "minimax"}[engine]
        for name in PROVIDERS[provider].key_envs:
            if os.environ.get(name):
                env[name] = os.environ[name]
    return env


class AntigravityParser:
    """`agy -p --output-format stream-json`: events {"event": "init" | "step_update" | "result", ...}."""

    def __init__(self, log: Callable[[str, str], None]):
        self.log = log
        self.result = EngineResult()
        self.final: dict[str, Any] | None = None
        self.texts: list[str] = []

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            self.log(redact(line[:500]), "stdout")
            return
        kind = event.get("event")
        if kind == "init":
            self.result.session_id = event.get("conversation_id")
        elif kind == "step_update":
            step = event.get("step_update") or {}
            if step.get("text_delta"):
                self.texts.append(step["text_delta"])
            tool = step.get("tool_info") or {}
            if tool and step.get("state") == "DONE":
                name = tool.get("name") or tool.get("tool_name") or step.get("step_type") or "tool"
                self.result.turns += 1
                self.result.tool_calls.append({"tool": f"antigravity.{name}", "status": "ok",
                                               "target": str(tool.get("path") or tool.get("command") or "")[:200]})
                self.log(redact(f"[antigravity] {name}"), "stdout")
        elif kind == "result":
            self.final = event.get("result") or {}

    def finish(self, exit_code: int, stderr_tail: str) -> EngineResult:
        res, final = self.result, self.final or {}
        usage = final.get("usage") or {}
        res.input_tokens = int(usage.get("input_tokens") or 0)
        res.output_tokens = int(usage.get("output_tokens") or 0) + int(usage.get("thinking_tokens") or 0)
        res.turns = int(final.get("num_turns") or res.turns)
        if final.get("status") == "SUCCESS" and exit_code == 0:
            res.status, res.text = "succeeded", final.get("response") or "".join(self.texts)
            return res
        reason = final.get("error") or stderr_tail.strip()[-1500:] or f"agy exited with code {exit_code}"
        res.error = f"antigravity {final.get('status') or 'failed'}: {reason}"[:2000]
        res.retryable = "authentication required" not in str(reason).lower() and "unknown model" not in str(reason).lower()
        return res


class KimiParser:
    """`kimi -p --output-format stream-json`: one OpenAI-style message per line (assistant, tool)."""

    def __init__(self, log: Callable[[str, str], None]):
        self.log = log
        self.result = EngineResult()
        self.last_text = ""

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            self.log(redact(line[:500]), "stdout")
            return
        if msg.get("role") != "assistant":
            return
        content = msg.get("content")
        if isinstance(content, list):  # content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if content:
            self.last_text = content
            self.log(redact("[kimi-code] " + content.strip().splitlines()[0][:300]), "stdout")
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") or {}
            self.result.turns += 1
            self.result.tool_calls.append({"tool": f"kimi.{fn.get('name', 'tool')}", "status": "ok",
                                           "target": str(fn.get("arguments") or "")[:200]})

    def finish(self, exit_code: int, stderr_tail: str) -> EngineResult:
        res = self.result
        if exit_code == 0:
            res.status, res.text = "succeeded", self.last_text
            return res
        res.error = f"kimi-code exited with code {exit_code}: {stderr_tail.strip()[-1500:]}"[:2000]
        res.retryable = exit_code == 75  # 1 = permanent (config, auth, quota), 75 = transient
        return res


class HarnessParser:
    """`dsh --profile headless --json`: NDJSON events session, status, tool_call, tool_result, text, thinking, final,
    error. Success is exit 0 and final.reason.kind == "completed" (a failed turn still ends with a final event)."""

    def __init__(self, log: Callable[[str, str], None]):
        self.log = log
        self.result = EngineResult()
        self.final: dict[str, Any] | None = None
        self.error: str | None = None
        self.texts: list[str] = []

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            self.log(redact(line[:500]), "stdout")
            return
        kind = event.get("type") or event.get("event")
        if kind == "session":
            self.result.session_id = event.get("sessionId") or event.get("id")
        elif kind == "tool_call":
            name = event.get("name") or (event.get("tool") or {}).get("name") or "tool"
            self.result.turns += 1
            self.result.tool_calls.append({"tool": f"dsh.{name}", "status": "ok",
                                           "target": json.dumps(event.get("input") or event.get("args") or {})[:200]})
            self.log(redact(f"[deepseek-harness] {name}"), "stdout")
        elif kind == "tool_result" and event.get("error"):
            err = event["error"]
            if self.result.tool_calls:
                denied = err.get("code") == "AUTO_REVIEW_DENIED"
                self.result.tool_calls[-1]["status"] = "engine_denied" if denied else "error"
        elif kind == "text" and event.get("text"):
            self.texts.append(event["text"])
        elif kind == "final":
            self.final = event
        elif kind == "error":
            self.error = str(event.get("message") or event.get("error") or "error")

    def finish(self, exit_code: int, stderr_tail: str) -> EngineResult:
        res, final = self.result, self.final or {}
        reason = final.get("reason") or {}
        kind = reason.get("kind") if isinstance(reason, dict) else reason
        answer = final.get("answer") or final.get("text") or final.get("output") or "".join(self.texts[-1:])
        if exit_code == 0 and kind == "completed":
            res.status, res.text = "succeeded", answer or ""
            return res
        why = self.error or (isinstance(reason, dict) and reason.get("message")) or stderr_tail.strip()[-1500:] \
            or f"dsh exited with code {exit_code} ({kind or 'no final event'})"
        res.error = f"deepseek-harness: {why}"[:2000]
        res.retryable = not any(s in str(why).lower() for s in ("401", "unauthorized", "api key", "insufficient"))
        return res


class MiniMaxCodeParser:
    """`mcode exec --output-format json`: one exec.result object (status, output, usage) at the end."""

    _NO_RETRY_EXITS = {2, 3}  # invocation/input error, configuration error

    def __init__(self, log: Callable[[str, str], None]):
        self.log = log
        self.result = EngineResult()
        self.lines: list[str] = []

    def feed(self, line: str) -> None:
        if line.strip():
            self.lines.append(line)

    def finish(self, exit_code: int, stderr_tail: str) -> EngineResult:
        res = self.result
        doc: dict[str, Any] = {}
        text = "".join(self.lines).strip()
        for candidate in (text, *reversed(self.lines)):
            try:
                parsed = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict):
                doc = parsed
                break
        usage = doc.get("usage") or {}
        res.input_tokens = int(usage.get("inputTokens") or 0)
        res.output_tokens = int(usage.get("outputTokens") or 0)
        res.session_id = doc.get("sessionId")
        if exit_code == 0 and doc.get("status") == "succeeded":
            res.status, res.text = "succeeded", str(doc.get("output") or "")
            self.log(f"[minimax-code] {doc.get('status')} ({(doc.get('model') or {}).get('modelId', '')})", "system")
            return res
        err = doc.get("error")
        why = (err.get("message") if isinstance(err, dict) else err) or stderr_tail.strip()[-1500:] \
            or f"mcode exited with code {exit_code}"
        res.error = f"minimax-code {doc.get('status') or 'failed'}: {why}"[:2000]
        res.retryable = exit_code not in self._NO_RETRY_EXITS
        return res


def make_parser(engine: str, log: Callable[[str, str], None]):
    if engine == "deepseek-harness":
        return HarnessParser(log)
    if engine == "minimax-code":
        return MiniMaxCodeParser(log)
    if engine == "antigravity":
        return AntigravityParser(log)
    if engine == "kimi-code":
        return KimiParser(log)
    return StreamParser(log, name=engine)  # Qwen Code's stream-json follows Claude Code's message format
