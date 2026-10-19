"""Rule-based model router (Plan §12, Design §3.5): task type, confidentiality, budget -> model route."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

MODEL_CLASSES = ("FAST", "BALANCED", "REASONING", "MULTIMODAL", "LOCAL_PRIVATE", "CODE_SPECIALIST")


@dataclass(frozen=True)
class RouteRequest:
    profile_class: str
    fallback_class: str | None = None
    task_type: str = "general"
    confidentiality: str = "internal"
    budget_remaining_usd: Decimal | None = None
    # Preferred provider for the run (e.g. "google"); None: the configured default_provider.
    provider: str | None = None


@dataclass
class ModelRoute:
    model_class: str
    provider: str
    model: str
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ModelRouter:
    def __init__(self, config: dict[str, Any], provider_override: str = ""):
        self.config = config
        self.classes: dict[str, dict[str, Any]] = config.get("classes", {})
        self.pricing: dict[str, dict[str, float]] = config.get("pricing", {})
        self.provider_override = provider_override
        self.default_provider: str = config.get("default_provider") or "anthropic"
        self.alternatives: dict[str, dict[str, dict[str, Any]]] = config.get("providers") or {}
        unknown = set(self.classes) - set(MODEL_CLASSES)
        for table in self.alternatives.values():
            unknown |= set(table) - set(MODEL_CLASSES)
        if unknown:
            raise ValueError(f"unknown model classes in routing config: {sorted(unknown)}")

    def providers(self) -> list[str]:
        """Providers a run may choose: the default one plus every alternative table."""
        return [self.default_provider, *[p for p in self.alternatives if p != self.default_provider]]

    @classmethod
    def load(cls, path: Path, provider_override: str = "") -> ModelRouter:
        with path.open("r", encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh) or {}, provider_override)

    def resolve(self, req: RouteRequest) -> ModelRoute:
        reasons: list[str] = []
        model_class = req.profile_class
        reasons.append(f"profile class {model_class}")

        rule = self.config.get("task_type_rules", {}).get(req.task_type)
        if rule:
            model_class = rule
            reasons.append(f"task type '{req.task_type}' -> {rule}")

        if req.confidentiality == "restricted":
            model_class = self.config.get("confidential_class", "LOCAL_PRIVATE")
            reasons.append(f"restricted project -> {model_class}")
        elif req.budget_remaining_usd is not None:
            threshold = Decimal(str(self.config.get("budget_downgrade_threshold_usd", 0)))
            if req.budget_remaining_usd < threshold:
                downgraded = self._downgrade(model_class)
                if downgraded != model_class:
                    reasons.append(f"budget ${req.budget_remaining_usd:.2f} < ${threshold} -> {downgraded}")
                    model_class = downgraded

        if model_class not in self.classes and req.fallback_class in self.classes:
            reasons.append(f"class {model_class} not configured, fallback {req.fallback_class}")
            model_class = req.fallback_class  # type: ignore[assignment]
        if model_class not in self.classes:
            raise ValueError(f"no route configured for model class '{model_class}'")

        entry = dict(self.classes[model_class])
        chosen = req.provider or self.default_provider
        if chosen not in self.providers():
            raise ValueError(f"unknown model provider '{chosen}' (configured: {', '.join(self.providers())})")
        if chosen != self.default_provider and entry.get("provider") != "unavailable":
            table = self.alternatives.get(chosen) or {}
            if model_class not in table:
                raise ValueError(f"no {chosen} model configured for class '{model_class}'")
            entry = {"provider": chosen, **table[model_class]}
            reasons.append(f"provider {chosen}")
        provider = entry.pop("provider")
        model = entry.pop("model")
        if self.provider_override and model_class != "LOCAL_PRIVATE":
            reasons.append(f"provider override '{self.provider_override}'")
            provider = self.provider_override
            model = "scripted" if self.provider_override == "scripted" else model
        return ModelRoute(model_class=model_class, provider=provider, model=model, params=entry,
                          reason="; ".join(reasons))

    def _downgrade(self, model_class: str) -> str:
        chain: list[str] = self.config.get("downgrade_chain", [])
        if model_class in chain:
            idx = chain.index(model_class)
            if idx + 1 < len(chain):
                return chain[idx + 1]
        return model_class

    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> Decimal:
        price = self.pricing.get(model)
        if not price:
            return Decimal("0")
        cost = (Decimal(str(price.get("input", 0))) * input_tokens
                + Decimal(str(price.get("output", 0))) * output_tokens) / Decimal(1_000_000)
        return cost.quantize(Decimal("0.000001"))
