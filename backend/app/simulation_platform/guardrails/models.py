"""Platform-level Pydantic shapes for methodological guardrails.

These models are the lane-agnostic surface for claims, evidence
pointers, attribution hints, validity notes, guardrail findings, and
the aggregated report-level view. They exist so any lane can produce or
consume the same shapes without reaching into :mod:`app.policy.schema`.

Each model validates its taxonomy-bearing fields against the
frozensets in :mod:`.taxonomy`. The policy lane's richer in-house
models (with policy-specific extras like ``expected_risk_tags`` on
claims or ``field_validation_prompt`` on validity flags) keep their
own wire shape and are not subclasses of these platform models; they
do, however, validate against the SAME taxonomies via the frozensets
re-exported here -- a cross-import-time check in
:mod:`app.policy.schema` pins that lockstep.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .taxonomy import (
    CONFIDENCE_BANDS,
    EVIDENCE_REF_KINDS,
    HYPOTHESIS_TAGS,
    SEVERITY_VARIANTS,
    VALIDITY_FLAG_KINDS,
)


def _strict_config() -> ConfigDict:
    """Strict, extras-forbidden Pydantic config used by every model here."""
    return ConfigDict(populate_by_name=True, extra="forbid")


# ---------------------------------------------------------------------------
# EvidenceRef -- typed pointer into a run artifact backing a claim.
# ---------------------------------------------------------------------------


class EvidenceRef(BaseModel):
    """Platform-level evidence pointer.

    ``ref_kind`` is validated against
    :data:`app.simulation_platform.guardrails.taxonomy.EVIDENCE_REF_KINDS`.
    The policy lane's :class:`app.policy.schema.EvidenceRef` uses
    ``ref_type`` and only carries ``(ref_type, ref_id)``; the platform
    shape additionally carries human-facing ``source_title`` and
    ``link`` so a lane that doesn't ship a renderer can still surface
    a clickable artifact pointer.
    """

    model_config = _strict_config()

    ref_kind: str = Field(..., min_length=1)
    ref_id: str = Field(..., min_length=1)
    source_title: Optional[str] = Field(default=None)
    link: Optional[str] = Field(default=None)

    @field_validator("ref_kind")
    @classmethod
    def _ref_kind_in_taxonomy(cls, value: str) -> str:
        if value not in EVIDENCE_REF_KINDS:
            raise ValueError(
                f"EvidenceRef.ref_kind={value!r} is not in the canonical "
                f"EVIDENCE_REF_KINDS taxonomy "
                f"({sorted(EVIDENCE_REF_KINDS)!r})."
            )
        return value


# ---------------------------------------------------------------------------
# DriverHint -- per-claim attribution hint at the platform layer.
# ---------------------------------------------------------------------------


class DriverHint(BaseModel):
    """Platform-level attribution hint.

    Distinct shape from the policy lane's
    :class:`app.policy.schema.DriverHint`; the platform record is
    keyed by ``factor_id`` + signed ``delta`` and uses the categorical
    :data:`CONFIDENCE_BANDS` vocabulary. The two models coexist by
    design -- the policy lane keeps its richer driver-attribution
    record (with cooccurrence-method literal + disclaimer string),
    while platform consumers that only need a sortable factor list
    use this lighter shape.
    """

    model_config = _strict_config()

    factor_id: str = Field(..., min_length=1)
    delta: float = Field(...)
    confidence: str = Field(..., min_length=1)
    method: str = Field(..., min_length=1)

    @field_validator("confidence")
    @classmethod
    def _confidence_in_bands(cls, value: str) -> str:
        if value not in CONFIDENCE_BANDS:
            raise ValueError(
                f"DriverHint.confidence={value!r} is not in the canonical "
                f"CONFIDENCE_BANDS taxonomy "
                f"({sorted(CONFIDENCE_BANDS)!r})."
            )
        return value


# ---------------------------------------------------------------------------
# ClaimLike -- minimal claim shape any lane can produce.
# ---------------------------------------------------------------------------


class ClaimLike(BaseModel):
    """Lane-agnostic minimal claim shape.

    Carries the smallest surface every lane shares: identifier,
    rendered text, a canonical hypothesis tag, optional segment
    pointers (Simpson's-Paradox hook), optional typed evidence
    pointers, and an optional numeric confidence in [0, 1] surfaced
    only when present.

    Lane-specific extras (e.g. policy's ``driver_hints`` and
    ``expected_risk_tags``) live on the lane's own Claim model; this
    platform shape stays minimal so a future lane can ship reports
    without inheriting policy-specific fields it doesn't need.
    """

    model_config = _strict_config()

    claim_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    tag: str = Field(..., min_length=1)
    segment_refs: List[str] = Field(default_factory=list)
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @field_validator("tag")
    @classmethod
    def _tag_in_taxonomy(cls, value: str) -> str:
        if value not in HYPOTHESIS_TAGS:
            raise ValueError(
                f"ClaimLike.tag={value!r} is not in the canonical "
                f"HYPOTHESIS_TAGS taxonomy "
                f"({sorted(HYPOTHESIS_TAGS)!r})."
            )
        return value


# ---------------------------------------------------------------------------
# ValidityFlag -- platform-level transparency / methodological note.
# ---------------------------------------------------------------------------


class ValidityFlag(BaseModel):
    """Platform-level transparency / methodological flag.

    The richer policy-lane :class:`app.policy.schema.ValidityFlag`
    keeps its own wire shape (with ``segment_refs``, ``source_fields``,
    ``field_validation_prompt`` and a fixed
    ``Literal["low","medium","high"]`` confidence axis); the platform
    record is leaner and validates ``kind`` / ``severity`` /
    ``confidence`` against the SAME platform frozensets a future
    cross-lane aggregator would consume.

    ``claim_refs`` is allowed to be empty -- a flag that applies to a
    scenario as a whole (e.g. power-law tail on an aggregate) has no
    specific claim to attach to.
    """

    model_config = _strict_config()

    flag_id: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1)
    severity: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    method: str = Field(..., min_length=1)
    claim_refs: List[str] = Field(default_factory=list)
    confidence: str = Field(..., min_length=1)

    @field_validator("kind")
    @classmethod
    def _kind_in_taxonomy(cls, value: str) -> str:
        if value not in VALIDITY_FLAG_KINDS:
            raise ValueError(
                f"ValidityFlag.kind={value!r} is not in the canonical "
                f"VALIDITY_FLAG_KINDS taxonomy "
                f"({sorted(VALIDITY_FLAG_KINDS)!r})."
            )
        return value

    @field_validator("severity")
    @classmethod
    def _severity_in_taxonomy(cls, value: str) -> str:
        if value not in SEVERITY_VARIANTS:
            raise ValueError(
                f"ValidityFlag.severity={value!r} is not in the canonical "
                f"SEVERITY_VARIANTS taxonomy "
                f"({sorted(SEVERITY_VARIANTS)!r})."
            )
        return value

    @field_validator("confidence")
    @classmethod
    def _confidence_in_taxonomy(cls, value: str) -> str:
        if value not in CONFIDENCE_BANDS:
            raise ValueError(
                f"ValidityFlag.confidence={value!r} is not in the canonical "
                f"CONFIDENCE_BANDS taxonomy "
                f"({sorted(CONFIDENCE_BANDS)!r})."
            )
        return value


# ---------------------------------------------------------------------------
# GuardrailFinding -- demotion / annotation record produced by an
# advisory pass.
# ---------------------------------------------------------------------------


class GuardrailFinding(BaseModel):
    """One outcome from an advisory claim-pass / guardrail-check run.

    A finding records that an advisory pass touched a specific
    ``claim_id``; ``demoted_from`` / ``demoted_to`` are populated when
    the pass changed the claim's tag (e.g. demoting a
    ``simulated_observation`` to ``requires_validation`` because a
    numeric-claim verifier flagged it). Both demotion fields are
    optional so the same record shape can describe an annotation-only
    finding that did not rewrite the claim.

    ``severity`` follows the same platform :data:`SEVERITY_VARIANTS`
    taxonomy as :class:`ValidityFlag` so a downstream renderer can
    surface both with consistent chrome.
    """

    model_config = _strict_config()

    finding_id: str = Field(..., min_length=1)
    claim_id: str = Field(..., min_length=1)
    kind: str = Field(..., min_length=1)
    severity: str = Field(..., min_length=1)
    demoted_from: Optional[str] = Field(default=None)
    demoted_to: Optional[str] = Field(default=None)
    message: str = Field(..., min_length=1)

    @field_validator("severity")
    @classmethod
    def _severity_in_taxonomy(cls, value: str) -> str:
        if value not in SEVERITY_VARIANTS:
            raise ValueError(
                f"GuardrailFinding.severity={value!r} is not in the canonical "
                f"SEVERITY_VARIANTS taxonomy "
                f"({sorted(SEVERITY_VARIANTS)!r})."
            )
        return value

    @field_validator("demoted_from", "demoted_to")
    @classmethod
    def _demoted_tag_in_taxonomy(cls, value: Optional[str]) -> Optional[str]:
        """Pin demotion-tag values to the platform-wide
        :data:`HYPOTHESIS_TAGS` taxonomy.

        Both fields are optional (an annotation-only finding sets
        neither), but when SET they MUST be a member of
        :data:`HYPOTHESIS_TAGS` so a downstream renderer / consumer
        does not have to defend against off-taxonomy strings. Catching
        this at construction is the only way to make the contract
        wire-frozen -- a free-form ``str`` would let a lane silently
        emit a typo'd tag that breaks every reader that switches on the
        taxonomy.
        """
        if value is None:
            return None
        if value not in HYPOTHESIS_TAGS:
            raise ValueError(
                f"GuardrailFinding demotion tag={value!r} is not in the "
                f"canonical HYPOTHESIS_TAGS taxonomy "
                f"({sorted(HYPOTHESIS_TAGS)!r})."
            )
        return value


# ---------------------------------------------------------------------------
# ReportGuardrailView -- aggregated guardrail surface for a single report.
# ---------------------------------------------------------------------------


class ReportGuardrailView(BaseModel):
    """Aggregated guardrail bundle attached (or attachable) to a report.

    Three independent surfaces: the deterministic list of
    methodological ``flags`` emitted by the transparency post-
    processor, the demotion/annotation ``findings`` emitted by an
    advisory claim pass, and a per-claim mapping of attribution
    ``driver_hints_by_claim`` (claim_id -> list of platform
    :class:`DriverHint`). Lanes that ship only a subset (e.g. the
    creator lane today emits zero flags) leave the unused fields
    defaulted.
    """

    model_config = _strict_config()

    flags: List[ValidityFlag] = Field(default_factory=list)
    findings: List[GuardrailFinding] = Field(default_factory=list)
    driver_hints_by_claim: Dict[str, List[DriverHint]] = Field(
        default_factory=dict,
    )


__all__ = [
    "ClaimLike",
    "EvidenceRef",
    "DriverHint",
    "ValidityFlag",
    "GuardrailFinding",
    "ReportGuardrailView",
]
