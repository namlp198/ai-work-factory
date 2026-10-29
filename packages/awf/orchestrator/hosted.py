"""Care of the repositories hosted on the control plane (ADR-017): branch cleanup and the GitHub mirror.

- Branches: a task branch (`awf/tasks/<run>/...`) goes as soon as its run is closed (workers already delete it
  once it is integrated); a run branch (`awf/run-<run>`) goes once it is merged into the base branch, and after
  `stale_branch_days` when its run failed, was cancelled or was never merged.
- Mirror: the release and base branches (main, develop) and every tag are pushed to `repository.mirror.url`
  whenever they differ from what was last pushed, never forced. The result is kept in `repository.mirror`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from awf import githost
from awf.domain.enums import RunStatus
from awf.domain.models import Project, WorkflowRun
from awf.logging import get_logger
from awf.tool_sdk.command import GitError

logger = get_logger("awf.hosted")
MIRROR_RETRY = timedelta(minutes=5)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def repo_of(settings, project: Project) -> Path | None:
    url = (project.repository or {}).get("url")
    if not githost.is_hosted(url):
        return None
    path = githost.repo_path(settings.git_root, githost.hosted_slug(url))
    return path if path.exists() else None


def branches_of(project: Project) -> tuple[str, str]:
    """(base, release): runs work on and merge into `base` (develop); `release` (main) only changes on Release."""
    repo = project.repository or {}
    base = repo.get("base_branch") or "main"
    return base, repo.get("release_branch") or base


def prune(session: Session, settings, project: Project, now: datetime | None = None) -> list[str]:
    """Delete the `awf/*` branches the project's runs no longer need; returns the names deleted."""
    path = repo_of(settings, project)
    if path is None:
        return []
    now = now or _utcnow()
    base, _ = branches_of(project)
    runs = {r.id[:8]: r for r in session.scalars(select(WorkflowRun).where(WorkflowRun.project_id == project.id))}
    stale = timedelta(days=settings.stale_branch_days)
    doomed = []
    for name in githost.branches(path):
        parts = name.split("/")
        if name.startswith("awf/tasks/") and len(parts) >= 3:
            run = runs.get(parts[2])
            if run is not None and run.status != RunStatus.RUNNING:
                doomed.append(name)
        elif name.startswith("awf/run-"):
            run = runs.get(name[len("awf/run-"):])
            if run is None or run.status == RunStatus.RUNNING:
                continue
            merged = githost.branch_sha(path, base) and githost.is_ancestor(path, f"refs/heads/{name}",
                                                                             f"refs/heads/{base}")
            finished = _aware(run.finished_at)
            if merged or (finished is not None and now - finished >= stale):
                doomed.append(name)
    deleted = githost.delete_branches(path, doomed)
    if deleted:
        logger.info("deleted branches", extra={"project": project.slug, "branches": deleted})
    return deleted


def mirror_state(project: Project) -> dict[str, Any]:
    return dict((project.repository or {}).get("mirror") or {})


def mirror_due(settings, project: Project, now: datetime | None = None) -> bool:
    state = mirror_state(project)
    path = repo_of(settings, project)
    if not state.get("url") or path is None:
        return False
    if githost.mirror_refs(path, branches_of(project)) == state.get("pushed"):
        return False
    last = state.get("at")
    if state.get("error") and last:
        return (now or _utcnow()) - datetime.fromisoformat(last) >= MIRROR_RETRY
    return True


def mirror(settings, project: Project) -> dict[str, Any]:
    """Push the mirror now and record the result in `repository.mirror` (the caller commits the session)."""
    state = mirror_state(project)
    path = repo_of(settings, project)
    if not state.get("url"):
        raise githost.GitHostError("this project has no mirror URL")
    if path is None:
        raise githost.GitHostError("this project's repository is not hosted on the control plane")
    base, release = branches_of(project)
    try:
        result = githost.mirror_push(path, state["url"], settings.mirror_token or None, [release, base])
    except GitError as exc:
        result = {"ok": False, "refs": {}, "message": str(exc)[-1500:]}
    now = _utcnow().isoformat()
    if result["ok"]:
        state.update(pushed=result["refs"], at=now, ok_at=now, error=None)
    else:
        state.update(at=now, error=result["message"] or "push failed")
        logger.warning("mirror push failed", extra={"project": project.slug, "error": state["error"][:300]})
    project.repository = {**(project.repository or {}), "mirror": state}
    return state


def housekeeping(session: Session, settings) -> None:
    """Prune and mirror every hosted project (the orchestrator calls this every few minutes)."""
    for project in session.scalars(select(Project).where(Project.status != "ARCHIVED")):
        if not githost.is_hosted((project.repository or {}).get("url")):
            continue
        try:
            prune(session, settings, project)
            if mirror_due(settings, project):
                mirror(settings, project)
        except (GitError, githost.GitHostError, OSError) as exc:
            logger.warning("hosted repository care failed", extra={"project": project.slug, "error": str(exc)[:300]})
