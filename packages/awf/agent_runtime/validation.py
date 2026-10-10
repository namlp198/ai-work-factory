"""Structured output validation against an agent contract."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

import jsonschema

from awf.agent_runtime.definitions import AgentContract, submit_tool_schema


@dataclass
class Finding:
    severity: str  # BLOCKER | MAJOR | MINOR | INFO
    code: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def has_section(markdown: str, section: str) -> bool:
    pattern = re.compile(r"^\s{0,3}#{1,6}\s+.*" + re.escape(section), re.IGNORECASE | re.MULTILINE)
    return bool(pattern.search(markdown))


def validate_submission(contract: AgentContract, submission: dict[str, Any]) -> list[Finding]:
    """Validate the `submit_result` payload. MAJOR findings mean the agent should fix and resubmit."""
    findings: list[Finding] = []
    try:
        jsonschema.validate(submission, submit_tool_schema(contract))
    except jsonschema.ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        findings.append(Finding("MAJOR", "MALFORMED_OUTPUT", f"{path}: {exc.message}"))
        return findings
    findings.extend(validate_outputs(contract, submission.get("outputs") or {}))
    return findings


def validate_outputs(contract: AgentContract, outputs: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for spec in contract.agent_outputs():
        value = outputs.get(spec.name)
        if value is None or (isinstance(value, str) and not value.strip()):
            if spec.required:
                findings.append(Finding("MAJOR", "MISSING_OUTPUT", f"required output '{spec.name}' is missing"))
            continue
        if spec.type == "json":
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    findings.append(Finding("MAJOR", "MALFORMED_OUTPUT", f"output '{spec.name}' is not valid JSON"))
                    continue
            if spec.schema_:
                try:
                    jsonschema.validate(value, spec.schema_)
                except jsonschema.ValidationError as exc:
                    findings.append(Finding("MAJOR", "MALFORMED_OUTPUT", f"output '{spec.name}': {exc.message}"))
        elif isinstance(value, str):
            for section in spec.required_sections:
                if not has_section(value, section):
                    findings.append(Finding("MAJOR", "MISSING_SECTION",
                                            f"output '{spec.name}' lacks required section '{section}'"))
        else:
            findings.append(Finding("MAJOR", "MALFORMED_OUTPUT", f"output '{spec.name}' must be text"))
    return findings
