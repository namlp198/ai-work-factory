"""Qwen, Kimi, DeepSeek and MiniMax through the shared Chat Completions adapter, and the Antigravity (Gemini),
Qwen Code, Kimi Code, DeepSeek Harness and MiniMax Code engines driving a stand-in CLI on a real repo (ADR-013)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from awf.agent_runtime import AgentContract
from awf.agent_runtime.adapters import ModelError, ToolDef, ToolOutcome, get_adapter
from awf.agent_runtime.adapters.chat_completions_adapter import ChatCompletionsAdapter
from awf.agent_runtime.providers import PROVIDERS, key_capabilities
from awf.domain.models import AgentRun
from awf.model_router import ModelRouter, RouteRequest
from awf.orchestrator.critic import Critic
from awf.orchestrator.task_router import CODING_ENGINES, engine_capabilities, engine_route
from awf.workflow_engine import parse_workflow
from awf.worker_sdk import ControlPlaneClient, JobExecutor
from awf.worker_sdk import Worker as WorkerDaemon
from awf.worker_sdk import cli_engines

ROOT = Path(__file__).resolve().parents[2]
FAKE = [sys.executable, str(ROOT / "tests" / "fakes" / "fake_cli_engine.py")]
DEVELOPER = "engineering.developer"
COMPAT = ("qwen", "kimi", "deepseek", "minimax")
REQUIREMENT = "\n\n".join(f"## {h}\nText." for h in ("Problem", "Scope", "Functional requirements",
                                                     "Non-functional requirements", "Acceptance criteria",
                                                     "Out of scope"))


def _completion(message, finish="stop", usage=None):
    return httpx.Response(200, json={"id": "cmpl-1", "model": "m", "choices": [
        {"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 900, "completion_tokens": 120}})


def _tool_call(call_id, name, args):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


class FakeAPI:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[tuple[str, dict, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((str(request.url), dict(request.headers), json.loads(request.content)))
        return self.replies.pop(0)


def _adapter(provider, fake):
    return ChatCompletionsAdapter(provider, http=httpx.Client(transport=httpx.MockTransport(fake)), key="sk-test",
                                  sleep=lambda s: None)


TOOLS = [ToolDef("fs_list", "List files", {"type": "object", "properties": {"path": {"type": "string"}}}),
         ToolDef("submit_result", "Submit", {"type": "object", "properties": {}})]


# ---------------------------------------------------------------------------- adapter
@pytest.mark.parametrize("provider, base", [
    ("qwen", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"), ("kimi", "https://api.moonshot.ai/v1"),
    ("deepseek", "https://api.deepseek.com"), ("minimax", "https://api.minimax.io/v1")])
def test_request_shape_and_reasoning_kept_across_tool_calls(provider, base, monkeypatch):
    monkeypatch.delenv(f"AWF_{provider.upper()}_BASE_URL", raising=False)
    fake = FakeAPI([_completion({"content": None, "reasoning_content": "I should list files.",
                                 "tool_calls": [_tool_call("call_1", "fs_list", {"path": "."})]}, finish="tool_calls"),
                    _completion({"content": "done"})])
    session = _adapter(provider, fake).start(system="You are an analyst.", user_message="Do it", tools=TOOLS, model="m",
                                             params={"max_tokens": 16000, "extra": {"thinking": {"type": "enabled"}}},
                                             meta={})
    turn = session.send()
    url, headers, body = fake.requests[0]
    assert url == f"{base}/chat/completions" and headers["authorization"] == "Bearer sk-test"
    assert body["messages"] == [{"role": "system", "content": "You are an analyst."}, {"role": "user", "content": "Do it"}]
    assert body["tools"][0] == {"type": "function", "function": {"name": "fs_list", "description": "List files",
                                                                 "parameters": TOOLS[0].input_schema}}
    assert body["max_tokens"] == 16000 and body["thinking"] == {"type": "enabled"}  # `extra` merged as is
    assert turn.tool_calls[0].id == "call_1" and turn.tool_calls[0].input == {"path": "."}
    assert (turn.input_tokens, turn.output_tokens) == (900, 120)

    final = session.send([ToolOutcome("call_1", "a.py")])
    messages = fake.requests[1][2]["messages"]
    assert messages[2]["reasoning_content"] == "I should list files."  # DeepSeek/Kimi reject it dropped
    assert messages[2]["tool_calls"][0]["id"] == "call_1"
    assert messages[3] == {"role": "tool", "tool_call_id": "call_1", "content": "a.py"}
    assert final.text == "done" and final.stop_reason == "end_turn"


def test_truncated_and_malformed_calls_are_answered_not_run():
    bad = {"id": "c9", "type": "function", "function": {"name": "fs_list", "arguments": "{oops"}}
    fake = FakeAPI([_completion({"content": "", "tool_calls": [bad]}, finish="tool_calls"),
                    _completion({"content": "", "tool_calls": [_tool_call("c2", "fs_list", {})]}, finish="length"),
                    _completion({"content": "ok"})])
    session = _adapter("deepseek", fake).start(system="s", user_message="u", tools=TOOLS, model="m", params={}, meta={})
    assert session.send().tool_calls == [] and session.send().tool_calls == [] and session.send().text == "ok"
    tools = [m for m in fake.requests[2][2]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tools] == ["c9", "c2"] and "output limit" in tools[1]["content"]


@pytest.mark.parametrize("response, retryable", [
    (httpx.Response(402, json={"error": {"message": "Insufficient Balance", "code": "insufficient_balance"}}), False),
    (httpx.Response(401, json={"error": {"message": "Authentication Fails", "type": "authentication_error"}}), False),
    (httpx.Response(200, json={"base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}}), False),
])
def test_errors(response, retryable):
    fake = FakeAPI([response])
    session = _adapter("minimax", fake).start(system="s", user_message="u", tools=TOOLS, model="m", params={}, meta={})
    with pytest.raises(ModelError) as exc:
        session.send()
    assert exc.value.retryable is retryable and len(fake.requests) == 1


def test_rate_limits_are_retried():
    fake = FakeAPI([httpx.Response(429, json={"error": {"message": "slow down"}}), _completion({"content": "ok"})])
    session = _adapter("kimi", fake).start(system="s", user_message="u", tools=TOOLS, model="m", params={}, meta={})
    assert session.send().text == "ok" and len(fake.requests) == 2


def test_keys_base_urls_and_factory(monkeypatch):
    for p in PROVIDERS.values():
        for name in p.key_envs:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-q")
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-k")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-d")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-m")
    assert key_capabilities() == {"llm-qwen", "llm-kimi", "llm-deepseek", "llm-minimax"}
    monkeypatch.setenv("AWF_QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/")  # China region
    adapter = get_adapter("qwen")
    assert isinstance(adapter, ChatCompletionsAdapter) and adapter.base == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with pytest.raises(ModelError, match="DEEPSEEK_API_KEY"):
        get_adapter("deepseek").start(system="s", user_message="u", tools=[], model="m", params={}, meta={})


@pytest.fixture
def router(platform):
    return ModelRouter.load(platform.settings.config_dir / "model-routing.yaml")


@pytest.mark.parametrize("provider, code_model", [("qwen", "qwen3.8-max"), ("kimi", "kimi-k3.1-pro"),
                                                  ("deepseek", "deepseek-v4-pro"), ("minimax", "MiniMax-M3")])
def test_router_maps_classes(router, provider, code_model):
    assert provider in router.providers()
    route = router.resolve(RouteRequest(profile_class="CODE_SPECIALIST", provider=provider))
    assert (route.provider, route.model) == (provider, code_model)
    assert router.resolve(RouteRequest(profile_class="REASONING", provider=provider,
                                       confidentiality="restricted")).provider == "unavailable"


# ---------------------------------------------------------------------------- a whole run on DeepSeek
def test_run_on_deepseek_end_to_end(client, platform, orchestrator, router, repository_config, tmp_path):
    platform.router = router
    orchestrator.router.models = router
    loaded = parse_workflow({"id": "analyst-only", "version": "1", "inputs": ["objective", "repository"],
                             "stages": [{"id": "requirement", "role": "product.analyst"}]}, platform.agents.roles())
    platform.workflows._workflows[loaded.spec.id] = loaded
    fake = FakeAPI([_completion({"content": "", "reasoning_content": "r1",
                                 "tool_calls": [_tool_call("c1", "fs_list", {})]}, finish="tool_calls"),
                    _completion({"content": "", "reasoning_content": "r2", "tool_calls": [_tool_call(
                        "c2", "submit_result", {"summary": "Requirement written",
                                                "outputs": {"requirement_document": REQUIREMENT}})]},
                                finish="tool_calls")])
    executor = JobExecutor(tmp_path / "ws", platform.policy, adapter_factory=lambda p: _adapter(p, fake))
    worker = WorkerDaemon(ControlPlaneClient(http=client), name="ds-box", workspace_root=tmp_path / "ws",
                          policy_path=ROOT / "config" / "policies.yaml", platform_name="linux-amd64",
                          capabilities=["git", "python", "llm-deepseek"], executor=executor)
    worker.register("")
    project = client.post("/api/v1/projects", json={"name": "P", "repository": repository_config,
                                                    "labels": {"platform": "linux", "llm": "deepseek"}}).json()
    run = client.post(f"/api/v1/projects/{project['id']}/workflow-runs",
                      json={"workflow_id": "analyst-only", "inputs": {"objective": "Health check"}})
    assert run.status_code == 201, run.text
    assert run.json()["llm"] == "deepseek" and "llm-deepseek" in run.json()["requirements"]["capabilities"]
    for _ in range(5):
        orchestrator.run_until_idle()
        worker.run_once()
    assert client.get(f"/api/v1/workflow-runs/{run.json()['id']}").json()["status"] == "COMPLETED"
    with platform.db.session() as s:
        agent_run = s.scalars(select(AgentRun)).one()
    assert (agent_run.model_provider, agent_run.model_name) == ("deepseek", "deepseek-flash")  # BALANCED class
    assert fake.requests[1][2]["messages"][2]["reasoning_content"] == "r1"


# ---------------------------------------------------------------------------- engines
def test_engine_table_and_routing(router, platform):
    assert set(CODING_ENGINES) == {"native", "claude-code", "codex", "antigravity", "qwen-code", "kimi-code",
                                   "deepseek-harness", "minimax-code"}
    profile = platform.agents.get(DEVELOPER).profile
    request = RouteRequest(profile_class=profile.model_policy.class_, fallback_class=profile.model_policy.fallback,
                           task_type=profile.task_type)
    for engine, provider in (("antigravity", "google"), ("qwen-code", "qwen"), ("kimi-code", "kimi"),
                             ("deepseek-harness", "deepseek"), ("minimax-code", "minimax")):
        chosen, route = engine_route(router, request, router.resolve(request), profile.engines, engine)
        assert (chosen, route.provider) == (engine, provider)
    roles = platform.workflows.get("software-development").spec.roles()
    # Antigravity and Kimi Code sign in on their own (e.g. a Google AI Pro account): the CLI, no API key.
    assert engine_capabilities(platform.agents, router, roles, "internal", "antigravity") == {"antigravity"}
    assert engine_capabilities(platform.agents, router, roles, "internal", "kimi-code") == {"kimi-code"}
    assert engine_capabilities(platform.agents, router, roles, "internal", "qwen-code") == {"qwen-code", "llm-qwen"}
    assert engine_capabilities(platform.agents, router, roles, "internal", "deepseek-harness") == {
        "deepseek-harness", "llm-deepseek"}
    assert engine_capabilities(platform.agents, router, roles, "internal", "minimax-code") == {"minimax-code"}


def _payload(platform, repository_config, engine, provider, model):
    definition = platform.agents.get(DEVELOPER)
    return {
        "job_type": "agent", "repository": repository_config,
        "workspace": {"integration_branch": "awf/run-test", "task_branch": "awf/tasks/run-test/t1", "write": True},
        "task": {"id": "t1", "stage_id": "development", "title": "Add engine health helper"},
        "context": {"user_message": "Add perception_service/engine_health.py with engine_health().", "objective": "x"},
        "agent": {"role": DEVELOPER, "contract": definition.contract.model_dump(by_alias=True),
                  "system_prompt": definition.system_prompt,
                  "route": {"model_class": "CODE_SPECIALIST", "provider": provider, "model": model, "params": {},
                            "reason": f"engine {engine}"},
                  "engine": engine},
    }


@pytest.mark.parametrize("engine, kind, provider, model, tokens", [
    ("antigravity", "agy", "google", "gemini-4-argon", (5000, 1000)),
    ("qwen-code", "qwen", "qwen", "qwen3.8-max", (4000, 500)),
    ("kimi-code", "kimi", "kimi", "kimi-k3.1-pro", (0, 0)),
    ("deepseek-harness", "dsh", "deepseek", "deepseek-v4-pro", (0, 0)),
    ("minimax-code", "mcode", "minimax", "MiniMax-M3", (3600, 600)),
])
def test_cli_engines_edit_and_the_platform_verifies(platform, repository_config, tmp_path, monkeypatch,
                                                    engine, kind, provider, model, tokens):
    record = tmp_path / "engine.json"
    monkeypatch.setenv("FAKE_ENGINE_KIND", kind)
    monkeypatch.setenv("FAKE_ENGINE_RECORD", str(record))
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope-test")
    monkeypatch.setenv("AWF_API_TOKEN", "admin-token-must-not-leak")
    monkeypatch.delenv("AWF_ANTIGRAVITY_MODEL", raising=False)
    monkeypatch.delenv("AWF_KIMI_CODE_MODEL", raising=False)
    executor = JobExecutor(tmp_path / "ws", platform.policy, engine_bins={engine: FAKE})
    result = executor.execute("job-1", _payload(platform, repository_config, engine, provider, model),
                              log=lambda line, stream="stdout": None)
    assert result["status"] == "succeeded", result.get("error")
    call = json.loads(record.read_text(encoding="utf-8"))
    if cli_engines.uses_stdin(engine):  # the prompt comes on stdin, no task file
        assert not call["task_file"] and call["argv"][-1] == "-" or call["argv"][-2:] == ["--input", "-"]
    else:
        assert call["argv"][:2] == ["-p", cli_engines.POINTER] and call["task_file"]
    assert "Platform rules for this job" in call["task"]
    assert "Add perception_service/engine_health.py" in call["task"] and not call["env"]["AWF_API_TOKEN"]
    if engine == "antigravity":
        assert "--dangerously-skip-permissions" in call["argv"] and "--model" not in call["argv"]  # agy's own model
    if engine == "deepseek-harness":
        assert call["argv"][:4] == ["--profile", "headless", "--json", "--permission-mode"]
        assert call["argv"][call["argv"].index("--approval-policy") + 1] == "never"
        assert {"tool": "dsh.shell", "status": "engine_denied", "target": '{"command": "curl example.com"}'}             in result["tool_calls"]
    if engine == "minimax-code":
        assert call["argv"][:4] == ["exec", "--output-format", "json", "--permission"]
        assert call["argv"][call["argv"].index("--permission") + 1] == "full"
    if engine == "qwen-code":
        assert call["argv"][call["argv"].index("--approval-mode") + 1] == "yolo"
        assert call["argv"][call["argv"].index("--model") + 1] == "qwen3.8-max"
        assert call["env"]["OPENAI_API_KEY"] == "sk-dashscope-test" and call["env"]["OPENAI_MODEL"] == "qwen3.8-max"
        assert call["env"]["OPENAI_BASE_URL"].startswith("https://dashscope")
    assert result["evidence"]["build"]["status"] == "pass" and result["evidence"]["test"]["status"] == "pass"
    diff = result["outputs"]["source_diff"]
    assert "engine_health" in diff and cli_engines.TASK_FILE not in diff  # the task file is never committed
    assert (result["usage"]["input_tokens"], result["usage"]["output_tokens"]) == tokens
    contract = AgentContract.model_validate(platform.agents.get(DEVELOPER).contract.model_dump(by_alias=True))
    assert Critic().evaluate_agent(contract, result).decision == "PASS"


@pytest.mark.parametrize("engine, kind, retryable", [("antigravity", "agy", False), ("kimi-code", "kimi", False),
                                                     ("deepseek-harness", "dsh", False), ("minimax-code", "mcode", False)])
def test_cli_engine_failures(platform, repository_config, tmp_path, monkeypatch, engine, kind, retryable):
    monkeypatch.setenv("FAKE_ENGINE_KIND", kind)
    monkeypatch.setenv("FAKE_ENGINE_MODE", "fail")
    monkeypatch.setenv("FAKE_ENGINE_RECORD", str(tmp_path / "engine.json"))
    executor = JobExecutor(tmp_path / "ws", platform.policy, engine_bins={engine: FAKE})
    result = executor.execute("job-1", _payload(platform, repository_config, engine, "google", "m"),
                              log=lambda line, stream="stdout": None)
    assert result["status"] == "failed" and result["retryable"] is retryable


def test_cli_engine_not_installed(platform, repository_config, tmp_path, monkeypatch):
    monkeypatch.delenv("AWF_QWEN_CODE_BIN", raising=False)
    monkeypatch.setattr(cli_engines.shutil, "which", lambda name: None)
    result = JobExecutor(tmp_path / "ws", platform.policy).execute(
        "job-1", _payload(platform, repository_config, "qwen-code", "qwen", "qwen3.8-max"),
        log=lambda line, stream="stdout": None)
    assert result["status"] == "failed" and "qwen-code is not installed" in result["error"]
