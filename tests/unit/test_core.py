from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from awf.agent_runtime import AgentContract
from awf.agent_runtime.validation import has_section, validate_submission
from awf.domain.enums import JobState, TaskState
from awf.domain.state_machine import InvalidTransition, check_job_transition, check_task_transition
from awf.model_router import ModelRouter, RouteRequest
from awf.orchestrator.critic import Critic, validate_task_graph
from awf.policy import PolicyConfig, PolicyEngine, Verdict
from awf.security import REDACTED, redact, redact_obj
from awf.tool_sdk import PathViolation, ToolContext, ToolRegistry, resolve_in_workspace
from awf.workflow_engine import WorkflowValidationError, parse_workflow

ROOT = Path(__file__).resolve().parents[2]


# ----------------------------------------------------------------------------- state machine
@pytest.mark.parametrize("src,dst", [
    (TaskState.NEW, TaskState.READY), (TaskState.READY, TaskState.WAITING), (TaskState.WAITING, TaskState.RUNNING),
    (TaskState.RUNNING, TaskState.REVIEW), (TaskState.REVIEW, TaskState.DONE),
    (TaskState.REVIEW, TaskState.APPROVAL_REQUIRED), (TaskState.APPROVAL_REQUIRED, TaskState.DONE),
    (TaskState.CHANGES_REQUIRED, TaskState.READY), (TaskState.FAILED, TaskState.READY),
])
def test_allowed_task_transitions(src, dst):
    assert check_task_transition(src, dst) == dst


@pytest.mark.parametrize("src,dst", [
    (TaskState.NEW, TaskState.DONE), (TaskState.DONE, TaskState.READY), (TaskState.CANCELLED, TaskState.READY),
    (TaskState.READY, TaskState.DONE), (TaskState.WAITING, TaskState.DONE),
])
def test_forbidden_task_transitions(src, dst):
    with pytest.raises(InvalidTransition):
        check_task_transition(src, dst)


def test_job_lifecycle():
    path = [JobState.QUEUED, JobState.CLAIMED, JobState.PREPARING, JobState.RUNNING, JobState.UPLOADING_RESULT,
            JobState.SUCCESS]
    for a, b in zip(path, path[1:]):
        check_job_transition(a, b)
    check_job_transition(JobState.RUNNING, JobState.FAILED)
    check_job_transition(JobState.FAILED, JobState.RETRYABLE)
    with pytest.raises(InvalidTransition):
        check_job_transition(JobState.SUCCESS, JobState.RUNNING)


# ----------------------------------------------------------------------------- workflow validation
def _wf(stages, status="active"):
    return {"id": "wf", "version": "1", "status": status, "stages": stages}


def test_workflow_detects_cycle():
    with pytest.raises(WorkflowValidationError, match="cycle"):
        parse_workflow(_wf([{"id": "a", "role": "r", "depends_on": ["b"]}, {"id": "b", "role": "r", "depends_on": ["a"]}]))


def test_workflow_unknown_dependency_and_role():
    with pytest.raises(WorkflowValidationError, match="unknown stage"):
        parse_workflow(_wf([{"id": "a", "role": "r", "depends_on": ["zzz"]}]))
    with pytest.raises(WorkflowValidationError, match="unknown roles"):
        parse_workflow(_wf([{"id": "a", "role": "ghost"}]), known_roles=["r"])
    # drafts may reference roles that do not exist yet
    parse_workflow(_wf([{"id": "a", "role": "ghost"}], status="draft"), known_roles=["r"])


def test_workflow_stage_shape_and_fix_loop_target():
    with pytest.raises(WorkflowValidationError):
        parse_workflow(_wf([{"id": "a"}]))  # neither role/tool/parallel nor gate
    with pytest.raises(WorkflowValidationError):
        parse_workflow(_wf([{"id": "a", "role": "r", "tool": "lint.run"}]))
    with pytest.raises(WorkflowValidationError, match="upstream"):
        parse_workflow(_wf([{"id": "a", "role": "r"}, {"id": "b", "role": "r"},
                            {"id": "c", "role": "r", "depends_on": ["a"], "on_changes_required": {"goto": "b"}}]))


def test_loop_span_of_default_workflow():
    import yaml

    data = yaml.safe_load((ROOT / "workflows" / "software-development.yaml").read_text(encoding="utf-8"))
    wf = parse_workflow(data)
    assert wf.graph.loop_span("development", "qa") == ["development", "verification", "review", "qa"]
    assert wf.graph.ancestors("planning") == {"architecture", "requirement"}


# ----------------------------------------------------------------------------- policy
@pytest.fixture
def policy() -> PolicyEngine:
    return PolicyEngine(PolicyConfig.load(ROOT / "config" / "policies.yaml"))


def test_policy_levels(policy):
    dev = {"filesystem": {"read": True, "workspace_write": True}, "git": {"read": True, "branch_write": True},
           "build": True, "test": True, "deploy": True, "production_access": True,
           "git_": {}, "shell": False}
    assert policy.evaluate("fs.write", dev).verdict == Verdict.ALLOW
    assert policy.evaluate("deploy.staging", dev).verdict == Verdict.ALLOW_NOTIFY
    assert policy.evaluate("deploy.production", dev).verdict == Verdict.REQUIRE_APPROVAL
    assert policy.evaluate("deploy.production", dev, {"deploy.production"}).verdict == Verdict.ALLOW
    assert policy.evaluate("db.delete", dev).verdict == Verdict.DENY  # level 3 is manual only
    assert policy.evaluate("unknown.action", dev).verdict == Verdict.DENY


def test_policy_rbac(policy):
    reviewer = {"filesystem": {"read": True, "workspace_write": False}, "git": {"read": True}}
    assert policy.evaluate("fs.read", reviewer).allowed
    assert not policy.evaluate("fs.write", reviewer).allowed
    assert not policy.evaluate("shell.exec", reviewer).allowed


def test_no_agent_has_production_permission_by_default(platform):
    for definition in platform.agents.all():
        perms = definition.contract.permissions
        for action in ("deploy.production", "git.merge_protected", "db.delete", "secret.rotate", "shell.exec"):
            assert not platform.policy.evaluate(action, perms).allowed, (definition.role, action)


def test_command_allowlist(policy):
    assert policy.command_allowed(["python", "-m", "pytest"])
    assert policy.command_allowed([r"C:\Python312\python.exe", "-V"])
    assert not policy.command_allowed(["bash", "-c", "rm -rf /"])
    assert not policy.command_allowed([])


# ----------------------------------------------------------------------------- tools
def test_path_traversal_protection(tmp_path):
    (tmp_path / "src").mkdir()
    assert resolve_in_workspace(tmp_path, "src/a.py") == (tmp_path / "src" / "a.py").resolve()
    for bad in ("../outside.txt", "src/../../x", "/etc/passwd", "C:/Windows/win.ini", ".git/config"):
        with pytest.raises(PathViolation):
            resolve_in_workspace(tmp_path, bad)


def test_tool_registry_enforces_policy_and_schema(tmp_path, policy):
    registry = ToolRegistry()
    reader = ToolContext(workspace=tmp_path, permissions={"filesystem": {"read": True}}, policy=policy)
    denied = registry.execute("fs.write", {"path": "a.txt", "content": "x"}, reader)
    assert denied.status == "denied" and not (tmp_path / "a.txt").exists()
    writer = ToolContext(workspace=tmp_path, permissions={"filesystem": {"read": True, "workspace_write": True}},
                         policy=policy)
    assert registry.execute("fs_write", {"path": "a.txt", "content": "x"}, writer).status == "ok"
    assert registry.execute("fs.write", {"path": "a.txt"}, writer).status == "error"  # schema: content missing
    assert registry.execute("fs.read", {"path": "../x"}, writer).status == "error"
    not_allowed = registry.execute("fs.read", {"path": "a.txt"}, writer, allowed=["fs.list"])
    assert not_allowed.status == "denied"
    assert [c["status"] for c in writer.calls] == ["ok", "error", "error", "denied"]


def test_fs_replace_preserves_line_endings(tmp_path, policy):
    (tmp_path / "f.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    ctx = ToolContext(workspace=tmp_path, permissions={"filesystem": {"read": True, "workspace_write": True}},
                      policy=policy)
    res = ToolRegistry().execute("fs.replace", {"path": "f.py", "old_text": "b = 2\n", "new_text": "b = 3\n"}, ctx)
    assert res.status == "ok"
    assert (tmp_path / "f.py").read_bytes() == b"a = 1\r\nb = 3\r\n"


# ----------------------------------------------------------------------------- model router
def _router() -> ModelRouter:
    return ModelRouter.load(ROOT / "config" / "model-routing.yaml")


def test_model_router_rules():
    r = _router()
    assert r.resolve(RouteRequest("REASONING")).model == "claude-opus-5-5"
    assert r.resolve(RouteRequest("BALANCED", task_type="log_summarization")).model_class == "FAST"
    restricted = r.resolve(RouteRequest("REASONING", confidentiality="restricted"))
    assert restricted.model_class == "LOCAL_PRIVATE" and restricted.provider == "unavailable"
    low_budget = r.resolve(RouteRequest("REASONING", budget_remaining_usd=Decimal("0.5")))
    assert low_budget.model_class == "CODE_SPECIALIST" and "budget" in low_budget.reason


def test_cost_estimate():
    assert _router().estimate_cost("claude-opus-5-5", 1_000_000, 100_000) == Decimal("6.000000")
    assert _router().estimate_cost("unknown", 10, 10) == Decimal("0")


# ----------------------------------------------------------------------------- contracts and critic
def test_contract_output_validation(platform):
    contract: AgentContract = platform.agents.get("engineering.solution-architect").contract
    assert validate_submission(contract, {"summary": "x"})[0].code == "MALFORMED_OUTPUT"
    findings = validate_submission(contract, {"summary": "x", "outputs": {
        "architecture_document": "# Arch\n## Context\nx", "adr": "## Status\n## Context\n## Decision\n## Consequences"}})
    missing = {f.message.split("'")[3] for f in findings if f.code == "MISSING_SECTION"}
    assert {"Constraints", "Trade-offs", "Security considerations"} <= missing
    assert has_section("### 3. Failure modes\n", "Failure modes")


def test_critic_requires_evidence_not_claims(platform):
    contract = platform.agents.get("engineering.developer").contract
    outputs = {"implementation_summary": "## Changes\n## Tests\nAll tests pass!\n## Risks"}
    res = Critic().evaluate_agent(contract, {"outputs": outputs, "evidence": {}})
    assert res.decision == "CHANGES_REQUIRED"
    assert {f.code for f in res.findings} >= {"MISSING_ACCEPTANCE_EVIDENCE", "NO_CHANGES"}
    ok = Critic().evaluate_agent(contract, {"outputs": outputs, "evidence": {
        "build": {"status": "pass"}, "test": {"status": "pass", "failed": 0}, "has_changes": True}})
    assert ok.decision == "PASS"


def test_critic_reads_secrets_in_a_diff_by_line(platform):
    """Removing a committed password is the fix; sample passwords in tests are noted; a quoted password in
    production code is sent back; a real key format blocks (run 044f4928 failed on the first two)."""
    contract = platform.agents.get("engineering.developer").contract

    def secrets(*lines):
        diff = "diff --git a/x b/x\n" + "\n".join(lines)
        found = Critic().evaluate_agent(contract, {"outputs": {"source_diff": redact(diff)}}).findings
        return [(f.severity, f.code, f.message) for f in found if f.code.startswith("SECRET")]

    fixed = secrets("+++ b/appsettings.json", '-  "AdminPassword": "Abc@123456",', '+  "UserStoreFile": "data/users.json",',
                    "+++ b/tests/App.Tests/AuthTests.cs", '+    var body = new { username = "ann", password = "Correct-Horse-1" };',
                    '+    Login(new { password: "Wrong-Pass-99" });')
    assert [(sev, code) for sev, code, _ in fixed] == [("MINOR", "SECRET_LITERAL_IN_TESTS")]
    [(sev, code, msg)] = secrets("+++ b/src/Program.cs", '+var adminPassword = "Abc@123456";')
    assert (sev, code) == ("MAJOR", "SECRET_LITERAL") and "src/Program.cs" in msg  # sent back, not a failed run
    [(sev, code, msg)] = secrets("+++ b/tests/conftest.py", "+API_KEY = 'sk-ant-api03-abcdefghijklmnopqrstu'")
    assert (sev, code) == ("BLOCKER", "SECRET_IN_OUTPUT") and "tests/conftest.py" in msg


def test_critic_blocks_secrets_and_permission_abuse(platform):
    contract = platform.agents.get("engineering.code-reviewer").contract
    report = "## Summary\n## Findings\n## Verdict\n" + REDACTED
    assert Critic().evaluate_agent(contract, {"outputs": {"review_report": report}}).decision == "BLOCK"
    denied = [{"tool": "shell.exec", "status": "denied", "policy": {"verdict": "DENY"}}] * 5
    clean = "## Summary\n## Findings\n## Verdict\n"
    res = Critic().evaluate_agent(contract, {"outputs": {"review_report": clean}, "tool_calls": denied})
    assert res.decision == "BLOCK" and res.findings[0].code == "PERMISSION_ESCALATION"


def test_critic_overrides_verdict_contradicting_evidence(platform):
    contract = platform.agents.get("engineering.test-engineer").contract
    report = "## Scope\n## Test cases\n## Results\n## Coverage gaps\n"
    res = Critic().evaluate_agent(contract, {"outputs": {"test_report": report}, "decision": "APPROVE",
                                             "evidence": {"test": {"status": "fail", "failed": 2}}})
    assert res.decision == "PASS" and res.verdict == "CHANGES_REQUIRED"


def test_task_graph_validation():
    assert validate_task_graph({"tasks": []})[0].code == "EMPTY_PLAN"
    cyclic = {"tasks": [{"key": "T1", "acceptance_criteria": ["a"], "depends_on": ["T2"]},
                        {"key": "T2", "acceptance_criteria": ["a"], "depends_on": ["T1"]}]}
    assert "PLAN_CYCLE" in {f.code for f in validate_task_graph(cyclic)}


# ----------------------------------------------------------------------------- security
def test_secret_redaction(monkeypatch):
    monkeypatch.setenv("MY_SERVICE_TOKEN", "supersecretvalue123")
    text = 'key sk-ant-api03-abcdefghijklmnop and token supersecretvalue123\nDB_PASSWORD=hunter22 "AdminPassword": "Abc@123456"'
    out = redact(text)
    assert "sk-ant-api03" not in out and "supersecretvalue123" not in out and "hunter22" not in out
    assert "Abc@123456" not in out and '"AdminPassword": "[REDACTED-VALUE]"' in out
    code = 'var adminPassword = builder.Configuration["FileServer:AdminPassword"] ?? "change-me";'
    assert redact(code) == code  # unquoted code is not a secret
    assert redact_obj({"api_key": "x", "nested": ["supersecretvalue123"]}) == {"api_key": REDACTED,
                                                                              "nested": [REDACTED]}
