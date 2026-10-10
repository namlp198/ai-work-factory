# ADR-001 — Artifact-driven orchestration

- **Status:** Accepted (Phase 0)
- **Date:** 2026-10-10

## Context
Multi-agent systems built as free-form chats are hard to audit, loop indefinitely and lose context between roles.

## Decision
Agents hand off work only through **versioned artifacts + task state**. An agent receives a task, a bounded
context built from upstream artifacts, and must finish with a single `submit_result` call that satisfies its
contract. There is no agent-to-agent chat.

## Consequences
- Every output is an `artifacts` row (type, version, sha256 checksum, provenance) with `artifact_relations` edges.
- The agent loop is bounded by `timeout_policy.max_turns` / `seconds`.
- Implementation: `awf/agent_runtime/runtime.py`, `awf/orchestrator/artifacts.py`, `awf/orchestrator/context.py`.
