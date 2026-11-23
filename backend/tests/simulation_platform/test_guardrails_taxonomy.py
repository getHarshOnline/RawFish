"""Pin every platform-level guardrail taxonomy frozenset value-for-value.

The frozensets in
:mod:`app.simulation_platform.guardrails.taxonomy` are the canonical
allowlists every lane validates against. Any drift here is a wire-
contract break that would let one lane emit a value the other lane
silently rejects; pinning the exact membership in a test means a
member added to (or removed from) any frozenset trips a loud failure
in CI before the change can ship.
"""
from __future__ import annotations

from app.simulation_platform.guardrails.taxonomy import (
    CONFIDENCE_BANDS,
    EVIDENCE_REF_KINDS,
    HYPOTHESIS_TAGS,
    SEVERITY_VARIANTS,
    VALIDITY_FLAG_KINDS,
)


def test_hypothesis_tags_are_the_canonical_five() -> None:
    """The 5-tag epistemic taxonomy is the product's non-negotiable contract."""
    assert HYPOTHESIS_TAGS == frozenset({
        "source_grounded",
        "simulated_observation",
        "inference",
        "assumption",
        "requires_validation",
    })
    assert len(HYPOTHESIS_TAGS) == 5


def test_validity_flag_kinds_are_the_canonical_nine() -> None:
    """The 9-kind validity-flag taxonomy mirrors the paradox-hook fields."""
    assert VALIDITY_FLAG_KINDS == frozenset({
        "power_law_tail",
        "monte_carlo_coverage",
        "survivorship_or_cohort_gap",
        "observer_effect",
        "simpson_aggregate_disagreement",
        "mean_reversion",
        "utility_curve",
        "confirmation_bias",
        "selection_axis",
    })
    assert len(VALIDITY_FLAG_KINDS) == 9


def test_severity_variants_are_the_three_step_ladder() -> None:
    assert SEVERITY_VARIANTS == frozenset({"info", "notice", "warning"})
    assert len(SEVERITY_VARIANTS) == 3


def test_confidence_bands_are_the_categorical_three() -> None:
    assert CONFIDENCE_BANDS == frozenset({"low", "advisory", "source-grounded"})
    assert len(CONFIDENCE_BANDS) == 3


def test_evidence_ref_kinds_mirror_the_policy_lane() -> None:
    """The 4 evidence-ref kinds mirror the policy lane's Literal exactly."""
    assert EVIDENCE_REF_KINDS == frozenset({
        "simulation_action",
        "seed_text_line",
        "agent_interview",
        "external_source",
    })
    assert len(EVIDENCE_REF_KINDS) == 4


def test_all_taxonomies_are_frozensets_of_strings() -> None:
    """Defense-in-depth: callers may iterate / hash; ensure type stability."""
    for taxonomy in (
        HYPOTHESIS_TAGS,
        VALIDITY_FLAG_KINDS,
        SEVERITY_VARIANTS,
        CONFIDENCE_BANDS,
        EVIDENCE_REF_KINDS,
    ):
        assert isinstance(taxonomy, frozenset)
        assert all(isinstance(v, str) for v in taxonomy)
        assert all(v == v.strip() and v for v in taxonomy)
