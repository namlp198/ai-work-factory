---
sdd: 1.1
doc: tasks
change: "0002"
status: Approved
owner: tech lead
updated: 2026-10-21
---

# Tasks for change 0002: Account lockout

## Approach

Migration and the lockout module first, then the endpoint change.

## Tasks

### TASK-0002-01 — Record failures and answer `is_locked`
- **Status:** Done
- **Requirements:** `FR-AUTH-003.1`, `FR-AUTH-003.3`
- **Depends on:** None
- **Files:** `auth/lockout.py`, `migrations/0007_sign_in_failures.py`
- **Completion:** `is_locked` is true after 5 failures in 10 minutes and false 15 minutes later.
- **Verification:** unit tests marked `FR-AUTH-003.1` and `FR-AUTH-003.3`

### TASK-0002-02 — Check the lock in the sign-in endpoint
- **Status:** Done
- **Requirements:** `FR-AUTH-003.1`, `FR-AUTH-003.2`
- **Depends on:** `TASK-0002-01`
- **Files:** `auth/routes.py`
- **Completion:** a locked account gets HTTP 423 with or without the right password.
- **Verification:** integration tests marked `FR-AUTH-003.1` and `FR-AUTH-003.2`

## Risks and fallback

| Risk | Impact | Mitigation / fallback | Owner |
|---|---|---|---|
| Lockout used against a known user | user waits 15 minutes | accepted; per-IP limiting is a later change | product owner |
