"""Canonical identities for the RawFish policy lanes.

RawFish exposes separate policy lanes: ``internal_policy`` and
``public_policy``. Each lane also maps to the policy schema's ``scope``
field because the shared policy report/default tables are keyed by that
schema value. Adapters and the registry speak lane ids; schema scopes are
only a serialization detail at the policy-model boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class PolicyLaneDefinition:
    lane_id: str
    label: str
    summary: str
    schema_scope: str


INTERNAL_POLICY = PolicyLaneDefinition(
    lane_id="internal_policy",
    label="Internal Policy",
    summary="Pre-mortem cockpit for internal organizational policy.",
    schema_scope="internal_org",
)

PUBLIC_POLICY = PolicyLaneDefinition(
    lane_id="public_policy",
    label="Public Policy",
    summary="Pre-mortem cockpit for civic and public-policy stress tests.",
    schema_scope="public_policy",
)

POLICY_LANES_BY_ID: Mapping[str, PolicyLaneDefinition] = {
    INTERNAL_POLICY.lane_id: INTERNAL_POLICY,
    PUBLIC_POLICY.lane_id: PUBLIC_POLICY,
}

POLICY_LANES_BY_SCHEMA_SCOPE: Mapping[str, PolicyLaneDefinition] = {
    INTERNAL_POLICY.schema_scope: INTERNAL_POLICY,
    PUBLIC_POLICY.schema_scope: PUBLIC_POLICY,
}


def lane_for_id(lane_id: str) -> PolicyLaneDefinition:
    return POLICY_LANES_BY_ID[lane_id]


def lane_for_schema_scope(scope: str) -> PolicyLaneDefinition:
    return POLICY_LANES_BY_SCHEMA_SCOPE[scope]
