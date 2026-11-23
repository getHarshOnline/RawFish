"""RawFish public-policy lane adapter."""

from __future__ import annotations

from app.policy.adapter import PolicyUseCase
from app.policy_core.lane_identity import PUBLIC_POLICY


class PublicPolicyUseCase(PolicyUseCase):
    lane = PUBLIC_POLICY


PUBLIC_POLICY_USE_CASE: PublicPolicyUseCase = PublicPolicyUseCase()


__all__ = ["PUBLIC_POLICY_USE_CASE", "PublicPolicyUseCase"]
