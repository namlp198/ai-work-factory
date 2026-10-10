from __future__ import annotations

import dataclasses
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


@pytest.fixture
def platform(tmp_path, monkeypatch):
    from awf.platform import Platform
    from awf.settings import build_settings

    monkeypatch.setenv("AWF_SCRIPTED_RESPONSES", str(DEMO_SCRIPT))
    settings = dataclasses.replace(
        build_settings(ROOT),
        database_url=f"sqlite:///{(tmp_path / 'awf.db').as_posix()}",
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
