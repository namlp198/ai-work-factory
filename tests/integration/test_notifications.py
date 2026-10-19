"""Notifications (outbox -> Telegram / WhatsApp / Zalo) and approvals from Telegram, against fake provider APIs."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from awf.domain.models import Approval, Notification, SystemState
from awf.notify.config import NotifyConfig
from awf.notify.service import NotificationService
from awf.workflow_engine import parse_workflow

ROOT = Path(__file__).resolve().parents[2]
BOT = "123456:TEST-bot-token-abcdef"

GATED = {
    "id": "gated", "version": "1", "inputs": ["objective", "repository"],
    "stages": [
        {"id": "requirement", "role": "product.analyst"},
        {"id": "approve", "depends_on": ["requirement"],
         "gate": {"type": "human_approval", "action": "approve_architecture", "description": "Check the design"}},
        {"id": "lint", "tool": "lint.run", "depends_on": ["approve"]},
    ],
}


class FakeApi:
    """Records every request; answers like the provider would."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.updates: list[dict] = []
        self.fail: dict[str, list[httpx.Response]] = {}
        self.next_id = 100

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = dict(httpx.QueryParams(request.content.decode()))
        self.calls.append((path, body))
        key = path.rsplit("/", 1)[-1]
        queued = self.fail.get(key)
        if queued:
            return queued.pop(0)
        if "api.telegram.org" in str(request.url):
            if key == "getUpdates":
                updates, self.updates = self.updates, []
                return httpx.Response(200, json={"ok": True, "result": updates})
            self.next_id += 1
            return httpx.Response(200, json={"ok": True, "result": {"message_id": self.next_id}})
        if "graph.facebook.com" in str(request.url):
            return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})
        if key == "access_token":
            return httpx.Response(200, json={"access_token": f"zat-{len(self.calls)}", "refresh_token": "zrt-new",
                                             "expires_in": "90000"})
        if key == "cs":
            return httpx.Response(200, json={"error": 0, "message": "Success", "data": {"message_id": "z1"}})
        return httpx.Response(404, json={})

    def sent(self, method: str) -> list[dict]:
        return [body for path, body in self.calls if path.endswith("/" + method)]


@pytest.fixture
def api():
    return FakeApi()


@pytest.fixture
def telegram_env(monkeypatch):
    monkeypatch.setenv("AWF_TELEGRAM_BOT_TOKEN", BOT)
    monkeypatch.setenv("AWF_TELEGRAM_CHAT_IDS", "-1001,-1002")
    monkeypatch.setenv("AWF_TELEGRAM_APPROVERS", "42")
    monkeypatch.setenv("AWF_PUBLIC_URL", "https://factory.example.com")


def _service(platform, orchestrator, api) -> NotificationService:
    return NotificationService(platform, orchestrator, NotifyConfig.from_env(),
                               http=httpx.Client(transport=httpx.MockTransport(api)))


def _gated_run(client, platform, orchestrator, worker, repository_config) -> tuple[str, str]:
    loaded = parse_workflow(GATED, platform.agents.roles())
    platform.workflows._workflows[loaded.spec.id] = loaded
    p = client.post("/api/v1/projects", json={"name": "Robo", "repository": repository_config,
                                              "labels": {"platform": "linux"}}).json()
    run_id = client.post(f"/api/v1/projects/{p['id']}/workflow-runs",
                         json={"workflow_id": "gated", "inputs": {"objective": "Thêm health check"}}).json()["id"]
    for _ in range(5):
        orchestrator.run_until_idle()
        worker.run_once()
    orchestrator.run_until_idle()
    approval = client.get("/api/v1/approvals", params={"state": "pending"}).json()[0]
    return run_id, approval["id"]


def _callback(data: str, user_id: int = 42, chat_id: int = -1001, message_id: int = 101) -> dict:
    return {"update_id": 1, "callback_query": {"id": "cq1", "data": data,
                                               "from": {"id": user_id, "username": "nam"},
                                               "message": {"message_id": message_id, "chat": {"id": chat_id}}}}


def _notes(platform) -> list[Notification]:
    with platform.db.session() as s:
        return list(s.scalars(select(Notification).order_by(Notification.created_at)))


# ---------------------------------------------------------------------------- outbox
def test_nothing_is_queued_without_a_channel(client, platform, orchestrator, worker, repository_config):
    _gated_run(client, platform, orchestrator, worker, repository_config)
    assert _notes(platform) == []


def test_approval_request_reaches_every_chat_with_buttons_and_link(client, platform, orchestrator, worker,
                                                                   repository_config, telegram_env, api):
    run_id, approval_id = _gated_run(client, platform, orchestrator, worker, repository_config)
    note = next(n for n in _notes(platform) if n.kind == "approval")
    assert note.approval_id == approval_id and note.link == f"https://factory.example.com/#/approvals/{approval_id}"
    assert "Duyệt kiến trúc" in note.title and "Thêm health check" in note.body and "Check the design" in note.body

    service = _service(platform, orchestrator, api)
    service.deliver_pending()
    sent = api.sent("sendMessage")
    assert {m["chat_id"] for m in sent} == {"-1001", "-1002"}
    msg = sent[0]
    assert msg["parse_mode"] == "HTML" and f"#/approvals/{approval_id}" in msg["text"]
    assert msg["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"ap:{approval_id}:y"
    assert _notes(platform)[0].state == "SENT"


def test_without_approvers_messages_carry_only_the_link(client, platform, orchestrator, worker, repository_config,
                                                        telegram_env, api, monkeypatch):
    monkeypatch.setenv("AWF_TELEGRAM_APPROVERS", "")
    _gated_run(client, platform, orchestrator, worker, repository_config)
    _service(platform, orchestrator, api).deliver_pending()
    assert all("reply_markup" not in m for m in api.sent("sendMessage"))


def test_event_filter(client, platform, orchestrator, worker, repository_config, telegram_env, monkeypatch):
    monkeypatch.setenv("AWF_NOTIFY_EVENTS", "failed,completed")
    _gated_run(client, platform, orchestrator, worker, repository_config)
    assert _notes(platform) == []


# ---------------------------------------------------------------------------- approving from Telegram
def test_two_taps_approve_from_telegram(client, platform, orchestrator, worker, repository_config, telegram_env, api):
    run_id, approval_id = _gated_run(client, platform, orchestrator, worker, repository_config)
    service = _service(platform, orchestrator, api)
    service.deliver_pending()

    api.updates = [_callback(f"ap:{approval_id}:y")]
    service.poll_telegram(timeout=0)
    confirm = api.sent("editMessageReplyMarkup")[-1]["reply_markup"]["inline_keyboard"][0][0]
    assert confirm["callback_data"] == f"cf:{approval_id}:y"
    assert client.get(f"/api/v1/approvals/{approval_id}").json()["state"] == "PENDING"  # first tap only asks

    api.updates = [{**_callback(f"cf:{approval_id}:y"), "update_id": 2}]
    service.poll_telegram(timeout=0)
    approval = client.get(f"/api/v1/approvals/{approval_id}").json()
    assert approval["state"] == "APPROVED" and approval["approved_by"] == "telegram:nam"
    with platform.db.session() as s:
        assert s.get(SystemState, "telegram.offset").value == {"offset": 3}

    # The orchestrator carries on; the decision is announced and the buttons vanish from every copy.
    orchestrator.run_until_idle()
    worker.run_once()
    orchestrator.run_until_idle()
    service.deliver_pending()
    resolved = next(n for n in _notes(platform) if n.kind == "approval_resolved")
    assert resolved.state == "SENT" and "telegram:nam" in resolved.body
    cleared = [m for m in api.sent("editMessageReplyMarkup") if m["reply_markup"] == {"inline_keyboard": []}]
    assert {m["chat_id"] for m in cleared} >= {"-1001", "-1002"}
    assert client.get(f"/api/v1/workflow-runs/{run_id}").json()["status"] == "COMPLETED"
    completed = next(n for n in _notes(platform) if n.kind == "completed")
    assert completed.title == "✅ Run hoàn tất" and completed.link.endswith(f"#/runs/{run_id}")


def test_reject_from_telegram(client, platform, orchestrator, worker, repository_config, telegram_env, api):
    _, approval_id = _gated_run(client, platform, orchestrator, worker, repository_config)
    service = _service(platform, orchestrator, api)
    api.updates = [_callback(f"cf:{approval_id}:n")]
    service.poll_telegram(timeout=0)
    assert client.get(f"/api/v1/approvals/{approval_id}").json()["state"] == "REJECTED"


@pytest.mark.parametrize("user_id,chat_id", [(7, -1001), (42, -999)])
def test_strangers_and_other_chats_cannot_approve(client, platform, orchestrator, worker, repository_config,
                                                  telegram_env, api, user_id, chat_id):
    _, approval_id = _gated_run(client, platform, orchestrator, worker, repository_config)
    service = _service(platform, orchestrator, api)
    api.updates = [_callback(f"cf:{approval_id}:y", user_id=user_id, chat_id=chat_id)]
    service.poll_telegram(timeout=0)
    assert client.get(f"/api/v1/approvals/{approval_id}").json()["state"] == "PENDING"
    answer = api.sent("answerCallbackQuery")[-1]
    assert answer["show_alert"] is True and "không có quyền" in answer["text"]


def test_button_on_an_already_decided_approval(client, platform, orchestrator, worker, repository_config,
                                               telegram_env, api):
    _, approval_id = _gated_run(client, platform, orchestrator, worker, repository_config)
    client.post(f"/api/v1/approvals/{approval_id}/approve", json={"comment": "từ dashboard"})
    service = _service(platform, orchestrator, api)
    api.updates = [_callback(f"cf:{approval_id}:n")]
    service.poll_telegram(timeout=0)
    assert client.get(f"/api/v1/approvals/{approval_id}").json()["state"] == "APPROVED"
    assert "đã được xử lý" in api.sent("answerCallbackQuery")[-1]["text"]


def test_id_and_pending_commands(client, platform, orchestrator, worker, repository_config, telegram_env, api):
    _gated_run(client, platform, orchestrator, worker, repository_config)
    service = _service(platform, orchestrator, api)
    api.updates = [{"update_id": 5, "message": {"text": "/start", "chat": {"id": 555}, "from": {"id": 9}}},
                   {"update_id": 6, "message": {"text": "/pending", "chat": {"id": -1001}, "from": {"id": 42}}},
                   {"update_id": 7, "message": {"text": "/pending", "chat": {"id": 555}, "from": {"id": 9}}}]
    service.poll_telegram(timeout=0)
    texts = [(m["chat_id"], m["text"], "reply_markup" in m) for m in api.sent("sendMessage")]
    assert any(chat == 555 and "Chat ID: 555" in text and "User ID: 9" in text for chat, text, _ in texts)
    assert any(chat == -1001 and "Duyệt kiến trúc" in text and buttons for chat, text, buttons in texts)
    assert not any(chat == 555 and "Duyệt kiến trúc" in text for chat, text, _ in texts)  # unknown chat: nothing


# ---------------------------------------------------------------------------- delivery guarantees
def test_retry_per_recipient_and_secrets_stay_out(client, platform, orchestrator, worker, repository_config,
                                                  telegram_env, api):
    _gated_run(client, platform, orchestrator, worker, repository_config)

    def outage(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    api.fail["sendMessage"] = [httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
                               httpx.Response(502, json={"ok": False, "error_code": 502, "description": "Bad Gateway"})]
    service = _service(platform, orchestrator, api)
    service.deliver_pending()
    note = _notes(platform)[0]
    assert note.state == "PENDING" and note.attempts == 1
    assert note.deliveries["telegram"]["done"] == ["-1001"]

    with platform.db.transaction() as s:
        s.get(Notification, note.id).next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    service.telegram.http = httpx.Client(transport=httpx.MockTransport(outage))
    service.deliver_pending()
    note = _notes(platform)[0]
    assert BOT not in json.dumps(note.deliveries) and "[REDACTED]" in note.deliveries["telegram"]["error"]

    with platform.db.transaction() as s:
        s.get(Notification, note.id).next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    service.telegram.http = httpx.Client(transport=httpx.MockTransport(api))
    before = len(api.sent("sendMessage"))
    service.deliver_pending()
    note = _notes(platform)[0]
    assert note.state == "SENT" and len(api.sent("sendMessage")) == before + 1  # only the chat that missed it


def test_blocked_chat_gives_partial(client, platform, orchestrator, worker, repository_config, telegram_env, api):
    _gated_run(client, platform, orchestrator, worker, repository_config)
    api.fail["sendMessage"] = [httpx.Response(403, json={"ok": False, "error_code": 403,
                                                         "description": "Forbidden: bot was blocked by the user"})]
    _service(platform, orchestrator, api).deliver_pending()
    note = _notes(platform)[0]
    assert note.state == "PARTIAL" and "blocked" in note.deliveries["telegram"]["error"]


# ---------------------------------------------------------------------------- WhatsApp and Zalo
def test_whatsapp_text_and_template(client, platform, orchestrator, worker, repository_config, api, monkeypatch):
    monkeypatch.setenv("AWF_WHATSAPP_TOKEN", "EAAG-whatsapp-token")
    monkeypatch.setenv("AWF_WHATSAPP_PHONE_NUMBER_ID", "10987")
    monkeypatch.setenv("AWF_WHATSAPP_TO", "+84901234567")
    _gated_run(client, platform, orchestrator, worker, repository_config)
    _service(platform, orchestrator, api).deliver_pending()
    path, body = api.calls[-1]
    assert path == "/v21.0/10987/messages" and body["to"] == "84901234567" and body["type"] == "text"
    assert "Duyệt kiến trúc" in body["text"]["body"]

    monkeypatch.setenv("AWF_WHATSAPP_TEMPLATE", "awf_alert")
    with platform.db.transaction() as s:
        s.add(Notification(kind="test", event_type="TEST", title="T", body="line 1\nline 2", deliveries={}))
    _service(platform, orchestrator, api).deliver_pending()
    body = api.calls[-1][1]
    param = body["template"]["components"][0]["parameters"][0]["text"]
    assert body["type"] == "template" and body["template"]["name"] == "awf_alert" and "\n" not in param


def test_whatsapp_outside_window_explains(client, platform, orchestrator, worker, repository_config, api, monkeypatch):
    monkeypatch.setenv("AWF_WHATSAPP_TOKEN", "EAAG-whatsapp-token")
    monkeypatch.setenv("AWF_WHATSAPP_PHONE_NUMBER_ID", "10987")
    monkeypatch.setenv("AWF_WHATSAPP_TO", "84901234567")
    _gated_run(client, platform, orchestrator, worker, repository_config)
    api.fail["messages"] = [httpx.Response(400, json={"error": {"code": 131047, "message": "Re-engagement message"}})]
    _service(platform, orchestrator, api).deliver_pending()
    note = _notes(platform)[0]
    assert note.state == "FAILED" and "AWF_WHATSAPP_TEMPLATE" in note.deliveries["whatsapp"]["error"]


def test_zalo_refreshes_and_keeps_the_rotated_token(client, platform, orchestrator, worker, repository_config, api,
                                                    monkeypatch):
    monkeypatch.setenv("AWF_ZALO_APP_ID", "1234")
    monkeypatch.setenv("AWF_ZALO_APP_SECRET", "zalo-app-secret")
    monkeypatch.setenv("AWF_ZALO_REFRESH_TOKEN", "zrt-initial")
    monkeypatch.setenv("AWF_ZALO_USER_IDS", "u1")
    _gated_run(client, platform, orchestrator, worker, repository_config)
    service = _service(platform, orchestrator, api)
    service.deliver_pending()
    refresh = [b for p, b in api.calls if p.endswith("/access_token")]
    assert refresh[0] == {"refresh_token": "zrt-initial", "app_id": "1234", "grant_type": "refresh_token"}
    cs = [b for p, b in api.calls if p.endswith("/v3.0/oa/message/cs")]
    assert cs[0]["recipient"] == {"user_id": "u1"} and "Duyệt kiến trúc" in cs[0]["message"]["text"]
    with platform.db.session() as s:
        assert s.get(SystemState, "zalo.tokens").value["refresh_token"] == "zrt-new"  # single-use: kept for next time

    # Token rejected (-216): refreshed with the stored token once, then sent.
    api.fail["cs"] = [httpx.Response(200, json={"error": -216, "message": "Access token is invalid"})]
    with platform.db.transaction() as s:
        s.add(Notification(kind="test", event_type="TEST", title="T", body="b", deliveries={}))
    service.deliver_pending()
    refresh = [b for p, b in api.calls if p.endswith("/access_token")]
    assert refresh[-1]["refresh_token"] == "zrt-new"
    assert _notes(platform)[-1].state == "SENT"


# ---------------------------------------------------------------------------- API
def test_notifications_api(client, platform, orchestrator, worker, repository_config, telegram_env, api,
                           monkeypatch):
    run_id, approval_id = _gated_run(client, platform, orchestrator, worker, repository_config)
    detail = client.get(f"/api/v1/approvals/{approval_id}").json()
    assert detail["run"]["id"] == run_id and detail["project"]["name"] == "Robo"
    assert {a["type"] for a in detail["artifacts"]} >= {"requirement"}

    queued = client.post("/api/v1/notifications/test")
    assert queued.status_code == 202 and queued.json()["state"] == "PENDING"
    _service(platform, orchestrator, api).deliver_pending()
    listing = client.get("/api/v1/notifications").json()
    assert listing["channels"] == ["telegram"] and listing["telegram_approvals"] is True
    test_row = next(i for i in listing["items"] if i["kind"] == "test")
    assert test_row["state"] == "SENT" and test_row["deliveries"]["telegram"]["done"] == 2

    monkeypatch.delenv("AWF_TELEGRAM_BOT_TOKEN")
    assert client.post("/api/v1/notifications/test").status_code == 409
