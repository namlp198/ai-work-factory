"""`awf env init`, `awf doctor` and bind safety: the per-machine setup must be reproducible."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from awf import cli, ops

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def home(tmp_path) -> Path:
    shutil.copy(ROOT / ".env.example", tmp_path / ".env.example")
    return tmp_path


def _env(path: Path) -> dict[str, str]:
    pairs = (line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()
             if line and not line.startswith("#") and "=" in line)
    return {k: v for k, v in pairs}


def test_env_init_control_plane_generates_tokens_and_never_overwrites(home, capsys):
    assert ops.env_init(home, "control-plane") == 0
    env = _env(home / ".env")
    assert env["AWF_ROLE"] == "control-plane" and env["AWF_API_HOST"] == "0.0.0.0"
    assert len(env["AWF_API_TOKEN"]) >= 32 and len(env["AWF_WORKER_REGISTRATION_TOKEN"]) >= 32
    assert env["AWF_WORKER_REGISTRATION_TOKEN"] in capsys.readouterr().out  # shown once for worker setup

    (home / ".env").write_text("AWF_ROLE=custom\n", encoding="utf-8")
    assert ops.env_init(home, "control-plane") == 0
    assert (home / ".env").read_text(encoding="utf-8") == "AWF_ROLE=custom\n"
    assert ops.env_init(home, "all", force=True) == 0
    assert _env(home / ".env")["AWF_API_HOST"] == "127.0.0.1"


def test_env_init_worker_requires_control_plane_details(home):
    assert ops.env_init(home, "worker") == 2
    assert not (home / ".env").exists()
    assert ops.env_init(home, "worker", control_url="http://factory:8000", registration_token="tok") == 0
    env = _env(home / ".env")
    assert env["AWF_CONTROL_URL"] == "http://factory:8000" and env["AWF_WORKER_REGISTRATION_TOKEN"] == "tok"
    assert env["AWF_API_TOKEN"] == ""  # workers never hold the admin token


def test_lan_bind_requires_tokens(monkeypatch):
    monkeypatch.delenv("AWF_API_EXPOSED_HOST", raising=False)
    monkeypatch.setenv("AWF_API_TOKEN", "")
    monkeypatch.setenv("AWF_WORKER_REGISTRATION_TOKEN", "")
    cli._check_bind("127.0.0.1")
    with pytest.raises(SystemExit):
        cli._check_bind("0.0.0.0")
    monkeypatch.setenv("AWF_API_EXPOSED_HOST", "127.0.0.1")  # container publishing on loopback only
    cli._check_bind("0.0.0.0")
    monkeypatch.delenv("AWF_API_EXPOSED_HOST")
    monkeypatch.setenv("AWF_API_TOKEN", "a" * 32)
    monkeypatch.setenv("AWF_WORKER_REGISTRATION_TOKEN", "b" * 32)
    cli._check_bind("0.0.0.0")


def test_installed_dependencies_match_lock_file():
    check = ops._check_lock(ROOT)
    assert check.status == "OK", check.detail
