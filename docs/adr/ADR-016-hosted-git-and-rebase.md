# ADR-016 — A shared repository on the control plane; rebase; conflicts resolved by an agent, then by you

- **Status:** Accepted (parts 1–3 of `docs/plan/LOCAL_REPO_TEAM.md`)

## Context
Working through GitHub kept the factory apart from the owner's local repositories: no flexible fetch, pull,
rebase or conflict handling as a team has, and every worker needed access to an external server. The owner chose:
the shared repository lives on the control plane, branches are rebased, an agent resolves conflicts and the owner
approves, GitHub stays an optional mirror (later).

## Decision
- **Git hosting.** The control plane keeps one bare repository per project under `AWF_GIT_ROOT` (default
  `var/git`) and serves it at `/git/<slug>.git` through `git http-backend` (no protocol code of our own). A project
  is hosted when `repository.url` is `awf-git:<slug>`; `POST /projects/{id}/git` creates it empty or as a copy of a
  URL or path. A run needs the base branch to exist ("push your code first").
- **Who writes what.** People authenticate with the git token (`AWF_GIT_TOKEN`, else a generated `.token` in the
  git root) or `AWF_API_TOKEN`, over HTTP Basic, and may update any branch. Workers authenticate with their own
  token as a bearer header and may update only `awf/*`. The ref updates are read from the receive-pack request
  before git sees the pack. The base branch accepts a worker push only with a merge ticket: a random value issued
  for the merge job after `approve_merge`, stored hashed in the run and valid while the run is running.
- **Worker access.** The worker turns `awf-git:<slug>` into `<control URL>/git/<slug>.git` and gives git its token
  (and the ticket) through `GIT_CONFIG_*` variables of the platform's git calls only: never a command line, a log
  or an engine's environment.
- **Rebase.** A writing task's branch is rebased onto the integration branch and fast-forwarded into it (a rejected
  push retries on the new tip). Delivery rebases the integration branch onto the latest base and builds and tests
  again when that changed anything. A new `merge` stage (`git.merge_to_base`, after `approve_merge`) fast-forwards
  the base branch of a hosted repository; for other repositories it only reports that the merge is the owner's.
- **Conflicts.** A rebase that conflicts is aborted and reported (kind `task` or `base`, source, target, files).
  The orchestrator adds a Developer task "Resolve conflict" in the same stage; its workspace holds the source
  squashed onto the target with the markers. The platform refuses a result with markers left, commits one commit,
  and pushes it (to the integration branch; for `base`, as the new integration branch with a lease). An agent task
  keeps its work and accepted outputs; a tool task (delivery, merge) waits for the resolver and runs again. When the
  resolver runs out of attempts, the run asks `resolve_conflict` with the files and the commands for the owner's
  PC; approve after pushing, reject to fail. More than 6 conflicts in a run fail it.
- **Backup.** `awf db backup` also writes a `git bundle` of every hosted repository.

## Consequences
- The control plane holds the main copy of the code and must be backed up; `var/git` is part of the backup.
- `awf/*` branches are rewritten by rebase; people should branch from them rather than commit on them while a run
  is active.
- Squashing a conflicted task into one commit loses that task's own commit boundaries (its branch keeps them).
- Mirroring to GitHub, read-only reference repositories and test screenshots (parts 4–6), branch cleanup and the
  develop/main flow: ADR-017.
