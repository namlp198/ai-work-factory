---
sdd: 1.1
doc: verification
change: "0002"
status: Passed
generator: example checker 0.1
revision: 3f2a9c1
generated: 2026-10-22T09:14:00Z
---

# Verification of change 0002

> **Generated file.** Every section except `Manual checks` is written from the test report, build/lint results
> and the test-link scan of revision `3f2a9c1`.

## Gates

| Gate | Status | Evidence / reason | Approved by |
|---|---|---|---|
| G1 Specification Ready | PASS | combined review, Q-0002-01 answered | product owner, 2026-10-21 |
| G2 Design Ready | PASS | combined review | tech lead, 2026-10-21 |
| G3 Plan Ready | PASS | combined review | tech lead, 2026-10-21 |
| G4 Implementation Complete | PASS | both tasks Done; code review approved | code reviewer, 2026-10-22 |
| G5 Verification Passed | PASS | every Must criterion PASS | checker |
| G6 Release Approved | BLOCKED | waiting for release approval | |

## Checks

| Check | Command | Result | Evidence |
|---|---|---|---|
| Build | `python -m compileall -q src` | PASS | CI run 1842, step build |
| Tests | `python -m pytest -q --junitxml=reports/junit.xml` | PASS | `reports/junit.xml`: 41 passed, 0 failed |
| Lint | `ruff check .` | PASS | CI run 1842, step lint |

## Criteria coverage

| Criterion | Priority | Tests | Result | Evidence |
|---|---|---|---|---|
| `FR-AUTH-003.1` | Must | `test_lockout.py::test_fifth_failure_locks`, `test_routes.py::test_locked_returns_423` | PASS | `reports/junit.xml` |
| `FR-AUTH-003.2` | Must | `test_routes.py::test_right_password_while_locked` | PASS | `reports/junit.xml` |
| `FR-AUTH-003.3` | Must | `test_lockout.py::test_unlocks_after_15_minutes` | PASS | `reports/junit.xml` |

## Manual checks

| Criterion | Procedure | Performed by | Date | Result | Evidence |
|---|---|---|---|---|---|

None

## Unverified and residual risk

- Lockout used against a known user: accepted risk (see tasks.md).
