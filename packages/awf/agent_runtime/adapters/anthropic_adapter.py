"""Claude adapter (Anthropic Python SDK, Messages API, manual tool-use loop).

A manual loop (rather than the SDK tool runner) is used because every tool call must pass through
the platform's Policy Engine and be recorded for audit, and the loop must stay provider-neutral.
"""

from __future__ import annotations

from typing import Any

import anthropic

from awf.agent_runtime.adapters.base import (
    REJECTED,
    TIMEOUT,
    ModelAdapter,
    ModelError,
    billing_error,
    ModelSession,
    REQUEST_TIMEOUT_S,
    ModelTurn,
    ToolCall,
    ToolDef,
    ToolOutcome,
)

# Models that accept server-side refusal fallbacks in the `"default"` form.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that do not accept output_config.effort.
_NO_EFFORT_MODELS = {"claude-haiku-4-5"}
# Phrases of a 400 that means the account cannot pay (prepaid credit used up), not a malformed request.
_BILLING_PHRASES = ("credit balance", "billing", "purchase credits")


class AnthropicSession(ModelSession):
    def __init__(self, client: anthropic.Anthropic, *, system: str, user_message: str,
                 tools: list[ToolDef], model: str, params: dict[str, Any]):
        self.client = client
        self.system = system
        self.model = model
        self.params = params
        self.tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]
        self.messages: list[dict[str, Any]] = []
        self._pending_user: list[dict[str, Any]] = [{"type": "text", "text": user_message}]

    def send(self, tool_outcomes: list[ToolOutcome] | None = None, user_text: str | None = None) -> ModelTurn:
        content: list[dict[str, Any]] = list(self._pending_user)
        self._pending_user = []
        # All tool results for one assistant turn go back in a single user message.
        for outcome in tool_outcomes or []:
            content.append({"type": "tool_result", "tool_use_id": outcome.call_id,
                            "content": outcome.content, "is_error": outcome.is_error})
        if user_text:
            content.append({"type": "text", "text": user_text})
        if content:
            self.messages.append({"role": "user", "content": content})

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(self.params.get("max_tokens", 16000)),
            "system": self.system,
            "tools": self.tools,
            "messages": self.messages,
            "cache_control": {"type": "ephemeral"},
        }
        effort = self.params.get("effort")
        if effort and self.model not in _NO_EFFORT_MODELS:
            kwargs["output_config"] = {"effort": effort}
        if self.model in _FALLBACK_MODELS:
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        try:
            # The SDK retries busy answers itself; the timeout keeps each try inside the agent's deadline.
            response = self.client.beta.messages.create(**kwargs, timeout=self.request_timeout())
        except anthropic.BadRequestError as exc:
            if any(p in str(exc.message).lower() for p in _BILLING_PHRASES):
                raise billing_error("anthropic", str(exc.message)[:500]) from exc
            raise ModelError(f"bad request: {exc.message}", retryable=False) from exc
        except anthropic.APITimeoutError as exc:
            raise ModelError("Claude did not answer in time (model busy or overloaded)", retryable=True,
                             code=TIMEOUT) from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as exc:
            raise ModelError(f"model provider rejected the request: {exc.message}", retryable=False,
                             code=REJECTED) from exc
        except anthropic.RateLimitError as exc:
            raise ModelError("rate limited by model provider", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise ModelError(f"model provider error {exc.status_code}", retryable=exc.status_code >= 500) from exc
        except anthropic.APIConnectionError as exc:
            raise ModelError("could not reach model provider", retryable=True) from exc

        if response.stop_reason == "refusal":
            raise ModelError("model declined the request (refusal)", retryable=False)

        # Echo the full content (including thinking blocks) back unchanged on the next turn.
        self.messages.append({"role": "assistant", "content": response.content})
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=b.name, input=dict(b.input or {}))
                 for b in response.content if b.type == "tool_use"]
        if response.stop_reason == "max_tokens" and calls:
            # A tool call cut off by max_tokens may carry truncated input; do not execute it.
            calls = []
            self._pending_user.append({"type": "text", "text": (
                "Your previous response hit the output limit before the tool call finished. "
                "Make smaller tool calls (e.g. write files one at a time).")})
        usage = response.usage
        input_tokens = (usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0) \
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        return ModelTurn(text=text, tool_calls=calls, stop_reason=response.stop_reason or "",
                         input_tokens=input_tokens, output_tokens=usage.output_tokens or 0,
                         raw={"model": response.model, "id": response.id})


class AnthropicAdapter(ModelAdapter):
    provider = "anthropic"

    def __init__(self, client: anthropic.Anthropic | None = None):
        self._client = client

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            # Credentials resolve from the environment (ANTHROPIC_API_KEY, auth token or profile).
            self._client = anthropic.Anthropic(max_retries=2, timeout=REQUEST_TIMEOUT_S)
        return self._client

    def start(self, *, system: str, user_message: str, tools: list[ToolDef], model: str,
              params: dict[str, Any], meta: dict[str, Any]) -> ModelSession:
        return AnthropicSession(self.client, system=system, user_message=user_message,
                                tools=tools, model=model, params=params)
