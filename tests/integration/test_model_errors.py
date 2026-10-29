"""Model failures a real run met first: an overloaded model that never answers, a key with no credit or quota.
Each request and retry wait stays inside the agent's time limit; billing errors fail at once with a hint; the
retries show in the job log as they happen."""

from __future__ import annotations

import time

import anthropic
import httpx
import pytest

from awf.agent_runtime.adapters import ModelError
from awf.agent_runtime.adapters.anthropic_adapter import AnthropicSession
from awf.agent_runtime.adapters.base import BILLING, TIMEOUT
from awf.agent_runtime.adapters.chat_completions_adapter import ChatCompletionsSession
from awf.agent_runtime.adapters.gemini_adapter import GeminiSession
from awf.agent_runtime.adapters.openai_adapter import OpenAISession
from awf.agent_runtime.providers import PROVIDERS
from awf.worker_sdk.worker import _LogBuffer

GEMINI_OK = {"candidates": [{"content": {"role": "model", "parts": [{"text": "done"}]}, "finishReason": "STOP"}],
             "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1}}


def _gemini(handler, sleeps=None, deadline=None):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    session = GeminiSession(http, system="s", user_message="u", tools=[], model="gemini-3.8-flash", params={},
                            key="k", sleep=(sleeps.append if sleeps is not None else lambda s: None))
    session.deadline = deadline
    notes: list[str] = []
    session.notify = notes.append
    return session, notes


def _answers(*responses):
    queue = list(responses)

    def handler(request):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler


def test_a_daily_or_zero_quota_is_billing_not_busy():
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                      "message": "You exceeded your current quota. Quota exceeded for metric: generate_content, limit: 0",
                      "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                   "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}
    session, _ = _gemini(_answers(httpx.Response(429, json=body)))
    with pytest.raises(ModelError) as exc:
        session.send()
    assert exc.value.code == BILLING and not exc.value.retryable
    assert "no credit or quota left on the google API account" in str(exc.value)
    assert PROVIDERS["google"].console in str(exc.value) and "subscription plan" in str(exc.value)


def test_a_per_minute_limit_waits_as_asked_and_retries():
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "You exceeded your current quota.",
                      "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                                   "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel"}]},
                                  {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}}
    sleeps: list[float] = []
    session, notes = _gemini(_answers(httpx.Response(429, json=body), httpx.Response(200, json=GEMINI_OK)), sleeps)
    assert session.send().text == "done"
    assert sleeps == [7.0] and "retrying in 7s" in notes[0] and "attempt 1/4" in notes[0]


def test_an_overloaded_model_is_retried_and_logged():
    busy = httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE",
                                               "message": "This model is currently experiencing high demand."}})
    sleeps: list[float] = []
    session, notes = _gemini(_answers(busy, httpx.ReadTimeout("slow"), httpx.Response(200, json=GEMINI_OK)), sleeps)
    assert session.send().text == "done"
    assert sleeps == [2, 4] and "high demand" in notes[0] and "did not answer within" in notes[1]


def test_no_wait_runs_past_the_agent_deadline():
    busy = httpx.Response(503, json={"error": {"code": 503, "status": "UNAVAILABLE", "message": "high demand"}})
    session, _ = _gemini(_answers(busy), deadline=time.monotonic() + 6)
    with pytest.raises(ModelError) as exc:
        session.send()
    assert exc.value.code == TIMEOUT and exc.value.retryable and "no time left" in str(exc.value)
    expired, _ = _gemini(_answers(), deadline=time.monotonic() - 1)
    with pytest.raises(ModelError, match="time limit was reached"):
        expired.send()


def test_each_request_gets_at_most_the_time_left():
    seen: list[float] = []

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json=GEMINI_OK)

    session, _ = _gemini(handler, deadline=time.monotonic() + 30)
    session.send()
    assert 20 < seen[0] <= 30  # not the 240 s per-request cap
    session.deadline = None
    session.send()
    assert seen[1] == 240


def test_a_model_that_never_answers_gives_up_with_a_clear_reason():
    session, _ = _gemini(_answers(*[httpx.ReadTimeout("slow")] * 4))
    with pytest.raises(ModelError) as exc:
        session.send()
    assert exc.value.code == TIMEOUT and "model busy or overloaded" in str(exc.value) and "4 attempts" in str(exc.value)


def test_openai_and_chat_completions_billing():
    quota = httpx.Response(429, json={"error": {"code": "insufficient_quota", "message": "You exceeded your quota."}})
    http = httpx.Client(transport=httpx.MockTransport(_answers(quota)))
    with pytest.raises(ModelError) as exc:
        OpenAISession(http, system="s", user_message="u", tools=[], model="m", params={}, key="k").send()
    assert exc.value.code == BILLING and "openai" in str(exc.value)

    broke = httpx.Response(402, json={"error": {"message": "Insufficient Balance", "type": "unknown_error"}})
    http = httpx.Client(transport=httpx.MockTransport(_answers(broke)))
    session = ChatCompletionsSession(http, PROVIDERS["deepseek"], system="s", user_message="u", tools=[], model="m",
                                     params={}, key="k", base="https://api.deepseek.com")
    with pytest.raises(ModelError) as exc:
        session.send()
    assert exc.value.code == BILLING and PROVIDERS["deepseek"].console in str(exc.value)


def test_claude_credit_balance_is_billing():
    httpx2 = pytest.importorskip("httpx2")  # the Anthropic SDK's own HTTP client

    def handler(request):
        return httpx2.Response(400, json={"type": "error", "error": {
            "type": "invalid_request_error",
            "message": "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing."}})

    client = anthropic.Anthropic(api_key="sk-ant-test", max_retries=0,
                                 http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    session = AnthropicSession(client, system="s", user_message="u", tools=[], model="claude-sonnet-5-5", params={})
    with pytest.raises(ModelError) as exc:
        session.send()
    assert exc.value.code == BILLING and not exc.value.retryable and "credit balance is too low" in str(exc.value)


def test_platform_notes_reach_the_control_plane_at_once():
    class Client:
        def __init__(self):
            self.batches: list[list[dict]] = []

        def log(self, job_id, lines):
            self.batches.append(lines)

    client = Client()
    buffer = _LogBuffer(client, "job-1")  # type: ignore[arg-type]
    buffer("[tool] fs_read")
    assert client.batches == []  # engine chatter waits for a batch
    buffer("[model] Gemini API 503: high demand; retrying in 2s", "system")
    assert [line["line"] for line in client.batches[0]] == ["[tool] fs_read",
                                                            "[model] Gemini API 503: high demand; retrying in 2s"]
    buffer.flush()
    assert len(client.batches) == 1  # nothing left: no empty upload


def test_the_run_page_says_what_a_task_is_waiting_for(client, platform, orchestrator, worker, repository_config):
    from sqlalchemy import select

    from awf.domain.models import Job, JobLog

    project = client.post("/api/v1/projects", json={"name": "P", "repository": repository_config,
                                                    "labels": {"platform": "linux"}}).json()
    run = client.post(f"/api/v1/projects/{project['id']}/workflow-runs",
                      json={"inputs": {"objective": "x"}}).json()
    orchestrator.run_until_idle()
    (item,) = client.get(f"/api/v1/workflow-runs/{run['id']}").json()["activity"]
    assert item["job_state"] == "QUEUED" and item["note"] is None and item["previous_error"] is None
    with platform.db.transaction() as s:
        job = s.scalars(select(Job)).one()
        job.error = "timeout after 900s"
        s.add(JobLog(job_id=job.id, stream="stdout", line="[tool] fs_read"))
        s.add(JobLog(job_id=job.id, stream="system", line="[model] Gemini API 503: high demand; retrying in 4s"))
    (item,) = client.get(f"/api/v1/workflow-runs/{run['id']}").json()["activity"]
    assert item["note"].startswith("[model] Gemini API 503") and item["previous_error"] == "timeout after 900s"
    assert item["title"] and item["attempt"] == 1


def test_an_account_that_cannot_pay_fails_the_task_at_once(client, platform, orchestrator, worker, repository_config,
                                                           monkeypatch):
    from sqlalchemy import select

    from awf.agent_runtime.adapters.base import billing_error
    from awf.agent_runtime.adapters.scripted import ScriptedAdapter
    from awf.domain.models import Job

    def broke(self, **kwargs):
        raise billing_error("anthropic", "Your credit balance is too low")

    monkeypatch.setattr(ScriptedAdapter, "start", broke)
    project = client.post("/api/v1/projects", json={"name": "P", "repository": repository_config,
                                                    "labels": {"platform": "linux"}}).json()
    run = client.post(f"/api/v1/projects/{project['id']}/workflow-runs", json={"inputs": {"objective": "x"}}).json()
    for _ in range(4):
        orchestrator.run_until_idle()
        worker.run_once()
    state = client.get(f"/api/v1/workflow-runs/{run['id']}").json()
    assert state["status"] == "FAILED" and "MODEL_BILLING" in state["error"]
    with platform.db.session() as s:
        assert len(s.scalars(select(Job)).all()) == 1  # no second attempt against the same empty account
