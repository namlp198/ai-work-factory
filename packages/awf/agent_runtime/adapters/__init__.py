"""Model adapter factory."""

from __future__ import annotations

from awf.agent_runtime.adapters.base import (
    ModelAdapter,
    ModelError,
    ModelSession,
    ModelTurn,
    ToolCall,
    ToolDef,
    ToolOutcome,
)


def get_adapter(provider: str) -> ModelAdapter:
    if provider == "scripted":
        from awf.agent_runtime.adapters.scripted import ScriptedAdapter

        return ScriptedAdapter()
    if provider == "anthropic":
        from awf.agent_runtime.adapters.anthropic_adapter import AnthropicAdapter

        return AnthropicAdapter()
    if provider == "google":
        from awf.agent_runtime.adapters.gemini_adapter import GeminiAdapter

        return GeminiAdapter()
    if provider == "openai":
        from awf.agent_runtime.adapters.openai_adapter import OpenAIAdapter

        return OpenAIAdapter()
    raise ModelError(f"model provider '{provider}' is not available on this worker", retryable=False)


__all__ = [
    "ModelAdapter",
    "ModelError",
    "ModelSession",
    "ModelTurn",
    "ToolCall",
    "ToolDef",
    "ToolOutcome",
    "get_adapter",
]
