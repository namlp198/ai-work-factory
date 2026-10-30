"""Dashboard login: exchange the admin token (once) or a one-time login ticket for a session cookie."""

from __future__ import annotations

import hmac
import threading
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from awf.security import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    new_session,
    verify_claims,
)
from awf_api.deps import AppState, get_state

router = APIRouter(prefix="/api/v1/session")

MAX_FAILURES = 10
FAILURE_WINDOW_SECONDS = 600


class _Throttle:
    """Per-client failed-login counter (single control-plane process)."""

    def __init__(self) -> None:
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._used_tickets: dict[str, float] = {}
        self._lock = threading.Lock()

    def blocked(self, client: str) -> bool:
        with self._lock:
            q = self._failures[client]
            while q and q[0] < time.time() - FAILURE_WINDOW_SECONDS:
                q.popleft()
            return len(q) >= MAX_FAILURES

    def fail(self, client: str) -> None:
        with self._lock:
            self._failures[client].append(time.time())

    def use_ticket(self, nonce: str, exp: float) -> bool:
        with self._lock:
            now = time.time()
            for key in [k for k, e in self._used_tickets.items() if e < now]:
                del self._used_tickets[key]
            if nonce in self._used_tickets:
                return False
            self._used_tickets[nonce] = exp
            return True


_throttle = _Throttle()


class LoginIn(BaseModel):
    token: str | None = Field(default=None, max_length=512)
    ticket: str | None = Field(default=None, max_length=2048)


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("")
def status(request: Request, state: AppState = Depends(get_state)) -> dict:
    expected = state.platform.settings.api_token
    if not expected:
        return {"auth_required": False, "authenticated": True}
    ok = verify_claims(expected, request.cookies.get(SESSION_COOKIE), "session") is not None
    return {"auth_required": True, "authenticated": ok}


@router.post("")
def login(body: LoginIn, request: Request, response: Response, state: AppState = Depends(get_state)) -> dict:
    expected = state.platform.settings.api_token
    if not expected:
        return {"authenticated": True, "auth_required": False}
    client = _client(request)
    if _throttle.blocked(client):
        raise HTTPException(429, "too many failed logins; try again later")
    subject = None
    if body.ticket:
        claims = verify_claims(expected, body.ticket, "login")
        if claims and _throttle.use_ticket(claims["nonce"], claims["exp"]):
            subject = claims.get("sub", "admin")
    token = (body.token or "").strip().strip("'\"").strip()
    if not body.ticket and token and hmac.compare_digest(token, expected):
        subject = "admin"
    if subject is None:
        _throttle.fail(client)
        other = state.platform.settings.worker_registration_token
        if token and other and hmac.compare_digest(token, other):
            raise HTTPException(422, "this is AWF_WORKER_REGISTRATION_TOKEN (for workers); paste AWF_API_TOKEN")
        raise HTTPException(401, "invalid token or expired login link")
    response.set_cookie(SESSION_COOKIE, new_session(expected, subject), max_age=SESSION_TTL_SECONDS,
                        httponly=True, samesite="strict", secure=request.url.scheme == "https", path="/")
    return {"authenticated": True, "auth_required": True}


@router.delete("")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"authenticated": False}
