---
sdd: 1.1
doc: change-spec
change: "0001"
status: In review
profile: Standard
owner: ndev
updated: 2026-10-28
---

# Change 0001: SDD conformance checker (`awf sdd check`)

## Summary

A command that checks a repository against SDD Repository Standard v1.1 and generates the verification report of
a change from real evidence. It is the tool `standards/sdd/docs/CHECKER.md` describes, built inside AWF first and
extracted into the standard's own repository later (roadmap stage 5). This first version covers the checks
needed to run SDD on AWF itself and on a pilot repository.

## Problem and context

- **Problem:** The standard's rules exist only as prose. Nobody can tell whether a repository conforms, and a
  verification report written by hand (or by an agent) proves nothing.
- **Affected users / systems:** the owner reviewing changes; AWF's critic and QA stages (roadmap 3.2); CI of
  repositories that adopt the standard.
- **Why now:** every later stage of the roadmap (process modes, spec in the repository, SDD workflow) depends on
  it, and AWF's own changes from now on need it.
- **References:** `standards/sdd/docs/CHECKER.md`, `standards/sdd/docs/GRAMMAR.md`, `docs/plan/ROADMAP.md` item 1.2.

## Scope

### In scope
- Reading the SDD documents of a repository: profile, testing, living specs, change folders.
- Checks `SDD001`–`SDD003`, `SDD010`–`SDD013`, `SDD020`, `SDD022`, `SDD023`, `SDD030`, `SDD040`, `SDD041`,
  `SDD050`–`SDD053` of `CHECKER.md`.
- Finding test links in test code and attaching results from a JUnit XML report.
- Generating `verification.md` for a change.
- The `awf sdd check` command, text and JSON output, exit codes.

### Out of scope
- `SDD021` (vague wording), `SDD054` (hand-edit detection), `SDD060` (promotion), `SDD070` (commit trailers):
  a later change.
- Creating repositories or change folders (`awf sdd init`, `new-change`): change 0002.
- Using the checker inside AWF's workflow stages: roadmap 3.2.
- Publishing the checker as its own package: roadmap 5.3.

## Constraints and assumptions

- **Technical:** Python 3.12, standard library plus PyYAML (already a dependency); lives in `packages/awf/sdd/`
  with no import from the orchestrator, worker or API, so it can be extracted unchanged.
- **Compatibility:** reads exactly the grammar of `standards/sdd/docs/GRAMMAR.md` v1.1.
- **Security / privacy:** reads files only under the given root; writes only the verification file it is asked to
  generate; no network access.
- **Operational:** works the same on Windows and Linux (paths, line endings).
- **Assumptions to validate:** JUnit XML from pytest, `dotnet test --logger junit` and CTest share the
  `testsuite/testcase/failure|error|skipped` structure; validated with a sample of each in the tests.

## Added

### FR-SDD-001 — Read SDD documents and report grammar errors
- **Statement:** The checker MUST read every SDD document under `docs/sdd/` of the given root and report each
  grammar violation as a finding with code, severity, file and line.
- **Priority:** Must
- **Verification:** Automated
- **Rationale:** Every other check depends on reliable parsing; findings must point at the line to fix.
- **Depends on:** None
- **Acceptance criteria:**
  - `FR-SDD-001.1`: Given a repository missing `AGENTS.md`, `docs/sdd/project-profile.md` or
    `docs/sdd/testing.md`, when the checker runs, then it reports `SDD001` naming each missing file.
  - `FR-SDD-001.2`: Given a document without front matter, or with an unknown `doc` kind or status, when the
    checker runs, then it reports `SDD002` with the file and line 1.
  - `FR-SDD-001.3`: Given a field value that still contains a template placeholder (`<…>`, `YYYY-MM-DD`), when the
    checker runs, then it reports `SDD003` with the file and line of that field.
  - `FR-SDD-001.4`: Given the example repository in `standards/sdd/examples/minimal`, when the checker runs, then
    it reports no error.

### FR-SDD-002 — Check identifiers
- **Statement:** The checker MUST validate requirement, criterion, task and question IDs against the grammar, the
  area register and each other across the whole repository.
- **Priority:** Must
- **Verification:** Automated
- **Depends on:** `FR-SDD-001`
- **Acceptance criteria:**
  - `FR-SDD-002.1`: Given an ID that does not match the grammar or uses an area missing from the profile's area
    table, when the checker runs, then it reports `SDD010`.
  - `FR-SDD-002.2`: Given the same requirement ID defined in two places (living specs and open changes), when the
    checker runs, then it reports `SDD011` for the second definition, naming the first.
  - `FR-SDD-002.3`: Given a change that references a requirement ID defined neither in the change nor in a living
    spec, when the checker runs, then it reports `SDD012`.
  - `FR-SDD-002.4`: Given register allocation and a change that adds a requirement with no `Reserved` or `Active`
    row in its area's register, when the checker runs, then it reports `SDD013`.

### FR-SDD-003 — Check change specs
- **Statement:** The checker MUST verify that every added or modified requirement of a change is complete, and
  that no open question remains once the change spec is approved.
- **Priority:** Must
- **Verification:** Automated
- **Depends on:** `FR-SDD-001`
- **Acceptance criteria:**
  - `FR-SDD-003.1`: Given an added requirement without `Statement`, `Priority`, `Verification` or acceptance
    criteria (and no `Not verifiable: <reason>`), when the checker runs, then it reports `SDD020` naming the
    missing field.
  - `FR-SDD-003.2`: Given a change spec with status `Approved` or later and a question with status `Open`, when the
    checker runs, then it reports `SDD022`; with status `Draft` or `In review` it reports nothing for that question.
  - `FR-SDD-003.3`: Given a modified requirement without `Previous statement` or `Reason`, when the checker runs,
    then it reports `SDD023`.

### FR-SDD-004 — Check links from spec to design and tasks
- **Statement:** For Standard and Critical profiles, the checker MUST verify that every added or modified
  requirement is mapped in the design and served by a task, and that tasks are complete and acyclic.
- **Priority:** Must
- **Verification:** Automated
- **Depends on:** `FR-SDD-001`
- **Acceptance criteria:**
  - `FR-SDD-004.1`: Given an added requirement missing from the design's `Requirement mapping` table, when the
    checker runs on a Standard profile, then it reports `SDD030`; on a Lightweight profile it does not.
  - `FR-SDD-004.2`: Given an added requirement that no task lists (by its ID or one of its criterion IDs) and that
    is not marked `Deferred`, when the checker runs on a Standard profile, then it reports `SDD040`.
  - `FR-SDD-004.3`: Given a task without `Requirements`, `Depends on`, `Completion` or `Verification`, or with a
    dependency on a missing task or a dependency cycle, when the checker runs, then it reports `SDD041`.

### FR-SDD-005 — Link tests to acceptance criteria and attach results
- **Statement:** The checker MUST find the acceptance-criterion IDs carried by tests, using the conventions of
  standard section 9, and when given a JUnit XML report MUST derive a result for each criterion.
- **Priority:** Must
- **Verification:** Automated
- **Depends on:** `FR-SDD-002`
- **Acceptance criteria:**
  - `FR-SDD-005.1`: Given tests marked with `@pytest.mark.req(...)`, `[Trait("Req", ...)]`, a `REQ:` comment
    directly above the test, or the ID in brackets in a test title, when the checker scans the directories named
    in `testing.md`, then each criterion is linked to those tests.
  - `FR-SDD-005.2`: Given a criterion with `Verification: Automated` that no test links, when the checker runs,
    then it reports `SDD050`.
  - `FR-SDD-005.3`: Given a test that links a criterion ID that does not exist, when the checker runs, then it
    reports `SDD051`.
  - `FR-SDD-005.4`: Given a JUnit XML report, when the checker runs, then a criterion is `PASS` when all its linked
    tests passed, `FAIL` when any failed or errored, `NOT RUN` when none of them ran, and `BLOCKED` when no report
    was given.
  - `FR-SDD-005.5`: Given a `Must` criterion whose result is not `PASS` and a report was given, when the checker
    runs, then it reports `SDD052`.

### FR-SDD-006 — Generate the verification report
- **Statement:** With `--write-verification`, the checker MUST write `verification.md` of the checked change from
  the evidence, keeping the hand-written `Manual checks` section unchanged.
- **Priority:** Must
- **Verification:** Automated
- **Depends on:** `FR-SDD-005`
- **Acceptance criteria:**
  - `FR-SDD-006.1`: Given a change and a report, when the checker writes the verification, then the file has front
    matter with `generator`, `revision` and `generated`, and the sections `Gates`, `Checks`, `Criteria coverage`,
    `Manual checks`, `Unverified and residual risk` in the grammar of `GRAMMAR.md`.
  - `FR-SDD-006.2`: Given an existing `verification.md` with rows in `Manual checks`, when the checker regenerates
    it, then those rows are kept byte for byte.
  - `FR-SDD-006.3`: Given a manual check with result `PASS` and an empty `Evidence` cell, when the checker runs,
    then it reports `SDD053`.
  - `FR-SDD-006.4`: Given the same inputs and revision, when the checker writes the verification twice, then both
    files are identical apart from the `generated` timestamp.

### FR-SDD-007 — Command line
- **Statement:** The checker MUST be available as `awf sdd check` with the options of `CHECKER.md` and stable exit
  codes.
- **Priority:** Must
- **Verification:** Automated
- **Depends on:** `FR-SDD-001`
- **Acceptance criteria:**
  - `FR-SDD-007.1`: Given `--root`, `--change <NNNN>` or `--all-open-changes`, `--junit <path>`, `--revision <sha>`,
    `--write-verification` and `--json`, when the command runs, then each option has the effect `CHECKER.md`
    describes.
  - `FR-SDD-007.2`: Given findings with at least one error, when the command ends, then it exits 1; with warnings
    only or none, it exits 0; with an unreadable root or bad arguments, it exits 2.
  - `FR-SDD-007.3`: Given `--json`, when the command runs, then stdout is one JSON object with `findings` (code,
    severity, path, line, message) and `coverage` (the four signals of `TRACEABILITY.md`).
  - `FR-SDD-007.4`: Given an exception row in `project-profile.md` naming a check code, when that check fires,
    then the finding is reported as `waived` and does not count as an error.

### NFR-SDD-001 — Offline and confined
- **Statement:** The checker MUST NOT access the network and MUST NOT read or write outside the given root, except
  the JUnit report path given on the command line.
- **Priority:** Must
- **Verification:** Automated
- **Acceptance criteria:**
  - `NFR-SDD-001.1`: Given a run with network access blocked in the test, when the checker runs on the example
    repository, then it completes with the same result.
  - `NFR-SDD-001.2`: Given a change folder whose name tries to escape the root (`..`), when the checker writes the
    verification, then it refuses with exit code 2 and writes nothing.

### NFR-SDD-002 — Fast enough for every commit
- **Statement:** The checker MUST finish within 10 seconds on a repository with 50 change folders and 2,000 test
  files, on the owner's development machine.
- **Priority:** Should
- **Verification:** Automated
- **Rationale:** It runs in CI on every push and inside AWF runs.
- **Acceptance criteria:**
  - `NFR-SDD-002.1`: Given a generated repository of that size, when the checker runs, then it completes in under
    10 seconds.

## Modified

None

## Retired

None

## Open questions

### Q-0001-01 — Should AWF's own CI fail on checker errors from day one?
- **Question:** When the checker lands, should `.github/workflows/ci.yml` of this repository fail on SDD errors at
  once, or only report them for a first period while older code and docs catch up?
- **Impact:** Medium
- **Status:** Open
- **Answer:** Proposed: report only (non-blocking step) until change 0002 is released, then blocking.

### Q-0001-02 — Where does the checker live until the standard is extracted?
- **Question:** Inside AWF (`packages/awf/sdd/`), or in a separate repository from the start?
- **Impact:** Low
- **Status:** Answered
- **Answer:** Inside AWF, with no dependency on the rest of AWF, so that it can move unchanged to the standard's
  repository at roadmap 5.3 (discussed 2026-10-28).

## Change history

| Date | Change | Reason | Author / approver |
|---|---|---|---|
| 2026-10-28 | Initial draft | roadmap item 1.2 | Claude (spec role) |
