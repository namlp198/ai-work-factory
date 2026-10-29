# Traceability

## Purpose

Follow every requirement through design, tasks, tests and evidence, and see what a change affects.

## Chain

`Requirement → acceptance criterion → design element → task → test → result`

Each link lives in exactly one place, so nothing has to be kept in step by hand:

| Link | Recorded in |
|---|---|
| requirement → acceptance criterion | the requirement item (`Acceptance criteria`) |
| requirement → design element | `design.md`, `Requirement mapping` table |
| requirement / criterion → task | `tasks.md`, each task's `Requirements` field |
| criterion → test | the test itself (convention in `testing.md`, standard section 9) |
| test → result | the machine-readable test report of the verified revision |

The **traceability matrix is generated**, never written by hand: the checker joins these sources and writes the
`Criteria coverage` table of `verification.md`. A hand-maintained matrix drifts and cannot prove anything.

## Rules

1. Every `Active` requirement has an implementation path (a task in the change that added or modified it) or a
   recorded status: `Deferred`, `Out of scope`, `Retired`.
2. Every `Must` requirement has acceptance criteria and a verification method.
3. Every criterion with `Verification: Automated` is linked from at least one test, and its result comes from a
   real test run of the verified revision.
4. Every task lists requirement IDs, or says why none applies.
5. An ID that appears in a table is not coverage by itself: the linked test must exist, run, and exercise the
   stated behaviour. Reviewers sample linked tests for that last point; tools cannot judge it.
6. Evidence names the revision (commit SHA) and, where results depend on it, the environment.

## Change impact

For a modified or retired requirement:

1. Search the repository for its ID: specs, designs, tasks, tests, code comments, ADRs.
2. List the dependent design elements, tasks, tests, interfaces and documentation in the change spec.
3. Re-open the affected gates.
4. Update the artifacts and re-run the affected checks.

## Coverage signals

The checker reports:

- `Must` criteria with a passing linked test / all `Must` criteria;
- requirements with at least one linked verification / all in-scope requirements;
- tasks with requirement links / all tasks;
- manual checks with evidence / all manual checks.

They are signals, not proof: a high percentage can still hide weak tests.
