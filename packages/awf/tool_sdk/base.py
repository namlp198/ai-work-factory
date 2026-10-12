"""Typed tool abstraction (Design §10, ADR-006). Agents never get a raw shell by default."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from awf.policy import PolicyEngine


class ToolError(Exception):
    """Expected tool failure; reported back to the model as an error result."""


class PathViolation(ToolError):
    pass


@dataclass
class ToolContext:
    workspace: Path
    permissions: dict[str, Any]
    policy: PolicyEngine
    repository: dict[str, Any] = field(default_factory=dict)
    base_ref: str | None = None
    # Evidence collected by worker-executed tools (build/test/lint). Never filled from model claims.
    evidence: dict[str, Any] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    approved_actions: set[str] = field(default_factory=set)
    log: Callable[[str, str], None] | None = None

    def emit(self, line: str, stream: str = "stdout") -> None:
        if self.log:
            self.log(line, stream)


@dataclass
class ToolResult:
    status: str  # ok | error | denied
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def for_model(self) -> str:
        """Tool output as sent to the model. Secrets are redacted: they never enter a prompt."""
        import json

        from awf.security import redact

        payload = {"status": self.status, **self.data}
        if self.message:
            payload["message"] = self.message
        return redact(json.dumps(payload, ensure_ascii=False, default=str))


Handler = Callable[[dict[str, Any], ToolContext], ToolResult]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    action: str
    handler: Handler

    @property
    def model_name(self) -> str:
        # Model-facing tool names must match ^[a-zA-Z0-9_-]{1,64}$
        return self.name.replace(".", "_")


def now_ms() -> int:
    return int(time.monotonic() * 1000)


def resolve_in_workspace(workspace: Path, relative: str) -> Path:
    """Path traversal protection: the resolved path must stay inside the workspace and outside .git."""
    if relative is None or "\x00" in str(relative):
        raise PathViolation("invalid path")
    root = workspace.resolve()
    text = str(relative).replace("\\", "/")
    rel = Path(text)
    # Same rule on every OS: no POSIX-absolute, drive-letter (C:/...) or UNC paths.
    if rel.is_absolute() or rel.drive or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        raise PathViolation("absolute paths are not allowed; use paths relative to the workspace")
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise PathViolation(f"path escapes the workspace: {relative}")
    parts = target.relative_to(root).parts
    if parts and parts[0] == ".git":
        raise PathViolation("access to .git is not allowed; use git tools")
    return target
