"""PersonaSpec extensibility acceptance: registering a fake
use case with a never-before-seen persona MUST surface through
the catalog endpoints WITHOUT any source edit (no schema
change, no frontend constant edit, no enum widening).

The generic ``/api/sim/use-cases/<fake_lane>/personas`` endpoint
validates the contract. RawFish intentionally keeps personas lane-scoped.

The frontend cockpit renders backend-supplied rows directly, so backend
registration remains the source of truth for lane personas.
contract end-to-end without changing the legacy
:class:`Persona` enum (which remains closed for the policy lane
specifically; the platform itself accepts any persona id any
adapter publishes).
"""
from __future__ import annotations

import pytest

from app.simulation_platform import registry as platform_registry
from app.simulation_platform.catalog import PersonaSpec
from app.simulation_platform.tests_helpers import build_fake_use_case
from tests._helpers import catalog_payload, make_app


@pytest.fixture
def app(monkeypatch):
    return make_app(monkeypatch, AUTH_ENABLED="false")


def _registry_snapshot():
    return dict(platform_registry._REGISTRY)  # noqa: SLF001


def _registry_restore(snapshot):
    platform_registry._REGISTRY.clear()  # noqa: SLF001
    platform_registry._REGISTRY.update(snapshot)  # noqa: SLF001


def test_fake_persona_surfaces_through_generic_endpoint(app):
    """Acceptance pin: a fresh adapter publishing a brand-new
    persona id (``arbitrator``) lands on the generic
    ``/api/sim/use-cases/<id>/personas`` lane endpoint with no
    edits to the policy schema, persona enum, or frontend
    constants required for the data to be wire-visible.
    """
    snapshot = _registry_snapshot()
    try:
        fake = build_fake_use_case(
            uid="dispute_resolution_lane",
            catalog_payload=catalog_payload(
                use_case_id="dispute_resolution_lane",
                personas=[
                    PersonaSpec(
                        persona_id="arbitrator",
                        label="Arbitrator",
                        cohort_question="Whose interests does this serve?",
                        segment_types_by_scope={
                            None: ["claimant_party", "respondent_party"],
                        },
                    ),
                ],
            ),
        )
        platform_registry.register(fake)
        client = app.test_client()

        generic = client.get(
            "/api/sim/use-cases/dispute_resolution_lane/personas"
        )
        assert generic.status_code == 200
        generic_data = generic.get_json()["data"]
        assert generic_data["use_case_id"] == "dispute_resolution_lane"
        ids = {p["persona"] for p in generic_data["personas"]}
        assert ids == {"arbitrator"}
    finally:
        _registry_restore(snapshot)


def test_segment_types_for_fake_persona_appear_in_denormalised_index(app):
    """The lane endpoint also enriches the per-persona
    segment-type index with the fake adapter's allowlist -- so a
    frontend picker for the new lane gets the right options out
    of the box without any frontend code change.
    """
    snapshot = _registry_snapshot()
    try:
        fake = build_fake_use_case(
            uid="dispute_resolution_lane_segments",
            catalog_payload=catalog_payload(
                use_case_id="dispute_resolution_lane_segments",
                personas=[
                    PersonaSpec(
                        persona_id="mediator",
                        label="Mediator",
                        cohort_question="Where is the deal value?",
                        segment_types_by_scope={
                            None: ["high_trust_party", "low_trust_party"],
                        },
                    ),
                ],
            ),
        )
        platform_registry.register(fake)
        client = app.test_client()
        res = client.get(
            "/api/sim/use-cases/dispute_resolution_lane_segments/personas"
        )
        assert res.status_code == 200, res.get_data(as_text=True)
        data = res.get_json()["data"]
        seg = data["segment_types_by_persona"].get("mediator")
        assert seg is not None
        assert set(seg) == {"high_trust_party", "low_trust_party"}
    finally:
        _registry_restore(snapshot)
