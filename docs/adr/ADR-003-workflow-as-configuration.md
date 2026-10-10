# ADR-003 — Workflow as configuration

- **Status:** Accepted (Phase 0)

## Decision
Workflows are YAML files in `workflows/` validated by a strict Pydantic schema plus a graph check (unknown
stages, cycles, invalid fix-loop targets, unknown roles/tools). The orchestrator contains no pipeline logic.

Supported stage features: `role`, `tool`, `parallel`, `foreach: planned_task`, `depends_on`, `gate`
(human approval), `on_changes_required` (fix loop with `goto` + `max_iterations`), `relation`, `worker`
requirements, `max_attempts`. Workflows with `status: draft` may reference roles that do not exist yet.

## Consequences
New pipelines (content, video) are added as files; the engine only grows when a new *kind* of step is needed.
