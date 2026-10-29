---
sdd: 1.1
doc: change-spec
change: "0002"
status: Verifying
profile: Standard
owner: platform team
updated: 2026-10-27
---

# Change 0002: Lock accounts after repeated failed sign-ins

## Summary

Lock an account for 15 minutes after 5 failed sign-ins within 10 minutes, so passwords cannot be guessed by
repeated attempts.

## Problem and context

- **Problem:** The sign-in endpoint accepts unlimited attempts.
- **Affected users / systems:** every internal user; the security team's audit.
- **Why now:** finding from the October security review.
- **References:** security review 2026-10, item 4.

## Scope

### In scope
- Counting failed sign-ins per account and locking the account.
- The response while an account is locked.

### Out of scope
- Rate limiting per IP address.
- Notifying the user by e-mail.

## Constraints and assumptions

- **Technical:** state kept in the existing PostgreSQL database; no new service.
- **Compatibility:** the sign-in response for a wrong password stays HTTP 401.
- **Security / privacy:** a locked account must not reveal whether the password was right.
- **Operational:** no migration downtime.
- **Assumptions to validate:** clocks of all instances are synchronised within 1 second (checked by NTP monitoring).

## Added

### FR-AUTH-003 — Lock the account after repeated failures
- **Statement:** The system MUST lock an account for 15 minutes after 5 failed sign-ins within 10 minutes.
- **Priority:** Must
- **Verification:** Automated
- **Rationale:** Limits password guessing to 5 attempts per 15 minutes per account.
- **Depends on:** `FR-AUTH-001`
- **Acceptance criteria:**
  - `FR-AUTH-003.1`: Given 4 failed sign-ins in the last 10 minutes, when a 5th fails, then the account is locked
    and HTTP 423 is returned.
  - `FR-AUTH-003.2`: Given a locked account, when the right password is sent, then HTTP 423 is returned and no
    token is issued.
  - `FR-AUTH-003.3`: Given an account locked 15 minutes ago, when the right password is sent, then a token is
    issued.

## Modified

None

## Retired

None

## Open questions

### Q-0002-01 — Status code while locked
- **Question:** Should a locked account answer 423 or keep 401 to reveal less?
- **Impact:** Medium
- **Status:** Answered
- **Answer:** 423, decided by the security team; the body does not say whether the password was right.

## Change history

| Date | Change | Reason | Author / approver |
|---|---|---|---|
| 2026-10-20 | Initial draft | security review item 4 | spec agent |
| 2026-10-21 | Q-0002-01 answered, approved (G1–G3) | review | product owner |
