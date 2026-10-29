---
sdd: 1.1
doc: design
change: "0002"
status: Approved
owner: tech lead
updated: 2026-10-21
---

# Design for change 0002: Account lockout

## Summary

Count failures in a new `sign_in_failures` table and check the lock before the password is verified, so a locked
account answers the same way whatever the password.

## Requirement mapping

| Requirement | Design elements | Notes |
|---|---|---|
| `FR-AUTH-003` | `auth/lockout.py` (`is_locked`, `record_failure`), table `sign_in_failures`, check in `auth/routes.py::sign_in` | lock checked before password verification |

## Components and responsibilities

- `auth/lockout.py`: records failures and answers whether an account is locked.
- `auth/routes.py::sign_in`: calls `is_locked` first, then verifies the password, then `record_failure` on failure.

## Interfaces and contracts

`POST /sign-in` gains one response: HTTP 423 `{"error": "account_locked", "retry_after": <seconds>}`. Other
responses are unchanged.

## Data model and persistence

- **Entities / schemas:** `sign_in_failures(account_id, failed_at)`, index on `(account_id, failed_at)`.
- **Invariants:** rows older than 24 hours are irrelevant and deleted nightly.
- **Migration / rollback:** additive migration; rollback drops the table.
- **Retention / deletion:** 24 hours.
- **Data sensitivity:** internal account IDs only.

## Runtime behaviour and failures

- **Normal path:** check lock → verify password → on failure record it.
- **Expected failures:** database unavailable → sign-in fails with HTTP 503, as today.
- **Timeouts / retries:** Not applicable: single local query.
- **Recovery / restart:** state is in the database; restarts do not reset locks.
- **Observability:** counter `auth_lockouts_total`.

## Security, privacy and safety

- **Trust boundaries:** unchanged.
- **Secrets / credentials:** Not applicable: no new secret.
- **Permissions:** unchanged.
- **Threats / mitigations:** password guessing (mitigated); lockout as denial of service against a known user
  (accepted, out of scope: per-IP limiting).
- **Hazards and safe state:** Not applicable: no safety function.

## Performance and resource budgets

Not applicable: one indexed query per sign-in, no budget in scope.

## Deployment, compatibility and rollback

- **Configuration changes:** None
- **Upgrade path:** run the migration before deploying.
- **Backward compatibility:** clients that do not know HTTP 423 treat it as a failed sign-in.
- **Rollback:** redeploy the previous image; the table is ignored.

## Alternatives

| Option | Benefits | Costs / risks | Decision |
|---|---|---|---|
| Counter column on the account row | one table | concurrent writes on a hot row | Rejected |
| Separate failures table | simple time window, no hot row | one more table | Selected |

## Decisions

None
