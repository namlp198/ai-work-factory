# ADR-007 — Ephemeral sandbox per coding task

- **Status:** Accepted (Phase 0)

## Decision
Every job gets a fresh clone under `<workspace_root>/<job_id>/repo`, removed after the job. Writing tasks work
on `awf/tasks/<run>/<task>-i<iteration>-a<attempt>` and are merged `--no-ff` into the run's integration branch
`awf/run-<run>`, which is pushed to the repository's origin. The base branch is never written.

## Consequences
- No two agents share a working tree; parallel tasks integrate through git (conflicts fail the task).
- Build outputs (`bin/`, `obj/`, `__pycache__/`, ...) are excluded through `.git/info/exclude`.
- Container isolation per job is a Phase 2 hardening step (Design §12).
