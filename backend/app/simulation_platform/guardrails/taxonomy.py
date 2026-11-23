"""Platform-level methodological-guardrail taxonomies.

This module is the canonical home for the closed taxonomies every lane
shares when emitting methodological guardrails (hypothesis tags on
claims, validity-flag kinds, severity / confidence vocabularies,
evidence-ref kinds). Each lane's own Pydantic models import these
frozensets and validate against them so the wire-level allowlists
cannot drift between lanes.

The values mirror what the policy lane has historically owned in
:mod:`app.policy.schema` and :mod:`app.policy.hypothesis_tags`; lifting
them here lets the creator lane (and any future lane) consume the
same taxonomy without importing ``app.policy.*``.

Editing any frozenset in this module is a SCHEMA-LEVEL BREAKING
CHANGE. Add a new value only when every consuming lane (policy +
creator + future) can simultaneously accept it.
"""
from __future__ import annotations


# 5-value provenance taxonomy attached to individual report claims.
# Mirrors :class:`app.policy.hypothesis_tags.HypothesisTag` value-for-value.
HYPOTHESIS_TAGS: frozenset[str] = frozenset({
    "source_grounded",
    "simulated_observation",
    "inference",
    "assumption",
    "requires_validation",
})


# 9-value closed taxonomy of methodological validity-flag kinds the
# transparency post-processor may emit against the canonical paradox-
# hook schema fields. Mirrors the inline Literal[...] on
# :class:`app.policy.schema.ValidityFlag.kind`.
VALIDITY_FLAG_KINDS: frozenset[str] = frozenset({
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


# Severity vocabulary surfaced on platform-level ValidityFlag /
# GuardrailFinding records. The three-step ladder
# (info -> notice -> warning) mirrors the policy Literal so the
# renderer can dispatch the same chrome.
SEVERITY_VARIANTS: frozenset[str] = frozenset({
    "info",
    "notice",
    "warning",
})


# Confidence-band vocabulary used by platform-level :class:`DriverHint`
# records. This is a NEW platform-only taxonomy; the policy lane's
# legacy ``attribution_confidence: Literal['low','medium','high']``
# field is unrelated and lives on the policy model. The three bands
# correspond to the categorical (not numeric) confidence convention
# the platform models adopt.
CONFIDENCE_BANDS: frozenset[str] = frozenset({
    "low",
    "advisory",
    "source-grounded",
})


# 4-value closed taxonomy of evidence-ref kinds. Mirrors the inline
# Literal on :class:`app.policy.schema.EvidenceRef.ref_type` (the
# policy model uses the field name ``ref_type``; the platform model
# uses ``ref_kind``, but the allowed values are identical).
EVIDENCE_REF_KINDS: frozenset[str] = frozenset({
    "simulation_action",
    "seed_text_line",
    "agent_interview",
    "external_source",
})


__all__ = [
    "HYPOTHESIS_TAGS",
    "VALIDITY_FLAG_KINDS",
    "SEVERITY_VARIANTS",
    "CONFIDENCE_BANDS",
    "EVIDENCE_REF_KINDS",
]
