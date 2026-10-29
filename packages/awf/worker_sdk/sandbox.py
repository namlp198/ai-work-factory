"""Ephemeral per-job workspace (ADR-007, Design §12).

    Create workspace -> git clone -> checkout task branch -> run agent tools -> build/test
    -> collect artifacts/logs -> commit/push if allowed -> cleanup

Branch model per workflow run (ADR-016: rebase, like a team keeping a linear history):
    <base>                      e.g. main; written only by the merge job after a human approved approve_merge
    awf/run-<run>               integration branch for the run, rebased onto the latest base before delivery
    awf/tasks/<run>/<task>      one branch per writing task, rebased onto the integration branch and
                                fast-forwarded into it
A rebase that conflicts is aborted and reported (IntegrationConflict with source, target and files); the
orchestrator then starts a "resolve conflict" task, whose workspace holds the conflicts as one squashed merge.
"""

from __future__ import annotations

import os
import random
import re
import shutil
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from awf.tool_sdk.command import GitError, git
from awf.tool_sdk.git_tools import diff_stats

JUNK_PATTERNS = ["__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", "*.egg-info/",
                 "bin/", "obj/", "TestResults/", "*.user", ".vs/",
                 ".venv/", "node_modules/"]


class IntegrationConflict(RuntimeError):
    """A rebase or merge that git could not do alone. `kind` is "task" (a task branch onto the integration branch)
    or "base" (the integration branch onto the base branch)."""

    def __init__(self, message: str, kind: str = "task", source: str = "", target: str = "",
                 files: list[str] | None = None):
        super().__init__(message)
        self.kind, self.source, self.target, self.files = kind, source, target, files or []

    def as_dict(self) -> dict:
        return {"kind": self.kind, "source": self.source, "target": self.target, "files": self.files}


CONFLICT_MARKER = re.compile(r"^(<{7}|={7}|>{7})( |$)", re.MULTILINE)


@dataclass
class CommitInfo:
    has_changes: bool
    sha: str | None = None
    task_branch: str | None = None
    integration_branch: str | None = None
    integrated: bool = False
    diff: str = ""
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "diff"}


class Workspace:
    def __init__(self, root: Path, job_id: str, git_env: dict[str, str] | None = None):
        self.root = root / job_id
        self.repo = self.root / "repo"
        self.base_branch = "main"
        self.integration_branch: str | None = None
        self.task_branch: str | None = None
        self.start_sha: str | None = None
        self.is_git = False
        self.git_env = git_env or {}  # credentials for a hosted repository (http.extraHeader), never logged
        self.resolve: dict | None = None  # set by start_resolve()

    def _git(self, args: list[str], check: bool = True, timeout: int = 300):
        return git(args, self.repo, timeout=timeout, check=check, env=self.git_env)

    @property
    def base_ref(self) -> str:
        return f"origin/{self.base_branch}"

    def prepare(self, repo_url: str, base_branch: str, integration_branch: str | None,
                task_branch: str | None) -> None:
        if self.root.exists():
            self.cleanup()
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_branch = base_branch
        git(["clone", "--no-tags", "--quiet", repo_url, str(self.repo)], self.root, timeout=900, env=self.git_env)
        self.is_git = True
        exclude = self.repo / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open("a", encoding="utf-8") as fh:
            fh.write("\n# ai-work-factory\n" + "\n".join(JUNK_PATTERNS) + "\n")

        start_point = self.base_ref
        if integration_branch:
            self.integration_branch = integration_branch
            if self._remote_branch_exists(integration_branch):
                start_point = f"origin/{integration_branch}"
            git(["checkout", "--quiet", "-B", integration_branch, start_point], self.repo)
        else:
            git(["checkout", "--quiet", "-B", base_branch, start_point], self.repo)
        if task_branch:
            self.task_branch = task_branch
            git(["checkout", "--quiet", "-B", task_branch], self.repo)
        self.start_sha = git(["rev-parse", "HEAD"], self.repo).stdout.strip()

    def _remote_branch_exists(self, branch: str) -> bool:
        result = git(["rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"], self.repo, check=False)
        return result.ok

    def commit_and_integrate(self, message: str, push: bool = True) -> CommitInfo:
        """Commit workspace changes on the task branch and rebase them onto the integration branch."""
        if self.resolve:
            return self._finish_resolve(message)
        git(["add", "-A"], self.repo)
        staged = git(["diff", "--cached", "--quiet"], self.repo, check=False)
        if staged.exit_code == 0:
            return CommitInfo(has_changes=False, task_branch=self.task_branch,
                              integration_branch=self.integration_branch)
        git(["commit", "--quiet", "-m", message], self.repo)
        sha = git(["rev-parse", "HEAD"], self.repo).stdout.strip()
        diff = git(["diff", self.start_sha or self.base_ref, sha], self.repo).stdout
        stats = diff_stats(git(["diff", "--numstat", self.start_sha or self.base_ref, sha], self.repo).stdout)
        info = CommitInfo(has_changes=True, sha=sha, task_branch=self.task_branch,
                          integration_branch=self.integration_branch, diff=diff, **stats)
        if not push:
            return info
        self._git(["push", "--quiet", "origin", f"{self.task_branch}:{self.task_branch}", "--force"])
        if self.integration_branch:
            info.sha = self._integrate()
            info.integrated = True
        return info

    def _integrate(self, attempts: int = 5) -> str:
        """Rebase the task branch onto the integration branch and fast-forward it there. A rejected push (another
        task of the run got in first) is retried on the new tip; a conflict is aborted and reported."""
        for attempt in range(1, attempts + 1):
            if attempt > 1:
                time.sleep(random.uniform(0.2, 1.0) * attempt)
            self._git(["fetch", "--quiet", "origin"])
            onto = (f"origin/{self.integration_branch}" if self._remote_branch_exists(self.integration_branch)
                    else self.start_sha)
            self._rebase(onto, kind="task", source=self.task_branch, target=self.integration_branch)
            sha = self._git(["rev-parse", "HEAD"]).stdout.strip()
            push = self._git(["push", "--quiet", "origin", f"HEAD:refs/heads/{self.integration_branch}"], check=False)
            if push.ok:
                self._git(["push", "--quiet", "--force", "origin", f"HEAD:refs/heads/{self.task_branch}"], check=False)
                return sha
            if attempt == attempts:
                raise GitError(f"push of {self.integration_branch} rejected: {push.stderr[-1000:]}")
        raise GitError("integration failed")

    def _rebase(self, onto: str, *, kind: str, source: str, target: str) -> None:
        result = self._git(["rebase", "--quiet", onto], check=False, timeout=900)
        if result.ok:
            return
        files = [f for f in self._git(["diff", "--name-only", "--diff-filter=U"], check=False).stdout.splitlines() if f]
        self._git(["rebase", "--abort"], check=False)
        raise IntegrationConflict(f"rebasing {source} onto {target} conflicts in: {', '.join(files) or 'unknown files'}",
                                  kind=kind, source=source, target=target, files=files)

    # ------------------------------------------------------------------ base branch
    def sync_with_base(self) -> dict:
        """Rebase the integration branch onto the latest base branch (someone pushed to main during the run).
        Returns {"updated": bool, "base": sha}; conflicts raise IntegrationConflict(kind="base")."""
        self._git(["fetch", "--quiet", "origin"])
        branch = self.integration_branch
        base_sha = self._git(["rev-parse", self.base_ref]).stdout.strip()
        if not branch or not self._remote_branch_exists(branch):
            return {"updated": False, "base": base_sha}
        if self._git(["merge-base", "--is-ancestor", self.base_ref, f"origin/{branch}"], check=False).ok:
            self._git(["checkout", "--quiet", "-B", branch, f"origin/{branch}"])
            return {"updated": False, "base": base_sha}
        old = self._git(["rev-parse", f"origin/{branch}"]).stdout.strip()
        self._git(["checkout", "--quiet", "-B", branch, f"origin/{branch}"])
        self._rebase(self.base_ref, kind="base", source=branch, target=self.base_branch)
        self._git(["push", "--quiet", f"--force-with-lease=refs/heads/{branch}:{old}", "origin",
                   f"HEAD:refs/heads/{branch}"])
        return {"updated": True, "base": base_sha}

    def merge_to_base(self) -> dict:
        """Fast-forward the base branch to the integration branch (after approve_merge). Never a merge commit and
        never a force push: if the base moved, the integration branch is rebased first."""
        for attempt in range(1, 4):
            synced = self.sync_with_base()
            head = self._git(["rev-parse", "HEAD"]).stdout.strip()
            push = self._git(["push", "--quiet", "origin", f"{head}:refs/heads/{self.base_branch}"], check=False)
            if push.ok:
                return {"merged": head, "rebased": synced["updated"], "base_before": synced["base"]}
            if attempt == 3:
                raise GitError(f"push of {self.base_branch} rejected: {push.stderr[-1000:]}")
        raise GitError("merge failed")

    # ------------------------------------------------------------------ conflict resolution
    def start_resolve(self, kind: str, source: str, target: str) -> list[str]:
        """Put the conflicts of `source` onto `target` in the working tree, as one squashed merge, for an agent
        to resolve. Returns the conflicted files."""
        self._git(["fetch", "--quiet", "origin"])
        self.resolve = {"kind": kind, "source": source, "target": target,
                        "lease": self._git(["rev-parse", f"origin/{self.integration_branch}"], check=False).stdout.strip()}
        self._git(["checkout", "--quiet", "-B", "awf-resolve", f"origin/{target}"])
        self.start_sha = self._git(["rev-parse", "HEAD"]).stdout.strip()
        self._git(["merge", "--squash", "--no-commit", f"origin/{source}"], check=False)
        files = [f for f in self._git(["diff", "--name-only", "--diff-filter=U"], check=False).stdout.splitlines() if f]
        self.resolve["files"] = files
        return files

    def _finish_resolve(self, message: str) -> CommitInfo:
        res = self.resolve or {}
        left = [f for f in res.get("files", []) if (self.repo / f).is_file()
                and CONFLICT_MARKER.search((self.repo / f).read_text(encoding="utf-8", errors="replace"))]
        if left:
            raise GitError(f"conflict markers are still in: {', '.join(left)}")
        git(["add", "-A"], self.repo)
        git(["commit", "--quiet", "-m", message], self.repo)
        sha = git(["rev-parse", "HEAD"], self.repo).stdout.strip()
        diff = git(["diff", self.start_sha, sha], self.repo).stdout
        stats = diff_stats(git(["diff", "--numstat", self.start_sha, sha], self.repo).stdout)
        branch = self.integration_branch
        if res.get("kind") == "base":  # the run's work on top of the latest base: replaces the integration branch
            self._git(["push", "--quiet", f"--force-with-lease=refs/heads/{branch}:{res.get('lease', '')}", "origin",
                       f"HEAD:refs/heads/{branch}"])
        else:
            push = self._git(["push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"], check=False)
            if not push.ok:
                raise GitError(f"{branch} moved while the conflict was being resolved; trying again "
                               f"({push.stderr.strip()[-300:]})")
        return CommitInfo(has_changes=True, sha=sha, task_branch=None, integration_branch=branch, integrated=True,
                          diff=diff, **stats)

    def export_patch(self) -> dict:
        target = self.integration_branch or "HEAD"
        ref = f"origin/{target}" if self.integration_branch and self._remote_branch_exists(target) else "HEAD"
        diff = git(["diff", f"{self.base_ref}...{ref}"], self.repo).stdout
        stats = diff_stats(git(["diff", "--numstat", f"{self.base_ref}...{ref}"], self.repo).stdout)
        commits = git(["log", "--no-merges", "--pretty=format:%h %s", f"{self.base_ref}..{ref}"],
                      self.repo).stdout.splitlines()
        return {"diff": diff, "commits": commits, "head": ref, **stats}

    def prepare_scratch(self) -> None:
        """Empty working directory for agents that need no repository (content, research)."""
        if self.root.exists():
            self.cleanup()
        self.repo.mkdir(parents=True)

    def cleanup(self) -> None:
        if self.root.exists():
            if sys.version_info >= (3, 12):
                shutil.rmtree(self.root, onexc=_force_remove)
            else:
                shutil.rmtree(self.root, onerror=_force_remove)


def _force_remove(func, path, _exc) -> None:
    # Git object files are read-only on Windows.
    os.chmod(path, stat.S_IWRITE)
    func(path)
