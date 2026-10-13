"""Machine setup helpers: `awf env init`, `awf doctor`.

Goal: every machine runs the same source and the same commands; the only per-machine input is `.env`
(role, URLs, tokens, credentials), which is generated here and never committed.
"""

from __future__ import annotations

import importlib.metadata as md
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROLES = ("all", "control-plane", "worker")
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


# ------------------------------------------------------------------------------------------- .env
def _set_key(lines: list[str], key: str, value: str) -> list[str]:
    pattern = re.compile(rf"^\s*#?\s*{re.escape(key)}=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{key}={value}"
            return lines
    lines.append(f"{key}={value}")
    return lines


def env_init(home: Path, role: str, control_url: str | None = None, registration_token: str | None = None,
             api_host: str | None = None, force: bool = False, api_port: int | None = None,
             public_url: str | None = None) -> int:
    """Write this machine's .env. `public_url` is how workers and browsers reach the control plane from
    other sites (https://factory.example.com via reverse proxy/tunnel, or http://factory.tailnet.ts.net:8000)."""
    target = home / ".env"
    if target.exists() and not force:
        print(f".env already exists at {target}; keeping it (use --force to regenerate).")
        return 0
    if role == "worker" and not (control_url and registration_token):
        print("role 'worker' needs --control-url and --registration-token (printed by the control-plane setup).")
        return 2
    public_url = (public_url or "").rstrip("/") or None
    lines = (home / ".env.example").read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {"AWF_ROLE": role}
    if role in ("all", "control-plane"):
        values["AWF_API_TOKEN"] = secrets.token_urlsafe(32)
        values["AWF_WORKER_REGISTRATION_TOKEN"] = registration_token or secrets.token_urlsafe(32)
        if api_host:
            values["AWF_API_HOST"] = api_host
        elif public_url and public_url.startswith("https://"):
            values["AWF_API_HOST"] = "127.0.0.1"  # TLS terminates in a reverse proxy/tunnel on this machine
        else:
            values["AWF_API_HOST"] = "0.0.0.0" if role == "control-plane" else "127.0.0.1"
        values["AWF_API_PORT"] = str(api_port or 8000)
        values["AWF_CONTROL_URL"] = control_url or f"http://127.0.0.1:{values['AWF_API_PORT']}"
        if public_url:
            values["AWF_PUBLIC_URL"] = public_url
    else:
        values["AWF_WORKER_REGISTRATION_TOKEN"] = registration_token or ""
        values["AWF_CONTROL_URL"] = control_url or ""
    for key, value in values.items():
        lines = _set_key(lines, key, value)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {target} (role={role})")
    if role in ("all", "control-plane"):
        url = public_url or _advertised_url(values["AWF_API_HOST"], values["AWF_API_PORT"])
        token = values["AWF_WORKER_REGISTRATION_TOKEN"]
        print("\nSet up each worker machine with (keep the token secret):")
        print(f"  Windows:  .\\scripts\\bootstrap.ps1 -Role worker -ControlUrl {url} -RegistrationToken {token}")
        print(f"  Linux:    scripts/bootstrap.sh --role worker --control-url {url} --registration-token {token}")
        print(f"Dashboard: {url}/  (admin token: AWF_API_TOKEN in {target}; or run `awf open` here)")
    return 0


def _advertised_url(host: str, port: str) -> str:
    if host in ("0.0.0.0", "::"):
        host = socket.gethostname()
    return f"http://{host}:{port}"


# ------------------------------------------------------------------------------------------- doctor
@dataclass
class Check:
    name: str
    status: str  # OK | WARN | FAIL
    detail: str = ""


def _locked_versions(home: Path) -> dict[str, str]:
    locked: dict[str, str] = {}
    lock = home / "requirements.lock"
    if not lock.is_file():
        return locked
    for line in lock.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" not in line:
            continue
        spec, _, marker = line.partition(";")
        if marker and "win32" in marker and sys.platform != "win32":
            continue
        name, version = spec.split("==", 1)
        locked[name.strip().lower().replace("_", "-")] = version.strip()
    return locked


def _check_python() -> Check:
    v = sys.version_info
    want = Path(__file__).resolve().parents[2].joinpath(".python-version")
    pinned = want.read_text(encoding="utf-8").strip() if want.is_file() else "3.12"
    have = f"{v.major}.{v.minor}"
    if have == pinned:
        return Check("python", "OK", f"{sys.version.split()[0]} ({sys.executable})")
    if v >= (3, 11):
        return Check("python", "WARN", f"{have} differs from pinned {pinned}; run scripts/bootstrap to use {pinned}")
    return Check("python", "FAIL", f"{have} is too old; need {pinned}")


def _check_lock(home: Path) -> Check:
    locked = _locked_versions(home)
    if not locked:
        return Check("dependencies", "WARN", "requirements.lock not found")
    drift = []
    for name, version in locked.items():
        try:
            installed = md.version(name)
        except md.PackageNotFoundError:
            drift.append(f"{name} missing")
            continue
        if installed != version:
            drift.append(f"{name} {installed} != {version}")
    if drift:
        return Check("dependencies", "FAIL", "; ".join(drift[:8]) + " -> rerun scripts/bootstrap")
    return Check("dependencies", "OK", f"{len(locked)} packages match requirements.lock")


def _check_git() -> Check:
    exe = shutil.which("git")
    if not exe:
        return Check("git", "FAIL", "git not found on PATH")
    out = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip()
    return Check("git", "OK", out)


def _check_definitions() -> Check:
    try:
        from awf.agent_runtime import AgentRegistry
        from awf.model_router import ModelRouter
        from awf.policy import PolicyConfig
        from awf.settings import get_settings
        from awf.tool_sdk import JOB_TOOLS
        from awf.workflow_engine import WorkflowRegistry

        s = get_settings()
        agents = AgentRegistry(s.agents_dir)
        workflows = WorkflowRegistry(s.workflows_dir, known_roles=agents.roles(), known_tools=JOB_TOOLS)
        ModelRouter.load(s.config_dir / "model-routing.yaml")
        PolicyConfig.load(s.config_dir / "policies.yaml")
        return Check("definitions", "OK", f"{len(agents.roles())} agents, {len(workflows.all())} workflows, config valid")
    except Exception as exc:
        return Check("definitions", "FAIL", f"{type(exc).__name__}: {exc}")


def _check_database() -> Check:
    try:
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
        from alembic.config import Config
        from sqlalchemy import create_engine

        from awf.settings import get_settings

        s = get_settings()
        cfg = Config(str(s.home / "alembic.ini"))
        cfg.set_main_option("script_location", str(s.home / "infrastructure" / "migrations"))
        head = ScriptDirectory.from_config(cfg).get_current_head()
        engine = create_engine(s.database_url)
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
        engine.dispose()
        if current != head:
            return Check("database", "WARN", f"schema at {current}, head is {head}; `awf start` migrates automatically")
        return Check("database", "OK", f"{_safe_url(s.database_url)} at revision {head}")
    except Exception as exc:
        return Check("database", "FAIL", f"{type(exc).__name__}: {exc}")


def _safe_url(url: str) -> str:
    return re.sub(r"//([^:/@]+):[^@]+@", r"//\1:***@", url)


def _check_public_url() -> Check:
    from awf.worker_sdk.client import InsecureTransport, check_transport_security

    url = os.environ.get("AWF_PUBLIC_URL", "")
    if not url:
        return Check("public url", "OK", "not set (workers on this LAN/VPN only)")
    try:
        check_transport_security(url)
    except InsecureTransport as exc:
        return Check("public url", "FAIL", str(exc))
    return Check("public url", "OK", url)


def _check_api_exposure() -> Check:
    host = os.environ.get("AWF_API_HOST", "127.0.0.1")
    if host not in LOOPBACK and not os.environ.get("AWF_API_TOKEN"):
        return Check("api exposure", "FAIL", f"AWF_API_HOST={host} without AWF_API_TOKEN")
    if host not in LOOPBACK and not os.environ.get("AWF_WORKER_REGISTRATION_TOKEN"):
        return Check("api exposure", "FAIL", f"AWF_API_HOST={host} without AWF_WORKER_REGISTRATION_TOKEN")
    return Check("api exposure", "OK", f"bind {host}:{os.environ.get('AWF_API_PORT', '8000')}")


def _check_control_plane_reachable() -> Check:
    import httpx

    from awf.worker_sdk.client import InsecureTransport, check_transport_security

    url = os.environ.get("AWF_CONTROL_URL", "")
    if not url:
        return Check("control plane", "FAIL", "AWF_CONTROL_URL is not set")
    try:
        check_transport_security(url)
    except InsecureTransport as exc:
        return Check("control plane", "FAIL", str(exc))
    try:
        resp = httpx.get(url.rstrip("/") + "/health", timeout=5)
        resp.raise_for_status()
        return Check("control plane", "OK", f"{url} -> {resp.json().get('status')} v{resp.json().get('version')}")
    except Exception as exc:
        return Check("control plane", "WARN", f"{url} not reachable now ({type(exc).__name__}); start it first")


def _check_worker_identity() -> Check:
    from awf.worker_sdk import detect_capabilities, detect_platform

    name = os.environ.get("AWF_WORKER_NAME") or socket.gethostname()
    platform = os.environ.get("AWF_WORKER_PLATFORM") or detect_platform()
    caps = os.environ.get("AWF_WORKER_CAPABILITIES") or ",".join(detect_capabilities())
    detail = f"name={name} platform={platform} capabilities={caps}"
    if not os.environ.get("AWF_WORKER_REGISTRATION_TOKEN"):
        return Check("worker", "WARN", detail + " (AWF_WORKER_REGISTRATION_TOKEN empty: only a development control plane accepts it)")
    return Check("worker", "OK", detail)


def _check_model_credentials() -> Check:
    provider = os.environ.get("AWF_MODEL_PROVIDER", "")
    if provider == "scripted":
        return Check("model", "OK", "scripted (offline) provider")
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return Check("model", "OK", "Claude credentials present")
    return Check("model", "WARN", "no ANTHROPIC_API_KEY on this worker: Claude-routed agent jobs will fail here "
                 "(scripted routes from the control plane still work)")


def doctor(home: Path) -> int:
    role = os.environ.get("AWF_ROLE", "all")
    checks = [_check_python(), _check_lock(home), _check_git(), _check_definitions()]
    checks.append(Check(".env", "OK" if (home / ".env").is_file() else "WARN",
                        f"role={role}" if (home / ".env").is_file() else "missing: run `awf env init`"))
    if role in ("all", "control-plane"):
        checks += [_check_database(), _check_api_exposure(), _check_public_url()]
    if role in ("all", "worker"):
        checks += [_check_worker_identity(), _check_model_credentials()]
    if role == "worker":
        checks.append(_check_control_plane_reachable())
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{c.status:<4}] {c.name:<{width}}  {c.detail}")
    failed = [c for c in checks if c.status == "FAIL"]
    print("\nresult:", "FAIL" if failed else "OK")
    return 1 if failed else 0
