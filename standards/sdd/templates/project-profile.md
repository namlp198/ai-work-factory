---
sdd: 1.1
doc: project-profile
status: Draft
owner: <person or role>
updated: YYYY-MM-DD
---

# Project profile

## Identity

- **Repository:** <name>
- **Owner:** <person or team>
- **Profile:** <Lightweight | Standard | Critical>
- **Reason for profile:** <impact if it fails, data sensitivity, operational criticality>
- **Languages / frameworks:** <list>
- **Build command:** <command>
- **Test command:** <command that also writes a machine-readable report, see testing.md>
- **Lint / static analysis command:** <command or Not applicable: reason>
- **Release method:** <method or Not applicable: reason>

## Areas

Every requirement belongs to one area. Codes are 2–8 uppercase letters.

| Area | Description | Living spec |
|---|---|---|
| <CODE> | <what it covers> | `docs/sdd/specs/<code>.md` |

## Requirement ID allocation

- **Method:** <Central (orchestrator allocates) | Register (reserve in the area spec's Register table)>
- **Orchestrator:** <name, or Not applicable: register allocation>

## Gate owners

| Gate | Owner | Approval required | Combined review |
|---|---|---|---|
| G1 Specification Ready | <role> | <yes / no> | <e.g. G1–G3 in one review (Standard)> |
| G2 Design Ready | <role> | <yes / no> | |
| G3 Plan Ready | <role> | <yes / no> | |
| G4 Implementation Complete | <role / checker> | <yes / no> | |
| G5 Verification Passed | <checker + role> | <yes / no> | |
| G6 Release Approved | <role> | <yes / no> | |

## Verification policy

- **Required test levels:** <unit / integration / system / acceptance / HIL / field>
- **Coverage threshold:** <number or Not enforced>
- **Security checks:** <dependency scan / SAST / threat review / Not applicable: reason>
- **Performance checks:** <benchmarks / budgets / Not applicable: reason>
- **Supported environments:** <OS, hardware, runtime, browser, device>
- **Test data policy:** <synthetic / anonymised / approved fixtures>

## Release and rollback

- **Release artifact:** <package, image, installer, firmware, deployment>
- **Rollback / recovery:** <method or Not applicable: reason>
- **Post-release monitoring:** <signals, owner, duration or Not applicable: reason>

## Exceptions

| Control or check code | Reason and scope | Risk | Compensating control | Approver | Date | Revisit / expiry |
|---|---|---|---|---|---|---|

Write `None` below the table when there are no exceptions.

## Local conventions

- **Branches / PRs:** <rules>
- **Commits:** <rules; `SDD-Change` / `SDD-Exempt` trailers>
- **Code style:** <formatter / linter>
- **Agent tools allowed:** <tools and permission boundaries>
- **Escalation triggers:** <ambiguous requirements, security change, schema migration, safety change, …>
