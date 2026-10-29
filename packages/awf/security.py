"""Secret redaction and token helpers (Technical Design §22)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Any

SENSITIVE_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH")

_REDACTION_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_\-]{32,}"),  # OpenAI keys
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),  # Google (Gemini) keys
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{16,}"),
]
# Values that only look secret by where they stand (often sample passwords in tests) get their own marker, so the
# critic can tell them from the key formats above. A value already redacted above is left as it is.
_LITERAL_PATTERNS = [
    # Quoted values: "AdminPassword": "...", password: '...', apiKey = "..." (JSON, YAML, code literals).
    # Unquoted code such as `adminPassword = config["x"]` is deliberately not matched.
    re.compile(r"(?i)([A-Za-z_]*(?:password|passwd|secret|api[_-]?key|token)['\"]?\s*[:=]\s*['\"])"
               r"(?!\[REDACTED)[^\s'\"]{6,}"),
    # .env style lines: DB_PASSWORD=..., export API_TOKEN=...
    re.compile(r"(?im)^(\s*(?:export\s+)?[A-Za-z_]*(?:password|passwd|secret|api[_-]?key|token)[A-Za-z_]*\s*=\s*)"
               r"(?!\[REDACTED)[^\s'\"]{6,}"),
]

REDACTED = "[REDACTED]"
REDACTED_LITERAL = "[REDACTED-VALUE]"


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
    for pattern in _LITERAL_PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + REDACTED_LITERAL, text)
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


# ------------------------------------------------------------------ dashboard sessions / login tickets
# Signed, self-contained tokens. The key is derived from AWF_API_TOKEN, so sessions survive server
# restarts and are all invalidated by rotating the admin token.

SESSION_COOKIE = "awf_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600
LOGIN_TICKET_TTL_SECONDS = 120


def _signing_key(api_token: str) -> bytes:
    return hashlib.sha256(b"awf-session-v1:" + api_token.encode("utf-8")).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_claims(api_token: str, claims: dict[str, Any]) -> str:
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64(hmac.new(_signing_key(api_token), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_claims(api_token: str, value: str | None, purpose: str) -> dict[str, Any] | None:
    """Return the claims if the signature, purpose and expiry are valid, else None."""
    if not api_token or not value or value.count(".") != 1:
        return None
    body, sig = value.split(".")
    expected = _b64(hmac.new(_signing_key(api_token), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        claims = json.loads(_unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if claims.get("purpose") != purpose or float(claims.get("exp", 0)) < time.time():
        return None
    return claims


def new_session(api_token: str, subject: str = "admin") -> str:
    return sign_claims(api_token, {"purpose": "session", "sub": subject, "exp": int(time.time()) + SESSION_TTL_SECONDS,
                                   "nonce": secrets.token_urlsafe(8)})


def new_login_ticket(api_token: str, subject: str = "admin") -> str:
    """Short-lived, single-use ticket that `awf open` puts in the dashboard URL fragment."""
    return sign_claims(api_token, {"purpose": "login", "sub": subject,
                                   "exp": int(time.time()) + LOGIN_TICKET_TTL_SECONDS,
                                   "nonce": secrets.token_urlsafe(16)})
