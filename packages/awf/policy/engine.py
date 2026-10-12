"""Permission / Policy Engine: RBAC (role permissions) + action level policy (Plan §14)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Any

import yaml


class Level(IntEnum):
    AUTO = 0
    AUTO_NOTIFY = 1
    APPROVAL_REQUIRED = 2
    MANUAL = 3


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    ALLOW_NOTIFY = "ALLOW_NOTIFY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    verdict: Verdict
    level: int | None
    reason: str

    @property
    def allowed(self) -> bool:
        return self.verdict in (Verdict.ALLOW, Verdict.ALLOW_NOTIFY)

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action, "verdict": self.verdict.value, "level": self.level, "reason": self.reason}


@dataclass
class PolicyConfig:
    actions: dict[str, dict[str, Any]]
    command_allowlist: list[str] = field(default_factory=list)
    limits: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> PolicyConfig:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PolicyConfig:
        return cls(
            actions=data.get("actions", {}),
            command_allowlist=list(data.get("command_allowlist", [])),
            limits=dict(data.get("limits", {})),
        )

    def limit(self, name: str, default: int) -> int:
        return int(self.limits.get(name, default))


def has_permission(permissions: dict[str, Any], dotted: str) -> bool:
    node: Any = permissions
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    if isinstance(node, dict):
        return any(bool(v) for v in node.values())
    return bool(node)


class PolicyEngine:
    def __init__(self, config: PolicyConfig):
        self.config = config

    def evaluate(self, action: str, permissions: dict[str, Any],
                 approved_actions: set[str] | None = None) -> PolicyDecision:
        rule = self.config.actions.get(action)
        if rule is None:
            return PolicyDecision(action, Verdict.DENY, None, "unknown action (default deny)")
        level = int(rule.get("level", Level.MANUAL))
        needed = rule.get("permission")
        if needed and not has_permission(permissions, needed):
            return PolicyDecision(action, Verdict.DENY, level, f"role lacks permission '{needed}'")
        if level == Level.AUTO:
            return PolicyDecision(action, Verdict.ALLOW, level, "auto")
        if level == Level.AUTO_NOTIFY:
            return PolicyDecision(action, Verdict.ALLOW_NOTIFY, level, "auto with notification")
        if level == Level.APPROVAL_REQUIRED:
            if approved_actions and action in approved_actions:
                return PolicyDecision(action, Verdict.ALLOW, level, "covered by human approval")
            return PolicyDecision(action, Verdict.REQUIRE_APPROVAL, level, "human approval required")
        return PolicyDecision(action, Verdict.DENY, level, "manual execution only")

    def command_allowed(self, argv: list[str]) -> bool:
        if not argv:
            return False
        # Normalise separators so a Windows path is recognised the same way on every OS.
        exe = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        for suffix in (".exe", ".cmd", ".bat"):
            if exe.endswith(suffix):
                exe = exe[: -len(suffix)]
        return exe in {c.lower() for c in self.config.command_allowlist}
