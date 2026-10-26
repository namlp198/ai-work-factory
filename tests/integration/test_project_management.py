"""Editing, deleting and archiving projects; the overview's recently finished runs."""

from __future__ import annotations


def _project(client, repository_config, name="P"):
    res = client.post("/api/v1/projects", json={"name": name, "repository": repository_config,
                                                "labels": {"platform": "linux"}, "budget_usd": 5})
    assert res.status_code == 201, res.text
    return res.json()


def test_edit_every_setting(client, repository_config):
    p = _project(client, repository_config)
    repo = {**repository_config, "url": "https://example.com/new.git", "base_branch": "develop",
            "build_command": ["C:\\Tools\\py.exe", "-m", "compileall", "src"]}
    res = client.patch(f"/api/v1/projects/{p['id']}", json={
        "name": "Renamed", "description": "d", "repository": repo, "budget_usd": None, "confidentiality": "public"})
    assert res.status_code == 200, res.text
    got = client.get(f"/api/v1/projects/{p['id']}").json()
    assert (got["name"], got["description"], got["budget_usd"], got["confidentiality"]) == ("Renamed", "d", None, "public")
    assert got["repository"]["url"] == "https://example.com/new.git" and got["repository"]["base_branch"] == "develop"
    assert got["repository"]["build_command"][0] == "C:\\Tools\\py.exe"  # argv kept as sent, backslashes intact
    assert got["labels"] == {"platform": "linux"} and got["run_count"] == 0


def test_delete_a_project_that_never_ran(client, repository_config):
    p = _project(client, repository_config)
    assert client.delete(f"/api/v1/projects/{p['id']}").json() == {"deleted": p["id"]}
    assert client.get(f"/api/v1/projects/{p['id']}").status_code == 404
    events = client.get("/api/v1/audit?limit=20").json()
    events = events.get("events", events) if isinstance(events, dict) else events
    assert any(e.get("event_type") == "PROJECT_DELETED" for e in events)


def test_a_project_with_runs_is_archived_not_deleted(client, orchestrator, repository_config):
    p = _project(client, repository_config)
    run = client.post(f"/api/v1/projects/{p['id']}/workflow-runs", json={"inputs": {"objective": "x"}})
    assert run.status_code == 201, run.text
    refused = client.delete(f"/api/v1/projects/{p['id']}")
    assert refused.status_code == 409 and "archive it instead" in refused.json()["detail"]
    busy = client.post(f"/api/v1/projects/{p['id']}/archive")
    assert busy.status_code == 409 and "running run" in busy.json()["detail"]

    client.post(f"/api/v1/workflow-runs/{run.json()['id']}/cancel")
    archived = client.post(f"/api/v1/projects/{p['id']}/archive")
    assert archived.status_code == 200 and archived.json()["status"] == "ARCHIVED"
    assert [x["id"] for x in client.get("/api/v1/projects").json()] == []
    assert [x["id"] for x in client.get("/api/v1/projects?archived=true").json()] == [p["id"]]
    assert client.get("/api/v1/overview").json()["projects"] == 0
    blocked = client.post(f"/api/v1/projects/{p['id']}/workflow-runs", json={"inputs": {"objective": "y"}})
    assert blocked.status_code == 422 and "archived" in blocked.json()["detail"]
    assert client.get(f"/api/v1/workflow-runs/{run.json()['id']}").status_code == 200  # history stays readable

    restored = client.post(f"/api/v1/projects/{p['id']}/restore")
    assert restored.json()["status"] == "ACTIVE"
    again = client.post(f"/api/v1/projects/{p['id']}/workflow-runs", json={"inputs": {"objective": "z"}})
    assert again.status_code == 201


def test_overview_lists_recently_finished_runs(client, repository_config):
    p = _project(client, repository_config, name="Shop")
    run = client.post(f"/api/v1/projects/{p['id']}/workflow-runs", json={"inputs": {"objective": "Add CSV export"}}).json()
    client.post(f"/api/v1/workflow-runs/{run['id']}/cancel")
    recent = client.get("/api/v1/overview").json()["recent_runs"]
    assert recent[0]["id"] == run["id"] and recent[0]["project"] == "Shop" and recent[0]["status"] == "CANCELLED"
    assert recent[0]["objective"] == "Add CSV export"
