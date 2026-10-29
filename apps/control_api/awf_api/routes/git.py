"""Git Smart HTTP for hosted project repositories (ADR-016): /git/<slug>.git/...

Authentication is HTTP Basic (what git sends from a credential manager) or a bearer header (what workers send):
- the git token (AWF_GIT_TOKEN, else var/git/.token, shown on the project page) or AWF_API_TOKEN: a person;
- a registered worker's token: a worker (awf/* branches; the base branch only with a merge ticket).
A development control plane without AWF_API_TOKEN is open, like the API.
"""

from __future__ import annotations

import base64
import hmac
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from awf import githost
from awf.domain.models import Project, Worker, WorkflowRun
from awf.security import hash_token
from awf_api.deps import AppState, get_state

router = APIRouter(tags=["git"])
MERGE_TICKET_HEADER = "X-AWF-Merge-Ticket"


def git_token(root: Path) -> str:
    """The token people use for git: AWF_GIT_TOKEN, else one generated once and kept in <git root>/.token."""
    import os

    if os.environ.get("AWF_GIT_TOKEN"):
        return os.environ["AWF_GIT_TOKEN"]
    path = root / ".token"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def _credential(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if auth.lower().startswith("basic "):
        try:
            return base64.b64decode(auth[6:]).decode("utf-8").split(":", 1)[1]
        except (ValueError, IndexError):
            return ""
    return ""


def _who(request: Request, state: AppState) -> tuple[str, str]:
    """(role, name) of the caller: ("person", ...) or ("worker", <worker name>)."""
    settings = state.platform.settings
    token = _credential(request)
    if token:
        for expected in (git_token(settings.git_root), settings.api_token):
            if expected and hmac.compare_digest(token, expected):
                return "person", "owner"
        with state.platform.db.session() as s:
            worker = s.scalar(select(Worker).where(Worker.token_hash == hash_token(token)))
            if worker is not None and worker.status != "revoked":
                return "worker", worker.name
    if not settings.api_token and settings.environment == "development":
        return "person", "owner"
    raise HTTPException(401, "git credentials required", headers={"WWW-Authenticate": 'Basic realm="AI Work Factory git"'})


def _project(state: AppState, slug: str) -> Project:
    with state.platform.db.session() as s:
        project = s.scalar(select(Project).where(Project.slug == slug))
        if project is None or not githost.is_hosted((project.repository or {}).get("url")):
            raise HTTPException(404, "no hosted repository with this name")
        s.expunge(project)
        return project


def _merge_ticket_valid(state: AppState, project: Project, ticket: str) -> bool:
    if not ticket:
        return False
    digest = hash_token(ticket)
    with state.platform.db.session() as s:
        runs = s.scalars(select(WorkflowRun).where(WorkflowRun.project_id == project.id,
                                                   WorkflowRun.status == "RUNNING"))
        return any(hmac.compare_digest((r.context_json or {}).get("merge_ticket", ""), digest) for r in runs)


@router.api_route("/git/{slug}.git/{rest:path}", methods=["GET", "POST"], include_in_schema=False)
async def git_http(slug: str, rest: str, request: Request, state: AppState = Depends(get_state)) -> Response:
    if ".." in rest.split("/") or "\\" in rest:
        raise HTTPException(400, "bad repository path")
    role, name = _who(request, state)
    project = _project(state, slug)
    body = await request.body()
    service = request.query_params.get("service") or rest.rsplit("/", 1)[-1]
    if service == "git-receive-pack" and request.method == "POST":
        try:
            body = githost.decode_body(body, request.headers.get("content-encoding"))
        except githost.GitHostError as exc:
            raise HTTPException(400, str(exc)) from None
        base = (project.repository or {}).get("base_branch") or "main"
        updates = githost.receive_commands(body)
        merge_ok = role == "worker" and _merge_ticket_valid(state, project, request.headers.get(MERGE_TICKET_HEADER, ""))
        refused = githost.check_push(updates, role, base, merge_ok)
        if refused:
            raise HTTPException(403, refused)
    elif request.method == "POST":
        body = githost.decode_body(body, request.headers.get("content-encoding"))
    result = await run_in_threadpool(
        githost.http_backend, state.platform.settings.git_root, f"/{slug}.git/{rest}", request.method,
        request.url.query, request.headers.get("content-type", ""), body, f"{role}:{name}")
    headers = {k: v for k, v in result.headers.items() if k.lower() not in ("content-length", "transfer-encoding")}
    return Response(content=result.body, status_code=result.status, headers=headers)
