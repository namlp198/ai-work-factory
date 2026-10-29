---
sdd: 1.1
doc: area-spec
area: AUTH
status: Current
owner: platform team
updated: 2026-10-27
---

# Living specification: Authentication (`AUTH`)

## Overview

Sign-in with e-mail and password for internal users, session tokens, and protection against password guessing.

## Requirements

### FR-AUTH-001 — Sign in with e-mail and password
- **Status:** Active
- **Statement:** The system MUST issue a session token when a user signs in with a registered e-mail and the
  matching password.
- **Priority:** Must
- **Verification:** Automated
- **Introduced by:** 0001
- **Acceptance criteria:**
  - `FR-AUTH-001.1`: Given a registered user, when they sign in with the right password, then a session token is
    returned.
  - `FR-AUTH-001.2`: Given a registered user, when they sign in with a wrong password, then HTTP 401 is returned
    and no token is issued.

### FR-AUTH-002 — Sessions expire
- **Status:** Active
- **Statement:** The system MUST reject a session token older than 12 hours.
- **Priority:** Must
- **Verification:** Automated
- **Introduced by:** 0001
- **Acceptance criteria:**
  - `FR-AUTH-002.1`: Given a token issued 12 hours and 1 minute ago, when it is used, then HTTP 401 is returned.

## Register

| ID | Status | Change |
|---|---|---|
| `FR-AUTH-001` | Active | 0001 |
| `FR-AUTH-002` | Active | 0001 |
| `FR-AUTH-003` | Reserved | 0002 |
