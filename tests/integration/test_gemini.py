"""Gemini as a model provider: the REST adapter against a fake Gemini API, routing a run to Gemini, and keeping
Gemini runs on machines that hold a Gemini key."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from awf.agent_runtime.adapters import ModelError, ToolDef, ToolOutcome
from awf.agent_runtime.adapters.gemini_adapter import GeminiAdapter, function_names, gemini_schema
from awf.domain.models import AgentRun
from awf.model_router import ModelRouter
from awf.workflow_engine import parse_workflow
from awf.worker_sdk import ControlPlaneClient, JobExecutor, resolve_capabilities
from awf.worker_sdk import Worker as WorkerDaemon
from awf.worker_sdk import worker as worker_module

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENT = "\n\n".join(f"## {h}\nText." for h in ("Problem", "Scope", "Functional requirements",
                                                     "Non-functional requirements", "Acceptance criteria",
                                                     "Out of scope"))


def _reply(*parts, finish="STOP", usage=None):
    return httpx.Response(200, json={
        "candidates": [{"content": {"role": "model", "parts": list(parts)}, "finishReason": finish}],
        "usageMetadata": usage or {"promptTokenCount": 1000, "candidatesTokenCount": 200, "thoughtsTokenCount": 300},
        "modelVersion": "gemini-3.8-flash", "responseId": "r1"})


class FakeGemini:
    """Replies from a list; records requests."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[tuple[str, dict, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((str(request.url), dict(request.headers), json.loads(request.content)))
        return self.replies.pop(0)


def _adapter(fake) -> GeminiAdapter:
    return GeminiAdapter(http=httpx.Client(transport=httpx.MockTransport(fake)), key="AIza-test-key",
                         sleep=lambda s: None)


TOOLS = [ToolDef("fs.list", "List files", {"type": "object", "properties": {"path": {"type": ["string", "null"]}},
                                            "additionalProperties": False, "$schema": "x"}),
         ToolDef("submit_result", "Submit", {"type": "object", "properties": {}, "required": []})]


# ---------------------------------------------------------------------------- adapter
def test_request_shape_and_thought_signature_round_trip():
    fake = FakeGemini([
        _reply({"text": "thinking out loud", "thought": True},
               {"functionCall": {"id": "c1", "name": "fs_list", "args": {"path": "."}}, "thoughtSignature": "sig-1"}),
        _reply({"text": "done"}),
    ])
    session = _adapter(fake).start(system="You are an analyst.", user_message="Do it", tools=TOOLS,
                                   model="gemini-4-argon", params={"effort": "high", "max_tokens": 8000}, meta={})
    turn = session.send()
    url, headers, body = fake.requests[0]
    assert url.endswith("/v1beta/models/gemini-4-argon:generateContent")
    assert headers["x-goog-api-key"] == "AIza-test-key"
    assert body["systemInstruction"] == {"parts": [{"text": "You are an analyst."}]}
    assert body["generationConfig"] == {"maxOutputTokens": 8000, "thinkingConfig": {"thinkingLevel": "high"}}
    decl = body["tools"][0]["functionDeclarations"][0]
    assert decl["name"] == "fs_list" and "additionalProperties" not in decl["parameters"]
    assert decl["parameters"]["properties"]["path"] == {"type": "string", "nullable": True}
    assert turn.tool_calls[0].name == "fs.list" and turn.tool_calls[0].input == {"path": "."}
    assert turn.text == "" and turn.input_tokens == 1000 and turn.output_tokens == 500  # thoughts are output

    session.send([ToolOutcome("c1", "a.py\nb.py")])
    contents = fake.requests[1][2]["contents"]
    assert contents[1]["role"] == "model" and contents[1]["parts"][1]["thoughtSignature"] == "sig-1"  # echoed
    assert contents[2] == {"role": "user", "parts": [{"functionResponse": {
        "name": "fs_list", "response": {"result": "a.py\nb.py"}, "id": "c1"}}]}


def test_tool_errors_and_calls_without_ids():
    fake = FakeGemini([_reply({"functionCall": {"name": "fs_list", "args": {}}}), _reply({"text": "ok"})])
    session = _adapter(fake).start(system="s", user_message="u", tools=TOOLS, model="gemini-2.5-pro",
                                   params={"effort": "high"}, meta={})
    turn = session.send()
    assert "thinkingConfig" not in fake.requests[0][2]["generationConfig"]  # 2.5 models: no thinkingLevel
    session.send([ToolOutcome(turn.tool_calls[0].id, "denied", is_error=True)])
    part = fake.requests[1][2]["contents"][2]["parts"][0]["functionResponse"]
    assert part == {"name": "fs_list", "response": {"error": "denied"}}


def test_truncated_tool_call_is_not_executed():
    fake = FakeGemini([_reply({"functionCall": {"name": "fs_list", "args": {"pa": 1}}}, finish="MAX_TOKENS"),
                       _reply({"text": "ok"})])
    session = _adapter(fake).start(system="s", user_message="u", tools=TOOLS, model="gemini-3.8-flash",
                                   params={}, meta={})
    assert session.send().tool_calls == []
    session.send()
    assert "output limit" in fake.requests[1][2]["contents"][-1]["parts"][0]["text"]


def test_retries_rate_limits_then_succeeds():
    fake = FakeGemini([httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED", "message": "slow"}}),
                       httpx.Response(503, json={}), _reply({"text": "hi"})])
    turn = _adapter(fake).start(system="s", user_message="u", tools=[], model="m", params={}, meta={}).send()
    assert turn.text == "hi" and len(fake.requests) == 3 and "tools" not in fake.requests[-1][2]


@pytest.mark.parametrize("response,retryable,text", [
    (httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT", "message": "bad schema"}}), False, "rejected"),
    (httpx.Response(403, json={"error": {"status": "PERMISSION_DENIED", "message": "key"}}), False, "rejected"),
    (_reply({"text": ""}, finish="SAFETY"), False, "declined"),
    (httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), False, "blocked"),
])
def test_errors(response, retryable, text):
    with pytest.raises(ModelError) as exc:
        _adapter(FakeGemini([response])).start(system="s", user_message="u", tools=[], model="m", params={},
                                               meta={}).send()
    assert exc.value.retryable is retryable and text in str(exc.value)


def test_missing_key():
    with pytest.raises(ModelError, match="GEMINI_API_KEY"):
        GeminiAdapter(key="").start(system="s", user_message="u", tools=[], model="m", params={}, meta={})


def test_schema_and_name_helpers():
    assert gemini_schema({"type": "object"}) == {"type": "object", "properties": {}}
    names = function_names([ToolDef("fs.list", "", {}), ToolDef("fs_list", "", {}), ToolDef("9x", "", {})])
    assert names == {"fs.list": "fs_list", "fs_list": "fs_list_2", "9x": "_9x"}


# ---------------------------------------------------------------------------- routing
def test_router_maps_classes_to_gemini():
    from awf.model_router import RouteRequest

    router = ModelRouter.load(ROOT / "config" / "model-routing.yaml")
    assert router.providers() == ["anthropic", "google", "openai"]
    route = router.resolve(RouteRequest(profile_class="REASONING", provider="google"))
    assert (route.provider, route.model, route.params) == ("google", "gemini-4-argon", {"effort": "high"})
    assert router.resolve(RouteRequest(profile_class="REASONING")).provider == "anthropic"
    restricted = router.resolve(RouteRequest(profile_class="REASONING", provider="google",
                                             confidentiality="restricted"))
    assert restricted.provider == "unavailable"  # never remapped to a hosted model
    with pytest.raises(ValueError, match="unknown model provider"):
        router.resolve(RouteRequest(profile_class="FAST", provider="mistral"))
    assert router.estimate_cost("gemini-3.8-flash", 1_000_000, 1_000_000) == pytest.approx(4.5)


def test_worker_reports_the_keys_it_has(monkeypatch):
    monkeypatch.setattr(worker_module, "detect_capabilities", lambda: ["git"])
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert resolve_capabilities() == ["git"]
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert resolve_capabilities() == ["git", "llm-google"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    assert resolve_capabilities("git,llm-openai") == ["git", "llm-anthropic", "llm-google"]  # keys win


# ---------------------------------------------------------------------------- a whole run on Gemini
ANALYST_ONLY = {"id": "analyst-only", "version": "1", "inputs": ["objective", "repository"],
                "stages": [{"id": "requirement", "role": "product.analyst"}]}


@pytest.fixture
def live_routing(platform, orchestrator):
    """Real provider routing (the suite normally overrides every provider with the scripted one)."""
    router = ModelRouter.load(platform.settings.config_dir / "model-routing.yaml")
    platform.router = router
    orchestrator.router.models = router
    loaded = parse_workflow(ANALYST_ONLY, platform.agents.roles())
    platform.workflows._workflows[loaded.spec.id] = loaded
    return router


def _project(client, repository_config, **labels):
    return client.post("/api/v1/projects", json={"name": "P", "repository": repository_config,
                                                 "labels": {"platform": "linux", **labels}}).json()


def test_run_on_gemini_end_to_end(client, platform, orchestrator, live_routing, repository_config, tmp_path):
    fake = FakeGemini([
        _reply({"functionCall": {"id": "c1", "name": "fs_list", "args": {}}, "thoughtSignature": "sig-1"}),
        _reply({"functionCall": {"id": "c2", "name": "submit_result", "args": {
            "summary": "Requirement written", "outputs": {"requirement_document": REQUIREMENT}}},
            "thoughtSignature": "sig-2"}),
    ])
    executor = JobExecutor(tmp_path / "ws", platform.policy,
                           adapter_factory=lambda provider: _adapter(fake) if provider == "google" else None)
    worker = WorkerDaemon(ControlPlaneClient(http=client), name="gemini-box", workspace_root=tmp_path / "ws",
                          policy_path=ROOT / "config" / "policies.yaml", platform_name="linux-amd64",
                          capabilities=["git", "python", "llm-google"], executor=executor)
    worker.register("")
    run = client.post(f"/api/v1/projects/{_project(client, repository_config)['id']}/workflow-runs",
                      json={"workflow_id": "analyst-only", "llm": "google", "inputs": {"objective": "Health check"}})
    assert run.status_code == 201, run.text
    assert run.json()["llm"] == "google" and "llm-google" in run.json()["requirements"]["capabilities"]
    for _ in range(5):
        orchestrator.run_until_idle()
        worker.run_once()
    final = client.get(f"/api/v1/workflow-runs/{run.json()['id']}").json()
    assert final["status"] == "COMPLETED", final.get("error")
    with platform.db.session() as s:
        agent_run = s.scalars(select(AgentRun)).one()
    assert (agent_run.model_provider, agent_run.model_name) == ("google", "gemini-3.8-flash")  # BALANCED class
    assert agent_run.token_input == 2000 and agent_run.token_output == 1000 and float(agent_run.cost_estimate) > 0
    assert fake.requests[1][2]["contents"][1]["parts"][0]["thoughtSignature"] == "sig-1"


def test_gemini_run_needs_a_machine_with_a_gemini_key(client, platform, orchestrator, live_routing,
                                                      repository_config, tmp_path):
    claude_only = WorkerDaemon(ControlPlaneClient(http=client), name="claude-box", workspace_root=tmp_path / "w",
                               policy_path=ROOT / "config" / "policies.yaml", platform_name="linux-amd64",
                               capabilities=["git", "llm-anthropic"])
    claude_only.register("")
    project = _project(client, repository_config, llm="google")  # the project's default provider
    refused = client.post(f"/api/v1/projects/{project['id']}/workflow-runs",
                          json={"workflow_id": "analyst-only", "inputs": {"objective": "x"}})
    assert refused.status_code == 422 and "claude-box: missing llm-google" in refused.json()["detail"]
    ok = client.post(f"/api/v1/projects/{project['id']}/workflow-runs",
                     json={"workflow_id": "analyst-only", "llm": "anthropic", "inputs": {"objective": "x"}})
    assert ok.status_code == 201 and ok.json()["llm"] == "anthropic"
    bad = client.post(f"/api/v1/projects/{project['id']}/workflow-runs",
                      json={"workflow_id": "analyst-only", "llm": "mistral", "inputs": {"objective": "x"}})
    assert bad.status_code == 422 and "unknown model provider" in bad.json()["detail"]
