"""Model providers the factory can call: one table for workers (which keys mean which capability), the router
(which providers need a key) and the adapters (how to reach each API). ADR-010, ADR-012, ADR-013.

`api` picks the adapter:
- anthropic         Claude Messages API (Anthropic SDK)
- gemini            Gemini generateContent (REST)
- openai_responses  OpenAI Responses API (REST)
- chat_completions  OpenAI-compatible Chat Completions (REST): Qwen, Kimi, DeepSeek, MiniMax

Base URLs default to the international endpoints; each can be overridden per worker with AWF_<ID>_BASE_URL
(for example the China endpoints: dashscope.aliyuncs.com, api.moonshot.cn, api.minimaxi.com).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    api: str
    key_envs: tuple[str, ...]
    console: str  # where the key is created
    base_url: str = ""

    @property
    def capability(self) -> str:
        return f"llm-{self.id}"

    def key(self) -> str:
        return next((os.environ[n] for n in self.key_envs if os.environ.get(n)), "")

    def endpoint(self) -> str:
        return (os.environ.get(f"AWF_{self.id.upper()}_BASE_URL") or self.base_url).rstrip("/")


PROVIDERS: dict[str, Provider] = {p.id: p for p in (
    Provider("anthropic", "Claude", "anthropic", ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"), "platform.claude.com"),
    Provider("google", "Gemini", "gemini", ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "aistudio.google.com"),
    Provider("openai", "ChatGPT", "openai_responses", ("OPENAI_API_KEY",), "platform.openai.com"),
    Provider("qwen", "Qwen", "chat_completions", ("DASHSCOPE_API_KEY",), "modelstudio.console.alibabacloud.com",
             "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    Provider("kimi", "Kimi", "chat_completions", ("MOONSHOT_API_KEY", "KIMI_API_KEY"), "platform.kimi.ai",
             "https://api.moonshot.ai/v1"),
    Provider("deepseek", "DeepSeek", "chat_completions", ("DEEPSEEK_API_KEY",), "platform.deepseek.com",
             "https://api.deepseek.com"),
    Provider("minimax", "MiniMax", "chat_completions", ("MINIMAX_API_KEY",), "platform.minimax.io",
             "https://api.minimax.io/v1"),
)}

# Providers whose agents need an API key on the worker (scripted/unavailable need nothing).
LLM_PROVIDERS = tuple(PROVIDERS)


def key_capabilities() -> set[str]:
    """llm-<provider> for every provider whose key is set on this machine."""
    return {p.capability for p in PROVIDERS.values() if p.key()}
