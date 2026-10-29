from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO_TEMPLATE = ROOT / "examples" / "demo" / "perception-service"
DEMO_SCRIPT = ROOT / "examples" / "demo" / "scripted-responses.yaml"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


# Run the suite against PostgreSQL (what a shared control plane uses) instead of a SQLite file per test:
#   AWF_TEST_DATABASE_URL=postgresql+psycopg://awf:<password>@127.0.0.1:5432/awf_test pytest
# Every test starts from an empty schema. Never point this at a database whose data you want to keep.
TEST_DATABASE_URL = os.environ.get("AWF_TEST_DATABASE_URL", "")


def _fresh_postgres(url: str) -> None:
    from sqlalchemy import create_engine, text

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()


@pytest.fixture(autouse=True)
def _no_machine_engine_settings(monkeypatch):
    """A developer machine is often a worker too: its .env engine settings (AWF_CLAUDE_CODE_AUTH=login, a real
    `claude` path...) must not change what the tests see. They are set empty (= not configured) rather than removed,
    because the .env loader never overrides a variable that exists."""
    prefixes = ("AWF_CLAUDE_CODE_", "AWF_CODEX_", "AWF_ANTIGRAVITY_", "AWF_QWEN_CODE_", "AWF_KIMI_CODE_",
                "AWF_DEEPSEEK_HARNESS_", "AWF_MINIMAX_CODE_")
    env_file = ROOT / ".env"
    names = {n for n in os.environ if n.startswith(prefixes)}
    if env_file.is_file():
        names |= {ln.split("=", 1)[0].strip() for ln in env_file.read_text(encoding="utf-8").splitlines()
                  if "=" in ln and ln.strip().startswith(prefixes)}
    for name in names:
        monkeypatch.setenv(name, "")


@pytest.fixture
def platform(tmp_path, monkeypatch):
    from awf.platform import Platform
    from awf.settings import build_settings

    monkeypatch.setenv("AWF_SCRIPTED_RESPONSES", str(DEMO_SCRIPT))
    if TEST_DATABASE_URL:
        _fresh_postgres(TEST_DATABASE_URL)
    settings = dataclasses.replace(
        build_settings(ROOT),
        database_url=TEST_DATABASE_URL or f"sqlite:///{(tmp_path / 'awf.db').as_posix()}",
        artifact_root=tmp_path / "artifacts",
        model_provider_override="scripted",
        environment="development",
        api_token="",
        worker_registration_token="",
    )
    p = Platform.build(settings)
    p.db.create_all()
    return p


@pytest.fixture
def orchestrator(platform):
    from awf.orchestrator.orchestrator import Orchestrator

    return Orchestrator(platform)


@pytest.fixture
def demo_repo(tmp_path) -> Path:
    repo = tmp_path / "perception-service"
    shutil.copytree(DEMO_TEMPLATE, repo)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=test", "-c", "user.email=test@localhost", "commit", "-q", "-m", "initial")
    return repo


@pytest.fixture
def repository_config(demo_repo) -> dict:
    py = sys.executable
    return {
        "url": str(demo_repo),
        "base_branch": "main",
        "build_command": [py, "-m", "compileall", "-q", "perception_service"],
        "test_command": [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        "lint_command": [py, "-m", "compileall", "-q", "perception_service", "tests"],
    }


@pytest.fixture
def client(platform, orchestrator):
    from fastapi.testclient import TestClient

    from awf_api.main import create_app

    app = create_app(platform, orchestrator, embedded_orchestrator=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def worker(client, platform, tmp_path):
    from awf.worker_sdk import ControlPlaneClient, Worker

    w = Worker(ControlPlaneClient(http=client), name="test-linux-01", workspace_root=tmp_path / "workspaces",
               policy_path=ROOT / "config" / "policies.yaml", capabilities=["git", "python"],
               platform_name="linux-amd64")
    w.register("")
    return w


def git_show(repo: Path, ref_path: str) -> str:
    return _git(repo, "show", ref_path)


def git_branches(repo: Path) -> list[str]:
    return [b.strip().lstrip("* ") for b in _git(repo, "branch", "--list").splitlines()]
