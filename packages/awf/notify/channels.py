"""Delivery channels. Each sends one notification to one recipient and reports what it sent (refs), so the
service can retry only the recipients that failed and edit messages later (Telegram buttons)."""

from __future__ import annotations

import html
import time
from typing import Any, Callable, Protocol

import httpx

from awf.domain.models import Notification
from awf.notify.config import TelegramConfig, WhatsAppConfig, ZaloConfig


class DeliveryError(Exception):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class StateStore(Protocol):
    def get(self, key: str) -> dict[str, Any]: ...
    def set(self, key: str, value: dict[str, Any]) -> None: ...


def plain_text(note: Notification, limit: int = 1800) -> str:
    parts = [note.title, note.body or ""]
    if note.link:
        parts.append(f"Mở: {note.link}")
    text = "\n".join(p for p in parts if p)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _post(http: httpx.Client, url: str, what: str, **kwargs: Any) -> httpx.Response:
    try:
        return http.post(url, **kwargs)
    except httpx.HTTPError as exc:
        raise DeliveryError(f"{what}: {type(exc).__name__}: {exc}", retryable=True) from None


# ---------------------------------------------------------------------------- Telegram
APPROVE, REJECT = "y", "n"


def approval_keyboard(approval_id: str) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": "✅ Duyệt", "callback_data": f"ap:{approval_id}:{APPROVE}"},
                                 {"text": "❌ Từ chối", "callback_data": f"ap:{approval_id}:{REJECT}"}]]}


def confirm_keyboard(approval_id: str, decision: str) -> dict[str, Any]:
    label = "✅ Chắc chắn DUYỆT" if decision == APPROVE else "❌ Chắc chắn TỪ CHỐI"
    return {"inline_keyboard": [[{"text": label, "callback_data": f"cf:{approval_id}:{decision}"},
                                 {"text": "↩️ Quay lại", "callback_data": f"cx:{approval_id}"}]]}


class TelegramChannel:
    name = "telegram"

    def __init__(self, config: TelegramConfig, http: httpx.Client | None = None):
        self.config = config
        self.http = http or httpx.Client(timeout=40)

    @property
    def recipients(self) -> list[str]:
        return self.config.chat_ids

    def call(self, method: str, http_timeout: float | None = None, **params: Any) -> Any:
        """Bot API call; `params` are Telegram's own (getUpdates has a `timeout` of its own)."""
        url = f"{self.config.api_base}/bot{self.config.bot_token}/{method}"
        kwargs: dict[str, Any] = {"json": params}
        if http_timeout is not None:
            kwargs["timeout"] = http_timeout
        resp = _post(self.http, url, f"telegram {method}", **kwargs)
        try:
            data = resp.json()
        except ValueError:
            raise DeliveryError(f"telegram {method}: HTTP {resp.status_code}", retryable=resp.status_code >= 500) from None
        if not data.get("ok"):
            retry_after = (data.get("parameters") or {}).get("retry_after")
            retryable = resp.status_code >= 500 or resp.status_code == 429
            msg = f"telegram {method}: {data.get('error_code')} {data.get('description')}"
            if retry_after:
                msg += f" (retry after {retry_after}s)"
            raise DeliveryError(msg, retryable=retryable)
        return data.get("result")

    def render(self, note: Notification) -> str:
        lines = [f"<b>{html.escape(note.title)}</b>"]
        if note.body:
            lines.append(html.escape(note.body))
        if note.link:
            lines.append(f"Mở: {html.escape(note.link)}")
        return "\n".join(lines)[:4000]

    def send(self, note: Notification, recipient: str) -> dict[str, Any]:
        params: dict[str, Any] = {"chat_id": recipient, "text": self.render(note), "parse_mode": "HTML",
                                  "disable_web_page_preview": True}
        if note.kind == "approval" and note.approval_id and self.config.approvers:
            params["reply_markup"] = approval_keyboard(note.approval_id)
        result = self.call("sendMessage", **params)
        return {"chat_id": recipient, "message_id": result.get("message_id")}

    def close_approval(self, refs: list[dict[str, Any]], status: str) -> None:
        """The approval was decided (here or elsewhere): drop the buttons and say so on every copy."""
        for ref in refs:
            try:
                self.call("editMessageReplyMarkup", chat_id=ref["chat_id"], message_id=ref["message_id"],
                          reply_markup={"inline_keyboard": []})
                self.call("sendMessage", chat_id=ref["chat_id"], text=html.escape(status), parse_mode="HTML",
                          reply_to_message_id=ref["message_id"])
            except DeliveryError:
                pass  # message too old or deleted: the separate resolution notification still goes out


# ---------------------------------------------------------------------------- WhatsApp Cloud API
class WhatsAppChannel:
    """Text messages reach a number only within 24 h of its last message to the business; outside that window
    WhatsApp requires an approved template (AWF_WHATSAPP_TEMPLATE, one body parameter {{1}})."""

    name = "whatsapp"

    def __init__(self, config: WhatsAppConfig, http: httpx.Client | None = None):
        self.config = config
        self.http = http or httpx.Client(timeout=30)

    @property
    def recipients(self) -> list[str]:
        return self.config.recipients

    def send(self, note: Notification, recipient: str) -> dict[str, Any]:
        c = self.config
        to = recipient.lstrip("+")
        if c.template:
            # Template parameters cannot contain new lines or tabs.
            flat = " · ".join(" ".join(part.split()) for part in plain_text(note, 1000).splitlines() if part.strip())
            body: dict[str, Any] = {"messaging_product": "whatsapp", "to": to, "type": "template",
                                    "template": {"name": c.template, "language": {"code": c.template_language},
                                                 "components": [{"type": "body", "parameters": [
                                                     {"type": "text", "text": flat}]}]}}
        else:
            body = {"messaging_product": "whatsapp", "to": to, "type": "text",
                    "text": {"body": plain_text(note, 4000), "preview_url": False}}
        resp = _post(self.http, f"{c.api_base}/{c.api_version}/{c.phone_number_id}/messages", "whatsapp",
                     json=body, headers={"Authorization": f"Bearer {c.token}"})
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400 or "error" in data:
            err = data.get("error") or {}
            code = err.get("code")
            hint = (" - outside the 24 h window: set AWF_WHATSAPP_TEMPLATE to an approved template"
                    if code in (131047, 131026) else "")
            raise DeliveryError(f"whatsapp: HTTP {resp.status_code} {code} {err.get('message', '')}{hint}",
                                retryable=resp.status_code >= 500 or resp.status_code == 429 or code in (4, 80007, 130429))
        messages = data.get("messages") or [{}]
        return {"to": to, "message_id": messages[0].get("id")}


# ---------------------------------------------------------------------------- Zalo Official Account
class ZaloChannel:
    """Consultation (tư vấn) messages from an Official Account to users who follow it and wrote to it recently.
    The OA access token is short-lived; it is refreshed with the refresh token, and the new pair (refresh tokens
    are single use) is kept in the database."""

    name = "zalo"
    STATE_KEY = "zalo.tokens"

    def __init__(self, config: ZaloConfig, state: StateStore, http: httpx.Client | None = None,
                 clock: Callable[[], float] = time.time):
        self.config = config
        self.state = state
        self.http = http or httpx.Client(timeout=30)
        self.clock = clock

    @property
    def recipients(self) -> list[str]:
        return self.config.user_ids

    def _tokens(self) -> dict[str, Any]:
        saved = self.state.get(self.STATE_KEY)
        if saved.get("refresh_token") or saved.get("access_token"):
            return saved
        return {"access_token": self.config.access_token, "refresh_token": self.config.refresh_token,
                "expires_at": 0 if not self.config.access_token else self.clock() + 3600}

    def _refresh(self, tokens: dict[str, Any]) -> dict[str, Any]:
        if not tokens.get("refresh_token"):
            raise DeliveryError("zalo: access token expired and no refresh token (AWF_ZALO_REFRESH_TOKEN)", False)
        resp = _post(self.http, self.config.oauth_url, "zalo token refresh",
                     data={"refresh_token": tokens["refresh_token"], "app_id": self.config.app_id,
                           "grant_type": "refresh_token"},
                     headers={"secret_key": self.config.app_secret})
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if not data.get("access_token"):
            raise DeliveryError(f"zalo token refresh failed: {data.get('error')} {data.get('error_name', '')} "
                                f"{data.get('error_description', '')}".strip(), retryable=resp.status_code >= 500)
        fresh = {"access_token": data["access_token"],
                 "refresh_token": data.get("refresh_token") or tokens["refresh_token"],
                 "expires_at": self.clock() + int(data.get("expires_in") or 3600) - 300}
        self.state.set(self.STATE_KEY, fresh)
        return fresh

    def _access_token(self, force: bool = False) -> str:
        tokens = self._tokens()
        if force or not tokens.get("access_token") or float(tokens.get("expires_at") or 0) <= self.clock():
            tokens = self._refresh(tokens)
        return tokens["access_token"]

    def send(self, note: Notification, recipient: str) -> dict[str, Any]:
        body = {"recipient": {"user_id": recipient}, "message": {"text": plain_text(note, 2000)}}
        for attempt in (1, 2):
            token = self._access_token(force=attempt == 2)
            resp = _post(self.http, f"{self.config.api_base}/v3.0/oa/message/cs", "zalo",
                         json=body, headers={"access_token": token})
            try:
                data = resp.json()
            except ValueError:
                raise DeliveryError(f"zalo: HTTP {resp.status_code}", retryable=resp.status_code >= 500) from None
            error = data.get("error", -1)
            if error == 0:
                return {"user_id": recipient, "message_id": (data.get("data") or {}).get("message_id")}
            if error in (-216, -124) and attempt == 1:  # access token invalid/expired: refresh once
                continue
            raise DeliveryError(f"zalo: {error} {data.get('message', '')}", retryable=resp.status_code >= 500)
        raise DeliveryError("zalo: access token rejected after refresh", retryable=False)
