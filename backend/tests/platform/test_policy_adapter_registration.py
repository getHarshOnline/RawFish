"""Conformance tests for the split RawFish policy lanes."""
from __future__ import annotations

import pytest

from app.internal_policy.adapter import (
    INTERNAL_POLICY_USE_CASE,
    InternalPolicyUseCase,
)
from app.public_policy.adapter import (
    PUBLIC_POLICY_USE_CASE,
    PublicPolicyUseCase,
)
from app.policy.schema import PERSONA_SEGMENT_TYPES, Persona, PolicySurface
from app.simulation_platform.registry import get, register


_LANES = [
    ("internal_policy", INTERNAL_POLICY_USE_CASE, InternalPolicyUseCase, PolicySurface.INTERNAL_ORG),
    ("public_policy", PUBLIC_POLICY_USE_CASE, PublicPolicyUseCase, PolicySurface.PUBLIC_POLICY),
]


def _ensure_registered() -> None:
    for _, lane, _, _ in _LANES:
        register(lane)


@pytest.mark.parametrize("lane_id,lane,cls,_scope", _LANES)
def test_policy_use_cases_registered_under_split_ids(
    lane_id, lane, cls, _scope,
) -> None:
    _ensure_registered()
    looked_up = get(lane_id)
    assert looked_up is lane
    assert isinstance(looked_up, cls)


@pytest.mark.parametrize("lane_id,lane,_cls,scope", _LANES)
def test_catalog_publishes_one_fixed_scope_policy_persona(
    lane_id, lane, _cls, scope,
) -> None:
    catalog = lane.catalog()
    assert catalog.use_case_id == lane_id
    assert catalog.lane_manifest.lane_id == lane_id

    persona_ids = [p.persona_id for p in catalog.personas]
    assert persona_ids == ["policy"]

    policy_spec = catalog.personas[0]
    assert policy_spec.use_case_id == lane_id
    assert policy_spec.scopes is None
    assert list(policy_spec.segment_types_by_scope.keys()) == [None]
    assert policy_spec.segment_types_by_scope[None] == sorted(
        PERSONA_SEGMENT_TYPES[(Persona.POLICY, scope)]
    )


@pytest.mark.parametrize("_lane_id,lane,_cls,_scope", _LANES)
def test_policy_artifact_names_exact_mapping(_lane_id, lane, _cls, _scope) -> None:
    names = lane.artifact_names()
    assert names == {
        "report_json": "policy_report.json",
        "report_md": "policy_report.md",
        "sensitivity": "sensitivity.json",
    }


def test_internal_and_public_policy_lanes_publish_distinct_report_sections():
    internal = INTERNAL_POLICY_USE_CASE.catalog().personas[0]
    public = PUBLIC_POLICY_USE_CASE.catalog().personas[0]
    assert internal.report_sections_by_scope[None]
    assert public.report_sections_by_scope[None]
    assert (
        internal.report_sections_by_scope[None]
        != public.report_sections_by_scope[None]
    )
