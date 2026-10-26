"""ChatGPT (OpenAI) as a model provider and Codex as a coding engine: the Responses API adapter against a fake
API, a whole run on OpenAI, keys on workers, and the Codex engine driving a fake `codex` CLI on a real repo."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from awf.agent_runtime import AgentContract
from awf.agent_runtime.adapters import ModelError, ToolDef, ToolOutcome
from awf.agent_runtime.adapters.openai_adapter import OpenAIAdapter
from awf.domain.models import AgentRun
from awf.model_router import ModelRouter, RouteRequest
from awf.orchestrator.critic import Critic
from awf.orchestrator.task_router import engine_capabilities, engine_route
from awf.workflow_engine import parse_workflow
from awf.worker_sdk import ControlPlaneClient, JobExecutor, resolve_capabilities
from awf.worker_sdk import Worker as WorkerDaemon
from awf.worker_sdk import codex
from awf.worker_sdk import worker as worker_module

ROOT = Path(__file__).resolve().parents[2]
FAKE_CODEX = [sys.executable, str(ROOT / "tests" / "fakes" / "fake_codex.py")]
DEVELOPER = "engineering.developer"
REQUIREMENT = "\n\n".join(f"## {h}\nText." for h in ("Problem", "Scope", "Functional requirements",
                                                     "Non-functional requirements", "Acceptance criteria",
                                                     "Out of scope"))


def _response(*items, status="completed", usage=None, incomplete=None):
    body = {"id": "resp_1", "model": "gpt-6.1-sol", "status": status, "output": list(items),
            "usage": usage or {"input_tokens": 1000, "output_tokens": 300}}
    if incomplete:
        body["incomplete_details"] = {"reason": incomplete}
    return httpx.Response(200, json=body)


def _call(call_id, name, args):
    return {"type": "function_call", "id": f"fc_{call_id}", "call_id": call_id, "name": name,
            "arguments": json.dumps(args)}


def _reasoning(n):
    return {"type": "reasoning", "id": f"rs_{n}", "summary": [], "encrypted_content": f"enc-{n}"}


class FakeOpenAI:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[tuple[str, dict, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((str(request.url), dict(request.headers), json.loads(request.content)))
        return self.replies.pop(0)


def _adapter(fake) -> OpenAIAdapter:
    return OpenAIAdapter(http=httpx.Client(transport=httpx.MockTransport(fake)), key="sk-test-openai",
                         sleep=lambda s: None)


TOOLS = [ToolDef("fs_list", "List files", {"type": "object", "properties": {"path": {"type": "string"}}}),
         ToolDef("submit_result", "Submit", {"type": "object", "properties": {}, "required": []})]


# ---------------------------------------------------------------------------- adapter
def test_request_shape_and_stateless_round_trip():
    fake = FakeOpenAI([_response(_reasoning(1), _call("c1", "fs_list", {"path": "."})),
                       _response({"type": "message", "role": "assistant",
                                  "content": [{"type": "output_text", "text": "done"}]})])
    session = _adapter(fake).start(system="You are an analyst.", user_message="Do it", tools=TOOLS,
                                   model="gpt-6.1-sol", params={"effort": "high", "max_tokens": 8000}, meta={})
    turn = session.send()
    url, headers, body = fake.requests[0]
    assert url == "https://api.openai.com/v1/responses" and headers["authorization"] == "Bearer sk-test-openai"
    assert body["instructions"] == "You are an analyst." and body["store"] is False
    assert body["reasoning"] == {"effort": "high"} and body["max_output_tokens"] == 8000
    assert body["input"] == [{"role": "user", "content": "Do it"}]
    assert body["tools"][0] == {"type": "function", "name": "fs_list", "description": "List files",
                                "parameters": TOOLS[0].input_schema, "strict": False}
    assert turn.tool_calls[0].id == "c1" and turn.tool_calls[0].input == {"path": "."}
    assert turn.input_tokens == 1000 and turn.output_tokens == 300

    final = session.send([ToolOutcome("c1", "a.py\nb.py")])
    items = fake.requests[1][2]["input"]
    assert items[1] == _reasoning(1)  # reasoning goes back unchanged with the call it led to
    assert items[2]["type"] == "function_call" and items[3] == {"type": "function_call_output", "call_id": "c1",
                                                                  "output": "a.py\nb.py"}
    assert final.text == "done" and final.stop_reason == "end_turn"


def test_truncated_and_malformed_calls_are_answered_not_run():
    bad = {"type": "function_call", "call_id": "c9", "name": "fs_list", "arguments": "{not json"}
    fake = FakeOpenAI([_response(bad), _response(_call("c2", "fs_list", {}), status="incomplete",
                                                 incomplete="max_output_tokens"),
                       _response({"type": "message", "content": [{"type": "output_text", "text": "ok"}]})])
    session = _adapter(fake).start(system="s", user_message="u", tools=TOOLS, model="gpt-6.1-sol", params={}, meta={})
    assert session.send().tool_calls == []
    assert session.send().tool_calls == [] and session.send().text == "ok"
    outputs = [i for i in fake.requests[2][2]["input"] if i.get("type") == "function_call_output"]
    assert [o["call_id"] for o in outputs] == ["c9", "c2"] and "output limit" in outputs[1]["output"]


@pytest.mark.parametrize("status, error, retryable", [
    (401, {"message": "Incorrect API key", "code": "invalid_api_key"}, False),
    (429, {"message": "You exceeded your current quota", "code": "insufficient_quota"}, False),
    (400, {"message": "bad", "type": "invalid_request_error"}, False),
])
def test_errors(status, error, retryable):
    fake = FakeOpenAI([httpx.Response(status, json={"error": error})])
    session = _adapter(fake).start(system="s", user_message="u", tools=TOOLS, model="gpt-6.1-sol", params={}, meta={})
    with pytest.raises(ModelError) as exc:
        session.send()
    assert exc.value.retryable is retryable and len(fake.requests) == 1


def test_rate_limits_are_retried_and_refusals_stop():
    fake = FakeOpenAI([httpx.Response(429, json={"error": {"message": "slow down", "code": "rate_limit_exceeded"}}),
                       _response({"type": "message", "content": [{"type": "refusal", "refusal": "no"}]})])
    session = _adapter(fake).start(system="s", user_message="u", tools=TOOLS, model="gpt-6.1-sol", params={}, meta={})
    with pytest.raises(ModelError, match="declined") as exc:
        session.send()
    assert not exc.value.retryable and len(fake.requests) == 2


def test_missing_key():
    with pytest.raises(ModelError, match="OPENAI_API_KEY"):
        OpenAIAdapter(key="").start(system="s", user_message="u", tools=[], model="m", params={}, meta={})


def test_router_maps_classes_to_openai(platform):
    router = ModelRouter.load(platform.settings.config_dir / "model-routing.yaml")
    assert "openai" in router.providers()
    route = router.resolve(RouteRequest(profile_class="CODE_SPECIALIST", provider="openai"))
    assert (route.provider, route.model, route.params.get("effort")) == ("openai", "gpt-6.1-sol", "high")
    assert router.resolve(RouteRequest(profile_class="FAST", provider="openai")).model == "gpt-6-luna"
    assert router.resolve(RouteRequest(profile_class="REASONING", provider="openai",
                                       confidentiality="restricted")).provider == "unavailable"
    assert float(router.estimate_cost("gpt-6.1-sol", 1_000_000, 1_000_000)) == pytest.approx(12.0)


def test_worker_reports_the_openai_key(monkeypatch):
    monkeypatch.setattr(worker_module, "detect_capabilities", lambda: ["git"])
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    assert resolve_capabilities() == ["git", "llm-openai"]


# ---------------------------------------------------------------------------- a whole run on ChatGPT
ANALYST_ONLY = {"id": "analyst-only", "version": "1", "inputs": ["objective", "repository"],
                "stages": [{"id": "requirement", "role": "product.analyst"}]}


@pytest.fixture
def live_routing(platform, orchestrator):
    router = ModelRouter.load(platform.settings.config_dir / "model-routing.yaml")
    platform.router = router
    orchestrator.router.models = router
    loaded = parse_workflow(ANALYST_ONLY, platform.agents.roles())
    platform.workflows._workflows[loaded.spec.id] = loaded
    return router


def test_run_on_openai_end_to_end(client, platform, orchestrator, live_routing, repository_config, tmp_path):
    fake = FakeOpenAI([_response(_call("c1", "fs_list", {})),
                       _response(_call("c2", "submit_result", {"summary": "Requirement written",
                                                               "outputs": {"requirement_document": REQUIREMENT}}))])
    executor = JobExecutor(tmp_path / "ws", platform.policy,
                           adapter_factory=lambda provider: _adapter(fake) if provider == "openai" else None)
    worker = WorkerDaemon(ControlPlaneClient(http=client), name="gpt-box", workspace_root=tmp_path / "ws",
                          policy_path=ROOT / "config" / "policies.yaml", platform_name="linux-amd64",
                          capabilities=["git", "python", "llm-openai"], executor=executor)
    worker.register("")
    project = client.post("/api/v1/projects", json={"name": "P", "repository": repository_config,
                                                    "labels": {"platform": "linux"}}).json()
    run = client.post(f"/api/v1/projects/{project['id']}/workflow-runs",
                      json={"workflow_id": "analyst-only", "llm": "openai", "inputs": {"objective": "Health check"}})
    assert run.status_code == 201, run.text
    assert run.json()["llm"] == "openai" and "llm-openai" in run.json()["requirements"]["capabilities"]
    for _ in range(5):
        orchestrator.run_until_idle()
        worker.run_once()
    final = client.get(f"/api/v1/workflow-runs/{run.json()['id']}").json()
    assert final["status"] == "COMPLETED", final.get("error")
    with platform.db.session() as s:
        agent_run = s.scalars(select(AgentRun)).one()
    assert (agent_run.model_provider, agent_run.model_name) == ("openai", "gpt-6.1-sol")
    assert agent_run.token_input == 2000 and float(agent_run.cost_estimate) > 0


# ---------------------------------------------------------------------------- Codex engine
@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    record = tmp_path / "codex-call.json"
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(record))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test-0123456789abcdefghijklmnop")
    monkeypatch.setenv("AWF_API_TOKEN", "admin-token-must-not-leak")
    monkeypatch.setenv("FAKE_CODEX_MODE", "ok")
    return record


def _payload(platform, repository_config):
    definition = platform.agents.get(DEVELOPER)
    return {
        "job_type": "agent", "repository": repository_config,
        "workspace": {"integration_branch": "awf/run-test", "task_branch": "awf/tasks/run-test/t1", "write": True},
        "task": {"id": "t1", "stage_id": "development", "title": "Add engine health helper"},
        "context": {"user_message": "Add perception_service/engine_health.py with engine_health().", "objective": "x"},
        "agent": {"role": DEVELOPER, "contract": definition.contract.model_dump(by_alias=True),
                  "system_prompt": definition.system_prompt,
                  "route": {"model_class": "CODE_SPECIALIST", "provider": "openai", "model": "gpt-6.1-sol",
                            "params": {"effort": "high"}, "reason": "engine codex"},
                  "engine": "codex"},
    }


def _execute(platform, tmp_path, payload, bin_=FAKE_CODEX):
    executor = JobExecutor(tmp_path / "ws", platform.policy, codex_bin=bin_, keep_workspaces=True)
    return executor.execute("job-1", payload, log=lambda line, stream="stdout": None)


def test_codex_edits_and_the_platform_verifies(platform, repository_config, tmp_path, fake_codex):
    result = _execute(platform, tmp_path, _payload(platform, repository_config))
    assert result["status"] == "succeeded", result.get("error")
    call = json.loads(fake_codex.read_text(encoding="utf-8"))
    argv = call["argv"]
    assert argv[:2] == ["exec", "--json"] and argv[-1] == "-"
    assert argv[argv.index("--model") + 1] == "gpt-6.1-sol" and argv[argv.index("--sandbox") + 1] == "workspace-write"
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules"):
        assert flag in argv
    overrides = [argv[i + 1] for i, a in enumerate(argv) if a == "-c"]
    assert {"approval_policy=never", "web_search=disabled", "sandbox_workspace_write.network_access=false",
            "shell_environment_policy.inherit=core", "model_reasoning_effort=high"} <= set(overrides)
    assert "Platform rules for this job" in call["prompt"] and "Add perception_service/engine_health.py" in call["prompt"]
    assert call["codex_api_key"].startswith("sk-openai-test") and not call["env_has_awf_token"]
    assert not call["env_has_openai_key"]  # the key reaches codex as CODEX_API_KEY only
    assert Path(call["codex_home"]).parent == tmp_path / "ws" / "job-1"  # a fresh CODEX_HOME per job

    assert result["evidence"]["build"]["status"] == "pass" and result["evidence"]["test"]["status"] == "pass"
    tools = [c["tool"] for c in result["tool_calls"]]
    assert tools[:2] == ["codex.file_change", "codex.command"] and {"build.run", "test.run"} <= set(tools)
    assert "engine_health" in result["outputs"]["source_diff"]
    assert result["usage"]["engine"] == "codex" and result["usage"]["cost_usd"] is None  # estimated from tokens
    assert (result["usage"]["input_tokens"], result["usage"]["output_tokens"]) == (3000, 600)
    contract = AgentContract.model_validate(platform.agents.get(DEVELOPER).contract.model_dump(by_alias=True))
    assert Critic().evaluate_agent(contract, result).decision == "PASS"


def test_codex_claims_are_not_evidence(platform, repository_config, tmp_path, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "broken")
    result = _execute(platform, tmp_path, _payload(platform, repository_config))
    assert result["status"] == "succeeded" and result["evidence"]["build"]["status"] == "fail"


def test_codex_auth_failure_is_not_retried(platform, repository_config, tmp_path, fake_codex, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_MODE", "auth")
    result = _execute(platform, tmp_path, _payload(platform, repository_config))
    assert result["status"] == "failed" and not result["retryable"] and "Incorrect API key" in result["error"]


def test_codex_not_installed(platform, repository_config, tmp_path, monkeypatch):
    monkeypatch.delenv("AWF_CODEX_BIN", raising=False)
    monkeypatch.setattr(codex.shutil, "which", lambda name: None)
    result = _execute(platform, tmp_path, _payload(platform, repository_config), bin_=None)
    assert result["status"] == "failed" and "codex is not installed" in result["error"]


def test_codex_routing(platform):
    router = ModelRouter.load(platform.settings.config_dir / "model-routing.yaml")
    profile = platform.agents.get(DEVELOPER).profile
    request = RouteRequest(profile_class=profile.model_policy.class_, fallback_class=profile.model_policy.fallback,
                           task_type=profile.task_type)
    engine, route = engine_route(router, request, router.resolve(request), profile.engines, "codex")
    assert (engine, route.provider, route.model) == ("codex", "openai", "gpt-6.1-sol")  # a Claude run, Codex dev
    roles = platform.workflows.get("software-development").spec.roles()
    assert engine_capabilities(platform.agents, router, roles, "internal", "codex") == {"codex", "llm-openai"}
