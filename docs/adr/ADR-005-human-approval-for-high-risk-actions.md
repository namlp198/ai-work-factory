# ADR-005 — Human approval for high-risk actions

- **Status:** Accepted (Phase 0)

## Decision
Approval is a first-class resource (`approvals` table, `/api/v1/approvals`). Workflow `gate` stages and
contracts with `human_approval: true` stop the task in `APPROVAL_REQUIRED` until a human approves or rejects.
Policy level 2 actions (protected merge, production deploy) are never executed autonomously; level 3 is manual.

MVP gates: architecture approval and merge approval. Production release approval arrives with the release
workflow (Phase 2).

## Consequences
Rejecting an agent task re-runs it with the reviewer's comment as feedback (while attempts remain);
rejecting a gate-only task fails the run.
