"""Helpers for policy schema scope values."""

from __future__ import annotations

from typing import Any, Optional

from app.policy.persona_defaults import resolve_product_drift_scope
from app.policy.schema import Persona, PolicySurface, Scenario


def scenario_policy_scope(scenario: Scenario) -> Optional[PolicySurface]:
    """Return the schema scope that backs shared policy tables."""

    if scenario.persona == Persona.POLICY:
        return scenario.scope
    if scenario.persona == Persona.PRODUCT:
        domain_context = scenario.preset_overrides.get("domain_context")
        surface = (
            domain_context.get("surface")
            if isinstance(domain_context, dict)
            else None
        )
        return resolve_product_drift_scope(surface)
    return None


def vocabulary_scope(scenario: Scenario) -> Optional[PolicySurface]:
    """Return the scope key used by policy vocabulary/defaults tables."""

    if scenario.persona == Persona.POLICY:
        return scenario.scope
    return None


def scope_wire_value(scope: Any) -> str:
    """Render a schema scope or plain value for prompts and diagnostics."""

    if scope is None:
        return "n/a"
    value = getattr(scope, "value", None)
    return str(value if value is not None else scope)
