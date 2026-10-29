"""Filesystem/docs tools scoped to the job workspace."""

from __future__ import annotations

import fnmatch
import os
import re

from awf.tool_sdk.base import ToolContext, ToolError, ToolResult, ToolSpec, resolve_in_workspace

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache", "build", "dist"}


def _limits(ctx: ToolContext, name: str, default: int) -> int:
    return ctx.policy.config.limit(name, default)


def fs_list(inp: dict, ctx: ToolContext) -> ToolResult:
    root = resolve_in_workspace(ctx.workspace, inp.get("path") or ".")
    if not root.is_dir():
        raise ToolError(f"not a directory: {inp.get('path')}")
    pattern = inp.get("glob") or "*"
    max_entries = min(int(inp.get("max_entries") or 500), 2000)
    ws = ctx.workspace.resolve()
    entries: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fname in sorted(filenames):
            if fnmatch.fnmatch(fname, pattern):
                rel = os.path.relpath(os.path.join(dirpath, fname), ws).replace("\\", "/")
                entries.append(rel)
                if len(entries) >= max_entries:
                    return ToolResult("ok", {"files": entries, "truncated": True})
        if not inp.get("recursive", True):
            break
    return ToolResult("ok", {"files": entries, "truncated": False})


def fs_read(inp: dict, ctx: ToolContext) -> ToolResult:
    path = resolve_in_workspace(ctx.workspace, inp["path"])
    if not path.is_file():
        raise ToolError(f"file not found: {inp['path']}")
    max_bytes = _limits(ctx, "file_read_max_bytes", 400_000)
    data = path.read_bytes()
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    start = int(inp.get("start_line") or 1)
    end = inp.get("end_line")
    if start > 1 or end:
        lines = text.splitlines()
        text = "\n".join(lines[start - 1: int(end) if end else None])
    return ToolResult("ok", {"path": inp["path"], "content": text, "truncated": truncated})


def fs_write(inp: dict, ctx: ToolContext) -> ToolResult:
    path = resolve_in_workspace(ctx.workspace, inp["path"], write=True)
    content = inp.get("content", "")
    max_bytes = _limits(ctx, "file_write_max_bytes", 400_000)
    encoded = content.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ToolError(f"content exceeds {max_bytes} bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    path.write_bytes(encoded)
    return ToolResult("ok", {"path": inp["path"], "bytes": len(encoded), "created": not existed})


def fs_replace(inp: dict, ctx: ToolContext) -> ToolResult:
    path = resolve_in_workspace(ctx.workspace, inp["path"], write=True)
    if not path.is_file():
        raise ToolError(f"file not found: {inp['path']}")
    # Bytes in/out so line endings are preserved exactly (no platform newline translation).
    text = path.read_bytes().decode("utf-8")
    old, new = inp["old_text"], inp["new_text"]
    if text.count(old) == 0 and "\r\n" in text and "\r\n" not in old:
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    count = text.count(old)
    if count != 1:
        raise ToolError(f"old_text must match exactly once (matched {count} times)")
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
    return ToolResult("ok", {"path": inp["path"], "replaced": 1})


def fs_search(inp: dict, ctx: ToolContext) -> ToolResult:
    root = resolve_in_workspace(ctx.workspace, inp.get("path") or ".")
    try:
        regex = re.compile(inp["pattern"])
    except re.error as exc:
        raise ToolError(f"invalid regex: {exc}") from exc
    glob = inp.get("glob") or "*"
    ws = ctx.workspace.resolve()
    matches: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fname in sorted(filenames):
            if not fnmatch.fnmatch(fname, glob):
                continue
            fpath = os.path.join(dirpath, fname)
            try:
                with open(fpath, encoding="utf-8") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if regex.search(line):
                            matches.append({"file": os.path.relpath(fpath, ws).replace("\\", "/"),
                                            "line": lineno, "text": line.rstrip()[:300]})
                            if len(matches) >= 200:
                                return ToolResult("ok", {"matches": matches, "truncated": True})
            except (UnicodeDecodeError, OSError):
                continue
    return ToolResult("ok", {"matches": matches, "truncated": False})


TOOLS = [
    ToolSpec("fs.list", "List files in the workspace (relative paths).",
             {"type": "object", "properties": {
                 "path": {"type": "string", "description": "Directory relative to workspace root", "default": "."},
                 "glob": {"type": "string", "description": "Filename glob, e.g. *.py"},
                 "recursive": {"type": "boolean", "default": True},
                 "max_entries": {"type": "integer"}}},
             "fs.read", fs_list),
    ToolSpec("fs.read", "Read a text file from the workspace.",
             {"type": "object", "properties": {
                 "path": {"type": "string"},
                 "start_line": {"type": "integer"},
                 "end_line": {"type": "integer"}}, "required": ["path"]},
             "fs.read", fs_read),
    ToolSpec("fs.search", "Search files with a regular expression.",
             {"type": "object", "properties": {
                 "pattern": {"type": "string"},
                 "path": {"type": "string"},
                 "glob": {"type": "string"}}, "required": ["pattern"]},
             "fs.read", fs_search),
    ToolSpec("fs.write", "Create or overwrite a text file in the workspace.",
             {"type": "object", "properties": {
                 "path": {"type": "string"},
                 "content": {"type": "string"}}, "required": ["path", "content"]},
             "fs.write", fs_write),
    ToolSpec("fs.replace", "Replace one exact occurrence of old_text with new_text in a file.",
             {"type": "object", "properties": {
                 "path": {"type": "string"},
                 "old_text": {"type": "string"},
                 "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]},
             "fs.write", fs_replace),
]
