"""A shared repository on the control plane, used like a team (ADR-016): your PC and the workers fetch, push and
rebase through Git Smart HTTP; workers write only awf/* branches; main moves only after approve_merge; conflicts
go to a resolve task, and to you when the agent cannot resolve them."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from awf import githost
from awf.worker_sdk import ControlPlaneClient
from awf.worker_sdk import Worker as WorkerDaemon
from awf.worker_sdk.sandbox import IntegrationConflict, Workspace

ROOT = Path(__file__).resolve().parents[2]


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "user.name=me", "-c", "user.email=me@pc",
                             *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode:
        raise AssertionError(f"git {' '.join(args)}: {result.stderr}")
    return result.stdout.strip()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(platform, orchestrator):
    from awf_api.main import create_app

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(create_app(platform, orchestrator, embedded_orchestrator=False),
                                        host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base + "/health", timeout=1)
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield base
    srv.should_exit = True
    thread.join(timeout=10)


# ---------------------------------------------------------------------------- permissions
def test_workers_may_only_push_awf_branches():
    z, a = "0" * 40, "a" * 40
    upd = lambda ref: githost.RefUpdate(z, a, ref)  # noqa: E731
    assert githost.check_push([upd("refs/heads/awf/run-1")], "worker", "main", False) is None
    assert "refused" in githost.check_push([upd("refs/heads/main")], "worker", "main", False)
    assert githost.check_push([upd("refs/heads/main")], "worker", "main", True) is None  # merge ticket
    assert "refused" in githost.check_push([githost.RefUpdate(a, z, "refs/heads/main")], "worker", "main", True)
    assert githost.check_push([upd("refs/heads/main"), upd("refs/tags/v1")], "person", "main", False) is None
    line = f"{z} {a} refs/heads/awf/x\0report-status side-band-64k".encode()
    body = f"{len(line) + 4:04x}".encode() + line + b"0000PACK..."
    assert githost.receive_commands(body) == [githost.RefUpdate(z, a, "refs/heads/awf/x")]


# ---------------------------------------------------------------------------- the team flow, over HTTP
def test_pc_and_workers_share_the_repository(server, platform, orchestrator, demo_repo, tmp_path):
    api = httpx.Client(base_url=server + "/api/v1", timeout=30)
    py = __import__("sys").executable
    project = api.post("/projects", json={"name": "Shared", "labels": {"platform": "linux"}, "repository": {
        "url": "placeholder", "build_command": [py, "-m", "compileall", "-q", "perception_service"],
        "test_command": [py, "-m", "pytest", "-q", "-p", "no:cacheprovider"]}}).json()
    info = api.post(f"/projects/{project['id']}/git", json={}).json()
    assert info["hosted"] and info["path"] == "/git/shared.git" and info["base_sha"] is None
    refused = api.post(f"/projects/{project['id']}/workflow-runs", json={"inputs": {"objective": "x"}})
    assert refused.status_code == 422 and "git push factory main" in refused.json()["detail"]

    # Your PC: the existing repository gets the control plane as a remote and pushes main.
    remote = server + info["path"]
    _git(demo_repo, "remote", "add", "factory", remote)
    _git(demo_repo, "push", "-q", "factory", "main")
    assert api.get(f"/projects/{project['id']}/git").json()["base_sha"] == _git(demo_repo, "rev-parse", "main")

    worker = WorkerDaemon(ControlPlaneClient(server), name="box", workspace_root=tmp_path / "w",
                          policy_path=ROOT / "config" / "policies.yaml", platform_name="linux-amd64",
                          capabilities=["git", "python"])
    worker.register("")
    run = api.post(f"/projects/{project['id']}/workflow-runs", json={"inputs": {"objective": "Health check"}}).json()
    for _ in range(40):
        orchestrator.run_until_idle()
        for a in api.get("/approvals", params={"state": "pending"}).json():
            api.post(f"/approvals/{a['id']}/approve", json={"comment": "ok"})
        if not worker.run_once() and api.get(f"/workflow-runs/{run['id']}").json()["status"] != "RUNNING":
            break
    state = api.get(f"/workflow-runs/{run['id']}").json()
    assert state["status"] == "COMPLETED", state.get("error")

    # develop moved only through the approved merge job, as a fast-forward of the rebased integration branch;
    # the run's branches are gone, and main waits for a Release (ADR-017).
    _git(demo_repo, "fetch", "-q", "--prune", "factory")
    main_before = _git(demo_repo, "rev-parse", "main")
    assert _git(demo_repo, "rev-parse", "factory/main") == main_before
    assert _git(demo_repo, "log", "--merges", "--oneline", "factory/develop") == ""  # linear history
    orchestrator.run_until_idle()  # housekeeping prunes whatever the workers left
    assert sorted(githost.branches(githost.repo_path(platform.settings.git_root, "shared"))) == ["develop", "main"]
    done = api.post(f"/projects/{project['id']}/release", json={}).json()
    assert done["released"] == _git(demo_repo, "rev-parse", "factory/develop") and not done["merge_commit"]
    _git(demo_repo, "pull", "-q", "--ff-only", "factory", "main")
    assert _git(demo_repo, "rev-parse", "main") != main_before and (demo_repo / "perception_service").exists()

    # A worker's token cannot move main by itself.
    token = worker.client.worker_token
    clone = tmp_path / "rogue"
    _git(tmp_path, "-c", f"http.extraHeader=Authorization: Bearer {token}", "clone", "-q", remote, str(clone))
    (clone / "x.txt").write_text("x", encoding="utf-8")
    _git(clone, "add", "x.txt")
    _git(clone, "commit", "-qm", "x")
    pushed = subprocess.run(["git", "-c", f"http.extraHeader=Authorization: Bearer {token}", "push", "origin",
                             "HEAD:main"], cwd=clone, capture_output=True, text=True)
    assert pushed.returncode != 0 and "403" in pushed.stderr


# ---------------------------------------------------------------------------- rebase and conflicts
@pytest.fixture
def bare(tmp_path, demo_repo):
    path = githost.create_repo(tmp_path / "git", "proj", import_from=str(demo_repo))
    return path


def _workspace(tmp_path, bare, job, task_branch=None):
    ws = Workspace(tmp_path / "ws", job)
    ws.prepare(str(bare), "main", "awf/run-1", task_branch)
    return ws


def _edit(ws: Workspace, text: str) -> None:
    (ws.repo / "perception_service" / "shared.py").write_text(text, encoding="utf-8")


def test_tasks_rebase_onto_each_other(tmp_path, bare):
    a = _workspace(tmp_path, bare, "a", "awf/tasks/1/a")
    b = _workspace(tmp_path, bare, "b", "awf/tasks/1/b")  # started before a was integrated
    (a.repo / "a.txt").write_text("a", encoding="utf-8")
    (b.repo / "b.txt").write_text("b", encoding="utf-8")
    assert a.commit_and_integrate("task a").integrated
    assert b.commit_and_integrate("task b").integrated  # rebased onto a, no merge commit
    log = _git(bare, "log", "--format=%s", "awf/run-1")
    assert log.splitlines()[:2] == ["task b", "task a"] and _git(bare, "log", "--merges", "--oneline", "awf/run-1") == ""


def test_a_conflict_is_resolved_in_one_squashed_commit(tmp_path, bare):
    a = _workspace(tmp_path, bare, "a", "awf/tasks/1/a")
    b = _workspace(tmp_path, bare, "b", "awf/tasks/1/b")
    _edit(a, "VALUE = 'a'\n")
    _edit(b, "VALUE = 'b'\n")
    a.commit_and_integrate("task a")
    with pytest.raises(IntegrationConflict) as exc:
        b.commit_and_integrate("task b")
    conflict = exc.value.as_dict()
    assert conflict == {"kind": "task", "source": "awf/tasks/1/b", "target": "awf/run-1",
                        "files": ["perception_service/shared.py"]}
    assert _git(bare, "rev-parse", "awf/tasks/1/b")  # b's work is kept on its branch

    r = _workspace(tmp_path, bare, "r")
    assert r.start_resolve("task", conflict["source"], conflict["target"]) == ["perception_service/shared.py"]
    assert "<<<<<<<" in (r.repo / "perception_service" / "shared.py").read_text(encoding="utf-8")
    with pytest.raises(Exception, match="conflict markers are still in"):
        r.commit_and_integrate("resolve")
    _edit(r, "VALUE = 'a+b'\n")
    info = r.commit_and_integrate("Resolve conflict")
    assert info.integrated
    assert _git(bare, "show", "awf/run-1:perception_service/shared.py") == "VALUE = 'a+b'"


def test_the_run_follows_main_and_merges_by_fast_forward(tmp_path, bare, demo_repo):
    a = _workspace(tmp_path, bare, "a", "awf/tasks/1/a")
    (a.repo / "feature.txt").write_text("f", encoding="utf-8")
    a.commit_and_integrate("feature")
    # Meanwhile someone pushes to main.
    pc = tmp_path / "pc"
    _git(tmp_path, "clone", "-q", str(bare), str(pc))
    (pc / "hotfix.txt").write_text("h", encoding="utf-8")
    _git(pc, "add", "hotfix.txt")
    _git(pc, "commit", "-qm", "hotfix")
    _git(pc, "push", "-q", "origin", "main")

    d = _workspace(tmp_path, bare, "d")
    assert d.sync_with_base()["updated"]
    assert _git(bare, "merge-base", "--is-ancestor", "main", "awf/run-1", check=False) == ""
    merged = _workspace(tmp_path, bare, "m").merge_to_base()
    assert merged["merged"] == _git(bare, "rev-parse", "main") and merged["deleted_branches"] == ["awf/run-1"]
    assert _git(bare, "log", "--format=%s", "main").splitlines()[:2] == ["feature", "hotfix"]


def test_a_conflict_with_main_is_reported_as_base(tmp_path, bare):
    a = _workspace(tmp_path, bare, "a", "awf/tasks/1/a")
    _edit(a, "VALUE = 'run'\n")
    a.commit_and_integrate("run change")
    pc = tmp_path / "pc"
    _git(tmp_path, "clone", "-q", str(bare), str(pc))
    (pc / "perception_service" / "shared.py").write_text("VALUE = 'main'\n", encoding="utf-8")
    _git(pc, "add", "-A")
    _git(pc, "commit", "-qm", "main change")
    _git(pc, "push", "-q", "origin", "main")
    with pytest.raises(IntegrationConflict) as exc:
        _workspace(tmp_path, bare, "d").sync_with_base()
    assert exc.value.kind == "base" and exc.value.target == "main" and exc.value.source == "awf/run-1"
    r = _workspace(tmp_path, bare, "r")
    r.start_resolve("base", "awf/run-1", "main")
    _edit(r, "VALUE = 'main and run'\n")
    r.commit_and_integrate("Resolve conflict with main")
    assert _git(bare, "merge-base", "--is-ancestor", "main", "awf/run-1", check=False) == ""  # now on top of main
    assert _git(bare, "show", "awf/run-1:perception_service/shared.py") == "VALUE = 'main and run'"


# ---------------------------------------------------------------------------- orchestrator
def test_a_conflict_becomes_a_resolve_task_then_a_question_for_you(client, platform, orchestrator, worker,
                                                                   repository_config):
    from sqlalchemy import select

    from awf.domain.models import Approval, Task, TaskDependency, WorkflowRun

    project = client.post("/api/v1/projects", json={"name": "P", "repository": repository_config,
                                                    "labels": {"platform": "linux"}}).json()
    run_id = client.post(f"/api/v1/projects/{project['id']}/workflow-runs", json={"inputs": {"objective": "x"}}).json()["id"]
    conflict = {"kind": "base", "source": "awf/run-x", "target": "main", "files": ["a.py"]}
    workflow = platform.workflows.get("software-development")
    with platform.db.transaction() as s:
        run = s.get(WorkflowRun, run_id)
        delivery = orchestrator._new_task(s, run, workflow.spec.stage("delivery"), key="delivery", title="Delivery",
                                          task_type="tool", role=None, iteration=1,
                                          input_json={"tool": "git.export_patch"})
        s.flush()
        resolver = orchestrator._spawn_resolve(s, run, workflow.spec.stage("delivery"), delivery, conflict)
        assert resolver.assigned_role == "engineering.developer" and resolver.input_json["resolve"] == conflict
        assert s.scalars(select(TaskDependency).where(TaskDependency.task_id == delivery.id)).one().depends_on_task_id == resolver.id
        resolver.state = "RUNNING"
        orchestrator._fail_task(s, run, resolver, "conflict markers are still in: a.py")
        assert resolver.state == "APPROVAL_REQUIRED"  # not FAILED: you are asked first
        approval = s.scalars(select(Approval).where(Approval.task_id == resolver.id)).one()
    assert approval.action == "resolve_conflict"
    assert "git push --force factory" in "\n".join(approval.scope["commands"])
    assert approval.scope["files"] == ["a.py"]


def test_backup_bundles_every_hosted_repository(tmp_path, bare):
    from awf import dbops

    githost.create_repo(tmp_path / "git", "empty")  # nothing pushed: no bundle
    (bundle,) = dbops.backup_git(tmp_path / "git", tmp_path / "backups", keep=3)
    assert bundle.name.startswith("git-proj-")
    restored = tmp_path / "restored.git"
    _git(tmp_path, "clone", "-q", "--bare", str(bundle), str(restored))
    assert _git(restored, "rev-parse", "main") == _git(bare, "rev-parse", "main")
