"""Agent runtime: the bounded tool-use loop that turns a task into a contract-valid submission.

The loop is bounded by max_turns and a wall-clock timeout; it never lets an agent chat indefinitely.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from awf.agent_runtime.adapters import ModelAdapter, ModelError, ToolDef, ToolOutcome
from awf.agent_runtime.adapters.scripted import SUBMIT_TOOL
from awf.agent_runtime.definitions import AgentContract, submit_tool_schema
from awf.agent_runtime.validation import validate_submission
from awf.tool_sdk import ToolContext, ToolRegistry

RUNTIME_RULES = """

## Runtime rules (enforced by the platform)
- You work inside an isolated workspace. Paths are relative to the workspace root.
- Use only the tools provided. Commands you cannot run through a tool are not available to you.
- Build/test results are recorded by the platform as evidence; do not claim results you did not obtain.
- When finished, call `submit_result` exactly once with every required output. If the platform rejects
  the submission, fix the listed problems and call `submit_result` again.
"""


@dataclass
class AgentResult:
    status: str  # succeeded | failed
    summary: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    decision: str | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    retryable: bool = True
    duration_ms: int = 0


class AgentRuntime:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def tool_defs(self, contract: AgentContract) -> list[ToolDef]:
        defs = [ToolDef(name=s.model_name, description=s.description, input_schema=s.input_schema)
                for s in self.registry.specs_for(contract.allowed_tools)]
        defs.append(ToolDef(
            name=SUBMIT_TOOL,
            description="Submit the final, contract-compliant result of this task. Call once when done.",
            input_schema=submit_tool_schema(contract),
        ))
        return defs

    def run(self, *, contract: AgentContract, system_prompt: str, user_message: str, adapter: ModelAdapter,
            model: str, params: dict[str, Any], tool_ctx: ToolContext, meta: dict[str, Any]) -> AgentResult:
        started = time.monotonic()
        max_turns = contract.timeout_policy.max_turns
        deadline = started + contract.timeout_policy.seconds
        result = AgentResult(status="failed")
        try:
            session = adapter.start(system=system_prompt + RUNTIME_RULES, user_message=user_message,
                                    tools=self.tool_defs(contract), model=model, params=params, meta=meta)
            outcomes: list[ToolOutcome] | None = None
            nudge: str | None = None
            for turn_no in range(1, max_turns + 1):
                if time.monotonic() > deadline:
                    result.error = f"timeout after {contract.timeout_policy.seconds}s"
                    break
                turn = session.send(outcomes, nudge)
                result.turns = turn_no
                result.input_tokens += turn.input_tokens
                result.output_tokens += turn.output_tokens
                outcomes, nudge = [], None
                if not turn.tool_calls:
                    nudge = "You have not submitted a result yet. Continue the task, then call submit_result."
                    continue
                submission = None
                for call in turn.tool_calls:
                    if call.name == SUBMIT_TOOL:
                        submission = call
                        continue
                    tool_ctx.emit(f"[tool] {call.name}")
                    res = self.registry.execute(call.name, call.input, tool_ctx, allowed=contract.allowed_tools)
                    outcomes.append(ToolOutcome(call.id, res.for_model(), is_error=res.status != "ok"))
                if submission is not None:
                    problems = validate_submission(contract, submission.input)
                    if not problems:
                        result.status = "succeeded"
                        result.summary = submission.input.get("summary", "")
                        result.outputs = submission.input.get("outputs", {})
                        result.decision = submission.input.get("decision")
                        result.findings = submission.input.get("findings", []) or []
                        break
                    tool_ctx.calls.append({"tool": SUBMIT_TOOL, "status": "rejected",
                                           "problems": [p.as_dict() for p in problems]})
                    text = "Submission rejected:\n" + "\n".join(f"- [{p.code}] {p.message}" for p in problems)
                    outcomes.append(ToolOutcome(submission.id, text, is_error=True))
            else:
                result.error = f"no valid submission within {max_turns} turns"
        except ModelError as exc:
            result.error = str(exc)
            result.retryable = exc.retryable
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result
