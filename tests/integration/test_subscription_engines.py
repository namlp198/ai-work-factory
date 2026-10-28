"""Every role on a signed-in engine (ADR-014): engine scope `all`, Claude Code on a Claude Pro/Max login, read-only
roles that answer with an `awf-result` block validated against their contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from awf.agent_runtime import AgentContract
from awf.model_router import ModelRouter, RouteRequest
from awf.orchestrator.task_router import engine_route, run_capabilities
from awf.worker_sdk import ControlPlaneClient, JobExecutor
from awf.worker_sdk import Worker as WorkerDaemon
from awf.worker_sdk import claude_code
from awf.worker_sdk.executor import needs_result_block, parse_result_block

ROOT = Path(__file__).resolve().parents[2]
FAKE = [sys.executable, str(ROOT / "tests" / "fakes" / "fake_claude.py")]
DEVELOPER, REVIEWER, ANALYST = "engineering.developer", "engineering.code-reviewer", "product.analyst"
REVIEW = {"summary": "Looks right.", "decision": "APPROVE", "findings": [],
          "outputs": {"review_report": "## Summary\nok\n## Findings\nnone\n## Verdict\nAPPROVE"}}


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    record = tmp_path / "claude-calls.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0123456789abcdef")  # present, but login mode ignores it
    monkeypatch.setenv("AWF_CLAUDE_CODE_AUTH", "login")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "review")
    monkeypatch.setenv("FAKE_CLAUDE_RESULT", json.dumps(REVIEW))
    return record


def _calls(record: Path) -> list[dict]:
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines()]


def _payload(platform, repository_config, role, write):
    definition = platform.agents.get(role)
    return {
        "job_type": "agent",
        "repository": repository_config,
        "workspace": {"integration_branch": "awf/run-test", "task_branch": "awf/tasks/run-test/t1", "write": write},
        "task": {"id": "t1", "stage_id": "review", "title": "Review the change"},
        "context": {"user_message": "Review the health helper.", "objective": "Health helper"},
        "agent": {"role": role, "contract": definition.contract.model_dump(by_alias=True),
                  "system_prompt": definition.system_prompt,
                  "route": {"model_class": "REASONING", "provider": "anthropic", "model": "claude-opus-5-5",
                            "params": {"effort": "high", "max_budget_usd": 2}, "reason": "engine claude-code"},
                  "engine": "claude-code"},
    }


def _execute(platform, tmp_path, payload):
    executor = JobExecutor(tmp_path / "ws", platform.policy, claude_code_bin=FAKE)
    lines: list[str] = []
    return executor.execute("job-1", payload, log=lambda line, stream="stdout": lines.append(line)), lines


# ---------------------------------------------------------------------------- worker side
def test_read_only_role_runs_on_the_signed_in_plan(platform, repository_config, tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("AWF_CLAUDE_CODE_MODEL", "sonnet")
    result, _ = _execute(platform, tmp_path, _payload(platform, repository_config, REVIEWER, write=False))
    assert result["status"] == "succeeded", result.get("error")
    (call,) = _calls(fake_claude)
    argv = call["argv"]
    assert "--bare" not in argv and "--strict-mcp-config" in argv  # login mode: the plan, no user MCP servers
    assert argv[argv.index("--setting-sources") + 1] == "local"  # user permission rules are not loaded
    assert argv[argv.index("--model") + 1] == "sonnet" and "--max-budget-usd" not in argv
    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep,Bash"  # read-only: no Edit, no Write
    assert "Edit" not in argv[argv.index("--allowedTools") + 1].split(",")
    assert not call["env_has_key"] and not call["env_has_awf_token"]  # the CLI uses its login, not the key
    assert "read-only" in call["system"] and "awf-result" in call["system"] and '"decision"' in call["system"]
    assert result["decision"] == "APPROVE" and result["outputs"]["review_report"].startswith("## Summary")
    assert result["usage"]["billing"] == "subscription" and result["usage"]["cost_usd"] == 0.0
    assert "commit" not in result


def test_a_result_without_the_block_gets_one_correction(platform, repository_config, tmp_path, fake_claude,
                                                        monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "review_bad_first")
    result, lines = _execute(platform, tmp_path, _payload(platform, repository_config, REVIEWER, write=False))
    assert result["status"] == "succeeded" and result["decision"] == "APPROVE"
    first, second = _calls(fake_claude)
    assert "previous answer was rejected" not in first["prompt"]
    assert "previous answer was rejected" in second["prompt"] and "no ```awf-result block" in second["prompt"]
    assert any("engine result rejected" in line for line in lines)


def test_an_invalid_result_twice_fails_the_job(platform, repository_config, tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_RESULT", json.dumps({**REVIEW, "decision": "MAYBE"}))
    result, _ = _execute(platform, tmp_path, _payload(platform, repository_config, REVIEWER, write=False))
    assert result["status"] == "failed" and result["retryable"]
    assert "engine result rejected" in result["error"] and "MAYBE" in result["error"]
    assert len(_calls(fake_claude)) == 2


def test_api_key_mode_is_unchanged(platform, repository_config, tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("AWF_CLAUDE_CODE_AUTH", "api-key")
    result, _ = _execute(platform, tmp_path, _payload(platform, repository_config, REVIEWER, write=False))
    assert result["status"] == "succeeded"
    argv = _calls(fake_claude)[0]["argv"]
    assert "--bare" in argv and argv[argv.index("--max-budget-usd") + 1] == "2"
    assert _calls(fake_claude)[0]["env_has_key"] and "billing" not in result["usage"]
    assert result["usage"]["cost_usd"] == 0.05


def test_auth_mode_default(monkeypatch):
    monkeypatch.delenv("AWF_CLAUDE_CODE_AUTH", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert claude_code.auth_mode() == "login"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert claude_code.auth_mode() == "api-key"
    monkeypatch.setenv("AWF_CLAUDE_CODE_AUTH", "LOGIN")
    assert claude_code.auth_mode() == "login"


def test_result_block_parsing(platform):
    def contract(role):
        return AgentContract.model_validate(platform.agents.get(role).contract.model_dump(by_alias=True))

    reviewer = contract(REVIEWER)
    assert needs_result_block(reviewer) and needs_result_block(contract("engineering.solution-architect"))
    assert not needs_result_block(contract(ANALYST)) and not needs_result_block(contract(DEVELOPER))
    ok = f"text\n```awf-result\n{json.dumps({**REVIEW, 'decision': 'CHANGES_REQUIRED'})}\n```\n" \
         f"```awf-result\r\n{json.dumps(REVIEW)}\r\n```"
    submission, problems = parse_result_block(reviewer, ok)
    assert not problems and submission["decision"] == "APPROVE"  # the last block wins
    assert parse_result_block(reviewer, "```awf-result\n{oops\n```")[1][0].startswith("the awf-result block is not valid JSON")
    _, problems = parse_result_block(reviewer, f"```awf-result\n{json.dumps({**REVIEW, 'outputs': {}})}\n```")
    assert any("review_report" in p for p in problems)


# ---------------------------------------------------------------------------- control plane side
@pytest.fixture
def router(platform):
    return ModelRouter.load(platform.settings.config_dir / "model-routing.yaml")


def _route(router, platform, role, engine, scope):
    profile = platform.agents.get(role).profile
    request = RouteRequest(profile_class=profile.model_policy.class_, fallback_class=profile.model_policy.fallback,
                           task_type=profile.task_type, confidentiality="internal")
    return engine_route(router, request, router.resolve(request), profile.engines, engine, scope)[0]


def test_scope_all_puts_every_role_on_the_engine(router, platform):
    assert _route(router, platform, ANALYST, "claude-code", "coding") == "native"
    assert _route(router, platform, ANALYST, "claude-code", "all") == "claude-code"
    assert _route(router, platform, REVIEWER, "antigravity", "all") == "antigravity"
    roles = platform.workflows.get("software-development").spec.roles()
    # Signed-in engines need the CLI only: no API key anywhere in the run.
    assert run_capabilities(platform.agents, router, roles, "internal", "google", "antigravity", "all") == {"antigravity"}
    assert run_capabilities(platform.agents, router, roles, "internal", "anthropic", "claude-code", "all") == {"claude-code"}
    assert run_capabilities(platform.agents, router, roles, "internal", "qwen", "qwen-code", "all") == {"qwen-code",
                                                                                                         "llm-qwen"}
    # Scope coding: the other roles still call the provider's API.
    assert run_capabilities(platform.agents, router, roles, "internal", "google", "antigravity", "coding") == {
        "antigravity", "llm-google"}
    with pytest.raises(ValueError, match="unknown engine scope"):
        run_capabilities(platform.agents, router, roles, "internal", "google", "antigravity", "some")


def test_runs_on_a_plan_need_only_the_signed_in_cli(client, platform, orchestrator, router, repository_config,
                                                    tmp_path):
    platform.router = router
    orchestrator.router.models = router
    box = WorkerDaemon(ControlPlaneClient(http=client), name="pro-box", workspace_root=tmp_path / "w",
                       policy_path=ROOT / "config" / "policies.yaml", platform_name="linux-amd64",
                       capabilities=["git", "python", "claude-code"])  # no API key on this machine
    box.register("")
    project = client.post("/api/v1/projects", json={"name": "P", "repository": repository_config, "labels": {
        "platform": "linux", "llm": "anthropic"}}).json()
    url = f"/api/v1/projects/{project['id']}/workflow-runs"
    refused = client.post(url, json={"coding_engine": "claude-code", "inputs": {"objective": "x"}})
    assert refused.status_code == 422 and "missing llm-anthropic" in refused.json()["detail"]
    native = client.post(url, json={"engine_scope": "all", "inputs": {"objective": "x"}})
    assert native.status_code == 422 and "needs a coding engine" in native.json()["detail"]
    ok = client.post(url, json={"coding_engine": "claude-code", "engine_scope": "all", "inputs": {"objective": "x"}})
    assert ok.status_code == 201, ok.text
    assert ok.json()["engine_scope"] == "all" and ok.json()["requirements"]["capabilities"] == ["claude-code", "git"]
    bad = client.post(url, json={"coding_engine": "claude-code", "engine_scope": "every", "inputs": {"objective": "x"}})
    assert bad.status_code == 422
