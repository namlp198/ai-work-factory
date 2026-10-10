"""HTTP client for the worker protocol (Design §7 Worker Jobs, §11)."""

from __future__ import annotations

from typing import Any

import httpx


class ControlPlaneError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"control plane returned {status}: {detail}")
        self.status = status


class ControlPlaneClient:
    def __init__(self, base_url: str = "", http: httpx.Client | None = None, timeout: float = 60.0):
        self.http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self.worker_token: str | None = None
        self.worker_id: str | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.worker_token}"} if self.worker_token else {}

    def _call(self, method: str, url: str, **kwargs: Any) -> Any:
        resp = self.http.request(method, url, headers={**self._headers(), **kwargs.pop("headers", {})}, **kwargs)
        if resp.status_code >= 400:
            raise ControlPlaneError(resp.status_code, resp.text[:500])
        return resp.json() if resp.content else None

    def register(self, registration: dict[str, Any], registration_token: str) -> dict[str, Any]:
        data = self._call("POST", "/api/v1/workers/register", json=registration,
                          headers={"X-Registration-Token": registration_token})
        self.worker_id = data["id"]
        self.worker_token = data["worker_token"]
        return data

    def heartbeat(self, status: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/api/v1/workers/{self.worker_id}/heartbeat", json=status)

    def claim(self) -> dict[str, Any] | None:
        data = self._call("POST", f"/api/v1/workers/{self.worker_id}/claim")
        return data.get("job") if data else None

    def progress(self, job_id: str, state: str) -> None:
        self._call("POST", f"/api/v1/jobs/{job_id}/progress", json={"state": state})

    def log(self, job_id: str, lines: list[dict[str, str]]) -> None:
        if lines:
            self._call("POST", f"/api/v1/jobs/{job_id}/log", json={"lines": lines})

    def complete(self, job_id: str, result: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/api/v1/jobs/{job_id}/complete", json=result)

    def fail(self, job_id: str, error: str, retryable: bool, result: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._call("POST", f"/api/v1/jobs/{job_id}/fail",
                          json={"error": error, "retryable": retryable, "result": result or {}})
