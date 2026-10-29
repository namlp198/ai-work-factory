---
sdd: 1.1
doc: testing
status: Draft
owner: <person or role>
updated: YYYY-MM-DD
---

# Test strategy

## Objectives

<What risks and behaviours must testing establish?>

## Levels

| Level | Scope | Tool / environment | Required | Pass criteria |
|---|---|---|---|---|
| Unit | <modules> | <tool> | <yes / no — reason> | <criteria> |
| Integration | <interfaces, dependencies> | <tool> | <yes / no — reason> | <criteria> |
| System / acceptance | <end-to-end behaviour> | <tool> | <yes / no — reason> | <criteria> |
| Regression | <existing behaviour> | <tool> | <yes / no — reason> | <criteria> |
| Security | <threats, dependencies> | <tool> | <yes / no — reason> | <criteria> |
| Performance | <budgets> | <tool> | <yes / no — reason> | <criteria> |
| HIL / bench / field | <hardware behaviour> | <setup> | <yes / no — reason> | <criteria> |

## Commands

Run from the repository root.

```bash
# Build
<build command>

# Tests, with a machine-readable report for the checker
<test command, e.g. pytest --junitxml=reports/junit.xml>

# Lint / static analysis
<lint command or Not applicable: reason>
```

## Test linking

Tests carry the acceptance-criterion IDs they prove (standard section 9).

- **Convention:** <e.g. `@pytest.mark.req("FR-AUTH-001.1")` | `[Trait("Req", "FR-AUTH-001.1")]` | `// REQ: FR-AUTH-001.1` | ID in test title>
- **Test directories scanned:** <paths>
- **Report:** <path of the JUnit XML or equivalent>

## Test data and environment

- **Data sources:** <synthetic / approved fixtures / anonymised>
- **Secrets:** <how test secrets are injected; never commit real secrets>
- **Environment variables:** <names only>
- **Versions / platforms:** <versions>

## Defect severity

| Severity | Definition | Required action |
|---|---|---|
| Critical | data loss, safety or security compromise, core service unavailable | stop release; escalate |
| High | a `Must` requirement broken, no reasonable workaround | fix before release, or a recorded exception |
| Medium | partial impact, workaround exists | fix, or record accepted risk |
| Low | minor issue | track and prioritise |

## Limitations

<Tests that are not feasible: why, risk, compensating evidence, owner. Or None.>
