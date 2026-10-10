"""Tool registry: schema validation -> policy evaluation -> execution -> call record."""

from __future__ import annotations

import time
from typing import Any

import jsonschema

from awf.policy import Verdict
from awf.security import redact_obj
from awf.tool_sdk import build, fs, git_tools, shell
from awf.tool_sdk.base import ToolContext, ToolError, ToolResult, ToolSpec


class ToolRegistry:
    def __init__(self, specs: list[ToolSpec] | None = None):
        self._tools: dict[str, ToolSpec] = {}
        for spec in specs if specs is not None else default_tools():
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool '{spec.name}'")
        self._tools[spec.name] = spec

    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> ToolSpec:
        if name in self._tools:
            return self._tools[name]
        for spec in self._tools.values():
            if spec.model_name == name:
                return spec
        raise KeyError(name)

    def specs_for(self, allowed: list[str]) -> list[ToolSpec]:
        return [self._tools[n] for n in allowed if n in self._tools]

    def execute(self, name: str, tool_input: dict[str, Any], ctx: ToolContext,
                allowed: list[str] | None = None) -> ToolResult:
        started = time.monotonic()
        record: dict[str, Any] = {"tool": name, "input": _summarize_input(tool_input)}
        try:
            spec = self.get(name)
            record["tool"] = spec.name
            if allowed is not None and spec.name not in allowed:
                result = ToolResult("denied", message=f"tool '{spec.name}' is not in this role's tool set")
                record["policy"] = {"verdict": "DENY", "reason": "not in allowed_tools"}
            else:
                decision = ctx.policy.evaluate(spec.action, ctx.permissions, ctx.approved_actions)
                record["policy"] = decision.as_dict()
                if not decision.allowed:
                    hint = (" Ask for human approval through your result summary instead."
                            if decision.verdict == Verdict.REQUIRE_APPROVAL else "")
                    result = ToolResult("denied", message=f"policy {decision.verdict}: {decision.reason}.{hint}")
                else:
                    jsonschema.validate(tool_input, spec.input_schema)
                    result = spec.handler(tool_input, ctx)
        except KeyError:
            result = ToolResult("error", message=f"unknown tool '{name}'")
        except jsonschema.ValidationError as exc:
            result = ToolResult("error", message=f"invalid input: {exc.message}")
        except ToolError as exc:
            result = ToolResult("error", message=str(exc))
        except Exception as exc:  # tool bugs must not crash the agent loop
            result = ToolResult("error", message=f"{type(exc).__name__}: {exc}")
        record["status"] = result.status
        record["duration_ms"] = int((time.monotonic() - started) * 1000)
        if result.message:
            record["message"] = result.message[:500]
        ctx.calls.append(redact_obj(record))
        return result


def _summarize_input(tool_input: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (tool_input or {}).items():
        if isinstance(value, str) and len(value) > 200:
            out[key] = f"<{len(value)} chars>"
        else:
            out[key] = value
    return out


def default_tools() -> list[ToolSpec]:
    return [*fs.TOOLS, *git_tools.TOOLS, *build.TOOLS, *shell.TOOLS]


# Tools that run as standalone worker jobs (workflow `tool:` stages), not inside an agent loop.
JOB_TOOLS = ("lint.run", "test.run", "build.run", "git.export_patch")
