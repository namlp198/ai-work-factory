# ADR-002 — PostgreSQL as initial system of record

- **Status:** Accepted (Phase 0)

## Context
State, metadata and relations must be transactional and queryable. Graph databases add operational cost.

## Decision
PostgreSQL (with pgvector available for Phase 3) stores all state, metadata and artifact relations.
SQLite is supported for local development and tests through the same SQLAlchemy models. Schema changes go
through Alembic (`infrastructure/migrations`). No Neo4j.

## Consequences
- Traceability/impact analysis is a recursive walk over `artifact_relations` (`ArtifactService.impact`).
- On PostgreSQL a trigger makes `audit_events` append-only.
- Revisit graph DB only when relation queries become a bottleneck (Design §27).
