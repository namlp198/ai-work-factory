"""HTTP client for the worker protocol (Design §7 Worker Jobs, §11).

Workers always connect *out* to the control plane (no inbound ports), so they work behind NAT and
site firewalls. Over the internet the connection must be HTTPS; transient failures are retried with
exponential backoff, and results are re-sent until the control plane acknowledges them.
"""

from __future__ import annotations

import ipaddress
import os
import random
import time
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from awf.logging import get_logger

logger = get_logger("worker.client")

RETRYABLE_STATUS = {429, 502, 503, 504}
PRIVATE_SUFFIXES = (".local", ".lan", ".internal", ".home.arpa", ".ts.net")  # .ts.net: Tailscale (WireGuard)
CGNAT = ipaddress.ip_network("100.64.0.0/10")  # Tailscale / carrier-grade NAT addresses


class ControlPlaneError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"control plane returned {status}: {detail}" if status else detail)
        self.status = status


class InsecureTransport(ValueError):
    pass


def _truthy(value: str | None) -> bool:
    return (value or "").lower() in ("1", "true", "yes")


def check_transport_security(url: str) -> None:
    """Refuse to send worker tokens and source code over plain HTTP to a public address."""
    parsed = urlparse(url)
    if parsed.scheme == "https" or not parsed.hostname or _truthy(os.environ.get("AWF_ALLOW_INSECURE_HTTP")):
        return
    host = parsed.hostname.lower()
    if host == "localhost" or "." not in host or host.endswith(PRIVATE_SUFFIXES):
        return
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback or ip.is_private or ip.is_link_local or (ip.version == 4 and ip in CGNAT):
            return
    except ValueError:
        pass
    raise InsecureTransport(
        f"{url} is a public address over plain HTTP. Use https:// (reverse proxy or tunnel) or a private network "
        f"such as Tailscale/VPN. Set AWF_ALLOW_INSECURE_HTTP=1 only for testing.")


class ControlPlaneClient:
    def __init__(self, base_url: str = "", http: httpx.Client | None = None, timeout: float = 60.0,
                 result_retry_seconds: float | None = None, sleep: Callable[[float], None] = time.sleep):
        if http is None:
            check_transport_security(base_url)
            verify: Any = os.environ.get("AWF_CA_BUNDLE") or True  # private CA for self-hosted TLS
            http = httpx.Client(base_url=base_url, timeout=timeout, verify=verify)
        self.http = http
        self.worker_token: str | None = None
        self.worker_id: str | None = None
        self.result_retry_seconds = (result_retry_seconds if result_retry_seconds is not None
                                     else float(os.environ.get("AWF_RESULT_RETRY_SECONDS", "1800")))
        self._sleep = sleep

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.worker_token}"} if self.worker_token else {}

    def _call(self, method: str, url: str, retry_for: float = 0.0, **kwargs: Any) -> Any:
        """One request; with `retry_for` > 0, retry network errors and 429/5xx-gateway responses with backoff."""
        deadline = time.monotonic() + retry_for
        delay = 1.0
        extra_headers = kwargs.pop("headers", {})
        while True:
            try:
                resp = self.http.request(method, url, headers={**self._headers(), **extra_headers}, **kwargs)
                if resp.status_code in RETRYABLE_STATUS and time.monotonic() < deadline:
                    raise httpx.TransportError(f"retryable status {resp.status_code}")
                if resp.status_code >= 400:
                    raise ControlPlaneError(resp.status_code, resp.text[:500])
                return resp.json() if resp.content else None
            except httpx.TransportError as exc:
                if time.monotonic() >= deadline:
                    raise ControlPlaneError(0, f"control plane unreachable: {type(exc).__name__}: {exc}") from exc
                logger.warning("control plane call %s %s failed (%s); retrying in %.0fs", method, url, exc, delay)
                self._sleep(delay + random.uniform(0, delay / 4))
                delay = min(delay * 2, 60.0)

    def register(self, registration: dict[str, Any], registration_token: str) -> dict[str, Any]:
        data = self._call("POST", "/api/v1/workers/register", json=registration,
                          headers={"X-Registration-Token": registration_token})
        self.worker_id = data["id"]
        self.worker_token = data["worker_token"]
        return data

    def heartbeat(self, status: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/api/v1/workers/{self.worker_id}/heartbeat", json=status)

    def claim(self) -> dict[str, Any] | None:
        # No retry: if a claim response is lost the job's lease expires and it is requeued.
        data = self._call("POST", f"/api/v1/workers/{self.worker_id}/claim")
        return data.get("job") if data else None

    def progress(self, job_id: str, state: str) -> None:
        # Progress and log lines are best-effort: never stall the job during an outage for them.
        self._call("POST", f"/api/v1/jobs/{job_id}/progress", retry_for=5, json={"state": state})

    def log(self, job_id: str, lines: list[dict[str, str]]) -> None:
        if lines:
            self._call("POST", f"/api/v1/jobs/{job_id}/log", retry_for=5, json={"lines": lines})

    def complete(self, job_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/api/v1/jobs/{job_id}/complete", retry_for=self.result_retry_seconds, json=result)

    def fail(self, job_id: str, error: str, retryable: bool, result: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._call("POST", f"/api/v1/jobs/{job_id}/fail", retry_for=self.result_retry_seconds,
                          json={"error": error, "retryable": retryable, "result": result or {}})
