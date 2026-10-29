# Changelog

## 1.1 — 2026-10-27 (draft for review)

v1.1 keeps the lifecycle, the gates G1–G6, the profiles and the agent rules of v1.0. The changes make the
standard work with many concurrent changes and make conformance checkable by a tool.

| # | Change | Why | Where |
|---|---|---|---|
| 1 | **Living specs per area + one folder per change**, replacing the single `spec.md` / `design.md` / `tasks.md` / `verification.md` per repository | parallel branches no longer conflict over one file; the spec in force is separate from proposals; history stays per change | standard §4, §12 |
| 2 | **Requirement IDs with an area** (`FR-AUTH-001`) and a declared allocation method (central or register) | concurrent changes could both take `FR-014`; duplicates are now prevented and detected | standard §5 |
| 3 | **Acceptance criteria get IDs** (`FR-AUTH-001.1`); tasks, questions and defects are scoped by change number (`TASK-0042-03`) | coverage can be computed per criterion; scoped IDs need no coordination | standard §5.2 |
| 4 | **Document grammar**: YAML front matter, `### <ID> — <title>` items, `- **Field:** value` lines, fixed status vocabularies | tools can parse documents instead of guessing from prose | `docs/GRAMMAR.md` |
| 5 | **Test-linking convention** per stack (pytest marker, xUnit trait, `// REQ:` comment, ID in test title) and a machine-readable test report | links from criteria to tests and results can be found automatically | standard §9 |
| 6 | **`verification.md` is generated** from evidence; only `Manual checks` is hand-written; hand edits are detected | v1.0 asked agents to fill it in, contradicting its own rule that an agent's claim is not evidence | standard §10 |
| 7 | **Traceability matrix is generated**, each link recorded in one place | hand-kept matrices drift and prove nothing | `docs/TRACEABILITY.md` |
| 8 | **Exempt changes** defined, with an `SDD-Exempt` commit trailer | v1.0's "non-trivial change" had no criteria | standard §13 |
| 9 | **Gate approvals per profile** in one table; Standard may combine G1–G3 in one review before coding | one review instead of three for most work, still before any code | standard §11 |
| 10 | **`SPEC_CHANGE` state** and promotion at G6 | a spec found wrong during implementation goes back through approval; the living spec changes only on release | standard §12, `docs/AGENT_WORKFLOW.md` |
| 11 | **Open questions block G1**; repeated failures without a new diagnosis move a change to `BLOCKED` | the agent stops instead of guessing or looping | standard §6, `docs/AGENT_WORKFLOW.md` |
| 12 | **Conformance checker requirements** (`SDD001`–`SDD070`), replacing the shell CI that only checked file existence and a few placeholders | real checks: IDs, links, coverage, evidence, promotion | `docs/CHECKER.md`, `ci/sdd-check.yml` |
| 13 | **Single source of templates**; the starter copies in `docs/sdd/` are removed, a filled example is added | v1.0 had two diverging copies of each document (`docs/sdd/spec.md` lacked sections 7–8 of the template) | `templates/`, `examples/minimal/` |
| 14 | Metadata: owner, date, status, licence field | v1.0 named no owner, date or licence | `README.md`, front matter |

### Draft revisions (found while writing change 0001 of AI Work Factory)

- `GRAMMAR.md` §3: field values may continue on indented lines.
- `GRAMMAR.md` §3, `CHECKER.md` `SDD003`: text in inline code or fenced code is never a placeholder.

### Removed

- `docs/sdd/*` starter files (replaced by `templates/` and `examples/minimal/`).
- `.github/workflows/sdd-validate.yml` (replaced by `ci/sdd-check.yml`, which runs the checker).
- `.gitignore.sdd-example`: secret hygiene stays a rule in `AGENTS.md`; ignore files are project-specific.

### Migrating from 1.0

1. Move requirements from `docs/sdd/spec.md` into `docs/sdd/specs/<area>.md`, renaming `FR-001` to
   `FR-<AREA>-001`, and add a `Register` table.
2. Give each acceptance criterion an ID (`<requirement>.<n>`).
3. Move in-progress work into `docs/sdd/changes/<NNNN>-<slug>/`.
4. Add front matter to every document.
5. Mark tests with criterion IDs and enable a JUnit XML report.

## 1.0

Initial release: lifecycle, G1–G6, profiles, agent rules, templates, file-existence CI.
