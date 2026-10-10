"""Provider-neutral model adapter interface.

A session owns the provider-native conversation (so provider-specific blocks such as thinking
blocks are echoed back unchanged); the agent runtime only sees text, tool calls and usage.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolOutcome:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class ModelTurn:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


class ModelError(RuntimeError):
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class ModelSession(ABC):
    @abstractmethod
    def send(self, tool_outcomes: list[ToolOutcome] | None = None, user_text: str | None = None) -> ModelTurn:
        """Send the next user turn (tool results and/or text) and return the model's reply."""


class ModelAdapter(ABC):
    provider: str = "abstract"

    @abstractmethod
    def start(self, *, system: str, user_message: str, tools: list[ToolDef], model: str,
              params: dict[str, Any], meta: dict[str, Any]) -> ModelSession:
        """Open a conversation. `meta` carries role/task/contract info (used by the scripted adapter)."""
