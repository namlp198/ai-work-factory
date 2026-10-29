"""Critic: cross-domain quality control (Plan §5.4, Design §14).

The gate is rule + finding severity based, never a 0-10 score:
  any BLOCKER -> BLOCK, any MAJOR -> CHANGES_REQUIRED, otherwise PASS.

Separately from output quality, the critic computes the *verdict* of reviewer/QA/tool tasks
(APPROVE / CHANGES_REQUIRED). A verdict that contradicts platform evidence is overridden.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from awf.agent_runtime import AgentContract
from awf.agent_runtime.validation import Finding, validate_outputs
from awf.domain.enums import CriticDecision
from awf.security import REDACTED, REDACTED_LITERAL

APPROVE = "APPROVE"
CHANGES_REQUIRED = "CHANGES_REQUIRED"
POLICY_ABUSE_THRESHOLD = 5


@dataclass
class CriticResult:
    decision: CriticDecision
    findings: list[Finding] = field(default_factory=list)
    verdict: str | None = None  # APPROVE / CHANGES_REQUIRED for roles with a decision, and tool tasks
    recommended_next_action: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "verdict": self.verdict,
            "findings": [f.as_dict() for f in self.findings],
            "recommended_next_action": self.recommended_next_action,
        }


def _decide(findings: list[Finding]) -> CriticDecision:
    if any(f.severity == "BLOCKER" for f in findings):
        return CriticDecision.BLOCK
    if any(f.severity == "MAJOR" for f in findings):
        return CriticDecision.CHANGES_REQUIRED
    return CriticDecision.PASS


def _check_rule(rule: str, evidence: dict[str, Any]) -> Finding | None:
    key = {"build_pass": "build", "unit_test_pass": "test", "lint_pass": "lint"}.get(rule)
    if key:
        ev = evidence.get(key)
        if not ev:
            return Finding("MAJOR", "MISSING_ACCEPTANCE_EVIDENCE",
                           f"completion rule '{rule}' requires running {key}.run; no evidence recorded")
        if ev.get("status") == "not_configured":
            return Finding("MINOR", "EVIDENCE_NOT_CONFIGURED", f"no {key} command configured; '{rule}' not verifiable")
        if ev.get("status") != "pass":
            return Finding("MAJOR", "COMPLETION_RULE_FAILED",
                           f"completion rule '{rule}' failed (exit code {ev.get('exit_code')})")
        if key == "test" and ev.get("failed", 0):
            return Finding("MAJOR", "COMPLETION_RULE_FAILED", f"{ev.get('failed')} test(s) failed")
        return None
    if rule == "tests_executed":
        ev = evidence.get("test")
        if not ev:
            return Finding("MAJOR", "MISSING_ACCEPTANCE_EVIDENCE", "verdict requires running test.run; no evidence recorded")
        return None
    if rule == "has_changes" and not evidence.get("has_changes"):
        return Finding("MAJOR", "NO_CHANGES", "task requires source changes but none were committed")
    return None


_TEST_PATH = re.compile(r"(?i)(^|/)(tests?|specs?|__tests__|fixtures?)/|[._-](tests?|spec)\.|tests?[^/]*\.[a-z]+$")


def _redactions(line: str) -> tuple[int, int]:
    """(quoted literals such as a test password, key formats or this machine's secret values) redacted in a line."""
    return line.count(REDACTED_LITERAL), line.count(REDACTED)


def _diff_secret_findings(name: str, text: str) -> list[Finding]:
    """In a diff only added lines count: removing a committed password is the fix, not a leak. A real key format
    blocks the run; a quoted literal is sent back (production code) or noted (tests, which need sample passwords)."""
    path, keys, in_code, in_tests = "", set(), set(), set()
    for line in text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip().removeprefix("b/")
            continue
        if not line.startswith("+") or not any(_redactions(line)):
            continue
        literals, other = _redactions(line)
        if other:
            keys.add(path)
        if literals:
            (in_tests if _TEST_PATH.search(path) else in_code).add(path)
    if keys:
        return [Finding("BLOCKER", "SECRET_IN_OUTPUT", f"output '{name}' adds credential-like material in "
                        f"{', '.join(sorted(keys))} (redacted by the platform)")]
    findings = []
    if in_code:
        findings.append(Finding("MAJOR", "SECRET_LITERAL", f"output '{name}' adds a quoted password or token in "
                                f"{', '.join(sorted(in_code))}: read it from configuration or the environment instead"))
    if in_tests:
        findings.append(Finding("MINOR", "SECRET_LITERAL_IN_TESTS", "sample passwords or tokens in tests: "
                                + ", ".join(sorted(in_tests))))
    return findings


def _secret_findings(outputs: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for name, value in outputs.items():
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        if not any(_redactions(text)):
            continue
        if text.startswith("diff --git") or "\n+++ " in text:
            findings += _diff_secret_findings(name, text)
        elif any(_redactions(line)[1] for line in text.splitlines()):
            return [Finding("BLOCKER", "SECRET_IN_OUTPUT",
                            f"output '{name}' contained credential-like material (redacted by the platform)")]
        else:
            findings.append(Finding("MINOR", "SECRET_LITERAL", f"output '{name}' quoted a password or token value "
                                    "(redacted by the platform)"))
    return findings


def _policy_findings(tool_calls: list[dict[str, Any]]) -> list[Finding]:
    denied = [c for c in tool_calls if (c.get("policy") or {}).get("verdict") == "DENY" or c.get("status") == "denied"]
    if len(denied) >= POLICY_ABUSE_THRESHOLD:
        return [Finding("BLOCKER", "PERMISSION_ESCALATION",
                        f"{len(denied)} tool calls were denied by policy; the agent keeps acting outside its permissions")]
    if denied:
        return [Finding("INFO", "POLICY_DENIED", f"{len(denied)} tool call(s) denied by policy")]
    return []


def validate_task_graph(graph: Any) -> list[Finding]:
    tasks = (graph or {}).get("tasks") if isinstance(graph, dict) else None
    if not tasks:
        return [Finding("MAJOR", "EMPTY_PLAN", "planner produced no tasks")]
    keys = [t.get("key") for t in tasks]
    findings: list[Finding] = []
    if len(keys) != len(set(keys)):
        findings.append(Finding("MAJOR", "DUPLICATE_TASK_KEY", "task keys must be unique"))
    for t in tasks:
        for dep in t.get("depends_on") or []:
            if dep not in keys:
                findings.append(Finding("MAJOR", "UNKNOWN_DEPENDENCY", f"task {t.get('key')} depends on unknown {dep}"))
        if not t.get("acceptance_criteria"):
            findings.append(Finding("MAJOR", "MISSING_ACCEPTANCE_CRITERIA", f"task {t.get('key')} has no acceptance criteria"))
    # cycle check
    deps = {t.get("key"): set(t.get("depends_on") or []) for t in tasks}
    resolved: set[str] = set()
    while True:
        ready = {k for k, d in deps.items() if k not in resolved and d <= resolved}
        if not ready:
            break
        resolved |= ready
    if len(resolved) != len(deps):
        findings.append(Finding("MAJOR", "PLAN_CYCLE", "planned task dependencies contain a cycle"))
    return findings


class Critic:
    def evaluate_agent(self, contract: AgentContract, result: dict[str, Any]) -> CriticResult:
        outputs = result.get("outputs") or {}
        evidence = result.get("evidence") or {}
        findings: list[Finding] = []
        findings += validate_outputs(contract, outputs)
        for rule in contract.completion_rules:
            finding = _check_rule(rule, evidence)
            if finding:
                findings.append(finding)
        findings += _secret_findings(outputs)
        findings += _policy_findings(result.get("tool_calls") or [])
        for out in contract.agent_outputs():
            if out.type == "json" and out.schema_ and "tasks" in (out.schema_.get("properties") or {}):
                graph = outputs.get(out.name)
                if isinstance(graph, str):
                    try:
                        graph = json.loads(graph)
                    except json.JSONDecodeError:
                        graph = None
                findings += validate_task_graph(graph)

        verdict = None
        if contract.decision:
            verdict = result.get("decision") or contract.decision.values[0]
            test_ev = evidence.get("test") or {}
            if verdict != contract.decision.changes_required_value and (
                    test_ev.get("status") == "fail" or test_ev.get("failed", 0)):
                findings.append(Finding("MAJOR", "VERDICT_CONTRADICTS_EVIDENCE",
                                        "verdict approves the change but recorded tests fail; treated as CHANGES_REQUIRED"))
                verdict = contract.decision.changes_required_value
                # The output itself is fine; the contradiction drives the fix loop, not a retry.
                findings[-1].severity = "INFO"
            agent_blockers = [f for f in result.get("findings") or [] if f.get("severity") == "BLOCKER"]
            if verdict != contract.decision.changes_required_value and agent_blockers:
                findings.append(Finding("INFO", "VERDICT_CONTRADICTS_FINDINGS",
                                        "verdict approves despite BLOCKER findings; treated as CHANGES_REQUIRED"))
                verdict = contract.decision.changes_required_value
        decision = _decide(findings)
        return CriticResult(decision, findings, verdict, _next_action(decision, verdict))

    def evaluate_tool(self, tool_name: str, result: dict[str, Any]) -> CriticResult:
        evidence = result.get("evidence") or {}
        kind = tool_name.split(".")[0]
        findings: list[Finding] = []
        verdict = APPROVE
        if kind in ("lint", "test", "build"):
            ev = evidence.get(kind) or {}
            if ev.get("status") == "fail":
                verdict = CHANGES_REQUIRED
                findings.append(Finding("INFO", f"{kind.upper()}_FAILED", f"{tool_name} failed (exit {ev.get('exit_code')})"))
            elif ev.get("status") == "not_configured":
                findings.append(Finding("MINOR", "EVIDENCE_NOT_CONFIGURED", f"no command configured for {tool_name}"))
        findings += _secret_findings(result.get("outputs") or {})
        decision = _decide(findings)
        return CriticResult(decision, findings, verdict, _next_action(decision, verdict))


def _next_action(decision: CriticDecision, verdict: str | None) -> str:
    if decision == CriticDecision.BLOCK:
        return "Stop the workflow and escalate to a human."
    if decision == CriticDecision.CHANGES_REQUIRED:
        return "Re-run the task with the findings as feedback."
    if verdict == CHANGES_REQUIRED:
        return "Start the stage fix loop."
    return "Advance the workflow."
