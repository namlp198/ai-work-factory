---
sdd: 1.1
doc: testing
status: In review
owner: ndev
updated: 2026-10-28
---

# Test strategy

## Objectives

Prove that the platform executes workflows correctly end to end: state survives restarts, gates and critic
decisions follow the evidence, workers run only what policy allows, and secrets never leave a worker.

## Levels

| Level | Scope | Tool / environment | Required | Pass criteria |
|---|---|---|---|---|
| Unit | domain, workflow engine, policy, router, critic, redaction, SDD tooling | pytest | yes | all pass |
| Integration | API, worker protocol, adapters against fake providers, engines against fake CLIs | pytest, FastAPI TestClient, httpx MockTransport | yes | all pass |
| System / acceptance | whole workflows on a real git repository with the scripted provider | pytest (`tests/e2e`) | yes | all pass |
| Regression | the full suite | pytest | yes | all pass |
| Security | secret redaction, policy denial, path traversal | pytest | yes | all pass |
| Performance | Not applicable: no performance requirement in force | — | no — none in force | — |
| HIL / bench / field | Not applicable: no hardware | — | no — no hardware | — |

## Commands

Run from the repository root.

```bash
# Build
python -m compileall -q packages apps workers

# Tests, with a machine-readable report for the checker
python -m pytest -q -p no:logging --junitxml=var/reports/junit.xml

# Lint / static analysis
# Not applicable: no linter configured (exception in project-profile.md)
```

PostgreSQL variant: set `AWF_TEST_DATABASE_URL` (user guide §18).

## Test linking

- **Convention:** `@pytest.mark.req("FR-SDD-001.1")`; several IDs may be passed in one marker
- **Test directories scanned:** `tests/`
- **Report:** `var/reports/junit.xml`

Tests written before SDD adoption carry no IDs; they are linked when a change touches the behaviour they cover.

## Test data and environment

- **Data sources:** synthetic repositories (`examples/demo`), fake providers and fake CLIs (`tests/fakes/`)
- **Secrets:** none; tests use dummy keys such as `sk-test`
- **Environment variables:** `AWF_TEST_DATABASE_URL` (optional)
- **Versions / platforms:** Python 3.12 on Windows and Linux

## Defect severity

| Severity | Definition | Required action |
|---|---|---|
| Critical | data loss, secret leak, a worker executing outside policy, a write to a protected branch | stop release; escalate |
| High | a `Must` requirement broken, no reasonable workaround | fix before release, or a recorded exception |
| Medium | partial impact, workaround exists | fix, or record accepted risk |
| Low | minor issue | track and prioritise |

## Limitations

Real model providers and real coding-engine CLIs are not exercised by the suite (fakes only); roadmap stage 0
covers them with `awf selftest`.
