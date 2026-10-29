# Document grammar (normative)

SDD documents are Markdown that people read and tools parse. This file defines the parts a tool relies on. Prose
between these parts is free.

## 1. Front matter

Every SDD document starts with YAML front matter.

```yaml
---
sdd: 1.1                       # standard version
doc: change-spec               # one of the kinds below
change: "0042"                 # change number; change documents only
area: AUTH                     # living specs only (one area per file)
status: Draft                  # see section 6
owner: <person or role>
updated: 2026-10-27
---
```

Kinds: `project-profile`, `testing`, `area-spec`, `change-spec`, `design`, `tasks`, `verification`,
`completion`, `adr`.

## 2. IDs

| Item | Pattern | Example |
|---|---|---|
| Requirement | `(FR\|NFR\|SEC\|OPS\|SAF\|CON)-[A-Z]{2,8}-\d{3,}` | `FR-AUTH-001` |
| Acceptance criterion | `<requirement>\.\d+` | `FR-AUTH-001.2` |
| Task | `TASK-\d{4}-\d{2,}` | `TASK-0042-03` |
| Open question | `Q-\d{4}-\d{2,}` | `Q-0042-01` |
| Defect | `DEF-\d{4}-\d{2,}` | `DEF-0042-01` |
| Design element | `DES-\d{4}-\d{2,}` | `DES-0042-02` |
| Decision | `ADR-\d{4}` | `ADR-0007` |

An ID is written in backticks wherever it is referenced: `` `FR-AUTH-001` ``. Lists of IDs are comma-separated:
`` `FR-AUTH-001`, `FR-AUTH-002.1` ``.

## 3. Items

An item is a level-3 heading `### <ID> — <title>` (em dash or `--`), followed by field lines:

```markdown
### FR-AUTH-003 — Lock the account after repeated failures
- **Statement:** The system MUST lock an account for 15 minutes after 5 failed sign-ins within 10 minutes.
- **Priority:** Must
- **Verification:** Automated
- **Rationale:** Limits password guessing.
- **Depends on:** `FR-AUTH-001`
- **Acceptance criteria:**
  - `FR-AUTH-003.1`: Given 4 failed attempts, when a 5th fails within 10 minutes, then the account is locked.
  - `FR-AUTH-003.2`: Given a locked account, when 15 minutes pass, then sign-in is accepted again.
```

Rules:

- A field line is `- **<Field>:** <value>`. Field names are case-sensitive and in English.
- A value may continue on following lines indented by at least two spaces; they belong to the field until the
  next field line, criterion bullet or heading.
- An acceptance criterion is a nested bullet that starts with its ID in backticks and a colon.
- Unknown fields are allowed and ignored by tools.
- A required field with no meaningful value is written `None` or `Not applicable: <reason>`, never left out.
- A **placeholder** is `<…>` text or `YYYY-MM-DD` outside inline code and fenced code blocks. Text inside
  backticks is quoted, never a placeholder, so documents can mention template syntax.

## 4. Required fields per document

### Change spec (`changes/<NNNN>-<slug>/spec.md`)

Sections, in this order: `## Summary`, `## Problem and context`, `## Scope` (with `In scope` / `Out of scope`
lists), `## Constraints and assumptions`, `## Added`, `## Modified`, `## Retired`, `## Open questions`,
`## Change history`. Empty sections contain `None`.

| Section | Item fields |
|---|---|
| `Added` | `Statement`, `Priority`, `Verification`, `Acceptance criteria` (or `Not verifiable: <reason>`); optional `Rationale`, `Depends on` |
| `Modified` | the same as `Added`, plus `Previous statement`, `Reason` |
| `Retired` | `Reason` |
| `Open questions` (`### Q-…`) | `Question`, `Impact` (`Low` / `Medium` / `High`), `Status` (`Open` / `Answered` / `Accepted risk`), `Answer` (when not `Open`) |

### Living spec (`specs/<area>.md`)

Sections: `## Overview`, `## Requirements`, `## Register`.

- Items under `Requirements` have the `Added` fields plus `Status` (`Active` / `Retired`) and `Introduced by`
  (change number; `Modified by` lists later changes).
- `## Register` is a table with columns `ID`, `Status`, `Change`. Status is `Reserved`, `Active` or `Retired`.
  Every requirement ID ever published in the area appears exactly once.

### Design (`changes/<NNNN>-<slug>/design.md`)

`## Requirement mapping` is a table with columns `Requirement`, `Design elements`, `Notes`. Every requirement
under `Added` or `Modified` in the change spec appears in it. The other sections follow the design template; a
section that does not apply says `Not applicable: <reason>`.

### Tasks (`changes/<NNNN>-<slug>/tasks.md`)

One `### TASK-…` item per task, with `Status` (`Todo` / `In progress` / `Blocked` / `Done` / `Deferred`),
`Requirements` (IDs, or `none — <reason>`), `Depends on` (IDs or `None`), `Completion`, `Verification`; optional
`Files`, `Notes`.

### Verification (`changes/<NNNN>-<slug>/verification.md`)

Generated. Front matter adds `generator`, `revision` and `generated`. Sections: `## Gates`, `## Checks`,
`## Criteria coverage`, `## Manual checks` (the only hand-written section), `## Unverified and residual risk`.

`## Criteria coverage` is a table with columns `Criterion`, `Priority`, `Tests`, `Result`, `Evidence`, where
`Result` is `PASS`, `FAIL`, `BLOCKED` or `NOT RUN`, derived from the linked tests' results.

`## Manual checks` rows: `Criterion`, `Procedure`, `Performed by`, `Date`, `Result`, `Evidence`. A `PASS` row
without `Evidence` is an error.

## 5. Commit trailers

- `SDD-Change: 0042` links a commit to a change folder (optional, helps reviewers).
- `SDD-Exempt: <typo|docs|deps|build|revert> — <reason>` marks an exempt change (standard section 13).

## 6. Status vocabularies

| Document | Statuses |
|---|---|
| Change spec, design, tasks | `Draft`, `In review`, `Approved`, `Superseded` |
| Change folder (in `spec.md` front matter) | `Draft`, `In review`, `Approved`, `Implementing`, `Verifying`, `Released`, `Archived`, `Abandoned` |
| Living spec | `Current` |
| Verification | `Draft`, `Passed`, `Failed`, `Blocked` (set by the generator) |
| Completion | `Draft`, `Complete`, `Partially complete`, `Blocked` |
| Project profile, testing | `Draft`, `Approved` |
| ADR | `Proposed`, `Accepted`, `Rejected`, `Superseded` |

The change folder status in `spec.md` is the coarse form of the workflow states in `docs/AGENT_WORKFLOW.md`:
`Draft` (`REQUESTED`, `SPEC_DRAFT`, `NEEDS_CLARIFICATION`), `In review` (`SPEC_REVIEW` to `PLAN_READY`),
`Approved` (G1–G3 passed), `Implementing`, `Verifying`, `Released`, `Archived`, `Abandoned`. An orchestrator keeps
the fine-grained state; the document carries the coarse one.
