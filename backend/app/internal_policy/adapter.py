"""RawFish internal-policy lane adapter."""

from __future__ import annotations

from app.policy.adapter import PolicyUseCase
from app.policy_core.lane_identity import INTERNAL_POLICY


class InternalPolicyUseCase(PolicyUseCase):
    lane = INTERNAL_POLICY


INTERNAL_POLICY_USE_CASE: InternalPolicyUseCase = InternalPolicyUseCase()


__all__ = ["INTERNAL_POLICY_USE_CASE", "InternalPolicyUseCase"]
