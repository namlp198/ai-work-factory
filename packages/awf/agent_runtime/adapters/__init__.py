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
