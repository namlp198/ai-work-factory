---
sdd: 1.1
doc: project-profile
status: Approved
owner: platform team
updated: 2026-10-27
---

# Project profile

## Identity

- **Repository:** example-auth-service
- **Owner:** platform team
- **Profile:** Standard
- **Reason for profile:** Maintained internal service; a failure locks users out but holds no safety function.
- **Languages / frameworks:** Python 3.12, FastAPI
- **Build command:** `python -m compileall -q src`
- **Test command:** `python -m pytest -q --junitxml=reports/junit.xml`
- **Lint / static analysis command:** `ruff check .`
- **Release method:** container image tagged from `main`

## Areas

| Area | Description | Living spec |
|---|---|---|
| AUTH | sign-in, sessions, account lockout | `docs/sdd/specs/auth.md` |

## Requirement ID allocation

- **Method:** Register
- **Orchestrator:** Not applicable: register allocation

## Gate owners

| Gate | Owner | Approval required | Combined review |
|---|---|---|---|
| G1 Specification Ready | product owner | yes | G1–G3 in one review |
| G2 Design Ready | tech lead | yes | with G1 |
| G3 Plan Ready | tech lead | yes | with G1 |
| G4 Implementation Complete | checker + code reviewer | yes | |
| G5 Verification Passed | checker | no | |
| G6 Release Approved | product owner | yes | |

## Verification policy

- **Required test levels:** unit, integration
- **Coverage threshold:** Not enforced
- **Security checks:** dependency scan in CI
- **Performance checks:** Not applicable: no latency requirement in scope
- **Supported environments:** Linux containers
- **Test data policy:** synthetic users only

## Release and rollback

- **Release artifact:** container image
- **Rollback / recovery:** redeploy the previous image tag
- **Post-release monitoring:** sign-in error rate for 24 hours, platform team

## Exceptions

| Control or check code | Reason and scope | Risk | Compensating control | Approver | Date | Revisit / expiry |
|---|---|---|---|---|---|---|

None

## Local conventions

- **Branches / PRs:** one branch per change, `change/<NNNN>-<slug>`
- **Commits:** Conventional Commits with `SDD-Change` trailer
- **Code style:** ruff
- **Agent tools allowed:** read/write in the repository; no network
- **Escalation triggers:** schema migration, change to password or token handling
