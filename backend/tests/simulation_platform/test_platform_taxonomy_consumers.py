"""Pin that BOTH lane Claim models validate ``tag`` against the SAME
platform :data:`HYPOTHESIS_TAGS` source.

The policy lane's :class:`app.policy.schema.Claim` uses the
:class:`app.policy.hypothesis_tags.HypothesisTag` enum; the creator
lane's :class:`app.creator.report_generator.Claim`
uses a plain frozenset re-exported under the legacy name
``CANONICAL_CLAIM_TAGS``. Both must derive from the SAME platform
frozenset so the wire allowlist cannot drift between lanes.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.simulation_platform.guardrails.taxonomy import HYPOTHESIS_TAGS


def test_policy_canonical_set_equals_platform_taxonomy() -> None:
    """Policy's canonical hypothesis-tag frozenset == platform frozenset."""
    from app.policy.hypothesis_tags import CANONICAL_HYPOTHESIS_TAGS

    assert CANONICAL_HYPOTHESIS_TAGS == HYPOTHESIS_TAGS


def test_creator_canonical_set_is_the_platform_taxonomy() -> None:
    """Creator's CANONICAL_CLAIM_TAGS IS the platform frozenset (alias)."""
    from app.creator.report_generator import (
        CANONICAL_CLAIM_TAGS,
    )

    assert CANONICAL_CLAIM_TAGS == HYPOTHESIS_TAGS
    # Identity check: the creator lane re-exports the platform constant
    # directly, so a single object backs both names.
    assert CANONICAL_CLAIM_TAGS is HYPOTHESIS_TAGS


def test_policy_claim_accepts_every_platform_tag() -> None:
    """Every platform-taxonomy value parses on the policy Claim."""
    from app.policy.schema import Claim

    for tag in sorted(HYPOTHESIS_TAGS):
        c = Claim(claim_id=f"c_{tag}", text=f"text {tag}", tag=tag)  # type: ignore[arg-type]
        assert c.tag.value == tag


def test_creator_claim_accepts_every_platform_tag() -> None:
    """Every platform-taxonomy value parses on the creator Claim."""
    from app.creator.report_generator import Claim

    for tag in sorted(HYPOTHESIS_TAGS):
        c = Claim(claim_id=f"c_{tag}", text=f"text {tag}", tag=tag)
        assert c.tag == tag


def test_policy_claim_rejects_value_outside_platform_taxonomy() -> None:
    """Anything outside HYPOTHESIS_TAGS is refused on the policy Claim."""
    from app.policy.schema import Claim

    with pytest.raises(ValidationError):
        Claim(claim_id="c1", text="x", tag="not_a_tag")  # type: ignore[arg-type]


def test_creator_claim_rejects_value_outside_platform_taxonomy() -> None:
    """Anything outside HYPOTHESIS_TAGS is refused on the creator Claim."""
    from app.creator.report_generator import Claim

    with pytest.raises(ValidationError):
        Claim(claim_id="c1", text="x", tag="not_a_tag")
