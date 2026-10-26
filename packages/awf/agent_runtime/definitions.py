"""Agent contracts (what a role must deliver) and profiles (how it runs).

AGENT INSTANCE = Runtime + Role Profile + Tool Set + Permission + Context + Model Route  (Plan §6)

* Contract  (agents/contracts/<id>.yaml): role/objective, input/output schema, allowed tools,
  permissions, quality gate, completion rules, retry/timeout policy, human approval rule (Plan §7).
* Profile   (agents/profiles/<id>.yaml): model policy, context includes, prompt file (Design §9).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleInfo(_Strict):
    name: str
    objective: str


class InputSpec(_Strict):
    required: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)


OutputType = Literal["markdown", "text", "json", "source_diff", "test_evidence"]


class OutputSpec(_Strict):
    name: str
    type: OutputType = "markdown"
    artifact_type: str | None = None
    description: str = ""
    required: bool = True
    produced_by: Literal["agent", "worker"] = "agent"
    required_sections: list[str] = Field(default_factory=list)
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @property
    def extension(self) -> str:
        return {"markdown": "md", "text": "txt", "json": "json", "source_diff": "patch",
                "test_evidence": "json"}[self.type]

    @property
    def media_type(self) -> str:
        return {"markdown": "text/markdown", "text": "text/plain", "json": "application/json",
                "source_diff": "text/x-diff", "test_evidence": "application/json"}[self.type]

    @property
    def resolved_artifact_type(self) -> str:
        return self.artifact_type or self.name


class DecisionSpec(_Strict):
    """Some roles (reviewer, QA) emit a verdict that drives the workflow fix loop."""

    values: list[str] = Field(default_factory=lambda: ["APPROVE", "CHANGES_REQUIRED"])
    changes_required_value: str = "CHANGES_REQUIRED"


class RetryPolicy(_Strict):
    max_attempts: int = Field(default=2, ge=1, le=10)


class TimeoutPolicy(_Strict):
    seconds: int = Field(default=1800, ge=10)
    max_turns: int = Field(default=40, ge=1, le=500)


CompletionRule = Literal["build_pass", "unit_test_pass", "lint_pass", "has_changes", "tests_executed"]


class AgentContract(_Strict):
    id: str
    version: str
    role: RoleInfo
    inputs: InputSpec = Field(default_factory=InputSpec)
    outputs: list[OutputSpec]
    decision: DecisionSpec | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    permissions: dict[str, Any] = Field(default_factory=dict)
    completion_rules: list[CompletionRule] = Field(default_factory=list)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)
    timeout_policy: TimeoutPolicy = Field(default_factory=TimeoutPolicy)
    human_approval: bool = False

    @model_validator(mode="after")
    def _unique_outputs(self) -> AgentContract:
        names = [o.name for o in self.outputs]
        if len(names) != len(set(names)):
            raise ValueError(f"contract '{self.id}': duplicate output names")
        return self

    def agent_outputs(self) -> list[OutputSpec]:
        return [o for o in self.outputs if o.produced_by == "agent"]

    def worker_outputs(self) -> list[OutputSpec]:
        return [o for o in self.outputs if o.produced_by == "worker"]


class ModelPolicy(_Strict):
    class_: str = Field(alias="class")
    fallback: str | None = None
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ContextPolicy(_Strict):
    include: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)


class AgentProfile(_Strict):
    id: str
    version: str
    contract: str
    prompt: str
    task_type: str = "general"
    model_policy: ModelPolicy
    context: ContextPolicy = Field(default_factory=ContextPolicy)
    # Coding engines this role may run on besides the native runtime (ADR-011), e.g. [claude-code].
    engines: list[str] = Field(default_factory=list)


class AgentDefinition(BaseModel):
    profile: AgentProfile
    contract: AgentContract
    system_prompt: str

    @property
    def role(self) -> str:
        return self.profile.id


class AgentRegistry:
    def __init__(self, agents_dir: Path):
        self.agents_dir = agents_dir
        contracts: dict[str, AgentContract] = {}
        for path in sorted((agents_dir / "contracts").glob("*.yaml")):
            contract = AgentContract.model_validate(_read_yaml(path))
            contracts[contract.id] = contract
        self._definitions: dict[str, AgentDefinition] = {}
        for path in sorted((agents_dir / "profiles").glob("*.yaml")):
            profile = AgentProfile.model_validate(_read_yaml(path))
            if profile.contract not in contracts:
                raise ValueError(f"profile '{profile.id}' references unknown contract '{profile.contract}'")
            prompt_path = agents_dir / "prompts" / profile.prompt
            if not prompt_path.is_file():
                raise ValueError(f"profile '{profile.id}' prompt file missing: {prompt_path}")
            self._definitions[profile.id] = AgentDefinition(
                profile=profile,
                contract=contracts[profile.contract],
                system_prompt=prompt_path.read_text(encoding="utf-8"),
            )

    def get(self, role: str) -> AgentDefinition:
        try:
            return self._definitions[role]
        except KeyError:
            raise KeyError(f"unknown agent role '{role}'") from None

    def roles(self) -> list[str]:
        return sorted(self._definitions)

    def all(self) -> list[AgentDefinition]:
        return [self._definitions[r] for r in self.roles()]


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def submit_tool_schema(contract: AgentContract) -> dict[str, Any]:
    """JSON schema of the `submit_result` tool, derived from the contract's agent outputs."""
    output_props: dict[str, Any] = {}
    required: list[str] = []
    for out in contract.agent_outputs():
        if out.type == "json":
            prop = dict(out.schema_ or {"type": "object"})
        else:
            prop = {"type": "string"}
        desc = out.description or out.name
        if out.required_sections:
            desc += " Must contain these markdown sections (as headings): " + ", ".join(out.required_sections) + "."
        prop["description"] = desc
        output_props[out.name] = prop
        if out.required:
            required.append(out.name)
    props: dict[str, Any] = {
        "summary": {"type": "string", "description": "Short summary of what was done and why."},
        "outputs": {"type": "object", "properties": output_props, "required": required},
    }
    top_required = ["summary", "outputs"]
    if contract.decision:
        props["decision"] = {"type": "string", "enum": contract.decision.values,
                             "description": "Verdict for this task."}
        props["findings"] = {
            "type": "array",
            "description": "Findings supporting the verdict.",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["BLOCKER", "MAJOR", "MINOR", "INFO"]},
                    "file": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["severity", "message"],
            },
        }
        top_required.append("decision")
    return {"type": "object", "properties": props, "required": top_required}
