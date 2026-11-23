"""Canonical HypothesisTag / ExpectedRiskTag taxonomies.

:class:`HypothesisTag` is the 5-value provenance taxonomy attached to
individual REPORT CLAIMS by the report renderer (simulated_observation,
inference, source_grounded, assumption, requires_validation).
:class:`ExpectedRiskTag` is the allowlist of risk hypotheses a SCENARIO
PRESET author declares the simulation should stress-test (``H-*``).
Both are part of the report/preset contract: editing them is a breaking
change.
"""

from __future__ import annotations

from enum import Enum


class HypothesisTag(str, Enum):
    """5-value provenance taxonomy for individual report claims."""

    SIMULATED_OBSERVATION = "simulated_observation"
    INFERENCE = "inference"
    SOURCE_GROUNDED = "source_grounded"
    ASSUMPTION = "assumption"
    REQUIRES_VALIDATION = "requires_validation"


CANONICAL_HYPOTHESIS_TAGS = frozenset(t.value for t in HypothesisTag)


class ExpectedRiskTag(str, Enum):
    """Author-declared risk hypotheses a preset stress-tests.

    Allowlist enumerated from ``scenarios/presets/``; adding an ``H-*``
    requires editing this enum so the contract stays explicit.
    """

    H_RETENTION = "H-RETENTION"
    H_PARASOCIAL = "H-PARASOCIAL"
    H_CHURN = "H-CHURN"
    H_BACKLASH = "H-BACKLASH"
    H_ALGO_DEMOTION = "H-ALGO-DEMOTION"
    H_COHORT_SPLIT = "H-COHORT-SPLIT"
    H_DISSENT = "H-DISSENT"
    H_COMPLIANCE = "H-COMPLIANCE"
    H_MOBILIZATION = "H-MOBILIZATION"
    H_COALITION_FRAGMENT = "H-COALITION-FRAGMENT"
    H_PRESS_CYCLE = "H-PRESS-CYCLE"
    H_REGULATORY_RISK = "H-REGULATORY-RISK"
    H_OVERCONFIDENCE = "H-OVERCONFIDENCE"
    H_SILENT_FAILURE = "H-SILENT-FAILURE"
    H_LOW_SALIENCE = "H-LOW-SALIENCE"
    H_COMMUNICATION_FAIL = "H-COMMUNICATION-FAIL"
    H_CHURN_LATENT = "H-CHURN-LATENT"


CANONICAL_EXPECTED_RISK_TAGS = frozenset(t.value for t in ExpectedRiskTag)


__all__ = [
    "HypothesisTag",
    "CANONICAL_HYPOTHESIS_TAGS",
    "ExpectedRiskTag",
    "CANONICAL_EXPECTED_RISK_TAGS",
]
