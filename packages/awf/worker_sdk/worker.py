"""Worker daemon: register -> heartbeat -> claim -> execute -> submit result -> cleanup.

With max_jobs > 1 the daemon executes up to that many jobs at once, each on its own thread in its own
workspace; it only asks for work while a slot is free, and the control plane never hands it more."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from awf.logging import get_logger, log_event
from awf.policy import PolicyConfig, PolicyEngine
from awf.worker_sdk.claude_code import CAPABILITY as CLAUDE_CODE_CAPABILITY
from awf.worker_sdk.claude_code import claude_code_bin
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


def _probe(argv: list[str], timeout: float = 15) -> str:
    """stdout of a quick tool query, "" when the tool is missing, slow or fails."""
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def dotnet_capabilities(list_sdks: str, list_runtimes: str) -> set[str]:
    """`dotnet --list-sdks` / `--list-runtimes` -> dotnet-sdk-<major>, dotnet-windowsdesktop.

    A Linux machine can have the .NET SDK too, so `dotnet` alone does not say a WPF/WinForms project builds
    there; the Windows Desktop runtime only exists on Windows."""
    caps: set[str] = set()
    for line in list_sdks.splitlines():
        m = re.match(r"\s*(\d+)\.\d+", line)
        if m:
            caps.add(f"dotnet-sdk-{m.group(1)}")
    if any(line.strip().startswith("Microsoft.WindowsDesktop.App ") for line in list_runtimes.splitlines()):
        caps.add("dotnet-windowsdesktop")
    return caps


def visual_studio_capabilities(vswhere_json: str) -> set[str]:
    """vswhere output -> msbuild, vs-<major> (classic .NET Framework / C++ builds need Visual Studio's MSBuild)."""
    try:
        installs = json.loads(vswhere_json or "[]")
    except ValueError:
        return set()
    caps: set[str] = set()
    for item in installs if isinstance(installs, list) else []:
        major = str(item.get("installationVersion", "")).split(".")[0]
        if major.isdigit():
            caps.update({"msbuild", f"vs-{({'16': '2019', '17': '2022', '18': '2026'}).get(major, major)}"})
    return caps


def _vswhere() -> str | None:
    root = os.environ.get("ProgramFiles(x86)") or os.environ.get("ProgramFiles")
    path = Path(root) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe" if root else None
    return str(path) if path and path.is_file() else None


def detect_capabilities() -> list[str]:
    caps = {cap for exe, cap in PROBED_CAPABILITIES.items() if shutil.which(exe)}
    if "dotnet" in caps:
        caps |= dotnet_capabilities(_probe(["dotnet", "--list-sdks"]), _probe(["dotnet", "--list-runtimes"]))
    if shutil.which("msbuild"):
        caps.add("msbuild")
    if claude_code_bin():
        caps.add(CLAUDE_CODE_CAPABILITY)
    vswhere = _vswhere()
    if vswhere:
        caps |= visual_studio_capabilities(
            _probe([vswhere, "-products", "*", "-requires", "Microsoft.Component.MSBuild", "-format", "json"]))
    return sorted(caps)


# Model API keys on this machine -> llm-<provider> capabilities. Agent jobs run on the worker, so a run routed
# to a provider only goes to machines that hold its key.
LLM_KEYS = {"llm-anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
            "llm-google": ("GEMINI_API_KEY", "GOOGLE_API_KEY")}


def llm_capabilities() -> set[str]:
    return {cap for cap, names in LLM_KEYS.items() if any(os.environ.get(n) for n in names)}


def split_list(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def resolve_capabilities(override: str | None = None, extra: str | None = None) -> list[str]:
    """AWF_WORKER_CAPABILITIES replaces detection entirely; AWF_WORKER_EXTRA_CAPABILITIES adds to it
    (hardware or SDKs no probe can see, e.g. realsense, robo-station-sdk). llm-<provider> always follows the
    API keys actually present, whatever the override says."""
    caps = set(split_list(override)) if split_list(override) else set(detect_capabilities())
    caps = {c for c in caps if not c.startswith("llm-")} | llm_capabilities()
    return sorted(caps | set(split_list(extra)))


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
                 executor: JobExecutor | None = None, machine_id: str | None = None, max_jobs: int = 1):
        self.client = client
        self.name = name
        self.machine_id = machine_id
        self.platform = platform_name or detect_platform()
        self.capabilities = capabilities if capabilities is not None else detect_capabilities()
        self.labels = labels or {}
        self.poll_interval = poll_interval
        self.heartbeat_interval = heartbeat_interval
        self.executor = executor or JobExecutor(workspace_root, PolicyEngine(PolicyConfig.load(policy_path)))
        self.max_jobs = max(1, int(max_jobs))
        self.running_jobs = 0
        self._slots = threading.Semaphore(self.max_jobs)
        self._count_lock = threading.Lock()
        self._threads: set[threading.Thread] = set()
        self._stop = threading.Event()

    def register(self, registration_token: str) -> dict[str, Any]:
        data = self.client.register({
            "name": self.name,
            "platform": self.platform,
            "os_version": platform.platform(),
            "capabilities": self.capabilities,
            "labels": self.labels,
            "machine_id": self.machine_id,
            "max_jobs": self.max_jobs,
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
        with self._count_lock:
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
        except Exception as exc:  # executor bugs must still produce a reportable failure
            logger.exception("job %s crashed", job_id)
            result = {"status": "failed", "error": f"worker crash: {type(exc).__name__}: {exc}", "retryable": True}
        try:
            buffer.flush()
            self._submit(job_id, result)
        finally:
            with self._count_lock:
                self.running_jobs -= 1

    def _submit(self, job_id: str, result: dict[str, Any]) -> None:
        """Deliver the result, retrying through network outages (see ControlPlaneClient.result_retry_seconds).
        If it still cannot be delivered, the job's lease expires on the control plane and it is requeued."""
        try:
            if result.get("status") == "succeeded":
                self.client.complete(job_id, result)
            else:
                self.client.fail(job_id, result.get("error") or "job failed", bool(result.get("retryable", True)), result)
            log_event(logger, "JOB_RESULT_DELIVERED", "result delivered", job_id=job_id, status=result.get("status"))
        except ControlPlaneError as exc:
            if exc.status == 409:
                logger.warning("result for job %s not accepted (already recorded, reassigned or cancelled)", job_id)
            else:
                logger.error("result for job %s could not be delivered: %s", job_id, exc)

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
            # Ask for work only with a free slot; wake up as soon as one frees (or to notice stop()).
            if not self._slots.acquire(timeout=self.poll_interval):
                continue
            try:
                job = self.client.claim()
            except Exception as exc:
                self._slots.release()
                logger.warning("worker loop error: %s", exc)
                self._stop.wait(self.poll_interval)
                continue
            if not job:
                self._slots.release()
                self._stop.wait(self.poll_interval)
                continue
            self._start(job)

    def _start(self, job: dict[str, Any]) -> None:
        def run() -> None:
            try:
                self.run_job(job)
            finally:
                self._threads.discard(threading.current_thread())
                self._slots.release()

        # Daemon threads: stopping the process abandons running jobs, which the control plane requeues when
        # this machine registers again (or their lease expires) - the same as a crash with one job.
        thread = threading.Thread(target=run, name=f"job-{job['id'][:8]}", daemon=True)
        self._threads.add(thread)
        thread.start()

    def wait_idle(self, timeout: float | None = None) -> bool:
        """Wait until no job is executing (tests, graceful shutdown). True when idle."""
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in list(self._threads):
            thread.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
        return not any(t.is_alive() for t in list(self._threads))

    def stop(self) -> None:
        self._stop.set()
