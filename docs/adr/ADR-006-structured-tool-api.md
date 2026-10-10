# ADR-006 — Structured Tool API over unrestricted shell

- **Status:** Accepted (Phase 0)

## Decision
Agents call typed tools (`fs.*`, `git.*`, `build.run`, `test.run`, `lint.run`). Every call goes through
JSON-schema validation and the Policy Engine (`config/policies.yaml`), and is recorded. Build/test/lint commands
come from project configuration, never from model output, and must be in the command allowlist. `shell.exec`
exists but is level 3 (denied) by default.

## Consequences
- Path traversal protection, `.git` protection, timeouts, output limits and secret redaction are enforced in one place.
- Tool output is redacted before it reaches a model.
- Completion evidence (build/test results) is produced by the platform and cannot be faked by the agent.
