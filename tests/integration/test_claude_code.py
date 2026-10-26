"""Claude Code as the coding engine (ADR-011): the worker drives a (fake) `claude` CLI in the job workspace, then
builds and tests the result itself; the Critic judges that platform evidence, not the engine's claims."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from awf.agent_runtime import AgentContract
from awf.model_router import ModelRouter, RouteRequest
from awf.orchestrator.critic import Critic
from awf.orchestrator.task_router import engine_capabilities, engine_route
from awf.worker_sdk import ControlPlaneClient, JobExecutor
from awf.worker_sdk import Worker as WorkerDaemon
from awf.worker_sdk import claude_code
from awf.worker_sdk.executor import engine_outputs

ROOT = Path(__file__).resolve().parents[2]
FAKE = [sys.executable, str(ROOT / "tests" / "fakes" / "fake_claude.py")]
DEVELOPER = "engineering.developer"


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    record = tmp_path / "claude-call.json"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0123456789abcdef")
    monkeypatch.setenv("AWF_API_TOKEN", "admin-token-must-not-leak")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    return record


def _payload(platform, repository_config, engine="claude-code"):
    definition = platform.agents.get(DEVELOPER)
    return {
        "job_type": "agent",
        "repository": repository_config,
        "workspace": {"integration_branch": "awf/run-test", "task_branch": "awf/tasks/run-test/t1", "write": True},
        "task": {"id": "t1", "stage_id": "development", "title": "Add engine health helper"},
        "context": {"user_message": "Add perception_service/engine_health.py with engine_health().",
                    "objective": "Health helper"},
        "agent": {"role": DEVELOPER, "contract": definition.contract.model_dump(by_alias=True),
                  "system_prompt": definition.system_prompt,
                  "route": {"model_class": "CODE_SPECIALIST", "provider": "anthropic", "model": "claude-opus-5-5",
                            "params": {"effort": "high"}, "reason": "engine claude-code"},
                  "engine": engine},
    }


def _execute(platform, tmp_path, payload, bin_=FAKE):
    executor = JobExecutor(tmp_path / "ws", platform.policy, claude_code_bin=bin_)
    lines: list[str] = []
    result = executor.execute("job-1", payload, log=lambda line, stream="stdout": lines.append(line))
    return result, lines


def _critic(platform, result):
    contract = AgentContract.model_validate(platform.agents.get(DEVELOPER).contract.model_dump(by_alias=True))
    return Critic().evaluate_agent(contract, result)


# ---------------------------------------------------------------------------- worker side
def test_engine_edits_and_the_platform_verifies(platform, repository_config, tmp_path, fake_claude, demo_repo):
    result, lines = _execute(platform, tmp_path, _payload(platform, repository_config))
    assert result["status"] == "succeeded", result.get("error")
    call = json.loads(fake_claude.read_text(encoding="utf-8"))
    argv = call["argv"]
    for flag in ("--bare", "-p", "--no-session-persistence"):
        assert flag in argv
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--model") + 1] == "claude-opus-5-5" and argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--tools") + 1] == "Read,Edit,Write,Glob,Grep,Bash"  # no web tools
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "-m pytest -q -p no:cacheprovider *)" in allowed and "Bash(git diff *)" in allowed
    assert "Bash(git commit" not in allowed
    assert "Add perception_service/engine_health.py" in call["prompt"]  # task context arrives on stdin
    assert "Platform rules for this job" in call["system"] and "`## Changes`" in call["system"]
    assert call["env_has_key"] and not call["env_has_awf_token"]  # only the engine credential is passed

    # Evidence is the platform's own build and test run, after the engine exited.
    assert result["evidence"]["build"]["status"] == "pass" and result["evidence"]["test"]["status"] == "pass"
    tools = [c["tool"] for c in result["tool_calls"]]
    assert tools[:1] == ["claude.Write"] and {"build.run", "test.run"} <= set(tools)
    assert {"tool": "claude.Bash", "status": "engine_denied", "target": "curl x"} in result["tool_calls"]
    assert any(f["code"] == "ENGINE_DENIED" for f in result["findings"])
    assert "engine_health" in result["outputs"]["source_diff"] and result["commit"]["integrated"]
    assert result["usage"] == {"input_tokens": 2000, "output_tokens": 400, "turns": 4, "provider": "anthropic",
                               "model": "claude-opus-5-5", "engine": "claude-code", "cost_usd": 0.1234,
                               "session_id": "s-1"}
    assert _critic(platform, result).decision == "PASS"
    assert any("[claude-code] Write perception_service" in line.replace("\\", "/") for line in lines)


def test_engine_claims_are_not_evidence(platform, repository_config, tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "broken")  # reports success, leaves a syntax error behind
    result, _ = _execute(platform, tmp_path, _payload(platform, repository_config))
    assert result["status"] == "succeeded" and result["evidence"]["build"]["status"] == "fail"
    critic = _critic(platform, result)
    assert critic.decision == "CHANGES_REQUIRED"
    assert any(f.code == "COMPLETION_RULE_FAILED" for f in critic.findings)


def test_engine_may_not_commit(platform, repository_config, tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "commit")
    result, _ = _execute(platform, tmp_path, _payload(platform, repository_config))
    assert result["status"] == "failed" and not result["retryable"]
    assert result["error"].startswith("ENGINE_TOUCHED_GIT") and "commit" not in result


@pytest.mark.parametrize("mode, retryable, text", [
    ("max_turns", True, "claude-code error_max_turns: max turns reached"),
    ("crash", True, "without a result: boom"),
])
def test_engine_failures(platform, repository_config, tmp_path, fake_claude, monkeypatch, mode, retryable, text):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    result, _ = _execute(platform, tmp_path, _payload(platform, repository_config))
    assert result["status"] == "failed" and result["retryable"] is retryable and text in result["error"]
    assert "build" not in result["evidence"]  # nothing to verify


def test_engine_not_installed(platform, repository_config, tmp_path, monkeypatch):
    monkeypatch.delenv("AWF_CLAUDE_CODE_BIN", raising=False)
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: None)
    result, _ = _execute(platform, tmp_path, _payload(platform, repository_config), bin_=None)
    assert result["status"] == "failed" and not result["retryable"] and "not installed" in result["error"]


def test_summary_without_sections_still_meets_the_contract(platform):
    contract = AgentContract.model_validate(platform.agents.get(DEVELOPER).contract.model_dump(by_alias=True))
    out = engine_outputs(contract, "Added the helper; all good.")["implementation_summary"]
    assert out.startswith("## Changes\n\nAdded the helper") and "## Risks\n\n_Not stated by the engine._" in out
    full = "## Changes\nx\n## Tests\ny\n## Risks\nz"
    assert engine_outputs(contract, full)["implementation_summary"] == full


def test_stream_parser_and_rules():
    assert claude_code.bash_rules([["dotnet", "build", "App.csproj"]]) == [
        "Bash(dotnet build App.csproj)", "Bash(dotnet build App.csproj *)"]
    assert claude_code.command_text(["C:/Program Files/py.exe", "-m", "pytest"]) == '"C:/Program Files/py.exe" -m pytest'
    parser = claude_code.StreamParser(lambda line, stream: None)
    parser.feed("not json")
    parser.feed(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                            "api_error_status": 401, "errors": ["invalid x-api-key"], "usage": {}}))
    res = parser.finish(1, "")
    assert res.status == "failed" and not res.retryable and "invalid x-api-key" in res.error


# ---------------------------------------------------------------------------- control plane side
@pytest.fixture
def router(platform):
    return ModelRouter.load(platform.settings.config_dir / "model-routing.yaml")


def _route(router, platform, role, engine, confidentiality="internal", provider=None):
    profile = platform.agents.get(role).profile
    request = RouteRequest(profile_class=profile.model_policy.class_, fallback_class=profile.model_policy.fallback,
                           task_type=profile.task_type, confidentiality=confidentiality, provider=provider)
    return engine_route(router, request, router.resolve(request), profile.engines, engine)


def test_engine_applies_only_where_the_profile_allows_it(router, platform):
    engine, route = _route(router, platform, DEVELOPER, "claude-code")
    assert engine == "claude-code" and route.provider == "anthropic" and route.reason.endswith("engine claude-code")
    assert _route(router, platform, "product.analyst", "claude-code")[0] == "native"
    # A Gemini run still develops with Claude Code, on Claude.
    engine, route = _route(router, platform, DEVELOPER, "claude-code", provider="google")
    assert (engine, route.provider, route.model) == ("claude-code", "anthropic", "claude-opus-5-5")
    # Restricted code never goes to a hosted engine, and the offline provider keeps the native runtime.
    assert _route(router, platform, DEVELOPER, "claude-code", confidentiality="restricted")[0] == "native"
    assert _route(platform.router, platform, DEVELOPER, "claude-code")[0] == "native"  # scripted override
    roles = platform.workflows.get("software-development").spec.roles()
    assert engine_capabilities(platform.agents, router, roles, "internal", "claude-code") == {"claude-code",
                                                                                               "llm-anthropic"}
    assert engine_capabilities(platform.agents, router, roles, "internal", "native") == set()
    with pytest.raises(ValueError, match="unknown coding engine"):
        engine_capabilities(platform.agents, router, roles, "internal", "codex")


def test_runs_with_the_engine_need_a_machine_with_claude_code(client, platform, orchestrator, router,
                                                              repository_config, tmp_path):
    platform.router = router
    orchestrator.router.models = router
    plain = WorkerDaemon(ControlPlaneClient(http=client), name="plain-box", workspace_root=tmp_path / "w",
                         policy_path=ROOT / "config" / "policies.yaml", platform_name="linux-amd64",
                         capabilities=["git", "python", "llm-anthropic"])
    plain.register("")
    project = client.post("/api/v1/projects", json={"name": "P", "repository": repository_config, "labels": {
        "platform": "linux", "coding_engine": "claude-code"}}).json()
    refused = client.post(f"/api/v1/projects/{project['id']}/workflow-runs", json={"inputs": {"objective": "x"}})
    assert refused.status_code == 422 and "plain-box: missing claude-code" in refused.json()["detail"]
    native = client.post(f"/api/v1/projects/{project['id']}/workflow-runs",
                         json={"coding_engine": "native", "inputs": {"objective": "x"}})
    assert native.status_code == 201 and native.json()["coding_engine"] == "native"
    bad = client.post(f"/api/v1/projects/{project['id']}/workflow-runs",
                      json={"coding_engine": "codex", "inputs": {"objective": "x"}})
    assert bad.status_code == 422 and "unknown coding engine" in bad.json()["detail"]

    equipped = WorkerDaemon(ControlPlaneClient(http=client), name="claude-box", workspace_root=tmp_path / "w2",
                            policy_path=ROOT / "config" / "policies.yaml", platform_name="linux-amd64",
                            capabilities=["git", "python", "llm-anthropic", "claude-code"])
    equipped.register("")
    ok = client.post(f"/api/v1/projects/{project['id']}/workflow-runs", json={"inputs": {"objective": "x"}})
    assert ok.status_code == 201, ok.text
    assert ok.json()["coding_engine"] == "claude-code" and "claude-code" in ok.json()["requirements"]["capabilities"]
