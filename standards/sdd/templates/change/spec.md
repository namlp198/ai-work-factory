---
sdd: 1.1
doc: change-spec
change: "<NNNN>"
status: Draft
profile: <Lightweight | Standard | Critical>
owner: <person or role>
updated: YYYY-MM-DD
---

# Change <NNNN>: <title>

## Summary

<The change in a few sentences.>

## Problem and context

- **Problem:** <what problem exists>
- **Affected users / systems:** <who or what>
- **Why now:** <reason, value>
- **References:** <issues, logs, observations, or None>

## Scope

### In scope
- <item>

### Out of scope
- <item>

## Constraints and assumptions

- **Technical:** <platform, language, protocol, resource budget>
- **Compatibility:** <API, data, backward compatibility>
- **Security / privacy:** <constraints or Not applicable: reason>
- **Operational:** <deployment, uptime, offline use>
- **Assumptions to validate:** <assumption + how it will be validated>

## Added

### FR-<AREA>-<NNN> — <title>
- **Statement:** The system MUST <observable behaviour>.
- **Priority:** <Must | Should | Could>
- **Verification:** <Automated | Manual | Analysis | Inspection | Bench | HIL | Field>
- **Rationale:** <why, if not obvious>
- **Depends on:** <IDs or None>
- **Acceptance criteria:**
  - `FR-<AREA>-<NNN>.1`: Given <condition>, when <action>, then <observable result>.

### NFR-<AREA>-<NNN> — <title>
- **Statement:** The system MUST <measurable quality target>.
- **Priority:** <Must | Should | Could>
- **Verification:** <method>
- **Acceptance criteria:**
  - `NFR-<AREA>-<NNN>.1`: <metric, threshold and measurement conditions>.

## Modified

### FR-<AREA>-<NNN> — <title>
- **Statement:** <new statement>
- **Previous statement:** <statement as currently in the living spec>
- **Reason:** <why it changes>
- **Priority:** <Must | Should | Could>
- **Verification:** <method>
- **Acceptance criteria:**
  - `FR-<AREA>-<NNN>.1`: <criterion>

## Retired

### FR-<AREA>-<NNN> — <title>
- **Reason:** <why it no longer applies>

## Open questions

### Q-<NNNN>-01 — <short question>
- **Question:** <full question>
- **Impact:** <Low | Medium | High>
- **Status:** Open
- **Answer:** <filled when Answered or Accepted risk>

## Change history

| Date | Change | Reason | Author / approver |
|---|---|---|---|
| YYYY-MM-DD | Initial draft | <reason> | <role> |
