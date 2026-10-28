"""OpenAI adapter (ChatGPT models through the Responses API over REST, manual function-calling loop).

Same contract as the Claude and Gemini adapters: the session keeps the provider-native conversation and passes
the model's output items back unchanged (reasoning items carry encrypted reasoning that the next request needs);
the agent runtime only sees text, tool calls and usage. Requests use `store: false`, so OpenAI keeps no
conversation state: the full item list is resent each turn. REST through httpx keeps the worker free of another
SDK. Credentials: OPENAI_API_KEY on the worker.
"""

from __future__ import annotations

import json
import os
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

API_BASE = "https://api.openai.com/v1"
# 429 with these codes means the account cannot pay, not that it is busy: retrying will not help.
_NO_RETRY_CODES = {"insufficient_quota", "billing_hard_limit_reached"}


def api_key() -> str:
    return os.environ.get("OPENAI_API_KEY") or ""


def classify_error(resp: httpx.Response) -> tuple[str, str]:
    try:
        err = resp.json().get("error") or {}
    except ValueError:
        err = {}
    code = err.get("code") or err.get("type") or ""
    message = f"OpenAI API {resp.status_code} {code}: {str(err.get('message', ''))[:500]}"
    if code in _NO_RETRY_CODES:
        return message, "billing"
    if resp.status_code in (429, 500, 502, 503, 504):
        return message, "retry"
    if resp.status_code in (400, 401, 403, 404):
        return message, "fail"
    return message, "other"


class OpenAISession(ModelSession):
    def __init__(self, http: httpx.Client, *, system: str, user_message: str, tools: list[ToolDef], model: str,
                 params: dict[str, Any], key: str, base: str = API_BASE, sleep=time.sleep):
        self.http, self.model, self.params, self.key, self.base, self.sleep = http, model, params, key, base, sleep
        self.system = system
        self.to_openai = function_names(tools)
        self.from_openai = {v: k for k, v in self.to_openai.items()}
        self.tools = [{"type": "function", "name": self.to_openai[t.name], "description": t.description[:1000],
                       "parameters": t.input_schema, "strict": False} for t in tools]
        self.items: list[dict[str, Any]] = []
        self._pending: list[dict[str, Any]] = [{"role": "user", "content": user_message}]

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.post_json(self.http, f"{self.base}/responses", body, {"Authorization": f"Bearer {self.key}"},
                              provider="openai", name="OpenAI", classify=classify_error)

    def send(self, tool_outcomes: list[ToolOutcome] | None = None, user_text: str | None = None) -> ModelTurn:
        self.items.extend(self._pending)
        self._pending = []
        for outcome in tool_outcomes or []:
            output = f"ERROR: {outcome.content}" if outcome.is_error else outcome.content
            self.items.append({"type": "function_call_output", "call_id": outcome.call_id, "output": output})
        if user_text:
            self.items.append({"role": "user", "content": user_text})

        body: dict[str, Any] = {"model": self.model, "instructions": self.system, "input": self.items,
                                "store": False, "max_output_tokens": int(self.params.get("max_tokens", 32000))}
        if self.tools:
            body["tools"] = self.tools
        if self.params.get("effort"):
            body["reasoning"] = {"effort": str(self.params["effort"])}
        data = self._post(body)

        output = data.get("output") or []
        # Pass every output item back unchanged: reasoning items must accompany the function calls they led to.
        self.items.extend(output)
        texts: list[str] = []
        calls: list[ToolCall] = []
        for item in output:
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "refusal":
                        raise ModelError(f"model declined the request: {str(part.get('refusal', ''))[:300]}",
                                         retryable=False)
                    if part.get("type") == "output_text":
                        texts.append(part.get("text", ""))
            elif kind == "function_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = None
                name = self.from_openai.get(item.get("name", ""), item.get("name", ""))
                if not isinstance(args, dict):
                    # Malformed arguments: answer the call with an error so the conversation stays valid.
                    self._pending.append({"type": "function_call_output", "call_id": item.get("call_id"),
                                          "output": "ERROR: arguments were not a valid JSON object; call again."})
                    continue
                calls.append(ToolCall(id=item.get("call_id") or item.get("id") or f"awf-{len(self.items)}",
                                      name=name, input=args))
        truncated = data.get("status") == "incomplete" and \
            (data.get("incomplete_details") or {}).get("reason") == "max_output_tokens"
        if truncated and calls:
            # A call cut off by the output limit may carry truncated arguments: answer it instead of running it.
            for call in calls:
                self._pending.append({"type": "function_call_output", "call_id": call.id,
                                      "output": "ERROR: the response hit the output limit before this call finished. "
                                                "Make smaller tool calls (e.g. write files one at a time)."})
            calls = []
        usage = data.get("usage") or {}
        stop = "tool_use" if calls else ("max_tokens" if truncated else "end_turn")
        return ModelTurn(text="".join(texts), tool_calls=calls, stop_reason=stop,
                         input_tokens=int(usage.get("input_tokens") or 0),
                         output_tokens=int(usage.get("output_tokens") or 0),
                         raw={"model": data.get("model") or self.model, "id": data.get("id")})


class OpenAIAdapter(ModelAdapter):
    provider = "openai"

    def __init__(self, http: httpx.Client | None = None, key: str | None = None, base: str | None = None,
                 sleep=time.sleep):
        self.http = http or httpx.Client(timeout=REQUEST_TIMEOUT_S)
        self.key = key if key is not None else api_key()
        self.base = base or os.environ.get("AWF_OPENAI_API_BASE") or API_BASE
        self.sleep = sleep

    def start(self, *, system: str, user_message: str, tools: list[ToolDef], model: str,
              params: dict[str, Any], meta: dict[str, Any]) -> ModelSession:
        if not self.key:
            raise ModelError("OPENAI_API_KEY is not set on this worker", retryable=False)
        return OpenAISession(self.http, system=system, user_message=user_message, tools=tools, model=model,
                             params=params, key=self.key, base=self.base, sleep=self.sleep)
