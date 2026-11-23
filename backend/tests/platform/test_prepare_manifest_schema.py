"""Optional ``prepare_manifest_schema`` Protocol field contract.

A documented direction for the
:class:`app.simulation_platform.use_case.SimulationUseCase` Protocol
is to grow an optional ``prepare_manifest_schema: Optional[type]``
slot so the generic ``/prepare`` dispatch route can validate the
on-disk ``artifact_manifest.json`` shape via an adapter-declared
Pydantic class -- mirroring the ``report_schema`` slot that already
exists for the read-side ``/report`` route.

This test file pins three contracts. The third converts from a skip
into an active assertion when the Protocol field exists:

  1. The :class:`SimulationUseCase` Protocol exists and imports
     cleanly. The registry's structural dispatch depends on this;
     if the module breaks at import we want a loud failure here,
     not a misleading "no adapter declares it" skip.
  2. The module docstring documents the field's intended contract so
     a grep for ``prepare_manifest_schema`` lands on this file as
     the canonical pin.
  3. When the Protocol field exists, every registered adapter MUST
     declare it (``None`` is allowed); when it does not exist, the
     test skips with a pointer back to this docstring.
"""
from __future__ import annotations

import pytest


def test_simulation_use_case_protocol_imports_cleanly() -> None:
    """Sanity gate so a skip below cannot be masking a real import
    failure on the platform layer."""
    from app.simulation_platform.use_case import SimulationUseCase

    assert SimulationUseCase is not None


def test_prepare_manifest_schema_field_optional_today() -> None:
    """Pins the documented contract: ``prepare_manifest_schema`` is
    optional on the :class:`SimulationUseCase` Protocol.

    If the Protocol does NOT declare a ``prepare_manifest_schema``
    annotation, this test skips: no adapter is asked to wire one.

    If the Protocol DOES declare the annotation, every registered
    adapter must carry the attribute (``None`` is allowed; the
    attribute itself MUST be present so a future dispatch-layer call
    site that reads it via ``getattr`` does not need a defensive
    fallback). At least one adapter is expected to wire a real
    Pydantic class so the generic ``/prepare`` route can validate the
    on-disk manifest before persisting state.
    """
    from app.simulation_platform.use_case import SimulationUseCase

    has_field = "prepare_manifest_schema" in getattr(
        SimulationUseCase, "__annotations__", {},
    )
    if not has_field:
        pytest.skip(
            "SimulationUseCase.prepare_manifest_schema is not declared "
            "on the Protocol today; this test converts to a real "
            "attribute-presence assertion when the field exists."
        )

    # Reached only when the field is declared.
    from app.creator.adapter import CREATOR_USE_CASE
    from app.internal_policy.adapter import INTERNAL_POLICY_USE_CASE

    for adapter in (INTERNAL_POLICY_USE_CASE, CREATOR_USE_CASE):
        assert hasattr(adapter, "prepare_manifest_schema"), (
            f"adapter {adapter!r} must declare prepare_manifest_schema "
            "now that the Protocol slot exists (None is allowed; the "
            "attribute itself MUST be present for structural dispatch)"
        )


def test_prepare_manifest_schema_documented_on_protocol_when_present() -> None:
    """If the Protocol declares ``prepare_manifest_schema``, the
    declaration MUST carry a docstring covering the same shape as
    ``report_schema`` (the validated-on-read companion slot).

    The field is not declared today, so the body skips. This test
    gates against a silent attribute add that ships without
    documentation matching the ``report_schema`` pattern.
    """
    from app.simulation_platform.use_case import SimulationUseCase

    annotations = getattr(SimulationUseCase, "__annotations__", {})
    if "prepare_manifest_schema" not in annotations:
        pytest.skip(
            "prepare_manifest_schema is not declared on the Protocol "
            "today; this test gates the documentation contract that "
            "fires when the field is added."
        )

    # When the field exists, the Protocol's __doc__ MUST mention it.
    doc = SimulationUseCase.__doc__ or ""
    assert "prepare_manifest_schema" in doc, (
        "SimulationUseCase.__doc__ must mention prepare_manifest_schema "
        "(same documentation pattern as report_schema)"
    )
