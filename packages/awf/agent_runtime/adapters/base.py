"""Provider-neutral model adapter interface.

A session owns the provider-native conversation (so provider-specific blocks such as thinking
blocks are echoed back unchanged); the agent runtime only sees text, tool calls and usage.
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


def _is_http_error(exc: Exception) -> bool:
    import httpx

    return isinstance(exc, httpx.HTTPError)


def retry_after(resp: Any, default: float) -> float:
    """Seconds the provider asks us to wait (Retry-After header, Gemini RetryInfo), capped at 60."""
    value = (getattr(resp, "headers", None) or {}).get("retry-after")
    try:
        if value:
            return min(60.0, max(1.0, float(value)))
    except ValueError:
        pass
    match = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', getattr(resp, "text", "") or "")
    return min(60.0, max(1.0, float(match.group(1)))) if match else default


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
    def __init__(self, message: str, retryable: bool = True, code: str | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.code = code  # MODEL_BILLING (no credit or quota left), MODEL_REJECTED, MODEL_TIMEOUT, or None


# One request may take this long; the agent's own deadline (contract timeout) bounds the whole job.
REQUEST_TIMEOUT_S = 240.0
MAX_ATTEMPTS = 4
BILLING = "MODEL_BILLING"
TIMEOUT = "MODEL_TIMEOUT"
REJECTED = "MODEL_REJECTED"  # bad key, no access to the model, unknown model


def billing_error(provider: str, message: str) -> ModelError:
    """The account cannot pay (no credit, quota used up, plan without this model): retrying will not help."""
    from awf.agent_runtime.providers import PROVIDERS

    console = PROVIDERS[provider].console if provider in PROVIDERS else ""
    hint = f" Add credit or enable billing for this API key ({console})." if console else ""
    return ModelError(f"no credit or quota left on the {provider} API account: {message}.{hint} "
                      "A subscription plan (Pro, Plus) does not pay for API keys.", retryable=False, code=BILLING)


class ModelSession(ABC):
    # Set by the agent runtime: monotonic time the job must finish by, and where progress notes go (job log).
    deadline: float | None = None
    notify: Callable[[str], None] | None = None
    sleep: Callable[[float], None] = staticmethod(time.sleep)

    def request_timeout(self) -> float:
        """Seconds the next request may take: the per-request cap, or less when the deadline is near."""
        if self.deadline is None:
            return REQUEST_TIMEOUT_S
        left = self.deadline - time.monotonic()
        if left < 5:
            raise ModelError("timeout: the agent's time limit was reached while waiting for the model",
                             retryable=True, code=TIMEOUT)
        return min(REQUEST_TIMEOUT_S, left)

    def note(self, message: str) -> None:
        if self.notify:
            self.notify(message)

    def backoff(self, seconds: float, reason: str) -> None:
        """Wait before another attempt, unless that would run past the deadline."""
        if self.deadline is not None and time.monotonic() + seconds + 5 >= self.deadline:
            raise ModelError(f"{reason}; no time left for another attempt", retryable=True, code=TIMEOUT)
        self.note(f"{reason}; retrying in {seconds:g}s")
        self.sleep(seconds)

    def post_json(self, http: Any, url: str, body: dict[str, Any], headers: dict[str, str], *, provider: str,
                  name: str, classify: Callable[[Any], tuple[str, str]]) -> dict[str, Any]:
        """POST with the shared retry policy. `classify(response)` returns (message, kind) for a non-200 answer,
        kind being "retry" (busy or rate limited), "billing" (cannot pay) or "fail" (rejected)."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            timeout = self.request_timeout()
            try:
                resp = http.post(url, json=body, headers=headers, timeout=timeout)
            except Exception as exc:  # httpx.TimeoutException, ConnectError, ...
                if not _is_http_error(exc):
                    raise
                reason = (f"{name} did not answer within {timeout:.0f}s (model busy or overloaded)"
                          if "Timeout" in type(exc).__name__ else f"could not reach the {name} API")
                if attempt == MAX_ATTEMPTS:
                    raise ModelError(f"{reason}; gave up after {MAX_ATTEMPTS} attempts", retryable=True,
                                     code=TIMEOUT) from None
                self.backoff(2 ** attempt, f"{reason} (attempt {attempt}/{MAX_ATTEMPTS})")
                continue
            if resp.status_code == 200:
                return resp.json()
            message, kind = classify(resp)
            if kind == "billing":
                raise billing_error(provider, message)
            if kind == "retry" and attempt < MAX_ATTEMPTS:
                self.backoff(retry_after(resp, 2 ** attempt), f"{message} (attempt {attempt}/{MAX_ATTEMPTS})")
                continue
            if kind == "fail":
                code = REJECTED if resp.status_code in (401, 403, 404) else None
                raise ModelError(f"model provider rejected the request: {message}", retryable=False, code=code)
            raise ModelError(message, retryable=True)
        raise ModelError(f"{name} API unavailable", retryable=True)

    @abstractmethod
    def send(self, tool_outcomes: list[ToolOutcome] | None = None, user_text: str | None = None) -> ModelTurn:
        """Send the next user turn (tool results and/or text) and return the model's reply."""


class ModelAdapter(ABC):
    provider: str = "abstract"

    @abstractmethod
    def start(self, *, system: str, user_message: str, tools: list[ToolDef], model: str,
              params: dict[str, Any], meta: dict[str, Any]) -> ModelSession:
        """Open a conversation. `meta` carries role/task/contract info (used by the scripted adapter)."""
