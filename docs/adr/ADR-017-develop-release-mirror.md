# ADR-017 — Runs on develop, Release to main, branch cleanup, a GitHub mirror, references and test files

- **Status:** Accepted (parts 4–6 of `docs/plan/LOCAL_REPO_TEAM.md`, plus the owner's choices of 29 October 2026)

## Context
After the first full run on the shared repository (ADR-016), the owner asked for four things:
- the many `awf/*` branches a run leaves should go once they are merged;
- work should happen on `develop`, and only work that is really finished should reach `main`;
- the code must not depend on the control plane alone: a mirror on GitHub;
- the rest of the plan: read-only references, and the screenshots and reports tests produce.

The owner chose:
- delete branches as soon as they are done (failed runs kept 14 days);
- a Release button on the dashboard;
- mirror main, develop and tags;
- a GitHub token in the control plane's `.env`.

## Decision

### Two branches
`repository.base_branch` is where runs work and merge (`develop`). `repository.release_branch` (`main`) changes
only on Release. Workers can never write it: the merge ticket covers the base branch only.

Hosting a repository (`POST /projects/{id}/git`, `develop: true` by default) sets `base_branch: develop` and
`release_branch: <the old base>`. `develop` is created from `main` when the copy has none, or at the first run
start (`githost.ensure_branch`).

### Release
`POST /projects/{id}/release` (`tag` optional) runs on the bare repository:
- a fast-forward when `main` is an ancestor of `develop`;
- else a merge commit built with `git merge-tree --write-tree` and `commit-tree`;
- a conflict changes nothing and returns the commands for the owner's PC.

The ref is updated with its old value (`update-ref <new> <old>`), so a concurrent push is never lost. An optional
annotated tag is added.

### Branch cleanup
Workers delete, on the remote, which works for every repository:
- a task branch right after it is integrated;
- a resolved conflict's source branch;
- after a hosted merge, the run branch and the run's remaining task branches.

For an external repository, the run branch stays, because it is the pull request's source.

The orchestrator also runs housekeeping every 2 minutes, and right after a run closes. For each hosted project
(`orchestrator/hosted.py`) it deletes:
- task branches of closed runs;
- run branches merged into the base branch;
- run branches of failed, cancelled or unmerged runs after `AWF_STALE_BRANCH_DAYS` (14).

Only branches of this project's own runs are touched.

### Mirror
`repository.mirror.url` (https or ssh; no credentials in the URL, which is validated). The control plane pushes
the release branch, the base branch and every tag:
- after a push to them over HTTP, as a background task once git has its answer;
- after Release;
- from housekeeping when what was last pushed differs; a failed push is retried every 5 minutes;
- on "Push now".

The push is never forced: a mirror with commits of its own is reported, not overwritten.

The token (`AWF_MIRROR_TOKEN`) reaches git only as `http.<mirror url>.extraHeader` in `GIT_CONFIG_*` variables of
that one call, with the credential helper off. The result (`pushed` refs, `at`, `ok_at`, `error`) is kept in
`repository.mirror`; a PATCH keeps it while the URL is unchanged.

### Worker credentials
The worker's bearer header is now scoped to `http.<control URL>/git/.extraHeader`, so it never reaches another
host (a reference on GitHub, an external main repository).

### References (part 4)
`repository.references: [{name, url, branch}]`. The URL can be another hosted project (`awf-git:<slug>`), empty for
this repository, or any git URL.

The worker clones each one shallow into `.awf-refs/<name>` and drops its `.git`. Hosted references are cloned
with the worker's token; others with the machine's own access. `.awf-refs/` is in `info/exclude`: never
committed.

`fs.write` and `fs.replace` refuse paths under `.awf-refs/`. The task context lists the references.

### Test files (part 5)
`repository.test_artifacts` (globs). After a job that ran tests, the worker collects the matching files written
during the job, within limits: 20 files, 2 MB each, 10 MB in total. Text files are redacted. The files travel
base64 in the result.

The orchestrator stores them as `test_screenshot` (images) or `test_report` artifacts, and keeps only their names
in the stored job result.

Images among a downstream role's inputs are listed in its context. They are fetched by the worker through
`GET /jobs/{id}/attachments/{artifact}`, which serves only the job's own attachments to the worker that holds
the job, into `.awf-artifacts/`, which is excluded from commits. The dashboard shows a run's screenshots as a
gallery.

## Consequences
- The control plane must reach GitHub for the mirror. When it cannot, the dashboard shows the error and the push
  is retried.
- Someone pushing straight to GitHub makes the mirror fail until that commit is brought into the shared
  repository.
- A run branch is gone once merged; its history lives on `develop`. Failed runs keep theirs for 14 days.
- The native runtime does not see images yet; engines (Claude Code, Codex…) open them from the workspace.
- Test files travel in the job result (up to about 13 MB of JSON). Larger outputs need a direct upload later.
