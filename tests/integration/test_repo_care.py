"""Hosted repositories after a run (ADR-017): branches are cleaned up, runs work on develop and main changes only on
Release, and main, develop and the tags are mirrored to GitHub (here: another bare repository)."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from awf import githost
from awf.domain.models import Project, WorkflowRun
from awf.orchestrator import hosted
from awf.worker_sdk.sandbox import Workspace


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "user.name=me", "-c", "user.email=me@pc",
                             *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode:
        raise AssertionError(f"git {' '.join(args)}: {result.stderr}")
    return result.stdout.strip()


def _commit(bare: Path, tmp: Path, branch: str, name: str, text: str, start: str | None = None) -> str:
    pc = tmp / f"pc-{len(list(tmp.glob('pc-*')))}"
    _git(tmp, "clone", "-q", str(bare), str(pc))
    _git(pc, "switch", "-q", "-C", branch, f"origin/{start or branch}")
    (pc / name).write_text(text, encoding="utf-8")
    _git(pc, "add", "-A")
    _git(pc, "commit", "-qm", f"{branch}: {name}")
    _git(pc, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    return _git(pc, "rev-parse", "HEAD")


@pytest.fixture
def bare(tmp_path, demo_repo):
    path = githost.create_repo(tmp_path / "git", "proj", import_from=str(demo_repo))
    assert githost.ensure_branch(path, "develop", "main") and not githost.ensure_branch(path, "develop", "main")
    return path


# ---------------------------------------------------------------------------- release
def test_release_fast_forwards_main_then_merges_and_tags(tmp_path, bare):
    feature = _commit(bare, tmp_path, "develop", "feature.txt", "f")
    plan = githost.release_plan(bare, "develop", "main")
    assert plan.fast_forward and plan.commits == [f"{feature[:7]} develop: feature.txt"]
    done = githost.release(bare, "develop", "main", tag="v1.0")
    assert done["released"] == feature and not done["merge_commit"]
    assert _git(bare, "rev-parse", "main") == feature and _git(bare, "rev-parse", "v1.0^{commit}") == feature
    with pytest.raises(githost.GitHostError, match="already contains"):
        githost.release(bare, "develop", "main")

    _commit(bare, tmp_path, "main", "hotfix.txt", "h")  # a hotfix straight on main
    _commit(bare, tmp_path, "develop", "next.txt", "n")
    done = githost.release(bare, "develop", "main")
    assert done["merge_commit"] and _git(bare, "log", "-1", "--format=%s", "main") == "Release develop into main"
    assert {"hotfix.txt", "next.txt", "feature.txt"} <= set(_git(bare, "ls-tree", "--name-only", "main").split())


def test_a_release_that_conflicts_changes_nothing(tmp_path, bare):
    _commit(bare, tmp_path, "main", "same.txt", "main")
    _commit(bare, tmp_path, "develop", "same.txt", "develop")
    before = _git(bare, "rev-parse", "main")
    with pytest.raises(githost.ReleaseConflict) as exc:
        githost.release(bare, "develop", "main")
    assert exc.value.files == ["same.txt"] and _git(bare, "rev-parse", "main") == before


# ---------------------------------------------------------------------------- branch cleanup
def test_workers_delete_task_branches_once_integrated_and_the_run_branch_once_merged(tmp_path, bare):
    a = Workspace(tmp_path / "ws", "a")
    a.prepare(str(bare), "develop", "awf/run-12345678", "awf/tasks/12345678/a-i1-a1")
    (a.repo / "a.txt").write_text("a", encoding="utf-8")
    assert a.commit_and_integrate("task a").integrated
    assert githost.branches(bare) == ["awf/run-12345678", "develop", "main"]  # the task branch is gone

    m = Workspace(tmp_path / "ws", "m")
    m.prepare(str(bare), "develop", "awf/run-12345678", None)
    merged = m.merge_to_base()
    assert merged["deleted_branches"] == ["awf/run-12345678"] and githost.branches(bare) == ["develop", "main"]
    assert _git(bare, "rev-parse", "develop") == merged["merged"] and _git(bare, "show", "develop:a.txt") == "a"


def _hosted_project(platform, tmp_path, demo_repo, **repo) -> str:
    path = githost.create_repo(platform.settings.git_root, "care", import_from=str(demo_repo))
    githost.ensure_branch(path, "develop", "main")
    with platform.db.transaction() as s:
        p = Project(name="Care", slug="care", labels={"platform": "linux"},
                    repository={"url": "awf-git:care", "base_branch": "develop", "release_branch": "main", **repo})
        s.add(p)
        s.flush()
        return p.id


def _run(platform, project_id, status, finished_days_ago=None) -> str:
    with platform.db.transaction() as s:
        r = WorkflowRun(project_id=project_id, workflow_id="software-development", workflow_version="1", status=status,
                        input_json={}, context_json={})
        if finished_days_ago is not None:
            r.finished_at = datetime.now(timezone.utc) - timedelta(days=finished_days_ago)
        s.add(r)
        s.flush()
        return r.id[:8]


def test_housekeeping_prunes_what_the_runs_no_longer_need(platform, tmp_path, demo_repo):
    pid = _hosted_project(platform, tmp_path, demo_repo)
    path = githost.repo_path(platform.settings.git_root, "care")
    running, done, failed_old, failed_new = (_run(platform, pid, "RUNNING"), _run(platform, pid, "COMPLETED", 0),
                                             _run(platform, pid, "FAILED", 15), _run(platform, pid, "CANCELLED", 1))
    develop = githost.branch_sha(path, "develop")
    for run in (running, done, failed_old, failed_new):
        _git(path, "update-ref", f"refs/heads/awf/run-{run}", develop)
        _git(path, "update-ref", f"refs/heads/awf/tasks/{run}/t1-i1-a1", develop)
    _git(path, "update-ref", "refs/heads/awf/run-unknown1", develop)  # not one of this project's runs: kept
    _commit(path, tmp_path, "awf/run-" + done, "unmerged.txt", "u", start="develop")  # completed, never merged
    _commit(path, tmp_path, "awf/run-" + failed_new, "recent.txt", "r", start="develop")  # kept 14 days

    with platform.db.transaction() as s:
        deleted = hosted.prune(s, platform.settings, s.get(Project, pid))
    assert sorted(deleted) == sorted([f"awf/tasks/{done}/t1-i1-a1", f"awf/tasks/{failed_old}/t1-i1-a1",
                                      f"awf/tasks/{failed_new}/t1-i1-a1", f"awf/run-{failed_old}"])
    assert set(githost.branches(path)) == {"main", "develop", f"awf/run-{running}", f"awf/tasks/{running}/t1-i1-a1",
                                           f"awf/run-{done}", f"awf/run-{failed_new}", "awf/run-unknown1"}


# ---------------------------------------------------------------------------- mirror
def test_the_mirror_gets_main_develop_and_tags_and_reports_a_refusal(platform, tmp_path, demo_repo):
    github = tmp_path / "github.git"
    _git(tmp_path, "init", "-q", "--bare", str(github))
    pid = _hosted_project(platform, tmp_path, demo_repo, mirror={"url": str(github)})
    path = githost.repo_path(platform.settings.git_root, "care")
    _git(path, "update-ref", "refs/heads/awf/run-1", githost.branch_sha(path, "develop"))
    _git(path, "tag", "v0.1", "main")

    with platform.db.transaction() as s:
        project = s.get(Project, pid)
        assert hosted.mirror_due(platform.settings, project)
        state = hosted.mirror(platform.settings, project)
        assert state["error"] is None and not hosted.mirror_due(platform.settings, project)
    assert githost.branches(github) == ["develop", "main"]  # never awf/*
    assert _git(github, "tag") == "v0.1"

    # Someone pushes to GitHub directly: the control plane never forces, it reports and keeps trying.
    _commit(github, tmp_path, "main", "on-github.txt", "g")
    _commit(path, tmp_path, "main", "here.txt", "h")
    with platform.db.transaction() as s:
        state = hosted.mirror(platform.settings, s.get(Project, pid))
    assert state["error"] and "rejected" in state["error"]
    with platform.db.session() as s:
        project = s.get(Project, pid)
        assert not hosted.mirror_due(platform.settings, project)  # waits MIRROR_RETRY before the next try
        later = datetime.now(timezone.utc) + hosted.MIRROR_RETRY
        assert hosted.mirror_due(platform.settings, project, now=later)


def test_the_token_reaches_git_only_as_a_header_for_that_url():
    env = githost.mirror_env("https://github.com/me/app.git", "ghp_secret")
    assert env["GIT_CONFIG_KEY_1"] == "http.https://github.com/me/app.git.extraHeader"
    assert env["GIT_CONFIG_VALUE_1"].startswith("Authorization: Basic ") and "ghp_secret" not in env["GIT_CONFIG_VALUE_1"]
    assert githost.mirror_env("git@github.com:me/app.git", "ghp_secret")["GIT_CONFIG_COUNT"] == "1"  # ssh: its key


# ---------------------------------------------------------------------------- API
def test_api_hosts_on_develop_releases_and_mirrors(client, platform, tmp_path, demo_repo):
    github = tmp_path / "github.git"
    _git(tmp_path, "init", "-q", "--bare", str(github))
    p = client.post("/api/v1/projects", json={"name": "App", "labels": {"platform": "linux"},
                                              "repository": {"url": "pending"}}).json()
    info = client.post(f"/api/v1/projects/{p['id']}/git", json={"import_from": str(demo_repo)}).json()
    assert (info["base_branch"], info["release_branch"]) == ("develop", "main")
    assert info["release"]["commits"] == [] and set(info["branches"]) == {"develop", "main"}
    bad = client.patch(f"/api/v1/projects/{p['id']}", json={"repository": {
        "url": "awf-git:app", "mirror": {"url": "https://me:pw@github.com/me/app.git"}}})
    assert bad.status_code == 422

    path = githost.repo_path(platform.settings.git_root, "app")
    with platform.db.transaction() as s:  # a local path stands in for GitHub
        project = s.get(Project, p["id"])
        project.repository = {**project.repository, "mirror": {"url": str(github)}}
    feature = _commit(path, tmp_path, "develop", "feature.txt", "f")
    assert client.get(f"/api/v1/projects/{p['id']}/git").json()["release"]["fast_forward"]
    done = client.post(f"/api/v1/projects/{p['id']}/release", json={"tag": "v1.0"}).json()
    assert done["released"] == feature and done["mirror"]["error"] is None
    assert _git(github, "rev-parse", "main") == feature and _git(github, "tag") == "v1.0"
    again = client.post(f"/api/v1/projects/{p['id']}/release", json={})
    assert again.status_code == 409 and "already contains" in again.json()["detail"]
    assert client.post(f"/api/v1/projects/{p['id']}/mirror").json()["error"] is None


# ---------------------------------------------------------------------------- read-only references (part 4)
def test_references_are_cloned_read_only_and_never_committed(tmp_path, bare):
    from awf.tool_sdk import PathViolation
    from awf.tool_sdk.base import resolve_in_workspace

    ui_kit = githost.create_repo(tmp_path / "git", "ui-kit")
    _git(tmp_path, "clone", "-q", str(ui_kit), str(tmp_path / "kit"))
    (tmp_path / "kit" / "grid.css").write_text(".grid{}", encoding="utf-8")
    _git(tmp_path / "kit", "add", "-A")
    _git(tmp_path / "kit", "commit", "-qm", "grid")
    _git(tmp_path / "kit", "push", "-q", "origin", "HEAD:refs/heads/feature/grid")

    ws = Workspace(tmp_path / "ws", "a")
    ws.prepare(str(bare), "develop", "awf/run-87654321", "awf/tasks/87654321/a-i1-a1")
    notes = ws.add_references([{"name": "ui-kit", "url": str(ui_kit), "branch": "feature/grid"},
                               {"name": "old", "url": str(bare), "branch": "no-such-branch"}])
    assert notes[0].startswith(".awf-refs/ui-kit:") and "NOT AVAILABLE" in notes[1]
    assert (ws.repo / ".awf-refs" / "ui-kit" / "grid.css").read_text(encoding="utf-8") == ".grid{}"
    assert not (ws.repo / ".awf-refs" / "ui-kit" / ".git").exists()
    assert resolve_in_workspace(ws.repo, ".awf-refs/ui-kit/grid.css")  # readable
    with pytest.raises(PathViolation, match="read-only"):
        resolve_in_workspace(ws.repo, ".awf-refs/ui-kit/grid.css", write=True)

    (ws.repo / "page.css").write_text("@import grid", encoding="utf-8")
    (ws.repo / ".awf-refs" / "ui-kit" / "grid.css").write_text("changed by an engine", encoding="utf-8")
    ws.commit_and_integrate("page")
    assert ".awf-refs" not in _git(bare, "ls-tree", "-r", "--name-only", "awf/run-87654321")


def test_references_reach_the_agent_and_a_hosted_one_is_cloned_from_the_control_plane():
    from awf.worker_sdk import ControlPlaneClient
    from awf.worker_sdk import Worker as WorkerDaemon

    w = WorkerDaemon(ControlPlaneClient("http://factory:8000"), name="w", workspace_root=Path("."),
                     policy_path=Path(__file__).resolve().parents[2] / "config" / "policies.yaml")
    w.client.worker_token = "tok"
    out = w.with_git_access({"repository": {"url": "https://github.com/me/app.git", "references": [
        {"name": "kit", "url": "awf-git:ui-kit", "branch": "main"}, {"name": "self", "url": "", "branch": "v1"}]}})
    kit, own = out["references"]
    assert kit["url"] == "http://factory:8000/git/ui-kit.git" and kit["hosted"]
    assert own["url"] == "https://github.com/me/app.git" and not own["hosted"] and own["label"] == "this repository"
    # The worker's token is sent to the control plane's git only, never to GitHub.
    assert out["_git_env"]["GIT_CONFIG_KEY_0"] == "http.http://factory:8000/git/.extraHeader"
    assert out["repository"]["url"] == "https://github.com/me/app.git"


# ---------------------------------------------------------------------------- test screenshots (part 5)
def test_collect_test_artifacts_takes_new_files_within_limits(tmp_path, monkeypatch):
    import base64
    import os
    import time

    from awf.worker_sdk import executor

    (tmp_path / "TestResults").mkdir()
    old = tmp_path / "TestResults" / "old.png"
    old.write_bytes(b"old")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    (tmp_path / "TestResults" / "home.png").write_bytes(b"\x89PNG fake")
    (tmp_path / "TestResults" / "report.xml").write_text('<x password="Abc@12345"/>', encoding="utf-8")
    (tmp_path / "TestResults" / "big.png").write_bytes(b"x" * 300)
    monkeypatch.setitem(executor.TEST_ARTIFACT_LIMITS, "file_bytes", 200)
    found = executor.collect_test_artifacts(tmp_path, ["TestResults/**/*.png", "TestResults/*.xml", "../etc/*"],
                                            time.time() - 60)
    assert [f["path"] for f in found] == ["TestResults/home.png", "TestResults/report.xml"]
    assert found[0]["media_type"] == "image/png" and base64.b64decode(found[0]["data"]) == b"\x89PNG fake"
    assert b"Abc@12345" not in base64.b64decode(found[1]["data"])  # text reports are redacted


SCREENSHOTS = {
    "id": "screens", "version": "1", "inputs": ["objective", "repository"],
    "stages": [{"id": "development", "role": "engineering.developer"},
               {"id": "review", "role": "engineering.code-reviewer", "depends_on": ["development"]}],
}


def test_screenshots_become_artifacts_and_reach_the_reviewer(client, platform, orchestrator, worker, repository_config,
                                                             tmp_path, monkeypatch):
    import yaml

    from awf.workflow_engine import parse_workflow

    loaded = parse_workflow(SCREENSHOTS, platform.agents.roles())
    platform.workflows._workflows[loaded.spec.id] = loaded
    review = {"summary": "ok", "decision": "APPROVE",
              "outputs": {"review_report": "## Summary\nok\n## Findings\nnone\n## Verdict\nAPPROVE"}}
    script = {"roles": {
        "engineering.developer": [{"turns": [
            {"tool_calls": [{"name": "fs.write", "input": {"path": "perception_service/ui.py", "content": "X = 1\n"}},
                            {"name": "fs.write", "input": {"path": "TestResults/home.png", "content": "PNG-bytes"}}]},
            {"tool_calls": [{"name": "build.run", "input": {}}, {"name": "test.run", "input": {}}]},
            {"submit": {"summary": "done"}}]}],
        "engineering.code-reviewer": [{"turns": [
            {"tool_calls": [{"name": "fs.read", "input": {"path": ".awf-artifacts/01-home.png"}},
                            {"name": "test.run", "input": {}}]},
            {"submit": review}]}]}}
    (tmp_path / "script.yaml").write_text(yaml.safe_dump(script), encoding="utf-8")
    monkeypatch.setenv("AWF_SCRIPTED_RESPONSES", str(tmp_path / "script.yaml"))
    p = client.post("/api/v1/projects", json={"name": "Shots", "labels": {"platform": "linux"}, "repository": {
        **repository_config, "test_artifacts": ["TestResults/*.png"]}}).json()
    run_id = client.post(f"/api/v1/projects/{p['id']}/workflow-runs",
                         json={"workflow_id": "screens", "inputs": {"objective": "Home page"}}).json()["id"]
    for _ in range(30):
        orchestrator.run_until_idle()
        while worker.run_once():
            orchestrator.run_until_idle()
        if client.get(f"/api/v1/workflow-runs/{run_id}").json()["status"] != "RUNNING":
            break
    run = client.get(f"/api/v1/workflow-runs/{run_id}").json()
    assert run["status"] == "COMPLETED", run.get("error")
    arts = client.get(f"/api/v1/projects/{p['id']}/artifacts", params={"run_id": run_id}).json()
    shot = next(a for a in arts if a["type"] == "test_screenshot")
    assert shot["name"].endswith("/tests/TestResults/home.png") and shot["media_type"] == "image/png"
    assert client.get(f"/api/v1/artifacts/{shot['id']}/content").content == b"PNG-bytes"

    tasks = client.get(f"/api/v1/workflow-runs/{run_id}/tasks").json()
    reviewer = client.get(f"/api/v1/tasks/{next(t['id'] for t in tasks if t['stage_id'] == 'review')}").json()
    job = client.get(f"/api/v1/jobs/{reviewer['jobs'][0]['id']}").json()
    assert job["payload"]["attachments"][0]["file"] == "01-home.png"
    logs = client.get(f"/api/v1/jobs/{job['id']}/logs").json()
    lines = [x.get("line", "") for x in (logs.get("lines") if isinstance(logs, dict) else logs)]
    assert any("1 input file(s) in .awf-artifacts/" in line for line in lines), lines
