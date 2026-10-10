"""Arbitrary command execution: a special capability, denied by policy by default (ADR-006)."""

from __future__ import annotations

import shlex

from awf.tool_sdk.base import ToolContext, ToolError, ToolResult, ToolSpec
from awf.tool_sdk.command import run_command


def shell_exec(inp: dict, ctx: ToolContext) -> ToolResult:
    argv = shlex.split(inp["command"])
    if not ctx.policy.command_allowed(argv):
        raise ToolError(f"executable '{argv[0] if argv else ''}' is not in the command allowlist")
    result = run_command(argv, ctx.workspace, timeout=ctx.policy.config.limit("command_timeout_seconds", 600),
                         max_bytes=ctx.policy.config.limit("output_max_bytes", 200_000))
    return ToolResult("ok", result.summary())


TOOLS = [
    ToolSpec("shell.exec", "Run a command (argv, no shell). Requires the 'shell' permission; denied by default.",
             {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
             "shell.exec", shell_exec),
]
