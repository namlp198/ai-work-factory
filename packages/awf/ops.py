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


def _check_backups(role: str) -> Check:
    """A control plane that other machines depend on should be on PostgreSQL with pg_dump available."""
    from awf import dbops
    from awf.settings import get_settings

    url = get_settings().database_url
    if dbops.is_postgres(url):
        if not dbops.pg_tool("pg_dump"):
            return Check("backups", "WARN", "pg_dump not found: `awf db backup` cannot run (sudo apt install postgresql-client)")
        return Check("backups", "OK", "PostgreSQL; `awf db backup` uses pg_dump")
    if role == "control-plane":
        return Check("backups", "WARN", "SQLite on a shared control plane: fine to start, but for a 24/7 server use "
                     "PostgreSQL (sudo scripts/install-postgres.sh --import-sqlite)")
    return Check("backups", "OK", "SQLite; `awf db backup` makes an online copy")


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


def tailscale_self(run=None) -> dict | None:
    """This machine on the tailnet ({"dns": "pc.tailnet.ts.net", "ips": [...], "state": "Running"}), or None when
    the Tailscale CLI is missing or does not answer."""
    import json

    exe = shutil.which("tailscale")
    if not exe and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tailscale" / "tailscale.exe"
        exe = str(candidate) if candidate.is_file() else None
    if not exe:
        return None
    try:
        out = (run or subprocess.run)([exe, "status", "--json"], capture_output=True, text=True, timeout=10)
        data = json.loads(out.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    me = data.get("Self") or {}
    return {"dns": (me.get("DNSName") or "").rstrip("."), "ips": me.get("TailscaleIPs") or [],
            "state": data.get("BackendState") or ""}


def _tailnet_host(url: str) -> str | None:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host.endswith(".ts.net") or host.startswith("100."):
        return host
    return None


def _check_tailscale(role: str, me: dict | None = None, probe: bool = True) -> Check | None:
    """Only reported when this machine's URLs point into a tailnet, or Tailscale is installed."""
    url = os.environ.get("AWF_PUBLIC_URL" if role != "worker" else "AWF_CONTROL_URL", "")
    host = _tailnet_host(url)
    me = me if me is not None or not probe else tailscale_self()
    if me is None:
        if host:
            return Check("tailscale", "FAIL", f"{url} is a tailnet address but Tailscale is not installed/running here "
                                              f"(install it and log in to the same tailnet)")
        return None
    if me["state"] != "Running":
        return Check("tailscale", "FAIL" if host else "WARN", f"Tailscale is {me['state'] or 'not running'}: "
                                                              f"run `tailscale up`")
    detail = f"this machine is {me['dns'] or '?'} ({', '.join(me['ips'][:1])})"
    if role != "worker" and host and host.endswith(".ts.net") and me["dns"] and host != me["dns"].lower():
        return Check("tailscale", "WARN", f"{detail} but AWF_PUBLIC_URL names {host}")
    if role != "worker" and not url:
        detail += f"; for workers and phones set AWF_PUBLIC_URL=https://{me['dns']} (tailscale serve --bg 8000)"
    return Check("tailscale", "OK", detail)


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
    from awf.worker_sdk import detect_platform, resolve_capabilities

    name = os.environ.get("AWF_WORKER_NAME") or socket.gethostname()
    platform = os.environ.get("AWF_WORKER_PLATFORM") or detect_platform()
    caps = ",".join(resolve_capabilities(os.environ.get("AWF_WORKER_CAPABILITIES"),
                                         os.environ.get("AWF_WORKER_EXTRA_CAPABILITIES")))
    slots = os.environ.get("AWF_WORKER_MAX_JOBS") or "1"
    detail = f"name={name} platform={platform} max_jobs={slots} capabilities={caps}"
    if not os.environ.get("AWF_WORKER_REGISTRATION_TOKEN"):
        return Check("worker", "WARN", detail + " (AWF_WORKER_REGISTRATION_TOKEN empty: only a development control plane accepts it)")
    return Check("worker", "OK", detail)


def _check_model_credentials() -> Check:
    provider = os.environ.get("AWF_MODEL_PROVIDER", "")
    if provider == "scripted":
        return Check("model", "OK", "scripted (offline) provider")
    from awf.worker_sdk.worker import llm_capabilities

    caps = sorted(llm_capabilities())
    if caps:
        return Check("model", "OK", "model keys present: " + ", ".join(caps) + " (runs on other providers go elsewhere)")
    return Check("model", "WARN", "no ANTHROPIC_API_KEY, GEMINI_API_KEY or OPENAI_API_KEY on this worker: it only takes runs that "
                 "use no hosted model (scripted)")


def doctor(home: Path) -> int:
    role = os.environ.get("AWF_ROLE", "all")
    checks = [_check_python(), _check_lock(home), _check_git(), _check_definitions()]
    checks.append(Check(".env", "OK" if (home / ".env").is_file() else "WARN",
                        f"role={role}" if (home / ".env").is_file() else "missing: run `awf env init`"))
    if role in ("all", "control-plane"):
        checks += [_check_database(), _check_backups(role), _check_api_exposure(), _check_public_url()]
    tailscale = _check_tailscale(role)
    if tailscale:
        checks.append(tailscale)
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
