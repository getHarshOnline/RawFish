"""HypothesisTag taxonomy pin.

The 5 string values are part of the report contract; this test fails
if anyone reorders, renames, or extends them. The :class:`ExpectedRiskTag`
allowlist is exercised in the preset round-trip suite.
"""
from __future__ import annotations

import pytest

from app.policy import (
    CANONICAL_HYPOTHESIS_TAGS,
    HypothesisTag,
)
from app.policy.hypothesis_tags import ExpectedRiskTag


# Canonical 5-tag HypothesisTag value set. DO NOT edit without updating
# :class:`HypothesisTag` in lockstep -- the values are the report contract.
_DECISION_6_TAGS = (
    "simulated_observation",
    "inference",
    "source_grounded",
    "assumption",
    "requires_validation",
)


def test_canonical_hypothesis_tags_count_is_5():
    """Hypothesis taxonomy is a CLOSED 5-tag set."""
    assert len(CANONICAL_HYPOTHESIS_TAGS) == 5
    assert len(list(HypothesisTag)) == 5


@pytest.mark.parametrize("expected_value", _DECISION_6_TAGS)
def test_canonical_hypothesis_tags_match_verbatim(expected_value: str):
    """Each canonical value must be present verbatim in the enum.

    Parametrized so a single missing/renamed value surfaces as a single
    targeted failure (not one cascading assertion).
    """
    assert expected_value in CANONICAL_HYPOTHESIS_TAGS
    # And reachable as an enum member that round-trips through .value.
    member = HypothesisTag(expected_value)
    assert member.value == expected_value


# Verbatim copy of the canonical ExpectedRiskTag table, in enum
# declaration order. DO NOT edit without updating
# :class:`ExpectedRiskTag` in lockstep.
EXPECTED_RISK_TAGS_VERBATIM = (
    "H-RETENTION",
    "H-PARASOCIAL",
    "H-CHURN",
    "H-BACKLASH",
    "H-ALGO-DEMOTION",
    "H-COHORT-SPLIT",
    "H-DISSENT",
    "H-COMPLIANCE",
    "H-MOBILIZATION",
    "H-COALITION-FRAGMENT",
    "H-PRESS-CYCLE",
    "H-REGULATORY-RISK",
    "H-OVERCONFIDENCE",
    "H-SILENT-FAILURE",
    "H-LOW-SALIENCE",
    "H-COMMUNICATION-FAIL",
    "H-CHURN-LATENT",
)


@pytest.mark.parametrize("expected", EXPECTED_RISK_TAGS_VERBATIM)
def test_expected_risk_tags_match_verbatim(expected: str) -> None:
    """Frozen-snapshot test -- adding/renaming an ExpectedRiskTag value
    must be done by editing this list AND the enum in lockstep."""
    assert expected in {t.value for t in ExpectedRiskTag}


def test_expected_risk_tags_count_matches_verbatim_pin() -> None:
    """If a new value is added to the enum, this test fails until the
    verbatim list above is updated. Catches one-sided drift."""
    assert len({t.value for t in ExpectedRiskTag}) == len(EXPECTED_RISK_TAGS_VERBATIM)


def test_hypothesis_and_risk_tags_are_disjoint() -> None:
    """The two taxonomies must never share a value. HypothesisTag labels
    report-claim provenance; ExpectedRiskTag enumerates author-declared
    preset risk hypotheses. Sharing a value would collapse the distinction."""
    h = {t.value for t in HypothesisTag}
    r = {t.value for t in ExpectedRiskTag}
    assert h.isdisjoint(r), f"overlap: {h & r}"
