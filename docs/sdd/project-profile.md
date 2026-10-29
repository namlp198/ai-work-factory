---
sdd: 1.1
doc: project-profile
status: In review
owner: ndev
updated: 2026-10-28
---

# Project profile

## Identity

- **Repository:** ai-work-factory
- **Owner:** ndev
- **Profile:** Standard
- **Reason for profile:** Maintained platform that runs agents against other repositories with their credentials;
  a failure can push wrong code to a branch or leak a secret, but it controls no physical system and never merges
  into a protected branch on its own.
- **Languages / frameworks:** Python 3.12 (FastAPI, SQLAlchemy, Alembic), plain JavaScript dashboard
- **Build command:** `python -m compileall -q packages apps workers`
- **Test command:** `python -m pytest -q -p no:logging --junitxml=var/reports/junit.xml`
- **Lint / static analysis command:** Not applicable: no linter is configured yet; adding one is a later change
- **Release method:** `main` branch; machines update with `git pull` + bootstrap (user guide §21)

## Areas

| Area | Description | Living spec |
|---|---|---|
| SDD | the SDD conformance tooling (`awf sdd …`) | `docs/sdd/specs/sdd.md` |
| CORE | orchestrator, workflow engine, critic, artifacts, gates | `docs/sdd/specs/core.md` (written when first changed) |
| WORK | worker, sandbox, executor, coding engines | `docs/sdd/specs/work.md` (written when first changed) |
| PROV | model providers and routing | `docs/sdd/specs/prov.md` (written when first changed) |
| API | control API, authentication, sessions | `docs/sdd/specs/api.md` (written when first changed) |
| UI | dashboard and PWA | `docs/sdd/specs/ui.md` (written when first changed) |
| NOTE | notifications and chat approvals | `docs/sdd/specs/note.md` (written when first changed) |
| OPS | bootstrap, services, database operations, multi-machine setup | `docs/sdd/specs/ops.md` (written when first changed) |

Existing behaviour is described in the user guide (`docs/HUONG_DAN_SU_DUNG.md`) and the ADRs. A living spec for an
area is written when a change first touches that area; it is not back-filled in advance.

## Requirement ID allocation

- **Method:** Register
- **Orchestrator:** Not applicable: AWF itself is developed with register allocation until it runs its own changes

## Gate owners

| Gate | Owner | Approval required | Combined review |
|---|---|---|---|
| G1 Specification Ready | ndev | yes | G1–G3 in one review before coding |
| G2 Design Ready | ndev | yes | with G1 |
| G3 Plan Ready | ndev | yes | with G1 |
| G4 Implementation Complete | checker + ndev | yes | |
| G5 Verification Passed | checker | no | |
| G6 Release Approved | ndev (approves the commit / merge to `main`) | yes | |

## Verification policy

- **Required test levels:** unit, integration, end-to-end (all under `tests/`)
- **Coverage threshold:** Not enforced
- **Security checks:** secret redaction tests; dependency review on `requirements.lock` changes
- **Performance checks:** Not applicable: no performance requirement in force
- **Supported environments:** Windows 10/11 and Ubuntu 24.04 with Python 3.12; CI on `ubuntu-latest` and `windows-latest`
- **Test data policy:** synthetic repositories and fakes (`tests/fakes/`); no real keys

## Release and rollback

- **Release artifact:** the `main` branch
- **Rollback / recovery:** revert the commit; machines `git pull` + bootstrap; database migrations are additive
- **Post-release monitoring:** Not applicable: single operator; problems surface on the dashboard and in `awf doctor`

## Exceptions

| Control or check code | Reason and scope | Risk | Compensating control | Approver | Date | Revisit / expiry |
|---|---|---|---|---|---|---|
| Lint command | no linter configured in the repository | style drift | code review | ndev | 2026-10-28 | when a linter is added |

## Local conventions

- **Branches / PRs:** work on `develop`; `main` is the stable branch
- **Commits:** imperative subject, body explains why; `SDD-Change: NNNN` or `SDD-Exempt: …` trailer
- **Code style:** match the surrounding code (see `CLAUDE.md`)
- **Agent tools allowed:** full repository access; commits and pushes only when the owner asks
- **Escalation triggers:** schema migration, change to authentication or secret handling, change to what workers
  may execute, anything that could merge into a protected branch
