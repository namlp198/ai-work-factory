"""Worker daemon: register -> heartbeat -> claim -> execute -> submit result -> cleanup."""

from __future__ import annotations

import os
import platform
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from awf.logging import get_logger, log_event
from awf.policy import PolicyConfig, PolicyEngine
from awf.worker_sdk.client import ControlPlaneClient, ControlPlaneError
from awf.worker_sdk.executor import JobExecutor

logger = get_logger("worker")

PROBED_CAPABILITIES = {
    "git": "git", "python": "python", "python3": "python", "docker": "docker", "cmake": "cmake", "ctest": "ctest",
    "dotnet": "dotnet", "npm": "npm", "node": "node", "nvcc": "cuda", "ros2": "ros2",
}


def detect_platform() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)
    return f"{system}-{arch}"


def detect_capabilities() -> list[str]:
    caps = {cap for exe, cap in PROBED_CAPABILITIES.items() if shutil.which(exe)}
    return sorted(caps)


def system_load() -> dict[str, Any]:
    load: dict[str, Any] = {}
    try:
        load["cpu_load"] = round(os.getloadavg()[0] / (os.cpu_count() or 1), 2)  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        load["cpu_load"] = None
    load["cpu_count"] = os.cpu_count()
    return load


class _LogBuffer:
    def __init__(self, client: ControlPlaneClient, job_id: str, flush_every: int = 25):
        self.client, self.job_id, self.flush_every = client, job_id, flush_every
        self.lines: list[dict[str, str]] = []
        self.lock = threading.Lock()

    def __call__(self, line: str, stream: str = "stdout") -> None:
        with self.lock:
            self.lines.append({"stream": stream, "line": line[:4000]})
            if len(self.lines) >= self.flush_every:
                self._flush_locked()

    def flush(self) -> None:
        with self.lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        lines, self.lines = self.lines, []
        try:
            self.client.log(self.job_id, lines)
        except Exception as exc:  # logging must never kill the job
            logger.warning("log upload failed: %s", exc)


class Worker:
    def __init__(self, client: ControlPlaneClient, name: str, workspace_root: Path, policy_path: Path,
                 capabilities: list[str] | None = None, labels: dict[str, Any] | None = None,
                 platform_name: str | None = None, poll_interval: float = 3.0, heartbeat_interval: float = 15.0,
                 executor: JobExecutor | None = None):
        self.client = client
        self.name = name
        self.platform = platform_name or detect_platform()
        self.capabilities = capabilities if capabilities is not None else detect_capabilities()
        self.labels = labels or {}
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.executor = executor or JobExecutor(workspace_root, PolicyEngine(PolicyConfig.load(policy_path)))
        self.running_jobs = 0
        self._stop = threading.Event()

    def register(self, registration_token: str) -> dict[str, Any]:
        data = self.client.register({
            "name": self.name,
            "platform": self.platform,
            "os_version": platform.platform(),
            "capabilities": self.capabilities,
            "labels": self.labels,
        }, registration_token)
        log_event(logger, "WORKER_REGISTERED", f"registered as {data['id']}", worker_id=data["id"],
                  platform=self.platform, capabilities=self.capabilities)
        return data

    def heartbeat(self) -> None:
        self.client.heartbeat({"status": "online", "running_jobs": self.running_jobs, **system_load()})

    def run_once(self) -> bool:
        """Claim and execute at most one job. Returns True if a job was processed."""
        job = self.client.claim()
        if not job:
            return False
        self.run_job(job)
        return True

    def run_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        self.running_jobs += 1
        buffer = _LogBuffer(self.client, job_id)
        log_event(logger, "JOB_STARTED", f"executing {job['job_type']} job", job_id=job_id, worker_id=self.client.worker_id)

        def on_state(state: str) -> None:
            try:
                self.client.progress(job_id, state)
            except ControlPlaneError as exc:
                logger.warning("progress update rejected: %s", exc)

        try:
            result = self.executor.execute(job_id, job["payload"], log=buffer, on_state=on_state)
            buffer.flush()
            if result.get("status") == "succeeded":
                self.client.complete(job_id, result)
            else:
                self.client.fail(job_id, result.get("error") or "job failed", bool(result.get("retryable", True)), result)
        except ControlPlaneError as exc:
            logger.warning("result submission rejected for job %s: %s", job_id, exc)
        except Exception as exc:
            buffer.flush()
            logger.exception("job %s crashed", job_id)
            try:
                self.client.fail(job_id, f"worker crash: {type(exc).__name__}: {exc}", True)
            except ControlPlaneError:
                pass
        finally:
            self.running_jobs -= 1

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_interval):
            try:
                self.heartbeat()
            except Exception as exc:
                logger.warning("heartbeat failed: %s", exc)

    def serve_forever(self) -> None:
        self.heartbeat()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    self._stop.wait(self.poll_interval)
            except Exception as exc:
                logger.warning("worker loop error: %s", exc)
                self._stop.wait(self.poll_interval)

    def stop(self) -> None:
        self._stop.set()
