"""`awf` command line: run the control API, orchestrator, worker, migrations and the demo.

Every machine runs the same commands; per-machine differences live only in `.env` (see `awf env init`).
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


def _settings():
    from awf.settings import get_settings

    return get_settings()


def cmd_db(args: argparse.Namespace) -> int:
    settings = _settings()
    if args.action == "create":
        from awf.domain.db import Database

        Database(settings.database_url).create_all()
        print(f"schema created on {settings.database_url}")
        return 0
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(settings.home / "alembic.ini"))
    cfg.set_main_option("script_location", str(settings.home / "infrastructure" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    if args.action == "upgrade":
        command.upgrade(cfg, "head")
    elif args.action == "downgrade":
        command.downgrade(cfg, "base")
    print(f"database {args.action} done ({settings.database_url})")
    return 0


def _check_bind(host: str) -> None:
    from awf.ops import LOOPBACK

    # In a container the API must bind 0.0.0.0; the published address (AWF_API_EXPOSED_HOST) is what matters.
    host = os.environ.get("AWF_API_EXPOSED_HOST", host)
    if host not in LOOPBACK and not (os.environ.get("AWF_API_TOKEN") and os.environ.get("AWF_WORKER_REGISTRATION_TOKEN")):
        raise SystemExit(f"refusing to listen on {host}: set AWF_API_TOKEN and AWF_WORKER_REGISTRATION_TOKEN "
                         f"(run `awf env init`) before exposing the API beyond localhost")


def cmd_api(args: argparse.Namespace) -> int:
    import uvicorn

    from awf_api.main import create_app

    _check_bind(args.host)
    if args.embedded_orchestrator:
        os.environ["AWF_EMBEDDED_ORCHESTRATOR"] = "true"
        from awf.settings import reset_settings_cache

        reset_settings_cache()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_orchestrator(args: argparse.Namespace) -> int:
    from awf.logging import configure_logging, get_logger
    from awf.orchestrator.orchestrator import Orchestrator
    from awf.platform import Platform

    configure_logging()
    platform = Platform.build()
    orchestrator = Orchestrator(platform)
    get_logger("orchestrator").info("orchestrator loop started (tick %.1fs)", platform.settings.tick_interval_seconds)
    while True:
        try:
            if not orchestrator.tick():
                time.sleep(platform.settings.tick_interval_seconds)
        except KeyboardInterrupt:
            return 0


def build_worker(args: argparse.Namespace):
    from awf.worker_sdk import ControlPlaneClient, Worker

    settings = _settings()
    caps = [c.strip() for c in args.capabilities.split(",") if c.strip()] if args.capabilities else None
    labels = dict(item.split("=", 1) for item in (args.label or []))
    root = Path(args.workspace_root)
    root = root if root.is_absolute() else settings.home / root
    labels = {**_env_labels(), **labels}
    worker = Worker(ControlPlaneClient(args.url), name=args.name,
                    workspace_root=root.resolve(),
                    policy_path=settings.config_dir / "policies.yaml", capabilities=caps, labels=labels,
                    platform_name=args.platform, poll_interval=args.poll_interval)
    worker.register(os.environ.get("AWF_WORKER_REGISTRATION_TOKEN", ""))
    return worker


def _env_labels() -> dict[str, str]:
    raw = os.environ.get("AWF_WORKER_LABELS", "")
    return dict(item.strip().split("=", 1) for item in raw.split(",") if "=" in item)


def cmd_worker(args: argparse.Namespace) -> int:
    from awf.logging import configure_logging

    configure_logging()
    worker = build_worker(args)
    try:
        worker.serve_forever()
    except KeyboardInterrupt:
        worker.stop()
    return 0


# ----------------------------------------------------------------------------------------- start
def _wait_healthy(url: str, seconds: float = 30) -> bool:
    import httpx

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(url.rstrip("/") + "/health", timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def _worker_namespace() -> argparse.Namespace:
    return argparse.Namespace(
        url=os.environ.get("AWF_CONTROL_URL", "http://127.0.0.1:8000"),
        name=os.environ.get("AWF_WORKER_NAME") or socket.gethostname(),
        workspace_root=os.environ.get("AWF_WORKSPACE_ROOT", "var/workspaces"),
        platform=os.environ.get("AWF_WORKER_PLATFORM"),
        capabilities=os.environ.get("AWF_WORKER_CAPABILITIES"),
        label=None,
        poll_interval=float(os.environ.get("AWF_WORKER_POLL_INTERVAL", "3")),
    )


def cmd_start(args: argparse.Namespace) -> int:
    """Start whatever this machine's AWF_ROLE says: control-plane, worker, or all (both)."""
    from awf.logging import configure_logging

    role = args.role or os.environ.get("AWF_ROLE", "all")
    host = os.environ.get("AWF_API_HOST", "127.0.0.1")
    port = int(os.environ.get("AWF_API_PORT", "8000"))
    print(f"awf start: role={role}")
    if role not in ("all", "control-plane", "worker"):
        raise SystemExit(f"unknown AWF_ROLE '{role}' (expected all, control-plane or worker)")
    if role in ("control-plane", "all"):
        _check_bind(host)
        cmd_db(argparse.Namespace(action="upgrade"))  # idempotent: new migrations apply after `git pull`
    if role == "control-plane":
        return cmd_api(argparse.Namespace(host=host, port=port, embedded_orchestrator=True))
    worker_args = _worker_namespace()
    if role == "worker":
        configure_logging()
        if not _wait_healthy(worker_args.url, 60):
            print(f"control plane {worker_args.url} is not reachable yet")
        while True:  # register as soon as the control plane is up
            try:
                return cmd_worker(worker_args)
            except Exception as exc:  # noqa: BLE001 - keep the daemon alive across control-plane restarts
                print(f"worker could not register ({exc}); retrying in 10s")
                time.sleep(10)

    import uvicorn

    from awf.settings import reset_settings_cache
    from awf_api.main import create_app

    os.environ["AWF_EMBEDDED_ORCHESTRATOR"] = "true"
    reset_settings_cache()
    server = uvicorn.Server(uvicorn.Config(create_app(), host=host, port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    local = f"http://127.0.0.1:{port}"
    if not _wait_healthy(local):
        raise SystemExit("control API did not start")
    worker_args.url = local
    print(f"Dashboard: {local}/")
    try:
        return cmd_worker(worker_args)
    finally:
        server.should_exit = True


def cmd_env(args: argparse.Namespace) -> int:
    from awf.ops import env_init

    return env_init(_settings().home, args.role, args.control_url, args.registration_token, args.api_host, args.force)


def cmd_doctor(args: argparse.Namespace) -> int:
    from awf.ops import doctor

    return doctor(_settings().home)


# ----------------------------------------------------------------------------------------- demo
def _init_demo_repo(home: Path, target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(home / "examples" / "demo" / "perception-service", target)
    git = ["git", "-c", "core.autocrlf=false", "-c", "user.name=demo", "-c", "user.email=demo@localhost"]
    subprocess.run(git + ["init", "-q", "-b", "main"], cwd=target, check=True)
    subprocess.run(git + ["add", "-A"], cwd=target, check=True)
    subprocess.run(git + ["commit", "-q", "-m", "initial import"], cwd=target, check=True)
    return target


def cmd_demo(args: argparse.Namespace) -> int:
    """Self-contained demo: demo repo + API with embedded orchestrator + one local worker."""
    import httpx
    import uvicorn

    from awf.settings import reset_settings_cache

    home = _settings().home
    if not args.live_model:
        os.environ.setdefault("AWF_MODEL_PROVIDER", "scripted")
        os.environ.setdefault("AWF_SCRIPTED_RESPONSES", str(home / "examples" / "demo" / "scripted-responses.yaml"))
    os.environ["AWF_EMBEDDED_ORCHESTRATOR"] = "true"
    reset_settings_cache()
    settings = _settings()

    from awf.domain.db import Database
    from awf_api.main import create_app

    Database(settings.database_url).create_all()
    repo = _init_demo_repo(home, settings.home / "var" / "demo" / "perception-service")
    base = f"http://{args.host}:{args.port}"
    server = uvicorn.Server(uvicorn.Config(create_app(), host=args.host, port=args.port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        try:
            if httpx.get(base + "/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)

    headers = {"Authorization": f"Bearer {settings.api_token}"} if settings.api_token else {}
    api = httpx.Client(base_url=base, headers=headers, timeout=30)
    py = sys.executable
    project = api.post("/api/v1/projects", json={
        "name": f"perception-service demo {time.strftime('%H%M%S')}",
        "repository": {"url": str(repo), "base_branch": "main",
                       "build_command": [py, "-m", "compileall", "-q", "perception_service"],
                       "test_command": [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                       "lint_command": [py, "-m", "compileall", "-q", "perception_service", "tests"]},
    }).raise_for_status().json()
    run = api.post(f"/api/v1/projects/{project['id']}/workflow-runs", json={
        "workflow_id": "software-development",
        "inputs": {"objective": args.objective},
    }).raise_for_status().json()

    worker_args = argparse.Namespace(url=base, name="demo-linux-worker", workspace_root=str(settings.home / "var" / "workspaces"),
                                     platform="linux-amd64", capabilities="git,python", label=["location=local"],
                                     poll_interval=1.0)
    worker = build_worker(worker_args)
    threading.Thread(target=worker.serve_forever, daemon=True).start()

    print(f"\nDashboard: {base}/   (run {run['id']})")
    print(f"Demo repository: {repo}")
    if not args.auto_approve:
        print("Approve the architecture and merge gates in the dashboard. Ctrl+C to stop.\n")
    seen: set[str] = set()
    try:
        while True:
            time.sleep(1)
            state = api.get(f"/api/v1/workflow-runs/{run['id']}").json()
            line = " | ".join(f"{s['id']}:{s['status']}" for s in state["stages"])
            if line not in seen:
                seen.add(line)
                print(f"[{state['status']}] {line}")
            if args.auto_approve:
                for a in api.get("/api/v1/approvals", params={"state": "pending"}).json():
                    api.post(f"/api/v1/approvals/{a['id']}/approve", json={"comment": "auto-approved by demo"},
                             headers={"X-Actor": "demo"})
            if state["status"] != "RUNNING":
                print(f"\nRun finished: {state['status']} {state.get('error') or ''}")
                print(f"Integration branch: {state['integration_branch']} in {repo}")
                if args.auto_approve:
                    return 0 if state["status"] == "COMPLETED" else 1
                print("Dashboard stays up; Ctrl+C to exit.")
                while True:
                    time.sleep(3600)
    except KeyboardInterrupt:
        return 0
    finally:
        worker.stop()
        server.should_exit = True


def main(argv: list[str] | None = None) -> int:
    from awf.ops import ROLES
    from awf.settings import _default_home, load_dotenv

    load_dotenv(_default_home() / ".env")  # before argparse reads environment defaults
    parser = argparse.ArgumentParser(prog="awf", description="AI Work Factory")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("db", help="database schema management")
    p.add_argument("action", choices=["upgrade", "downgrade", "create"])
    p.set_defaults(func=cmd_db)

    p = sub.add_parser("start", help="start this machine according to AWF_ROLE in .env")
    p.add_argument("--role", choices=ROLES, help="override AWF_ROLE")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("env", help="machine configuration")
    p.add_argument("action", choices=["init"])
    p.add_argument("--role", choices=ROLES, default="all")
    p.add_argument("--control-url", help="worker: URL of the control plane, e.g. http://factory-host:8000")
    p.add_argument("--registration-token", help="worker: AWF_WORKER_REGISTRATION_TOKEN of the control plane")
    p.add_argument("--api-host", help="control plane bind address (default 0.0.0.0 for control-plane, 127.0.0.1 for all)")
    p.add_argument("--force", action="store_true", help="overwrite an existing .env")
    p.set_defaults(func=cmd_env)

    p = sub.add_parser("doctor", help="check this machine: python, dependencies, git, config, db, connectivity")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("api", help="run the control API (and dashboard)")
    p.add_argument("--host", default=os.environ.get("AWF_API_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("AWF_API_PORT", "8000")))
    p.add_argument("--embedded-orchestrator", action="store_true", help="run the orchestrator loop in-process")
    p.set_defaults(func=cmd_api)

    p = sub.add_parser("orchestrator", help="run the orchestrator loop")
    p.set_defaults(func=cmd_orchestrator)

    p = sub.add_parser("worker", help="run an execution worker")
    p.add_argument("--url", default=os.environ.get("AWF_CONTROL_URL", "http://127.0.0.1:8000"))
    p.add_argument("--name", default=os.environ.get("AWF_WORKER_NAME") or socket.gethostname())
    p.add_argument("--workspace-root", default=os.environ.get("AWF_WORKSPACE_ROOT", "var/workspaces"),
                   help="relative paths are resolved against the repository root")
    p.add_argument("--platform", default=os.environ.get("AWF_WORKER_PLATFORM"),
                   help="override detected platform, e.g. linux-amd64, windows-amd64, linux-arm64")
    p.add_argument("--capabilities", default=os.environ.get("AWF_WORKER_CAPABILITIES"),
                   help="comma separated; detected from installed tools when omitted")
    p.add_argument("--label", action="append", help="key=value label (repeatable)")
    p.add_argument("--poll-interval", type=float, default=float(os.environ.get("AWF_WORKER_POLL_INTERVAL", "3")))
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("demo", help="end-to-end demo on a local perception-service repository")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--objective", default="Add a camera pipeline health-check API with tests and documentation.")
    p.add_argument("--auto-approve", action="store_true", help="approve human gates automatically (headless)")
    p.add_argument("--live-model", action="store_true", help="use the configured Claude models instead of the "
                                                             "scripted offline responses")
    p.set_defaults(func=cmd_demo)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
