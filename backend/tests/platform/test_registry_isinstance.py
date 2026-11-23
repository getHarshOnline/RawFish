"""Registry enforces ``isinstance(uc, SimulationUseCase)`` at registration.

Without this check, an adapter that forgets to declare one of the
Protocol's now-required attributes (notably ``report_schema`` and
``guardrail_capabilities``) silently slips into the registry; the
AttributeError fires at the first request that reads the missing
attribute. The pin pulls the failure forward to import-time where it
is easy to fix.
"""
from __future__ import annotations

import pytest

from app.simulation_platform import registry as platform_registry
from app.simulation_platform.tests_helpers import build_fake_use_case


def _registry_snapshot() -> dict:
    return dict(platform_registry._REGISTRY)  # type: ignore[attr-defined]


def _registry_restore(snap: dict) -> None:
    platform_registry._REGISTRY.clear()  # type: ignore[attr-defined]
    platform_registry._REGISTRY.update(snap)  # type: ignore[attr-defined]


class _MalformedAdapter:
    """Bare adapter missing many Protocol members; ``isinstance`` against
    :class:`SimulationUseCase` must return False."""

    def use_case_id(self) -> str:
        return "malformed_lane"


def test_register_rejects_adapter_missing_protocol_members():
    snap = _registry_snapshot()
    try:
        with pytest.raises(TypeError) as excinfo:
            platform_registry.register(_MalformedAdapter())  # type: ignore[arg-type]
        msg = str(excinfo.value)
        # Error names the offender class.
        assert "_MalformedAdapter" in msg
        # Error names AT LEAST the structural members the dispatch
        # layer reads -- contributors should not have to grep the
        # Protocol to know what's missing.
        for member in ("catalog", "validate_scenario", "run_simulation",
                       "generate_report", "artifact_names",
                       "report_schema", "guardrail_capabilities"):
            assert member in msg, (
                f"register() error must name missing member {member!r}; "
                f"got: {msg!r}"
            )
    finally:
        _registry_restore(snap)


def test_register_accepts_fake_use_case_satisfying_protocol():
    """The platform-supplied FakeUseCase satisfies every Protocol
    member -- registration must succeed."""
    snap = _registry_snapshot()
    try:
        # No raise. The fake declares run_simulation, run_sensitivity,
        # report_schema (=None), guardrail_capabilities (=None) etc.
        platform_registry.register(build_fake_use_case(uid="protocol_pin_lane"))
    finally:
        _registry_restore(snap)


def test_register_accepts_internal_policy_adapter():
    from app.internal_policy.adapter import INTERNAL_POLICY_USE_CASE

    snap = _registry_snapshot()
    try:
        platform_registry.register(INTERNAL_POLICY_USE_CASE)
    finally:
        _registry_restore(snap)


def test_register_accepts_creator_adapter():
    from app.creator.adapter import CREATOR_USE_CASE

    snap = _registry_snapshot()
    try:
        platform_registry.register(CREATOR_USE_CASE)
    finally:
        _registry_restore(snap)
