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


def ensure_branch(path: Path, branch: str, start: str) -> bool:
    """Create `branch` at `start` when it does not exist yet (develop from main). True when it was created."""
    if branch_sha(path, branch):
        return False
    sha = branch_sha(path, start)
    if not sha:
        return False
    git(["update-ref", f"refs/heads/{branch}", sha, "0" * 40], path)
    return True


def delete_branches(path: Path, names: list[str]) -> list[str]:
    """Delete branches of a bare repository (only `awf/*`); returns the names deleted."""
    names = [n for n in dict.fromkeys(names) if n.startswith("awf/") and branch_sha(path, n)]
    for name in names:
        git(["update-ref", "-d", f"refs/heads/{name}"], path, check=False)
    return names


def is_ancestor(path: Path, ancestor: str, descendant: str) -> bool:
    return git(["merge-base", "--is-ancestor", ancestor, descendant], path, check=False).ok


# ---------------------------------------------------------------------------------------- release
@dataclass
class ReleasePlan:
    source: str
    target: str
    source_sha: str | None
    target_sha: str | None
    commits: list[str]
    fast_forward: bool

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def release_plan(path: Path, source: str, target: str) -> ReleasePlan:
    """What `release` would bring from `source` (develop) into `target` (main)."""
    s_sha, t_sha = branch_sha(path, source), branch_sha(path, target)
    commits: list[str] = []
    if s_sha:
        span = f"{t_sha}..{s_sha}" if t_sha else s_sha
        commits = git(["log", "--no-merges", "--pretty=format:%h %s", span], path, check=False).stdout.splitlines()
    ff = bool(s_sha) and (not t_sha or is_ancestor(path, t_sha, s_sha))
    return ReleasePlan(source, target, s_sha, t_sha, [c for c in commits if c], ff)


def release(path: Path, source: str, target: str, tag: str | None = None) -> dict:
    """Bring `source` into `target` on the control plane: a fast-forward, else a merge commit when the two merge
    without conflicts (git merge-tree). A conflict raises ReleaseConflict: resolve it on your PC."""
    plan = release_plan(path, source, target)
    if not plan.source_sha:
        raise GitHostError(f"branch '{source}' does not exist")
    if not plan.commits and plan.target_sha and is_ancestor(path, plan.source_sha, plan.target_sha):
        raise GitHostError(f"'{target}' already contains everything in '{source}'")
    old = plan.target_sha or "0" * 40
    if plan.fast_forward:
        new = plan.source_sha
    else:
        merged = git(["merge-tree", "--write-tree", "--name-only", plan.target_sha, plan.source_sha], path, check=False)
        lines = merged.stdout.splitlines()
        if merged.exit_code == 1:
            files = [ln for ln in lines[1:] if ln and not ln.startswith(("Auto-merging", "CONFLICT"))]
            raise ReleaseConflict(source, target, files)
        if not merged.ok:
            raise GitHostError(f"git merge-tree failed: {merged.stderr.strip()[:500]}")
        new = git(["commit-tree", lines[0], "-p", plan.target_sha, "-p", plan.source_sha,
                   "-m", f"Release {source} into {target}"], path).stdout.strip()
    git(["update-ref", f"refs/heads/{target}", new, old], path)
    if tag:
        git(["tag", "-a", tag, new, "-m", f"Release {tag}"], path)
    return {**plan.as_dict(), "released": new, "merge_commit": not plan.fast_forward, "tag": tag}


class ReleaseConflict(GitHostError):
    def __init__(self, source: str, target: str, files: list[str]):
        self.source, self.target, self.files = source, target, files
        super().__init__(f"'{source}' and '{target}' conflict in: {', '.join(files) or 'unknown files'}")


# ---------------------------------------------------------------------------------------- mirror
MIRROR_BRANCHES = ("main", "develop")


def mirror_refs(path: Path, branches: tuple[str, ...] | list[str]) -> dict[str, str]:
    """ref -> sha of what a mirror carries: the given branches (that exist) and every tag."""
    out = git(["for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags"], path,
              check=False).stdout
    wanted = {f"refs/heads/{b}" for b in branches}
    refs = {}
    for line in out.splitlines():
        ref, _, sha = line.partition(" ")
        if ref in wanted or ref.startswith("refs/tags/"):
            refs[ref] = sha
    return refs


def mirror_env(url: str, token: str | None) -> dict[str, str]:
    """git config for one push to `url`: the token as an HTTP header for that URL only (never in the URL, a
    command line or a log), and no credential helper or prompt."""
    env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": ""}
    if token and url.startswith("https://"):
        import base64

        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update({"GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_1": f"http.{url}.extraHeader",
                    "GIT_CONFIG_VALUE_1": f"Authorization: Basic {basic}"})
    return env


def mirror_push(path: Path, url: str, token: str | None, branches=MIRROR_BRANCHES) -> dict:
    """Push the mirrored branches and the tags to `url`, never forced: if the mirror has commits the control
    plane does not, the push is refused and reported (someone pushed to GitHub directly)."""
    refs = mirror_refs(path, branches)
    if not refs:
        return {"ok": True, "refs": {}, "message": "nothing to mirror yet"}
    result = git(["push", "--porcelain", url, *[f"{r}:{r}" for r in sorted(refs)]], path, check=False,
                 timeout=1800, env=mirror_env(url, token))
    message = (result.stderr.strip() or result.stdout.strip())[-1500:]
    return {"ok": result.ok, "refs": refs, "message": "" if result.ok else message}


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
