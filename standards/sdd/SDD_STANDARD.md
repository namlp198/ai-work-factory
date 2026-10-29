---
title: SDD Repository Standard
version: 1.1
status: Draft for review
date: 2026-10-27
owner: ndev (AI Work Factory)
licence: Not yet chosen (owner decision)
supersedes: 1.0
---

# SDD Repository Standard v1.1

## 1. Purpose

Provide a repeatable, auditable development workflow for repositories changed by humans and AI agents. SDD is the
project's working contract, not a documentation exercise. Version 1.1 keeps the intent of v1.0 and makes it
workable for many concurrent changes and checkable by tools: an orchestrator such as AI Work Factory, CI, or a
person with a terminal must be able to verify conformance without interpreting prose.

## 2. Lifecycle

1. **Specify**: define the problem, scope, constraints, requirements and acceptance criteria.
2. **Design**: describe the solution, interfaces, data, failure behaviour and consequential decisions.
3. **Plan**: split the approved design into sequenced, verifiable tasks.
4. **Implement**: change code in task-sized increments.
5. **Verify**: test and review the implementation against requirements and design.
6. **Release**: approve the result, record residual risks, promote the change into the living specification.

The lifecycle is iterative. Verification may reveal a defect in the implementation, the design or the
specification: return to the stage at fault and update everything downstream. A downstream artifact MUST NOT
silently override an approved upstream artifact.

## 3. Normative language

- **MUST / MUST NOT**: mandatory for conformance.
- **SHOULD / SHOULD NOT**: expected unless a documented reason exists.
- **MAY**: optional.

## 4. Repository layout

```text
repo/
├── AGENTS.md                         agent behaviour and guardrails (all profiles)
└── docs/sdd/
    ├── project-profile.md            profile, area register, gate owners, exceptions (all profiles)
    ├── testing.md                    test levels, commands, test-linking convention (all profiles)
    ├── specs/
    │   └── <area>.md                 LIVING specification: approved requirements in force, per area
    ├── changes/
    │   └── <NNNN>-<slug>/            one folder per change
    │       ├── spec.md               requirement deltas (added / modified / retired), open questions
    │       ├── design.md             Standard and Critical; Lightweight when useful
    │       ├── tasks.md              Standard and Critical; short checklist for Lightweight
    │       ├── verification.md       GENERATED from evidence (section 10)
    │       └── completion.md         Standard and Critical; short summary for Lightweight
    └── decisions/
        └── ADR-<NNNN>-<slug>.md      consequential decisions
```

- **Living specs** (`specs/`) describe the system as it is: only approved, in-force requirements, grouped by
  area. They are changed only by promoting a released change (section 12).
- **Change folders** (`changes/`) describe one proposed change. Concurrent changes never edit the same file
  until promotion, so parallel branches do not conflict over a single `spec.md`.
- `NNNN` is a zero-padded change number. A released or abandoned change folder is kept as history with status
  `Archived` or `Abandoned`.
- Project conventions for `src/` and `tests/` are kept. The standard prescribes only `docs/sdd/` and `AGENTS.md`.

## 5. Areas and requirement IDs

### 5.1 Areas

The project profile keeps an **area register**: a short uppercase code (2–8 letters, e.g. `AUTH`, `API`, `UI`,
`CAM`) per functional area, with the living spec file it maps to. Every requirement belongs to exactly one area.

### 5.2 ID format

`<TYPE>-<AREA>-<NNN>`, for example `FR-AUTH-001`.

| Type | Meaning |
|---|---|
| `FR` | functional requirement |
| `NFR` | non-functional (quality) requirement |
| `SEC` | security / privacy requirement |
| `OPS` | operations / maintainability requirement |
| `SAF` | safety / hazard-related requirement |
| `CON` | external constraint or compatibility requirement |

- Acceptance criteria are numbered under their requirement: `FR-AUTH-001.1`, `FR-AUTH-001.2`. Tests link to these
  IDs (section 9).
- Other IDs: tasks `TASK-<NNNN>-<nn>` (change number + sequence, e.g. `TASK-0042-03`), open questions
  `Q-<NNNN>-<nn>`, defects `DEF-<NNNN>-<nn>`, design elements `DES-<NNNN>-<nn>` (optional), decisions
  `ADR-<NNNN>`. Scoping them by change number makes them unique without coordination.
- Once published, an ID MUST NOT be reused for a different requirement. A retired requirement stays in the living
  spec with status `Retired` and a reason.

### 5.3 Allocating new requirement IDs

Requirement IDs are repository-wide, so concurrent changes can collide. One of these MUST be used, declared in
the project profile:

- **Central allocation** (orchestrated repositories): the orchestrator allocates the next number per area
  atomically when a change needs a new requirement. AI Work Factory does this for its runs.
- **Register allocation** (manual repositories): take the next free number from the area's ID register (the
  `Register` table at the end of `specs/<area>.md`) and add a `Reserved` row for it in the same branch. Two
  branches that reserve the same number conflict on that line when merged; the later branch renumbers.

In both cases the conformance checker (section 14) rejects duplicate IDs across the repository.

## 6. Specification rules

A change `spec.md` lists the requirements the change **adds**, **modifies** or **retires**, in that grammar
(see `docs/GRAMMAR.md`). Every added or modified requirement MUST have:

- a unique ID, a title, and a testable `Statement`;
- a `Priority`: `Must`, `Should` or `Could`;
- at least one acceptance criterion with its own ID, or a `Not verifiable` note with the reason;
- a `Verification` method: `Automated`, `Manual`, `Analysis`, `Inspection`, `Bench`, `HIL` or `Field`;
- a `Rationale` when the reason is not obvious, and `Depends on` when relevant.

A modified requirement keeps its ID and records the previous statement and the reason. Acceptance criteria SHOULD
be observable outcomes: Given/When/Then, or EARS (`WHEN <trigger> THE SYSTEM SHALL <response>`). Words such as
"fast", "robust", "user-friendly" or "secure" need a measurable or reviewable criterion.

Open questions are listed as `Q-…` items with status `Open`, `Answered` or `Accepted risk`. **G1 cannot pass while
a question is `Open`.**

## 7. Design rules

The change `design.md` MUST explain how the solution satisfies every added or modified requirement, through a
`Requirement mapping` table that lists each such ID with its design element(s). It includes the relevant parts of:

- components and responsibilities;
- interfaces, APIs, messages, protocols and versioning;
- data model, persistence, migrations and retention;
- error handling, timeouts, retries, idempotency and recovery;
- security, privacy, permissions and secrets;
- performance, availability, resource budgets and observability;
- compatibility, deployment, rollback and operations;
- alternatives and trade-offs for consequential choices (an ADR when the decision must outlive the change).

A section that does not apply is marked `Not applicable: <reason>` when a reader would otherwise expect it.

## 8. Planning rules

Every task in `tasks.md` MUST have a stable `TASK-…` ID, an outcome, `Requirements` (the requirement or
acceptance-criterion IDs it serves), `Depends on`, a completion condition, and `Verification` (the tests or
procedure that will prove it). Every added or modified requirement MUST be served by at least one task, or be
marked `Deferred` with a reason. Maintenance tasks without a requirement say why (`Requirements: none — <reason>`).

## 9. Implementation and test linking

- Implementation MUST follow the approved spec and design. A deviation that affects behaviour, interfaces,
  security, compatibility, risk or acceptance criteria MUST be recorded and approved per the profile (section 12).
- AI-written code gets the same review and verification as human-written code.
- **Tests carry the acceptance-criterion IDs they prove**, so coverage can be computed. The convention is set in
  `testing.md`; the defaults are:

| Stack | Convention |
|---|---|
| Python / pytest | `@pytest.mark.req("FR-AUTH-001.1")` (several IDs allowed) |
| .NET / xUnit, NUnit, MSTest | `[Trait("Req", "FR-AUTH-001.1")]`, `[Category("FR-AUTH-001.1")]`, `[TestCategory("FR-AUTH-001.1")]` |
| C++ / GoogleTest, Catch2 | comment on the line above the test: `// REQ: FR-AUTH-001.1` |
| JavaScript / TypeScript | ID in the test title: `test("rejects expired token [FR-AUTH-001.1]", …)` |
| Any other / manual | the ID in the test name, or in a `REQ:` comment directly above it |

- Test runs SHOULD produce a machine-readable report (JUnit XML or equivalent) so that results can be attached to
  each linked ID. `testing.md` states the command that produces it.

## 10. Verification rules

- `verification.md` is **generated** from evidence: the test report, build/lint results and the test-linking
  scan, by the orchestrator or by the conformance checker in CI. Its header names the generator and the revision.
- Agents and people MUST NOT write `PASS` for an automated check by hand. The only hand-written part is
  `Manual checks`: each row names the procedure, who ran it, when, and where the evidence is.
- Each check records the requirement/criterion IDs, the exact command or procedure, the environment when
  relevant, the result (`PASS`, `FAIL`, `BLOCKED`, `NOT RUN`) and the evidence location.
- `NOT RUN` and `BLOCKED` are not passes. A skipped test states the reason and the impact. Critical projects may
  not release with an unresolved mandatory check unless an exception is recorded.

## 11. Quality gates

Statuses: `PASS`, `FAIL`, `BLOCKED`, `NOT REQUIRED` (only where the profile allows it, with a reason).

| Gate | Passes when |
|---|---|
| **G1 Specification Ready** | requirements follow section 6; no `Open` question; required approval recorded |
| **G2 Design Ready** | every added/modified requirement is mapped; interfaces and material risks addressed |
| **G3 Plan Ready** | every requirement is served by a task or deferred; tasks have verification |
| **G4 Implementation Complete** | planned tasks done or blocked/deferred; tests added or updated; deviations recorded |
| **G5 Verification Passed** | generated verification shows every `Must` criterion `PASS`; manual checks recorded |
| **G6 Release Approved** | G1–G5 resolved; residual risks accepted by the owner; change promoted (section 12) |

Gate approvals by profile (owners are named in `project-profile.md`):

| Gate | Lightweight | Standard | Critical |
|---|---|---|---|
| G1 | author self-check | human approval — MAY be combined with G2 and G3 in one review before coding | separate human approval |
| G2 | `NOT REQUIRED` allowed, with reason | (combined with G1) | separate human approval |
| G3 | short checklist | (combined with G1) | separate human approval |
| G4 | automatic (checker) | automatic + code review | automatic + independent code review |
| G5 | automatic | automatic | automatic + independent verifier + required bench/HIL/field checks |
| G6 | author | human approval | human approval + release checklist |

A gate is re-opened when acceptance criteria or scope change, when a design, interface or data model changes
materially, when the implementation deviates from approved behaviour, when a linked test is changed, removed or
starts failing, or when the release artifact differs from the verified revision.

## 12. Change control and promotion

- **Changing an approved requirement** happens in a change folder: list it under `Modified` with the previous
  statement and the reason, identify the impacted design elements, tasks, tests and interfaces (search the
  repository for its ID), re-open the affected gates and re-run the affected checks.
- **Spec change during implementation**: when implementation or review shows that the spec is wrong or
  contradictory, the work returns to Specify with a recorded reason (`SPEC_CHANGE`), and G1 is approved again
  before the work continues. Acceptance criteria are never weakened to make an implementation pass.
- **Promotion at G6**: the change's added, modified and retired requirements are applied to `specs/<area>.md`
  in the same merge, the register rows go from `Reserved` to `Active` or `Retired`, and the change folder's status
  becomes `Archived`. Nothing else edits the living specs.

## 13. Exempt changes

These changes need no change folder. They are marked with a commit trailer `SDD-Exempt: <category> — <reason>`:

- `typo` — spelling, formatting or comments with no behavioural meaning;
- `docs` — non-normative documentation;
- `deps` — dependency patch updates without behaviour change, with the normal CI passing;
- `build` — CI or build configuration that does not change the product;
- `revert` — a revert of a previous commit.

A change that touches behaviour, interfaces, data, security, safety or any requirement is **not** exempt. A
reviewer may reject an exemption, in which case the change needs a change folder.

## 14. Conformance checking

Conformance is checked by a tool, not by reading. The checker (requirements in `docs/CHECKER.md`) validates the
grammar, the IDs, the links between spec, design, tasks and tests, the generated verification and the gate rules,
and it produces `verification.md`. The same checker runs in CI and inside an orchestrator. A repository conforms
when the checker reports no error for its selected profile, or when every remaining error is covered by a
recorded exception.

## 15. Profiles

- **Lightweight**: low-risk scripts, spikes, small utilities, prototypes.
- **Standard**: maintained applications, services, libraries, APIs, desktop apps, internal tools.
- **Critical**: safety-relevant control, privileged security functions, sensitive data, production-critical
  services, hardware control. Adds risk analysis, review independence, staged verification (simulation, bench,
  HIL, field) and explicit release approval. Unit tests alone are not evidence of physical safety.

Choose the profile by the impact of failure, not by the size of the code.

## 16. Exceptions

An exception to a MUST-level control is recorded in `project-profile.md` with the control waived, reason and
scope, risk, compensating control, approver, date, and expiry or revisit condition. An exception applies only to
that repository.

## 17. Definition of Done

A change is done only when:

- its requirements are implemented, or explicitly deferred with a reason;
- its design and plan are current;
- generated verification shows actual results, with every `Must` criterion `PASS` or covered by an exception;
- deviations, known issues and residual risks are recorded in `completion.md`;
- the gates required by the profile are passed;
- the change is promoted into the living specs and its folder is archived.
