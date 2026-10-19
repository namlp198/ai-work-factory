"""Notification settings, read from the control plane's environment (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# What gets announced. AWF_NOTIFY_EVENTS narrows it (comma separated).
ALL_KINDS = ("approval", "approval_resolved", "failed", "completed", "worker_offline", "run_moved")


def _list(name: str) -> list[str]:
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


@dataclass
class TelegramConfig:
    bot_token: str
    chat_ids: list[str]
    # Telegram user ids allowed to approve with the buttons in the chat. Empty: messages carry only the link.
    approvers: list[str] = field(default_factory=list)
    api_base: str = "https://api.telegram.org"


@dataclass
class WhatsAppConfig:
    token: str
    phone_number_id: str
    recipients: list[str]
    api_version: str = "v21.0"
    # Messages a business starts outside the 24 h customer-service window must use an approved template.
    template: str = ""
    template_language: str = "vi"
    api_base: str = "https://graph.facebook.com"


@dataclass
class ZaloConfig:
    app_id: str
    app_secret: str
    refresh_token: str
    user_ids: list[str]
    access_token: str = ""
    api_base: str = "https://openapi.zalo.me"
    oauth_url: str = "https://oauth.zaloapp.com/v4/oa/access_token"


@dataclass
class NotifyConfig:
    kinds: tuple[str, ...]
    public_url: str = ""
    telegram: TelegramConfig | None = None
    whatsapp: WhatsAppConfig | None = None
    zalo: ZaloConfig | None = None

    @property
    def channels(self) -> list[str]:
        return [name for name in ("telegram", "whatsapp", "zalo") if getattr(self, name) is not None]

    @property
    def enabled(self) -> bool:
        return bool(self.channels)

    @classmethod
    def from_env(cls) -> NotifyConfig:
        env = os.environ
        kinds = tuple(k for k in (_list("AWF_NOTIFY_EVENTS") or ALL_KINDS) if k in ALL_KINDS)
        telegram = whatsapp = zalo = None
        if env.get("AWF_TELEGRAM_BOT_TOKEN"):
            telegram = TelegramConfig(env["AWF_TELEGRAM_BOT_TOKEN"], _list("AWF_TELEGRAM_CHAT_IDS"),
                                      _list("AWF_TELEGRAM_APPROVERS"),
                                      env.get("AWF_TELEGRAM_API_BASE") or "https://api.telegram.org")
        if env.get("AWF_WHATSAPP_TOKEN") and env.get("AWF_WHATSAPP_PHONE_NUMBER_ID"):
            whatsapp = WhatsAppConfig(env["AWF_WHATSAPP_TOKEN"], env["AWF_WHATSAPP_PHONE_NUMBER_ID"],
                                      _list("AWF_WHATSAPP_TO"), env.get("AWF_WHATSAPP_API_VERSION") or "v21.0",
                                      env.get("AWF_WHATSAPP_TEMPLATE", ""),
                                      env.get("AWF_WHATSAPP_TEMPLATE_LANGUAGE") or "vi",
                                      env.get("AWF_WHATSAPP_API_BASE") or "https://graph.facebook.com")
        if env.get("AWF_ZALO_APP_ID") and (env.get("AWF_ZALO_REFRESH_TOKEN") or env.get("AWF_ZALO_ACCESS_TOKEN")):
            zalo = ZaloConfig(env["AWF_ZALO_APP_ID"], env.get("AWF_ZALO_APP_SECRET", ""),
                              env.get("AWF_ZALO_REFRESH_TOKEN", ""), _list("AWF_ZALO_USER_IDS"),
                              env.get("AWF_ZALO_ACCESS_TOKEN", ""),
                              env.get("AWF_ZALO_API_BASE") or "https://openapi.zalo.me",
                              env.get("AWF_ZALO_OAUTH_URL") or "https://oauth.zaloapp.com/v4/oa/access_token")
        return cls(kinds=kinds, public_url=(env.get("AWF_PUBLIC_URL") or "").rstrip("/"),
                   telegram=telegram, whatsapp=whatsapp, zalo=zalo)
