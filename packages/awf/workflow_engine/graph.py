"""Stage graph: validation, topological order, ancestry."""

from __future__ import annotations

from collections import defaultdict, deque

from awf.workflow_engine.schema import WorkflowSpec


class WorkflowValidationError(ValueError):
    pass


class StageGraph:
    def __init__(self, spec: WorkflowSpec):
        self.spec = spec
        self.ids = [s.id for s in spec.stages]
        self.deps: dict[str, list[str]] = {s.id: list(s.depends_on) for s in spec.stages}
        self.children: dict[str, list[str]] = defaultdict(list)
        for sid, deps in self.deps.items():
            for dep in deps:
                self.children[dep].append(sid)
        self.order = self._validate()

    def _validate(self) -> list[str]:
        seen: set[str] = set()
        for sid in self.ids:
            if sid in seen:
                raise WorkflowValidationError(f"duplicate stage id '{sid}'")
            seen.add(sid)
        for sid, deps in self.deps.items():
            for dep in deps:
                if dep not in seen:
                    raise WorkflowValidationError(f"stage '{sid}' depends on unknown stage '{dep}'")
                if dep == sid:
                    raise WorkflowValidationError(f"stage '{sid}' depends on itself")

        indegree = {sid: len(deps) for sid, deps in self.deps.items()}
        queue = deque(sid for sid in self.ids if indegree[sid] == 0)
        order: list[str] = []
        while queue:
            sid = queue.popleft()
            order.append(sid)
            for child in self.children[sid]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(order) != len(self.ids):
            cyclic = sorted(set(self.ids) - set(order))
            raise WorkflowValidationError(f"stage dependency cycle among {cyclic}")

        for stage in self.spec.stages:
            loop = stage.on_changes_required
            if loop:
                if loop.goto not in seen:
                    raise WorkflowValidationError(f"stage '{stage.id}': fix loop target '{loop.goto}' unknown")
                if loop.goto != stage.id and loop.goto not in self.ancestors(stage.id):
                    raise WorkflowValidationError(
                        f"stage '{stage.id}': fix loop target '{loop.goto}' must be an upstream stage"
                    )
        return order

    def ancestors(self, stage_id: str) -> set[str]:
        result: set[str] = set()
        stack = list(self.deps[stage_id])
        while stack:
            sid = stack.pop()
            if sid not in result:
                result.add(sid)
                stack.extend(self.deps[sid])
        return result

    def descendants(self, stage_id: str) -> set[str]:
        result: set[str] = set()
        stack = list(self.children[stage_id])
        while stack:
            sid = stack.pop()
            if sid not in result:
                result.add(sid)
                stack.extend(self.children[sid])
        return result

    def roots(self) -> list[str]:
        return [sid for sid in self.order if not self.deps[sid]]

    def loop_span(self, goto: str, current: str) -> list[str]:
        """Stages re-run by a fix loop: `goto`, `current`, and everything between them."""
        span = ({goto} | self.descendants(goto)) & (self.ancestors(current) | {current})
        return [sid for sid in self.order if sid in span]
