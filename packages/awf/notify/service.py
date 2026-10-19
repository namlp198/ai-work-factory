"""Notification service: delivers the outbox to Telegram / WhatsApp / Zalo and takes approvals from Telegram.

Runs next to the orchestrator (one instance per control plane: Telegram allows only one getUpdates reader).

    deliver loop    every 2 s: PENDING notifications -> every configured channel and recipient, retried with
                    backoff per recipient, at most MAX_ATTEMPTS times
    telegram loop   long-polls getUpdates: approve/reject buttons (two taps: choose, then confirm) from
                    allow-listed Telegram users in allow-listed chats; /id, /pending
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select

from awf.domain.enums import ApprovalState, NotificationState
from awf.domain.models import Approval, Notification, Project, SystemState, WorkflowRun
from awf.logging import get_logger
from awf.notify.channels import (
    APPROVE,
    DeliveryError,
    TelegramChannel,
    WhatsAppChannel,
    ZaloChannel,
    approval_keyboard,
    confirm_keyboard,
)
from awf.notify.config import NotifyConfig
from awf.notify.events import ACTION_LABELS
from awf.security import redact

logger = get_logger("notify")

MAX_ATTEMPTS = 8
TELEGRAM_OFFSET = "telegram.offset"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


class DbState:
    def __init__(self, db):
        self.db = db

    def get(self, key: str) -> dict[str, Any]:
        with self.db.session() as s:
            row = s.get(SystemState, key)
            return dict(row.value) if row else {}

    def set(self, key: str, value: dict[str, Any]) -> None:
        with self.db.transaction() as s:
            row = s.get(SystemState, key)
            if row is None:
                s.add(SystemState(key=key, value=value))
            else:
                row.value = value


class NotificationService:
    def __init__(self, platform, orchestrator, config: NotifyConfig | None = None, http: httpx.Client | None = None):
        self.p = platform
        self.orchestrator = orchestrator
        self.config = config or NotifyConfig.from_env()
        self.state = DbState(platform.db)
        self.telegram = TelegramChannel(self.config.telegram, http) if self.config.telegram else None
        self.channels: list[Any] = [c for c in (
            self.telegram,
            WhatsAppChannel(self.config.whatsapp, http) if self.config.whatsapp else None,
            ZaloChannel(self.config.zalo, self.state, http) if self.config.zalo else None,
        ) if c is not None]
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------ delivery
    def deliver_pending(self, limit: int = 20) -> int:
        with self.p.db.session() as s:
            ids = s.scalars(select(Notification.id).where(Notification.state == NotificationState.PENDING,
                                                          Notification.next_attempt_at <= utcnow())
                            .order_by(Notification.created_at).limit(limit)).all()
        for note_id in ids:
            with self.p.db.transaction() as s:
                note = s.get(Notification, note_id)
                if note is not None and note.state == NotificationState.PENDING:
                    self._deliver(s, note)
        return len(ids)

    def _deliver(self, session, note: Notification) -> None:
        if not self.channels:
            note.state = NotificationState.SKIPPED
            return
        deliveries = {k: dict(v) for k, v in (note.deliveries or {}).items()}
        retry = False
        for channel in self.channels:
            d = deliveries.setdefault(channel.name, {"done": [], "refs": [], "failed": [], "error": None})
            if note.kind == "approval_resolved" and channel is self.telegram and not d.get("closed"):
                self._close_telegram_buttons(session, note)
                d["closed"] = True
            for recipient in channel.recipients:
                if recipient in d["done"] or recipient in d["failed"]:
                    continue
                try:
                    d["refs"].append(channel.send(note, recipient))
                    d["done"].append(recipient)
                except DeliveryError as exc:
                    d["error"] = redact(str(exc))[:500]  # bot tokens must never reach the database or the logs
                    logger.warning("notification %s via %s to %s failed: %s", note.id, channel.name, recipient,
                                   d["error"])
                    if exc.retryable:
                        retry = True
                    else:
                        d["failed"].append(recipient)
        note.deliveries = deliveries
        note.attempts += 1
        if retry and note.attempts < MAX_ATTEMPTS:
            note.next_attempt_at = utcnow() + timedelta(seconds=min(3600, 15 * 2 ** note.attempts))
            return
        sent = any(d["done"] for d in deliveries.values())
        failed = any(d["failed"] or (set(self._recipients(name)) - set(d["done"])) for name, d in deliveries.items())
        note.state = (NotificationState.SENT if sent and not failed else
                      NotificationState.PARTIAL if sent else NotificationState.FAILED)
        note.sent_at = utcnow() if sent else None

    def _recipients(self, name: str) -> list[str]:
        return next((c.recipients for c in self.channels if c.name == name), [])

    def _close_telegram_buttons(self, session, note: Notification) -> None:
        refs = []
        for original in session.scalars(select(Notification).where(Notification.approval_id == note.approval_id,
                                                                   Notification.kind == "approval")):
            refs += ((original.deliveries or {}).get("telegram") or {}).get("refs") or []
        if refs:
            self.telegram.close_approval(refs, note.title)

    # ------------------------------------------------------------------ Telegram updates
    def poll_telegram(self, timeout: int = 25) -> int:
        if not self.telegram:
            return 0
        offset = int(self.state.get(TELEGRAM_OFFSET).get("offset") or 0)
        updates = self.telegram.call("getUpdates", http_timeout=timeout + 10, offset=offset, timeout=timeout,
                                     allowed_updates=["message", "callback_query"])
        for update in updates or []:
            try:
                if "callback_query" in update:
                    self.handle_callback(update["callback_query"])
                elif "message" in update:
                    self.handle_message(update["message"])
            except Exception:
                logger.exception("telegram update %s failed", update.get("update_id"))
            offset = max(offset, int(update["update_id"]) + 1)
            self.state.set(TELEGRAM_OFFSET, {"offset": offset})
        return len(updates or [])

    def _allowed_chat(self, chat_id: Any) -> bool:
        return str(chat_id) in self.config.telegram.chat_ids

    def _approver(self, user: dict[str, Any]) -> bool:
        return str(user.get("id")) in self.config.telegram.approvers

    def _answer(self, cq: dict[str, Any], text: str, alert: bool = False) -> None:
        try:
            self.telegram.call("answerCallbackQuery", callback_query_id=cq["id"], text=text, show_alert=alert)
        except DeliveryError:
            pass

    def _markup(self, cq: dict[str, Any], markup: dict[str, Any]) -> None:
        message = cq.get("message") or {}
        try:
            self.telegram.call("editMessageReplyMarkup", chat_id=(message.get("chat") or {}).get("id"),
                               message_id=message.get("message_id"), reply_markup=markup)
        except DeliveryError:
            pass

    def handle_callback(self, cq: dict[str, Any]) -> None:
        user = cq.get("from") or {}
        chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id")
        if not self._allowed_chat(chat_id) or not self._approver(user):
            logger.warning("telegram approval button from user %s in chat %s refused", user.get("id"), chat_id)
            self._answer(cq, "Bạn không có quyền duyệt từ Telegram.", alert=True)
            return
        parts = str(cq.get("data") or "").split(":")
        if len(parts) < 2:
            return self._answer(cq, "Nút không hợp lệ.")
        verb, approval_id = parts[0], parts[1]
        decision = parts[2] if len(parts) > 2 else ""
        with self.p.db.session() as s:
            approval = s.get(Approval, approval_id)
            state = approval.state if approval else None
        if state != ApprovalState.PENDING:
            self._markup(cq, {"inline_keyboard": []})
            return self._answer(cq, f"Yêu cầu này đã được xử lý ({state or 'không tồn tại'}).", alert=True)
        if verb == "ap":
            self._markup(cq, confirm_keyboard(approval_id, decision))
            return self._answer(cq, "Bấm lần nữa để xác nhận.")
        if verb == "cx":
            self._markup(cq, approval_keyboard(approval_id))
            return self._answer(cq, "Đã huỷ.")
        if verb == "cf":
            actor = f"telegram:{user.get('username') or user.get('id')}"
            try:
                with self.p.db.transaction() as s:
                    self.orchestrator.resolve_approval(s, s.get(Approval, approval_id), decision == APPROVE, actor,
                                                       comment="qua Telegram")
            except Exception as exc:  # resolved meanwhile (dashboard) or DB error
                return self._answer(cq, f"Không thực hiện được: {exc}", alert=True)
            self._markup(cq, {"inline_keyboard": []})
            return self._answer(cq, "Đã duyệt." if decision == APPROVE else "Đã từ chối.")
        self._answer(cq, "Nút không hợp lệ.")

    def handle_message(self, message: dict[str, Any]) -> None:
        text = (message.get("text") or "").strip().split("@")[0]
        chat_id = (message.get("chat") or {}).get("id")
        user = message.get("from") or {}
        if text in ("/start", "/id"):
            # Lets whoever sets the bot up find the ids to put in .env; reveals nothing about the factory.
            self.telegram.call("sendMessage", chat_id=chat_id,
                               text=f"AI Work Factory\nChat ID: {chat_id}\nUser ID: {user.get('id')}\n\n"
                                    f"Đặt AWF_TELEGRAM_CHAT_IDS (chat nhận tin) và AWF_TELEGRAM_APPROVERS "
                                    f"(người được duyệt) trong .env của máy trung tâm.")
        elif text == "/pending" and self._allowed_chat(chat_id):
            self._send_pending(chat_id, self._approver(user))

    def _send_pending(self, chat_id: Any, approver: bool) -> None:
        with self.p.db.session() as s:
            rows = s.scalars(select(Approval).where(Approval.state == ApprovalState.PENDING)
                             .order_by(Approval.created_at)).all()
            items = []
            for a in rows[:10]:
                run = s.get(WorkflowRun, a.workflow_run_id) if a.workflow_run_id else None
                project = s.get(Project, a.project_id)
                items.append((a.id, f"{ACTION_LABELS.get(a.action, a.action)} · {project.name if project else '?'}"
                                    f" · run {run.id[:8] if run else '?'}"))
        if not items:
            self.telegram.call("sendMessage", chat_id=chat_id, text="Không có yêu cầu nào đang chờ duyệt.")
            return
        for approval_id, line in items:
            params: dict[str, Any] = {"chat_id": chat_id, "text": f"⏳ {line}"}
            if approver:
                params["reply_markup"] = approval_keyboard(approval_id)
            self.telegram.call("sendMessage", **params)

    # ------------------------------------------------------------------ threads
    def start(self) -> None:
        if not self.channels:
            logger.info("notifications: no channel configured (see AWF_TELEGRAM_* / AWF_WHATSAPP_* / AWF_ZALO_*)")
            return
        logger.info("notifications via %s", ", ".join(c.name for c in self.channels))
        self._spawn("notify-deliver", self._deliver_loop)
        if self.telegram:
            self._spawn("notify-telegram", self._telegram_loop)

    def _spawn(self, name: str, target) -> None:
        thread = threading.Thread(target=target, name=name, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _deliver_loop(self) -> None:
        while not self._stop.wait(2.0):
            try:
                self.deliver_pending()
            except Exception:
                logger.exception("notification delivery failed")

    def _telegram_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_telegram()
            except Exception as exc:
                logger.warning("telegram polling: %s", redact(str(exc)))
                self._stop.wait(10)

    def stop(self) -> None:
        self._stop.set()
