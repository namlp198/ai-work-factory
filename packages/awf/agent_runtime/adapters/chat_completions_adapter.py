"""OpenAI-compatible Chat Completions adapter: Qwen (DashScope), Kimi (Moonshot), DeepSeek and MiniMax (ADR-013).

One manual function-calling loop for every provider that speaks `POST {base}/chat/completions`, as for the
other adapters: every tool call goes through the platform's policy engine. The assistant message is kept exactly
as returned, `reasoning_content` included: DeepSeek and Kimi reject a tool-call conversation whose earlier
reasoning was dropped (HTTP 400), and the others ignore it. Provider-specific request fields (thinking switches,
reasoning effort) come from `extra` in config/model-routing.yaml, so a new model needs no code.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from awf.agent_runtime.adapters.base import (
    ModelAdapter,
    ModelError,
    ModelSession,
    REQUEST_TIMEOUT_S,
    ModelTurn,
    ToolCall,
    ToolDef,
    ToolOutcome,
)
from awf.agent_runtime.adapters.gemini_adapter import function_names
from awf.agent_runtime.providers import PROVIDERS, Provider

_NO_RETRY_CODES = {"insufficient_quota", "insufficient_balance", "billing_hard_limit_reached", "invalid_api_key",
                   "Arrearage", "account_overdue"}


def classify_error(name: str, resp: httpx.Response) -> tuple[str, str]:
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    err = payload.get("error") if isinstance(payload.get("error"), dict) else payload
    code = str((err or {}).get("code") or (err or {}).get("type") or "")
    message = f"{name} API {resp.status_code} {code}: {str((err or {}).get('message', resp.text))[:500]}"
    if code == "invalid_api_key":
        return message, "fail"
    if code in _NO_RETRY_CODES or resp.status_code == 402:
        return message, "billing"
    if resp.status_code in (429, 500, 502, 503, 504):
        return message, "retry"
    if resp.status_code in (400, 401, 403, 404):
        return message, "fail"
    return message, "other"


class ChatCompletionsSession(ModelSession):
    def __init__(self, http: httpx.Client, provider: Provider, *, system: str, user_message: str,
                 tools: list[ToolDef], model: str, params: dict[str, Any], key: str, base: str, sleep=time.sleep):
        self.http, self.provider, self.model, self.params = http, provider, model, params
        self.key, self.base, self.sleep = key, base, sleep
        self.to_api = function_names(tools)
        self.from_api = {v: k for k, v in self.to_api.items()}
        self.tools = [{"type": "function", "function": {"name": self.to_api[t.name], "description": t.description[:1000],
                                                        "parameters": t.input_schema}} for t in tools]
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        self._pending: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        name = self.provider.label
        data = self.post_json(self.http, f"{self.base}/chat/completions", body,
                              {"Authorization": f"Bearer {self.key}"}, provider=self.provider.id, name=name,
                              classify=lambda resp: classify_error(name, resp))
        # Some gateways answer 200 with an error body (MiniMax base_resp, DashScope code).
        base_resp = data.get("base_resp") or {}
        if base_resp.get("status_code") not in (None, 0):
            raise ModelError(f"{name} API error {base_resp.get('status_code')}: "
                             f"{str(base_resp.get('status_msg', ''))[:500]}", retryable=False)
        return data

    def send(self, tool_outcomes: list[ToolOutcome] | None = None, user_text: str | None = None) -> ModelTurn:
        self.messages.extend(self._pending)
        self._pending = []
        for outcome in tool_outcomes or []:
            content = f"ERROR: {outcome.content}" if outcome.is_error else outcome.content
            self.messages.append({"role": "tool", "tool_call_id": outcome.call_id, "content": content})
        if user_text:
            self.messages.append({"role": "user", "content": user_text})

        body: dict[str, Any] = {"model": self.model, "messages": self.messages,
                                "max_tokens": int(self.params.get("max_tokens", 32000))}
        if self.tools:
            body["tools"] = self.tools
        body.update(self.params.get("extra") or {})
        data = self._post(body)

        choices = data.get("choices") or []
        if not choices:
            raise ModelError(f"{self.provider.label} returned no answer", retryable=True)
        choice = choices[0]
        finish = choice.get("finish_reason") or ""
        if finish == "content_filter":
            raise ModelError("model declined the request (content_filter)", retryable=False)
        message = {k: v for k, v in (choice.get("message") or {}).items() if v is not None}
        message["role"] = "assistant"
        message.setdefault("content", "")
        # Keep the whole assistant message (reasoning_content included) for the next request.
        self.messages.append(message)

        calls: list[ToolCall] = []
        for i, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function") or {}
            call_id = call.get("id") or f"awf-{len(self.messages)}-{i}"
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = None
            if not isinstance(args, dict) or finish == "length":
                # Malformed or cut off by the output limit: answer the call so the conversation stays valid.
                reason = ("the response hit the output limit before this call finished; make smaller tool calls"
                          if finish == "length" else "arguments were not a valid JSON object; call again")
                self._pending.append({"role": "tool", "tool_call_id": call_id, "content": f"ERROR: {reason}"})
                continue
            name = self.from_api.get(fn.get("name", ""), fn.get("name", ""))
            calls.append(ToolCall(id=call_id, name=name, input=args))
        usage = data.get("usage") or {}
        stop = "tool_use" if calls else ("max_tokens" if finish == "length" else "end_turn")
        text = message.get("content") if isinstance(message.get("content"), str) else ""
        return ModelTurn(text=text or "", tool_calls=calls, stop_reason=stop,
                         input_tokens=int(usage.get("prompt_tokens") or 0),
                         output_tokens=int(usage.get("completion_tokens") or 0),
                         raw={"model": data.get("model") or self.model, "id": data.get("id")})


class ChatCompletionsAdapter(ModelAdapter):
    def __init__(self, provider: str, http: httpx.Client | None = None, key: str | None = None,
                 base: str | None = None, sleep=time.sleep):
        self.spec = PROVIDERS[provider]
        self.provider = provider
        self.http = http or httpx.Client(timeout=REQUEST_TIMEOUT_S)
        self.key = key if key is not None else self.spec.key()
        self.base = (base or self.spec.endpoint()).rstrip("/")
        self.sleep = sleep

    def start(self, *, system: str, user_message: str, tools: list[ToolDef], model: str,
              params: dict[str, Any], meta: dict[str, Any]) -> ModelSession:
        if not self.key:
            raise ModelError(f"{' or '.join(self.spec.key_envs)} is not set on this worker", retryable=False)
        return ChatCompletionsSession(self.http, self.spec, system=system, user_message=user_message, tools=tools,
                                      model=model, params=params, key=self.key, base=self.base, sleep=self.sleep)
