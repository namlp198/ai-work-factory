# Quality gates

The gates and the approvals per profile are defined in the standard (section 11). This page lists the exit
criteria in checklist form and says which ones a tool checks and which need a person.

`[tool]` means the checker decides it; `[review]` means a person (or an independent reviewing agent, where the
profile allows) decides it.

## G1 — Specification Ready

- [tool] Front matter and grammar valid; every added/modified requirement has `Statement`, `Priority`,
  `Verification` and acceptance criteria with IDs (or `Not verifiable: <reason>`).
- [tool] IDs unique across the repository; new IDs reserved per the allocation method in the profile.
- [tool] No open question with status `Open`.
- [review] Problem, goals, non-goals and scope are explicit; criteria are observable, not vague.
- [review] Owner approval recorded (Standard: may be the combined G1–G3 review).

## G2 — Design Ready

- [tool] Every added/modified requirement appears in `Requirement mapping`.
- [tool] Sections that do not apply say `Not applicable: <reason>`.
- [review] Interfaces, data, error behaviour and compatibility are defined well enough to implement.
- [review] Security, privacy, safety, performance, deployment and rollback are addressed in proportion.
- [review] Consequential choices have an ADR.

## G3 — Plan Ready

- [tool] Every added/modified requirement is served by a task or marked `Deferred` with a reason.
- [tool] Every task has `Requirements`, `Depends on`, `Completion` and `Verification`; dependencies have no cycle
  and point to existing tasks.
- [review] Tasks are small enough to review; testing, documentation, migration and release work is included.

## G4 — Implementation Complete

- [tool] Every task is `Done`, `Blocked` or `Deferred`, with a reason for the last two.
- [tool] Every automated criterion is linked from at least one test.
- [review] Code review requirements of the profile are met; deviations are recorded.

## G5 — Verification Passed

- [tool] `verification.md` was generated from the verified revision.
- [tool] Every `Must` criterion is `PASS`, or covered by a recorded exception.
- [tool] Every manual check marked `PASS` has evidence.
- [review] Failures and skipped tests have a reason, an impact, an owner and a next step.

## G6 — Release Approved

- [tool] G1–G5 are `PASS` or `NOT REQUIRED` with a reason.
- [tool] The change is promoted into the living specs; register rows updated; folder status `Archived`.
- [review] Known issues and residual risks in `completion.md` are accepted by the release owner.
- [review] Critical profile: release checklist, rollback plan and required bench/HIL/field results present.

## Re-opening a gate

Re-open the affected gates when acceptance criteria or scope change, when a design, interface or data model
changes materially, when the implementation deviates from approved behaviour, when a linked test is changed,
removed or starts failing, or when the release artifact differs from the verified revision.
