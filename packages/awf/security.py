"""Secret redaction and token helpers (Technical Design §22)."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from typing import Any

SENSITIVE_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")

_REDACTION_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{16,}"),
    # Quoted values: "AdminPassword": "...", password: '...', apiKey = "..." (JSON, YAML, code literals).
    # Unquoted code such as `adminPassword = config["x"]` is deliberately not matched.
    re.compile(r"(?i)([A-Za-z_]*(?:password|passwd|secret|api[_-]?key|token)['\"]?\s*[:=]\s*['\"])[^\s'\"]{6,}"),
    # .env style lines: DB_PASSWORD=..., export API_TOKEN=...
    re.compile(r"(?im)^(\s*(?:export\s+)?[A-Za-z_]*(?:password|passwd|secret|api[_-]?key|token)[A-Za-z_]*\s*=\s*)"
               r"[^\s'\"]{6,}"),
]

REDACTED = "[REDACTED]"


def _known_secret_values() -> list[str]:
    values = []
    for name, value in os.environ.items():
        if value and len(value) >= 8 and any(m in name.upper() for m in SENSITIVE_ENV_MARKERS):
            values.append(value)
    return values


def redact(text: str) -> str:
    if not text:
        return text
    for value in _known_secret_values():
        text = text.replace(value, REDACTED)
    for pattern in _REDACTION_PATTERNS:
        text = pattern.sub(lambda m: (m.group(1) if m.lastindex else "") + REDACTED, text)
    return text


def redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: (REDACTED if _is_sensitive_key(k) else redact_obj(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    upper = str(key).upper()
    return any(m in upper for m in ("PASSWORD", "SECRET", "API_KEY", "APIKEY", "PRIVATE_KEY")) or upper in (
        "TOKEN",
        "AUTHORIZATION",
        "WORKER_TOKEN",
    )


def sanitized_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for subprocesses: strip anything that looks like a credential."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not any(m in k.upper() for m in SENSITIVE_ENV_MARKERS)
    }
    if extra:
        env.update(extra)
    return env


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str, token_hash: str | None) -> bool:
    if not token or not token_hash:
        return False
    return hmac.compare_digest(hash_token(token), token_hash)
