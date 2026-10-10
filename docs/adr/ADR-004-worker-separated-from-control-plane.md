# ADR-004 — Worker separated from control plane

- **Status:** Accepted (Phase 0)

## Decision
Execution happens on workers that register with a platform and capability list, heartbeat, and claim jobs
over HTTP (`/api/v1/workers/*`, `/api/v1/jobs/*`). Jobs carry `requirements` (platform prefix + capabilities);
the claim is an atomic `UPDATE ... WHERE state='QUEUED'`. The agent loop itself runs on the worker, next to
the sandbox; the control plane never touches repositories.

## Consequences
- Windows (.NET/WPF), Linux, Jetson and GPU workers run the same `awf.worker_sdk`.
- Stale heartbeats mark a worker offline and requeue its jobs.
- Model credentials live on workers, not in the control plane database.
