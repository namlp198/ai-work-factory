"""Ephemeral per-job workspace (ADR-007, Design §12).

    Create workspace -> git clone -> checkout task branch -> run agent tools -> build/test
    -> collect artifacts/logs -> commit/push if allowed -> cleanup

Branch model per workflow run:
    <base>                      e.g. main (never written)
    awf/<run>                   integration branch for the run
    awf/tasks/<run>/<task>      one branch per writing task, merged --no-ff into the integration branch
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from awf.tool_sdk.command import GitError, git
from awf.tool_sdk.git_tools import diff_stats

JUNK_PATTERNS = ["__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/", "*.egg-info/",
                 "bin/", "obj/", "TestResults/", "*.user", ".vs/",
                 ".venv/", "node_modules/"]


class IntegrationConflict(RuntimeError):
    pass


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
    def __init__(self, root: Path, job_id: str):
        self.root = root / job_id
        self.repo = self.root / "repo"
        self.base_branch = "main"
        self.integration_branch: str | None = None
        self.task_branch: str | None = None
        self.start_sha: str | None = None

    @property
    def base_ref(self) -> str:
        return f"origin/{self.base_branch}"

    def prepare(self, repo_url: str, base_branch: str, integration_branch: str | None,
                task_branch: str | None) -> None:
        if self.root.exists():
            self.cleanup()
        self.root.mkdir(parents=True, exist_ok=True)
        self.base_branch = base_branch
        git(["clone", "--no-tags", "--quiet", repo_url, str(self.repo)], self.root, timeout=900)
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
        """Commit workspace changes on the task branch and merge them into the integration branch."""
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
        git(["push", "--quiet", "origin", f"{self.task_branch}:{self.task_branch}", "--force"], self.repo)
        if self.integration_branch:
            self._integrate(message)
            info.integrated = True
        return info

    def _integrate(self, message: str, attempts: int = 3) -> None:
        for attempt in range(1, attempts + 1):
            git(["fetch", "--quiet", "origin"], self.repo)
            start = (f"origin/{self.integration_branch}" if self._remote_branch_exists(self.integration_branch)
                     else self.start_sha)
            git(["checkout", "--quiet", "-B", self.integration_branch, start], self.repo)
            merge = git(["merge", "--no-ff", "--quiet", "-m", f"Merge {self.task_branch}: {message.splitlines()[0]}",
                         self.task_branch], self.repo, check=False)
            if not merge.ok:
                git(["merge", "--abort"], self.repo, check=False)
                raise IntegrationConflict(
                    f"merge of {self.task_branch} into {self.integration_branch} conflicts: {merge.stdout[-1500:]}")
            push = git(["push", "--quiet", "origin", f"{self.integration_branch}:{self.integration_branch}"],
                       self.repo, check=False)
            if push.ok:
                return
            if attempt == attempts:
                raise GitError(f"push of {self.integration_branch} rejected: {push.stderr[-1000:]}")

    def export_patch(self) -> dict:
        target = self.integration_branch or "HEAD"
        ref = f"origin/{target}" if self.integration_branch and self._remote_branch_exists(target) else "HEAD"
        diff = git(["diff", f"{self.base_ref}...{ref}"], self.repo).stdout
        stats = diff_stats(git(["diff", "--numstat", f"{self.base_ref}...{ref}"], self.repo).stdout)
        commits = git(["log", "--no-merges", "--pretty=format:%h %s", f"{self.base_ref}..{ref}"],
                      self.repo).stdout.splitlines()
        return {"diff": diff, "commits": commits, "head": ref, **stats}

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
