# Conformance checker: requirements

This page specifies the tool that checks a repository against the standard (standard section 14). It is written
as requirements so that the checker itself can be built the SDD way. The reference implementation is planned as
`awf sdd check` in AI Work Factory; any tool meeting these requirements is conforming.

## Inputs

- the repository root (current directory, or `--root <path>`);
- the change to check: `--change 0042`, or `--all-open-changes` for every change not `Archived` or `Abandoned`;
- optionally the test report, `--junit <path>` (JUnit XML or equivalent), and the revision it was produced from,
  `--revision <sha>`;
- the profile, read from `docs/sdd/project-profile.md`.

## Outputs

- a list of findings, each with a code, a severity (`error` / `warning`), the file and line, and a message;
- with `--write-verification`, the generated `verification.md` of the change;
- exit code `0` when there is no error, `1` when there is at least one error, `2` when the checker could not run
  (bad arguments, unreadable repository);
- `--json` prints the findings and the coverage numbers as JSON for orchestrators.

## Checks

| Code | Severity | Check |
|---|---|---|
| `SDD001` | error | Required baseline files exist for the profile (`AGENTS.md`, `project-profile.md`, `testing.md`) |
| `SDD002` | error | Every SDD document has valid front matter with a known `doc` kind and status |
| `SDD003` | error | Template placeholders remain (`<…>` or `YYYY-MM-DD` outside inline code, per `GRAMMAR.md` §3) |
| `SDD010` | error | ID does not match the grammar, or its area is not in the area register |
| `SDD011` | error | Requirement ID defined twice in the repository (living specs and open changes) |
| `SDD012` | error | Requirement ID used in a change but neither defined there nor in a living spec |
| `SDD013` | error | New requirement ID not reserved per the profile's allocation method |
| `SDD020` | error | Added/modified requirement lacks `Statement`, `Priority`, `Verification` or acceptance criteria |
| `SDD021` | warning | Acceptance criterion uses an unmeasurable word (`fast`, `robust`, `user-friendly`, `secure`, …) without a number or reviewable condition |
| `SDD022` | error | Open question with status `Open` while the change spec's status is `Approved` or later |
| `SDD023` | error | Modified requirement lacks `Previous statement` or `Reason` |
| `SDD030` | error | Added/modified requirement missing from the design's `Requirement mapping` (Standard, Critical) |
| `SDD040` | error | Added/modified requirement served by no task and not `Deferred` (Standard, Critical) |
| `SDD041` | error | Task lacks `Requirements`, `Depends on`, `Completion` or `Verification`; dependency missing or cyclic |
| `SDD050` | error | Automated criterion linked from no test (scan per `testing.md` convention) |
| `SDD051` | error | Test links a criterion ID that does not exist |
| `SDD052` | error | `Must` criterion result is not `PASS` in the test report of the verified revision (at G5) |
| `SDD053` | error | Manual check marked `PASS` without evidence |
| `SDD054` | error | `verification.md` edited by hand outside `Manual checks` (content differs from regeneration) |
| `SDD060` | error | Released change not promoted: living spec or register not updated, folder not `Archived` |
| `SDD070` | warning | Commit changes product files without `SDD-Change` or `SDD-Exempt` trailer (opt-in, CI only) |

Every error can be waived only by an exception row in `project-profile.md` that names the code and the scope.

## Test-link scanning

- The checker reads the convention from `testing.md` (standard section 9 lists the defaults) and scans the test
  directories named there.
- A link is the criterion ID found in a marker, attribute, comment directly above the test, or the test title.
- Results come from the test report: a criterion is `PASS` when all linked tests passed, `FAIL` when any failed,
  `NOT RUN` when none ran, `BLOCKED` when the report is missing.

## Non-goals

- Judging whether a test really exercises the behaviour it claims (review does that).
- Judging the quality of prose, designs or decisions.
