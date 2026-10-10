"""Read-only git tools for agents. Commits/branches/pushes are done by the worker, not the model."""

from __future__ import annotations

import re

from awf.tool_sdk.base import ToolContext, ToolError, ToolResult, ToolSpec
from awf.tool_sdk.command import git

_SAFE_REF = re.compile(r"^[A-Za-z0-9._/\-]+$")


def diff_stats(numstat: str) -> dict:
    files = insertions = deletions = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            files += 1
            insertions += int(parts[0]) if parts[0].isdigit() else 0
            deletions += int(parts[1]) if parts[1].isdigit() else 0
    return {"files_changed": files, "insertions": insertions, "deletions": deletions}


def git_status(inp: dict, ctx: ToolContext) -> ToolResult:
    out = git(["status", "--porcelain=v1"], ctx.workspace).stdout
    return ToolResult("ok", {"changes": out.splitlines()})


def git_diff(inp: dict, ctx: ToolContext) -> ToolResult:
    """Diff of the workspace against the task base (default) or HEAD."""
    against = inp.get("against") or "base"
    if against == "base":
        ref = ctx.base_ref or "HEAD"
    elif against == "head":
        ref = "HEAD"
    else:
        raise ToolError("against must be 'base' or 'head'")
    if not _SAFE_REF.match(ref):
        raise ToolError("unsafe ref")
    paths = [p for p in (inp.get("paths") or []) if isinstance(p, str)]
    git(["add", "-A", "--intent-to-add"], ctx.workspace, check=False)
    args = ["diff", ref, "--"] + paths
    diff = git(args, ctx.workspace).stdout
    stats = diff_stats(git(["diff", "--numstat", ref, "--"] + paths, ctx.workspace).stdout)
    max_chars = 120_000
    return ToolResult("ok", {"base": ref, **stats, "diff": diff[:max_chars], "truncated": len(diff) > max_chars})


def git_log(inp: dict, ctx: ToolContext) -> ToolResult:
    limit = min(int(inp.get("limit") or 20), 200)
    out = git(["log", f"-{limit}", "--pretty=format:%h %s"], ctx.workspace).stdout
    return ToolResult("ok", {"commits": out.splitlines()})


TOOLS = [
    ToolSpec("git.status", "Show changed files in the workspace.",
             {"type": "object", "properties": {}}, "git.read", git_status),
    ToolSpec("git.diff", "Show the diff of the workspace against the task base branch ('base') or HEAD ('head').",
             {"type": "object", "properties": {
                 "against": {"type": "string", "enum": ["base", "head"], "default": "base"},
                 "paths": {"type": "array", "items": {"type": "string"}}}},
             "git.read", git_diff),
    ToolSpec("git.log", "Show recent commits.",
             {"type": "object", "properties": {"limit": {"type": "integer"}}}, "git.read", git_log),
]
