"""Git hosting on the control plane (ADR-016): one bare repository per project, served over Git Smart HTTP.

Your PC adds it as a remote (`git remote add factory https://<control plane>/git/<slug>.git`) and fetches, pulls
and pushes as with any server; workers on every machine clone from it and push their `awf/*` branches. The
protocol itself is `git http-backend` (shipped with git); this module only creates repositories, decides who
may update which branch, and runs the backend as a CGI program.

Who may write what (checked on the ref updates of a push, before git sees the pack):
- people (AWF_GIT_TOKEN or AWF_API_TOKEN): every branch;
- workers: `awf/*` branches only, plus the project's base branch with a merge ticket, which the control plane
  issues for the merge job after a human approved `approve_merge`.
"""

from __future__ import annotations

import gzip
import re
import shutil
import subprocess
import zlib
from dataclasses import dataclass
from pathlib import Path

from awf.tool_sdk.command import GitError, git

HOSTED_SCHEME = "awf-git:"  # repository.url of a hosted project: awf-git:<slug>
WORKER_PREFIX = "refs/heads/awf/"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")


class GitHostError(ValueError):
    pass


def is_hosted(url: str | None) -> bool:
    return bool(url) and str(url).startswith(HOSTED_SCHEME)


def hosted_slug(url: str) -> str:
    slug = str(url)[len(HOSTED_SCHEME):]
    if not _SLUG.match(slug):
        raise GitHostError(f"invalid hosted repository name '{slug}'")
    return slug


def repo_path(root: Path, slug: str) -> Path:
    """`root` is settings.git_root (AWF_GIT_ROOT, default var/git)."""
    if not _SLUG.match(slug):
        raise GitHostError(f"invalid repository name '{slug}'")
    return root / f"{slug}.git"


def _configure(path: Path) -> None:
    # Pushes over HTTP need receive-pack on; awf/* branches are rebased, so non-fast-forward updates are allowed.
    for key, value in (("http.receivepack", "true"), ("receive.denyNonFastForwards", "false"),
                       ("receive.denyDeletes", "false"), ("core.logAllRefUpdates", "true")):
        git(["config", key, value], path)


def create_repo(root: Path, slug: str, base_branch: str = "main", import_from: str | None = None) -> Path:
    """A new empty bare repository, or a bare copy of `import_from` (a URL or a path on this machine)."""
    path = repo_path(root, slug)
    if path.exists():
        raise GitHostError(f"repository {slug}.git already exists on the control plane")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if import_from:
            git(["clone", "--bare", "--quiet", import_from, str(path)], path.parent, timeout=1800)
        else:
            git(["init", "--bare", "--quiet", "-b", base_branch, str(path)], path.parent)
        _configure(path)
    except GitError:
        shutil.rmtree(path, ignore_errors=True)
        raise
    return path


def branch_sha(path: Path, branch: str) -> str | None:
    result = git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], path, check=False)
    return result.stdout.strip() or None if result.ok else None


def branches(path: Path) -> list[str]:
    out = git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], path, check=False).stdout
    return [b for b in out.splitlines() if b]


# ---------------------------------------------------------------------------------------- push permissions
@dataclass
class RefUpdate:
    old: str
    new: str
    ref: str


def receive_commands(body: bytes) -> list[RefUpdate]:
    """The ref updates at the start of a receive-pack request (pkt-lines up to the first flush)."""
    updates: list[RefUpdate] = []
    pos = 0
    while pos + 4 <= len(body):
        size = int(body[pos:pos + 4], 16)
        if size == 0:
            break
        line = body[pos + 4:pos + size].split(b"\0", 1)[0].decode("utf-8", "replace").strip()
        pos += size
        parts = line.split(" ")
        if len(parts) == 3:
            updates.append(RefUpdate(*parts))
    return updates


def check_push(updates: list[RefUpdate], role: str, base_branch: str, merge_allowed: bool) -> str | None:
    """Why this push is refused, or None. People may update anything; workers only awf/* (and the base branch
    with a merge ticket, never deleting it)."""
    if role != "worker":
        return None
    for u in updates:
        if u.ref.startswith(WORKER_PREFIX):
            continue
        if u.ref == f"refs/heads/{base_branch}" and merge_allowed and set(u.new) != {"0"}:
            continue
        return f"workers may only push awf/* branches; {u.ref} is refused"
    return None


def decode_body(body: bytes, encoding: str | None) -> bytes:
    if (encoding or "").lower() in ("gzip", "x-gzip"):
        try:
            return gzip.decompress(body)
        except (OSError, EOFError, zlib.error) as exc:
            raise GitHostError(f"bad gzip request body: {exc}") from None
    return body


# ---------------------------------------------------------------------------------------- CGI
@dataclass
class CgiResponse:
    status: int
    headers: dict[str, str]
    body: bytes


def http_backend(root: Path, path_info: str, method: str, query: str, content_type: str, body: bytes,
                 user: str) -> CgiResponse:
    """Run `git http-backend` for one request; `path_info` is /<slug>.git/<rest>."""
    env = {
        "GIT_PROJECT_ROOT": str(root), "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": path_info,
        "REQUEST_METHOD": method, "QUERY_STRING": query, "CONTENT_TYPE": content_type,
        "CONTENT_LENGTH": str(len(body)), "REMOTE_USER": user, "REMOTE_ADDR": "127.0.0.1",
        "GIT_HTTP_MAX_REQUEST_BUFFER": "1000M", "SERVER_PROTOCOL": "HTTP/1.1",
    }
    import os

    for name in ("PATH", "SYSTEMROOT", "HOME", "USERPROFILE", "TEMP", "TMP"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    exe = shutil.which("git") or "git"
    proc = subprocess.run([exe, "http-backend"], input=body, capture_output=True, env=env, timeout=3600)
    raw = proc.stdout
    head, sep, rest = raw.partition(b"\r\n\r\n")
    if not sep:
        head, sep, rest = raw.partition(b"\n\n")
    headers: dict[str, str] = {}
    status = 200
    for line in head.decode("latin-1").splitlines():
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        if name.strip().lower() == "status":
            status = int(value.strip().split(" ", 1)[0])
        else:
            headers[name.strip()] = value.strip()
    if proc.returncode != 0 and not raw:
        return CgiResponse(500, {"Content-Type": "text/plain"}, proc.stderr[-2000:] or b"git http-backend failed")
    return CgiResponse(status, headers, rest)
