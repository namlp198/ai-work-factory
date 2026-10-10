"""Build/test/lint wrappers. Commands come from project configuration, never from the model.

The results are recorded as *evidence* in the tool context; the Critic checks completion rules
(build_pass, unit_test_pass, lint_pass) against this evidence, not against what the agent claims.
"""

from __future__ import annotations

import re
import shlex

from awf.tool_sdk.base import ToolContext, ToolError, ToolResult, ToolSpec
from awf.tool_sdk.command import run_command

_PYTEST_COUNTS = re.compile(r"(\d+) (passed|failed|error|errors|skipped)")
# dotnet test: "Passed!  - Failed:     0, Passed:     5, Skipped:     0, Total:     5"
_DOTNET_COUNTS = re.compile(r"\b(Passed|Failed|Skipped):\s+(\d+)")


def configured_command(ctx: ToolContext, key: str) -> list[str] | None:
    value = ctx.repository.get(key)
    if not value:
        return None
    argv = shlex.split(value) if isinstance(value, str) else [str(v) for v in value]
    return argv or None


def parse_test_counts(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for num, kind in _PYTEST_COUNTS.findall(output):
        key = {"error": "errors"}.get(kind, kind)
        counts[key] = max(counts[key], int(num))
    for kind, num in _DOTNET_COUNTS.findall(output):
        counts[kind.lower()] = max(counts[kind.lower()], int(num))
    return counts


def _run(kind: str, key: str, inp: dict, ctx: ToolContext) -> ToolResult:
    argv = configured_command(ctx, key)
    if not argv:
        ctx.evidence[kind] = {"status": "not_configured"}
        return ToolResult("ok", {"status": "not_configured"},
                          f"no {key} configured for this repository; nothing to run")
    if not ctx.policy.command_allowed(argv):
        raise ToolError(f"executable '{argv[0]}' is not in the command allowlist")
    timeout = ctx.policy.config.limit("command_timeout_seconds", 600)
    max_bytes = ctx.policy.config.limit("output_max_bytes", 200_000)
    ctx.emit(f"$ {' '.join(argv)}")
    result = run_command(argv, ctx.workspace, timeout=timeout, max_bytes=max_bytes)
    for line in (result.stdout + result.stderr).splitlines()[-40:]:
        ctx.emit(line)
    evidence = {"status": "pass" if result.ok else "fail", "exit_code": result.exit_code,
                "duration_ms": result.duration_ms, "timed_out": result.timed_out}
    if kind == "test":
        evidence.update(parse_test_counts(result.stdout + "\n" + result.stderr))
    ctx.evidence[kind] = evidence
    history = ctx.evidence.setdefault(f"{kind}_history", [])
    history.append(evidence)
    return ToolResult("ok", {**evidence, **result.summary()})


def build_run(inp: dict, ctx: ToolContext) -> ToolResult:
    return _run("build", "build_command", inp, ctx)


def test_run(inp: dict, ctx: ToolContext) -> ToolResult:
    return _run("test", "test_command", inp, ctx)


def lint_run(inp: dict, ctx: ToolContext) -> ToolResult:
    return _run("lint", "lint_command", inp, ctx)


TOOLS = [
    ToolSpec("build.run", "Run the project's configured build command in the workspace.",
             {"type": "object", "properties": {}}, "build.run", build_run),
    ToolSpec("test.run", "Run the project's configured test command in the workspace and record test evidence.",
             {"type": "object", "properties": {}}, "test.run", test_run),
    ToolSpec("lint.run", "Run the project's configured lint/static analysis command.",
             {"type": "object", "properties": {}}, "lint.run", lint_run),
]
