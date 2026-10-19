# ADR-008 — Run placement: one machine per workflow run

- **Status:** Accepted
- **Supersedes:** the per-job routing of ADR-004 as the default (it remains available as `placement: stage`)

## Context
Jobs were routed one at a time: any worker meeting a job's requirements could claim it, so one software run
could do its architecture on machine A, development on B and review on C. The factory is operated as "a
machine takes a task and carries it through the whole process", and a machine must never get work it cannot
do (an Ubuntu box with a .NET SDK still cannot build a Windows-only robo-station project).

## Decision
- A workflow run is **placed on one worker**. `workflow_runs.assigned_worker_id` holds it; every job of the
  run is created `pinned` with `jobs.assigned_worker_id`, and only that worker can claim it.
- The run's **requirements** are computed once at `start_run`: the project's `labels.platform` and
  `repository.required_capabilities` merged with the `worker:` of **every** stage. The chosen machine must
  satisfy the whole run, not just its first step. Platforms merge by prefix (`linux` + `linux-arm64` →
  `linux-arm64`); incompatible platforms refuse the run.
- A run no registered (non-revoked) worker matches is **refused at creation**. With no worker registered at
  all, it is accepted and waits.
- The orchestrator **chooses** the machine at the run's first dispatch: online, fresh heartbeat, not draining,
  meets the requirements; then lowest `(queued + running jobs) / max_jobs`, fewest held runs, label
  `preferred_for` matching the project slug or workflow id, name. Runs do not wait for a free slot: a busy
  machine queues the job. With no candidate the run stays `RUNNING`, its tasks `READY`, no job is created,
  and `context_json.placement.wait` says why.
- A user may **pin** a run to a machine (`placement.worker` at creation) or **move** it
  (`POST /workflow-runs/{id}/reassign`, refused while one of its jobs is executing).
- **Failover**: a run leaves its machine (`RUN_UNASSIGNED`, then placement again in the same tick) when the
  machine is revoked or deleted, no longer meets the requirements, or has been offline for
  `run_reassign_after_seconds` (600 s) *while the run has work queued*. A run waiting on a human approval
  keeps its machine. A restart (re-registration with the same `machine_id`) keeps the run.
- A draining worker keeps executing the runs it holds and gets no new ones.
- Moving a run is safe because a run's state is never on the machine: code is on the integration branch,
  pushed to origin after every writing task, and artifacts are on the control plane. Uncommitted work of a
  job lost mid-flight is redone, exactly as for an expired lease.
- `placement: stage` in a workflow keeps the previous behaviour (per-job routing, no pinning).

## Consequences
- Stages that used to run in parallel on several machines (planned development tasks, parallel verification)
  run one after the other on a worker with one job slot. `workers.max_jobs` is in place for workers that
  execute several jobs concurrently.
- Runs created before this ADR (no `placement` in their context) keep per-job routing until they finish.
- Runs for workflows that need two operating systems must use `placement: stage`.
