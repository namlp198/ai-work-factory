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
    assert ops.env_values((home / ".env").read_text(encoding="utf-8").splitlines()) == {"AWF_ROLE": "custom"}
    assert ops.env_init(home, "all", force=True) == 0
    assert _env(home / ".env")["AWF_API_HOST"] == "127.0.0.1"


def test_env_init_worker_requires_control_plane_details(home):
    assert ops.env_init(home, "worker") == 2
    assert not (home / ".env").exists()
    assert ops.env_init(home, "worker", control_url="http://factory:8000", registration_token="tok") == 0
    env = _env(home / ".env")
    assert env["AWF_CONTROL_URL"] == "http://factory:8000" and env["AWF_WORKER_REGISTRATION_TOKEN"] == "tok"
    assert env["AWF_API_TOKEN"] == ""  # workers never hold the admin token


def test_bootstrap_again_updates_the_control_url_of_an_existing_env(home, capsys):
    """hp3-super-server: an old .env had AWF_CONTROL_URL without https://, and bootstrap kept it."""
    (home / ".env").write_text("AWF_ROLE=worker\nAWF_CONTROL_URL=factory.tail6d4070.ts.net\nAWF_WORKER_NAME=hp3\n",
                               encoding="utf-8")
    assert ops.env_init(home, "worker", control_url="https://factory.tail6d4070.ts.net/", registration_token="new") == 0
    env = _env(home / ".env")
    assert env["AWF_CONTROL_URL"] == "https://factory.tail6d4070.ts.net" and env["AWF_WORKER_REGISTRATION_TOKEN"] == "new"
    assert env["AWF_WORKER_NAME"] == "hp3"  # the rest is kept
    assert "updated AWF_CONTROL_URL, AWF_WORKER_REGISTRATION_TOKEN" in capsys.readouterr().out
    assert ops.env_init(home, "worker", control_url="factory.tail6d4070.ts.net", registration_token="x") == 2


def test_doctor_explains_a_control_url_without_scheme_and_a_stale_admin_token(monkeypatch):
    import httpx

    from awf.security import token_fingerprint

    monkeypatch.setenv("AWF_CONTROL_URL", "factory.tail6d4070.ts.net")
    check = ops._check_control_plane_reachable()
    assert check.status == "FAIL" and "https://factory.tail6d4070.ts.net" in check.detail

    monkeypatch.setenv("AWF_CONTROL_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("AWF_API_TOKEN", "the-token-in-env")
    running = {"status": "ok", "version": "x", "auth": token_fingerprint("the-token-it-started-with")}
    monkeypatch.setattr(httpx, "get", lambda url, timeout: httpx.Response(200, json=running, request=httpx.Request("GET", url)))
    check = ops._check_control_plane_reachable()
    assert check.status == "FAIL" and "restart the control plane" in check.detail
    running["auth"] = token_fingerprint("the-token-in-env")
    assert ops._check_control_plane_reachable().status == "OK"


OLD_WORKER_ENV = """# Per-machine configuration
AWF_ROLE=worker
AWF_CONTROL_URL=https://factory.tail6d4070.ts.net
AWF_WORKER_REGISTRATION_TOKEN=reg-secret
AWF_CLAUDE_CODE_AUTH=login
ANTHROPIC_API_KEY="sk-ant-quoted value"
MY_OWN_SETTING=42
AWF_ROLE=worker
"""


def test_an_old_env_gets_the_latest_layout_with_every_value_kept(home, capsys):
    """The two first workers had an .env from an older .env.example: sections and new settings were missing."""
    (home / ".env").write_text(OLD_WORKER_ENV, encoding="utf-8")
    assert ops.env_missing(home)  # doctor warns about it
    assert ops.env_upgrade(home)
    new = (home / ".env").read_text(encoding="utf-8")
    old_values = ops.env_values(OLD_WORKER_ENV.splitlines())
    assert ops.env_values(new.splitlines()) == old_values  # nothing the machine does changes
    assert "# AWF_MIRROR_TOKEN=" in new and "# AWF_WORKER_MAX_JOBS=3" in new  # new settings appear, commented
    assert "\nAWF_API_HOST=" not in new  # a default the old file did not set stays commented
    assert new.rstrip().endswith("MY_OWN_SETTING=42")  # unknown settings are kept at the end
    (backup,) = home.glob(".env.bak-*")
    assert backup.read_text(encoding="utf-8") == OLD_WORKER_ENV
    assert ops.env_missing(home) == [] and not ops.env_upgrade(home)  # idempotent
    assert "every value kept" in capsys.readouterr().out


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


# ---------------------------------------------------------------------------- tailscale in doctor
ME = {"dns": "factory.tail1234.ts.net", "ips": ["100.101.102.103"], "state": "Running"}


def test_tailscale_check_control_plane(monkeypatch):
    monkeypatch.setenv("AWF_PUBLIC_URL", "https://factory.tail1234.ts.net")
    assert ops._check_tailscale("control-plane", ME).status == "OK"
    monkeypatch.setenv("AWF_PUBLIC_URL", "https://other.tail1234.ts.net")
    assert ops._check_tailscale("control-plane", ME).status == "WARN"
    monkeypatch.delenv("AWF_PUBLIC_URL")
    hint = ops._check_tailscale("control-plane", ME)
    assert "AWF_PUBLIC_URL=https://factory.tail1234.ts.net" in hint.detail


def test_tailscale_check_worker(monkeypatch):
    monkeypatch.setenv("AWF_CONTROL_URL", "https://factory.tail1234.ts.net")
    assert ops._check_tailscale("worker", None, probe=False).status == "FAIL"  # tailnet URL, no Tailscale here
    assert ops._check_tailscale("worker", {**ME, "state": "Stopped"}).status == "FAIL"
    assert ops._check_tailscale("worker", ME).status == "OK"
    monkeypatch.setenv("AWF_CONTROL_URL", "https://factory.example.com")
    assert ops._check_tailscale("worker", None, probe=False) is None  # not a tailnet URL: nothing to say
    assert ops._check_tailscale("worker", {**ME, "state": "Stopped"}).status == "WARN"


def test_tailscale_self_parses_status_json(monkeypatch):
    import json
    import subprocess

    fake = subprocess.CompletedProcess([], 0, stdout=json.dumps({
        "BackendState": "Running", "Self": {"DNSName": "pc.tail1234.ts.net.", "TailscaleIPs": ["100.1.2.3"]}}))
    monkeypatch.setattr(ops.shutil, "which", lambda name: "/usr/bin/tailscale")
    assert ops.tailscale_self(run=lambda *a, **k: fake) == {"dns": "pc.tail1234.ts.net", "ips": ["100.1.2.3"],
                                                            "state": "Running"}


@pytest.mark.skipif(shutil.which("powershell") is None, reason="Windows PowerShell only")
@pytest.mark.parametrize("script", ["install-task.ps1", "bootstrap.ps1"])
def test_powershell_scripts_parse(script):
    import subprocess

    path = ROOT / "scripts" / script
    check = ("$e = $null; $t = $null; [System.Management.Automation.Language.Parser]::ParseFile("
             f"'{path}', [ref]$t, [ref]$e) | Out-Null; $e.Count")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", check], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "0", out.stdout + out.stderr


@pytest.mark.skipif(shutil.which("powershell") is None, reason="Windows PowerShell only")
def test_install_task_refuses_s4u_without_startup():
    import subprocess

    out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                          str(ROOT / "scripts" / "install-task.ps1"), "-S4U", "-TaskName", "awf-test-never-created"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode != 0 and "only applies together with -AtStartup" in out.stdout + out.stderr

