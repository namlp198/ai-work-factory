---
sdd: 1.1
doc: verification
change: "<NNNN>"
status: Draft
generator: <tool and version>
revision: <commit SHA verified>
generated: <timestamp>
---

# Verification of change <NNNN>

> **Generated file.** The conformance checker or the orchestrator writes every section except `Manual checks`
> from the test report, build/lint results and the test-link scan of the revision above. Do not edit generated
> sections: the checker regenerates them and reports hand edits (`SDD054`).

## Gates

| Gate | Status | Evidence / reason | Approved by |
|---|---|---|---|
| G1 Specification Ready | <PASS / FAIL / BLOCKED / NOT REQUIRED> | <evidence> | <who, when> |
| G2 Design Ready | | | |
| G3 Plan Ready | | | |
| G4 Implementation Complete | | | |
| G5 Verification Passed | | | |
| G6 Release Approved | | | |

## Checks

| Check | Command | Result | Evidence |
|---|---|---|---|
| Build | `<command>` | <PASS / FAIL / NOT RUN> | <log or CI link> |
| Tests | `<command>` | <PASS / FAIL / NOT RUN> | <report path, counts> |
| Lint | `<command>` | <PASS / FAIL / NOT RUN> | <log> |

## Criteria coverage

| Criterion | Priority | Tests | Result | Evidence |
|---|---|---|---|---|
| `FR-<AREA>-<NNN>.1` | Must | `<test ids>` | <PASS / FAIL / NOT RUN / BLOCKED> | <report> |

## Manual checks

Written by the person who performed the check. A `PASS` without evidence is an error.

| Criterion | Procedure | Performed by | Date | Result | Evidence |
|---|---|---|---|---|---|

## Unverified and residual risk

<Generated list of criteria not covered, plus notes from the verifier.>
