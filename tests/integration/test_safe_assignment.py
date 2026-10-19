"""Safe assignment: every project declares its platform, machines report precise capabilities, and the
dashboard/API say which machines can run a project and why the others cannot."""

from __future__ import annotations

from pathlib import Path

import pytest

from awf.worker_sdk import ControlPlaneClient
from awf.worker_sdk import Worker as WorkerDaemon
from awf.worker_sdk import worker as worker_module
from awf.worker_sdk.worker import dotnet_capabilities, resolve_capabilities, visual_studio_capabilities

ROOT = Path(__file__).resolve().parents[2]


def _daemon(client, tmp_path, name, platform_name, capabilities):
    w = WorkerDaemon(ControlPlaneClient(http=client), name=name, workspace_root=tmp_path / name,
                     policy_path=ROOT / "config" / "policies.yaml", capabilities=list(capabilities),
                     platform_name=platform_name)
    w.register("")
    return w


def _run(client, project_id, worker=None):
    body = {"inputs": {"objective": "Add a health check"}}
    if worker:
        body["placement"] = {"worker": worker}
    return client.post(f"/api/v1/projects/{project_id}/workflow-runs", json=body)


# ---------------------------------------------------------------------------- platform is mandatory
@pytest.mark.parametrize("labels", [{}, {"platform": ""}, {"platform": "Windows 11"}, {"platform": 3}])
def test_project_must_declare_a_platform(client, repository_config, labels):
    resp = client.post("/api/v1/projects", json={"name": "P", "repository": repository_config, "labels": labels})
    assert resp.status_code == 422 and "platform" in resp.text


def test_project_without_labels_is_refused(client, repository_config):
    assert client.post("/api/v1/projects", json={"name": "P", "repository": repository_config}).status_code == 422


@pytest.mark.parametrize("value", ["any", "windows", "linux", "linux-arm64", "windows-amd64", "darwin"])
def test_valid_platforms(client, repository_config, value):
    resp = client.post("/api/v1/projects", json={"name": "P", "slug": f"p-{value}", "repository": repository_config,
                                                 "labels": {"platform": value}})
    assert resp.status_code == 201, resp.text


def test_old_project_without_platform_is_fixed_with_patch(client, platform, orchestrator, worker, repository_config):
    with platform.db.transaction() as s:  # created before the platform became mandatory
        project = orchestrator.create_project(s, name="Legacy", repository=repository_config)
        project_id = project.id
    refused = _run(client, project_id)
    assert refused.status_code == 422
    assert "does not say which platform" in refused.json()["detail"] and "PATCH" in refused.json()["detail"]
    assert client.get(f"/api/v1/projects/{project_id}").json()["machines"][0]["reasons"] == ["project has no platform"]

    assert client.patch(f"/api/v1/projects/{project_id}", json={"labels": None}).status_code == 422
    assert client.patch(f"/api/v1/projects/{project_id}", json={"labels": {"site": "x"}}).status_code == 422
    fixed = client.patch(f"/api/v1/projects/{project_id}", json={"labels": {"platform": "linux"}})
    assert fixed.status_code == 200 and fixed.json()["labels"] == {"platform": "linux"}
    assert fixed.json()["machines"][0]["matches"] is True
    assert _run(client, project_id).status_code == 201
    events = [e["event_type"] for e in client.get("/api/v1/audit").json()]
    assert "PROJECT_UPDATED" in events


def test_patch_changes_only_the_fields_sent(client, repository_config):
    p = client.post("/api/v1/projects", json={"name": "P", "description": "keep", "repository": repository_config,
                                              "labels": {"platform": "linux"}, "budget_usd": 5}).json()
    resp = client.patch(f"/api/v1/projects/{p['id']}",
                        json={"repository": {**repository_config, "required_capabilities": ["dotnet"]}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["description"] == "keep" and data["budget_usd"] == 5 and data["labels"] == {"platform": "linux"}
    assert data["repository"]["required_capabilities"] == ["dotnet"]
    assert data["requirements"] == {"platform": "linux", "capabilities": ["dotnet", "git"]}
    assert client.patch("/api/v1/projects/nope", json={}).status_code == 404


# ---------------------------------------------------------------------------- the right machine, explained
def test_windows_desktop_project_goes_to_the_windows_machine_only(client, platform, orchestrator, repository_config,
                                                                 tmp_path):
    # Both machines have a .NET SDK; only the Windows one can build WPF/WinForms.
    ubuntu = _daemon(client, tmp_path, "ubuntu-01", "linux-amd64", ["git", "python", "dotnet", "dotnet-sdk-8"])
    pc = _daemon(client, tmp_path, "pc-robo", "windows-amd64",
                 ["git", "python", "dotnet", "dotnet-sdk-8", "dotnet-windowsdesktop", "msbuild"])
    project = client.post("/api/v1/projects", json={
        "name": "robo-station", "repository": {**repository_config,
                                               "required_capabilities": ["dotnet-windowsdesktop"]},
        "labels": {"platform": "windows"}}).json()

    detail = client.get(f"/api/v1/projects/{project['id']}").json()
    machines = {m["name"]: m for m in detail["machines"]}
    assert machines["pc-robo"]["matches"] is True and machines["pc-robo"]["reasons"] == []
    assert machines["ubuntu-01"]["reasons"] == ["platform is linux-amd64, needs windows",
                                                "missing dotnet-windowsdesktop"]

    pinned = _run(client, project["id"], worker="ubuntu-01")
    assert pinned.status_code == 422
    assert "ubuntu-01' does not meet" in pinned.json()["detail"] and "missing dotnet-windowsdesktop" in pinned.json()["detail"]
    run_id = _run(client, project["id"]).json()["id"]
    orchestrator.run_until_idle()
    assert ubuntu.client.claim() is None
    assert client.get(f"/api/v1/workflow-runs/{run_id}").json()["assigned_worker"]["name"] == "pc-robo"
    del pc


def test_refusal_lists_what_each_machine_lacks(client, repository_config, tmp_path):
    _daemon(client, tmp_path, "ubuntu-01", "linux-amd64", ["git", "python"])
    _daemon(client, tmp_path, "jetson-01", "linux-arm64", ["git", "cuda"])
    p = client.post("/api/v1/projects", json={"name": "P", "repository": {**repository_config,
                                                                           "required_capabilities": ["cuda", "ros2"]},
                                              "labels": {"platform": "linux"}}).json()
    detail = _run(client, p["id"]).json()["detail"]
    assert "jetson-01: missing ros2" in detail and "ubuntu-01: missing cuda, ros2" in detail


# ---------------------------------------------------------------------------- capability detection
def test_dotnet_capabilities_from_tool_output():
    sdks = "8.0.404 [C:\\Program Files\\dotnet\\sdk]\n9.0.100 [C:\\Program Files\\dotnet\\sdk]\n"
    runtimes = ("Microsoft.AspNetCore.App 8.0.11 [C:\\...]\nMicrosoft.NETCore.App 8.0.11 [C:\\...]\n"
                "Microsoft.WindowsDesktop.App 8.0.11 [C:\\...]\n")
    assert dotnet_capabilities(sdks, runtimes) == {"dotnet-sdk-8", "dotnet-sdk-9", "dotnet-windowsdesktop"}
    linux_runtimes = "Microsoft.NETCore.App 8.0.11 [/usr/share/dotnet/shared/Microsoft.NETCore.App]\n"
    assert dotnet_capabilities("8.0.404 [/usr/share/dotnet/sdk]\n", linux_runtimes) == {"dotnet-sdk-8"}
    assert dotnet_capabilities("", "") == set()


def test_visual_studio_capabilities_from_vswhere():
    assert visual_studio_capabilities('[{"installationVersion": "17.12.35527.113"}]') == {"msbuild", "vs-2022"}
    assert visual_studio_capabilities("[]") == set()
    assert visual_studio_capabilities("not json") == set()


def test_extra_capabilities_add_to_detection(monkeypatch):
    monkeypatch.setattr(worker_module, "detect_capabilities", lambda: ["dotnet", "git"])
    assert resolve_capabilities(None, "robo-station-sdk, realsense") == ["dotnet", "git", "realsense", "robo-station-sdk"]
    assert resolve_capabilities("git,python", "realsense") == ["git", "python", "realsense"]  # override replaces
    assert resolve_capabilities("", None) == ["dotnet", "git"]
