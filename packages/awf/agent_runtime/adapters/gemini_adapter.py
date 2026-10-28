"""Gemini adapter (Gemini API generateContent over REST, manual function-calling loop).

Same contract as the Claude adapter: the session keeps the provider-native conversation and echoes the model's
turns back unchanged (Gemini attaches thought signatures to parts and requires them on the next request); the
agent runtime only sees text, tool calls and usage. REST through httpx keeps the worker free of another SDK.
Credentials: GEMINI_API_KEY (or GOOGLE_API_KEY) on the worker.
"""

from __future__ import annotations

import os
import re
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

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
# JSON Schema keywords the Gemini function-declaration schema (an OpenAPI subset) accepts.
_SCHEMA_KEYS = {"type", "format", "title", "description", "nullable", "enum", "items", "properties", "required",
                "minItems", "maxItems", "minProperties", "maxProperties", "minLength", "maxLength", "pattern",
                "minimum", "maximum", "anyOf", "default", "example", "propertyOrdering"}
_REFUSALS = {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_SAFETY"}


def api_key() -> str:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""


def classify_error(resp: httpx.Response) -> tuple[str, str]:
    """Gemini answers 429 RESOURCE_EXHAUSTED both for "slow down" (per-minute limits) and for "this key cannot
    use this model" (a daily quota used up, or a limit of 0 on the free tier): only the first is worth a retry."""
    try:
        err = resp.json().get("error") or {}
    except ValueError:
        err = {}
    text = str(err.get("message", ""))
    message = f"Gemini API {resp.status_code} {err.get('status', '')}: {text[:500]}"
    if resp.status_code == 429:
        quota_ids = [str(v.get("quotaId", "")) for d in err.get("details") or [] for v in d.get("violations") or []]
        if "limit: 0" in text or any("PerDay" in q for q in quota_ids):
            return message, "billing"
        return message, "retry"
    if resp.status_code in (500, 502, 503, 504):
        return message, "retry"
    if resp.status_code in (400, 401, 403, 404):
        return message, "fail"
    return message, "other"


def gemini_schema(schema: Any) -> Any:
    """Our tool schemas are JSON Schema; keep what Gemini understands (drops additionalProperties, $schema...)."""
    if isinstance(schema, list):
        return [gemini_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SCHEMA_KEYS:
            continue
        if key == "type" and isinstance(value, list):  # ["string", "null"] -> string + nullable
            kinds = [v for v in value if v != "null"]
            out["type"] = kinds[0] if kinds else "string"
            if "null" in value:
                out["nullable"] = True
        elif key == "properties":
            out[key] = {name: gemini_schema(sub) for name, sub in value.items()}
        elif key in ("items", "anyOf"):
            out[key] = gemini_schema(value)
        else:
            out[key] = value
    if out.get("type") == "object" and "properties" not in out:
        out["properties"] = {}
    return out


def function_names(tools: list[ToolDef]) -> dict[str, str]:
    """Our tool name -> a Gemini-safe function name (letters, digits, underscores), unique."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for tool in tools:
        base = re.sub(r"[^A-Za-z0-9_]", "_", tool.name)[:60] or "tool"
        if not re.match(r"[A-Za-z_]", base):
            base = "_" + base
        name, n = base, 2
        while name in used:
            name, n = f"{base}_{n}", n + 1
        used.add(name)
        mapping[tool.name] = name
    return mapping


class GeminiSession(ModelSession):
    def __init__(self, http: httpx.Client, *, system: str, user_message: str, tools: list[ToolDef], model: str,
                 params: dict[str, Any], key: str, base: str = API_BASE, sleep=time.sleep):
        self.http, self.model, self.params, self.key, self.base, self.sleep = http, model, params, key, base, sleep
        self.system = system
        self.to_gemini = function_names(tools)
        self.from_gemini = {v: k for k, v in self.to_gemini.items()}
        self.tools = [{"functionDeclarations": [
            {"name": self.to_gemini[t.name], "description": t.description[:1000],
             "parameters": gemini_schema(t.input_schema)} for t in tools]}] if tools else []
        self.contents: list[dict[str, Any]] = []
        self._pending: list[dict[str, Any]] = [{"text": user_message}]
        self._calls: dict[str, str] = {}  # our call id -> our tool name

    def _config(self) -> dict[str, Any]:
        config: dict[str, Any] = {"maxOutputTokens": int(self.params.get("max_tokens", 16000))}
        effort = self.params.get("effort")
        # thinkingLevel exists on Gemini 3 and later; 2.5 models use a token budget instead.
        if effort and not self.model.startswith("gemini-2"):
            config["thinkingConfig"] = {"thinkingLevel": str(effort)}
        return config

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.post_json(self.http, f"{self.base}/models/{self.model}:generateContent", body,
                              {"x-goog-api-key": self.key}, provider="google", name="Gemini", classify=classify_error)

    def send(self, tool_outcomes: list[ToolOutcome] | None = None, user_text: str | None = None) -> ModelTurn:
        parts: list[dict[str, Any]] = list(self._pending)
        self._pending = []
        for outcome in tool_outcomes or []:
            name = self._calls.get(outcome.call_id, "")
            response = {"error": outcome.content} if outcome.is_error else {"result": outcome.content}
            fr: dict[str, Any] = {"name": self.to_gemini.get(name, name), "response": response}
            if not outcome.call_id.startswith("awf-"):
                fr["id"] = outcome.call_id
            parts.append({"functionResponse": fr})
        if user_text:
            parts.append({"text": user_text})
        if parts:
            self.contents.append({"role": "user", "parts": parts})

        body: dict[str, Any] = {"systemInstruction": {"parts": [{"text": self.system}]},
                                "contents": self.contents, "generationConfig": self._config()}
        if self.tools:
            body["tools"] = self.tools
        data = self._post(body)

        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            raise ModelError(f"Gemini returned no answer (blocked: {reason})", retryable=not reason)
        candidate = candidates[0]
        finish = candidate.get("finishReason") or ""
        if finish in _REFUSALS:
            raise ModelError(f"model declined the request ({finish})", retryable=False)
        content = candidate.get("content") or {"role": "model", "parts": []}
        content.setdefault("role", "model")
        content.setdefault("parts", [])
        # Echo the model turn exactly as received: thought signatures ride on its parts.
        self.contents.append(content)

        text = "".join(p.get("text", "") for p in content["parts"] if "text" in p and not p.get("thought"))
        calls: list[ToolCall] = []
        for i, part in enumerate(content["parts"]):
            fc = part.get("functionCall")
            if not fc:
                continue
            ours = self.from_gemini.get(fc.get("name", ""), fc.get("name", ""))
            call_id = fc.get("id") or f"awf-{len(self.contents)}-{i}"
            self._calls[call_id] = ours
            calls.append(ToolCall(id=call_id, name=ours, input=dict(fc.get("args") or {})))
        if finish == "MAX_TOKENS" and calls:
            calls = []  # a call cut off by the output limit may have truncated arguments
            self._pending.append({"text": "Your previous response hit the output limit before the tool call "
                                          "finished. Make smaller tool calls (e.g. write files one at a time)."})
        usage = data.get("usageMetadata") or {}
        output_tokens = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
        stop = "tool_use" if calls else ("max_tokens" if finish == "MAX_TOKENS" else "end_turn")
        return ModelTurn(text=text, tool_calls=calls, stop_reason=stop,
                         input_tokens=int(usage.get("promptTokenCount") or 0), output_tokens=output_tokens,
                         raw={"model": data.get("modelVersion") or self.model, "id": data.get("responseId")})


class GeminiAdapter(ModelAdapter):
    provider = "google"

    def __init__(self, http: httpx.Client | None = None, key: str | None = None, base: str | None = None,
                 sleep=time.sleep):
        self.http = http or httpx.Client(timeout=REQUEST_TIMEOUT_S)
        self.key = key if key is not None else api_key()
        self.base = base or os.environ.get("AWF_GEMINI_API_BASE") or API_BASE
        self.sleep = sleep

    def start(self, *, system: str, user_message: str, tools: list[ToolDef], model: str,
              params: dict[str, Any], meta: dict[str, Any]) -> ModelSession:
        if not self.key:
            raise ModelError("GEMINI_API_KEY is not set on this worker", retryable=False)
        return GeminiSession(self.http, system=system, user_message=user_message, tools=tools, model=model,
                             params=params, key=self.key, base=self.base, sleep=self.sleep)
