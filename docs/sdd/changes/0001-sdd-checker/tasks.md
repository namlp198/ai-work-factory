---
sdd: 1.1
doc: tasks
change: "0001"
status: In review
owner: ndev
updated: 2026-10-28
---

# Tasks for change 0001: SDD conformance checker

## Approach

Bottom-up: grammar and loading first, because every check depends on them; then the checks; then test links and
results; then the generated verification and the command. Each task ships with its tests, marked with the
criterion IDs they prove. The last task checks this change with the checker itself and records the result.

## Tasks

### TASK-0001-01 — Parse the grammar and load the repository model
- **Status:** Todo
- **Requirements:** `FR-SDD-001.1`, `FR-SDD-001.2`, `FR-SDD-001.3`, `NFR-SDD-001.2`
- **Depends on:** None
- **Files:** `packages/awf/sdd/grammar.py`, `model.py`, `load.py`, `tests/unit/test_sdd_grammar.py`
- **Completion:** the example repository and AWF's own `docs/sdd` load into the model with every item, field and
  line number; malformed documents produce `SDD001`–`SDD003`.
- **Verification:** unit tests marked `FR-SDD-001.1`–`FR-SDD-001.3`, `NFR-SDD-001.2`

### TASK-0001-02 — Identifier checks
- **Status:** Todo
- **Requirements:** `FR-SDD-002.1`, `FR-SDD-002.2`, `FR-SDD-002.3`, `FR-SDD-002.4`
- **Depends on:** `TASK-0001-01`
- **Files:** `packages/awf/sdd/checks.py`, `tests/unit/test_sdd_checks.py`
- **Completion:** each of `SDD010`–`SDD013` fires on a crafted repository and not on the example.
- **Verification:** unit tests marked `FR-SDD-002.1`–`FR-SDD-002.4`

### TASK-0001-03 — Change spec, design and task checks
- **Status:** Todo
- **Requirements:** `FR-SDD-003.1`, `FR-SDD-003.2`, `FR-SDD-003.3`, `FR-SDD-004.1`, `FR-SDD-004.2`, `FR-SDD-004.3`
- **Depends on:** `TASK-0001-01`
- **Files:** `packages/awf/sdd/checks.py`, `tests/unit/test_sdd_checks.py`
- **Completion:** `SDD020`, `SDD022`, `SDD023`, `SDD030`, `SDD040`, `SDD041` fire on crafted repositories; profile
  rules respected (Lightweight skips `SDD030`/`SDD040`).
- **Verification:** unit tests marked `FR-SDD-003.1`–`FR-SDD-003.3`, `FR-SDD-004.1`–`FR-SDD-004.3`

### TASK-0001-04 — Test links and JUnit results
- **Status:** Todo
- **Requirements:** `FR-SDD-005.1`, `FR-SDD-005.2`, `FR-SDD-005.3`, `FR-SDD-005.4`, `FR-SDD-005.5`
- **Depends on:** `TASK-0001-02`
- **Files:** `packages/awf/sdd/testlinks.py`, `checks.py`, `tests/unit/test_sdd_testlinks.py`,
  `tests/fixtures/sdd/junit/` (pytest, dotnet and CTest samples)
- **Completion:** the four conventions are linked; results follow the PASS / FAIL / NOT RUN / BLOCKED rules;
  `SDD050`–`SDD052` fire.
- **Verification:** unit tests marked `FR-SDD-005.1`–`FR-SDD-005.5`

### TASK-0001-05 — Generate `verification.md`
- **Status:** Todo
- **Requirements:** `FR-SDD-006.1`, `FR-SDD-006.2`, `FR-SDD-006.3`, `FR-SDD-006.4`
- **Depends on:** `TASK-0001-04`
- **Files:** `packages/awf/sdd/verification.py`, `tests/unit/test_sdd_verification.py`
- **Completion:** the generated file parses with the grammar; manual rows survive regeneration; output is
  deterministic.
- **Verification:** unit tests marked `FR-SDD-006.1`–`FR-SDD-006.4`

### TASK-0001-06 — `awf sdd check` command, waivers, JSON
- **Status:** Todo
- **Requirements:** `FR-SDD-007.1`, `FR-SDD-007.2`, `FR-SDD-007.3`, `FR-SDD-007.4`, `FR-SDD-001.4`, `NFR-SDD-001.1`,
  `NFR-SDD-002.1`
- **Depends on:** `TASK-0001-05`
- **Files:** `packages/awf/cli.py`, `packages/awf/sdd/report.py`, `packages/awf/sdd/__init__.py`, `pyproject.toml`
  (`req` marker), `tests/integration/test_sdd_cli.py`
- **Completion:** the command runs on the example (no error) and on AWF itself; exit codes and JSON as specified;
  the size test passes.
- **Verification:** integration tests marked `FR-SDD-007.1`–`FR-SDD-007.4`, `FR-SDD-001.4`, `NFR-SDD-001.1`,
  `NFR-SDD-002.1`

### TASK-0001-07 — Documentation, CI step and self-check
- **Status:** Todo
- **Requirements:** none — documentation, CI wiring and the change's own verification
- **Depends on:** `TASK-0001-06`
- **Files:** `docs/HUONG_DAN_SU_DUNG.md` (new section), `.github/workflows/ci.yml` (step per `Q-0001-01`),
  `docs/sdd/changes/0001-sdd-checker/verification.md` (generated), `docs/plan/ROADMAP.md` (status)
- **Completion:** the guide explains `awf sdd check`; CI runs it; `awf sdd check --change 0001 --junit … --write-verification`
  reports no error for this change.
- **Verification:** the generated `verification.md` of this change

## Risks and fallback

| Risk | Impact | Mitigation / fallback | Owner |
|---|---|---|---|
| Regex test attribution picks the wrong test in unusual code layouts | a criterion linked to the wrong test | documented conventions require the marker directly above the test; `SDD050` still fires when nothing links | ndev |
| JUnit dialects differ more than assumed | results `NOT RUN` instead of `PASS` | samples of each dialect in the tests; unknown shapes fall back to `NOT RUN`, never `PASS` | ndev |
| The grammar proves too strict in practice | friction for authors | collect friction during this change and 0002; adjust in v1.1 before approval (roadmap 1.4) | ndev |
