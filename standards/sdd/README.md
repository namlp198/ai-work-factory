# SDD Repository Standard v1.1

A practical Spec-Driven Development standard for repositories changed by humans and AI agents, designed to be
**checked by tools**: an orchestrator such as AI Work Factory, CI, or a person running the checker.

**Flow:** Specify → Design → Plan → Implement → Verify → Release

- **Status:** Draft for review (2026-10-27). Supersedes v1.0.
- **Owner:** ndev (AI Work Factory).
- **Licence:** not yet chosen (owner decision).
- **What changed since v1.0:** [`CHANGELOG.md`](CHANGELOG.md).

## Contents

| File | Purpose |
|---|---|
| [`SDD_STANDARD.md`](SDD_STANDARD.md) | the normative standard (MUST / SHOULD / MAY) |
| [`AGENTS.md`](AGENTS.md) | instructions for AI agents, to copy into adopting repositories |
| [`docs/GRAMMAR.md`](docs/GRAMMAR.md) | the document grammar tools parse: front matter, IDs, items, fields |
| [`docs/TRACEABILITY.md`](docs/TRACEABILITY.md) | where each link of the chain lives; generated matrix; change impact |
| [`docs/QUALITY_GATES.md`](docs/QUALITY_GATES.md) | G1–G6 exit criteria, marked tool-checked or reviewed |
| [`docs/AGENT_WORKFLOW.md`](docs/AGENT_WORKFLOW.md) | roles, change states, handoffs, escalation, retries |
| [`docs/CHECKER.md`](docs/CHECKER.md) | requirements for the conformance checker (`SDD001`–`SDD070`) |
| [`templates/`](templates/) | the single source of every SDD document |
| [`examples/minimal/`](examples/minimal/) | a filled example: one area, one change in verification |
| [`ci/sdd-check.yml`](ci/sdd-check.yml) | CI workflow running the checker |

## Layout in an adopting repository

```text
repo/
├── AGENTS.md
└── docs/sdd/
    ├── project-profile.md          profile, areas, ID allocation, gate owners, exceptions
    ├── testing.md                  test levels, commands, test-linking convention
    ├── specs/<area>.md             living spec: requirements in force, per area
    ├── changes/<NNNN>-<slug>/      one folder per change
    │   ├── spec.md                 added / modified / retired requirements, open questions
    │   ├── design.md
    │   ├── tasks.md
    │   ├── verification.md         generated from evidence
    │   └── completion.md
    └── decisions/ADR-<NNNN>-<slug>.md
```

## Adopting

1. Copy `AGENTS.md` to the repository root.
2. Create `docs/sdd/` with `project-profile.md` and `testing.md` from `templates/`: choose the profile, list the
   areas, the ID allocation method and the test-linking convention.
3. Create one living spec per area from `templates/area-spec.md`. Existing behaviour may be described gradually,
   area by area, as changes touch it.
4. For each non-exempt change, create `docs/sdd/changes/<NNNN>-<slug>/` from `templates/change/` and fill
   `spec.md` before any code.
5. Mark tests with the acceptance-criterion IDs they prove, and make the test command write a JUnit XML report.
6. Add `ci/sdd-check.yml` once the checker is available, so CI checks conformance and regenerates
   `verification.md`.

## Principles

- Requirements describe outcomes; implementation details only when a constraint is intended.
- Every requirement has a stable ID, and every acceptance criterion has its own ID that tests carry.
- Design elements and tasks link back to requirement IDs; nothing is linked by hand twice.
- Verification results come from tool runs and are generated, never typed. Missing evidence is `NOT RUN` or
  `BLOCKED`, never `PASS`.
- When a requirement changes, the spec changes, with its reason; requirements are never weakened to match the
  implementation.
- The process is proportional to risk: Lightweight, Standard, Critical.
