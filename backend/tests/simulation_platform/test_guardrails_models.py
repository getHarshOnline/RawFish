"""Pin the platform-level guardrail Pydantic shapes.

Every model exercises its happy path AND its invalid-taxonomy
rejection path so a future refactor that loosens validation gets
caught immediately.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.simulation_platform.guardrails.models import (
    ClaimLike,
    DriverHint,
    EvidenceRef,
    GuardrailFinding,
    ReportGuardrailView,
    ValidityFlag,
)


# ---------------------------------------------------------------------------
# EvidenceRef
# ---------------------------------------------------------------------------


def test_evidence_ref_happy_path_minimum_fields() -> None:
    ref = EvidenceRef(ref_kind="simulation_action", ref_id="abc-123")
    assert ref.ref_kind == "simulation_action"
    assert ref.ref_id == "abc-123"
    assert ref.source_title is None
    assert ref.link is None


def test_evidence_ref_happy_path_with_optional_fields() -> None:
    ref = EvidenceRef(
        ref_kind="external_source",
        ref_id="https://example.com",
        source_title="Example",
        link="https://example.com",
    )
    assert ref.source_title == "Example"
    assert ref.link == "https://example.com"


def test_evidence_ref_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError) as excinfo:
        EvidenceRef(ref_kind="zep_node", ref_id="x")
    assert "EVIDENCE_REF_KINDS" in str(excinfo.value)


# ---------------------------------------------------------------------------
# DriverHint
# ---------------------------------------------------------------------------


def test_driver_hint_happy_path() -> None:
    hint = DriverHint(
        factor_id="seg_alpha",
        delta=0.42,
        confidence="advisory",
        method="cooccurrence_v1",
    )
    assert hint.factor_id == "seg_alpha"
    assert hint.delta == 0.42
    assert hint.confidence == "advisory"
    assert hint.method == "cooccurrence_v1"


def test_driver_hint_accepts_negative_delta() -> None:
    # ``delta`` is unconstrained-sign by design; a driver can push the
    # outcome in either direction.
    hint = DriverHint(
        factor_id="seg_beta",
        delta=-0.3,
        confidence="low",
        method="cooccurrence_v1",
    )
    assert hint.delta == -0.3


def test_driver_hint_rejects_unknown_confidence() -> None:
    with pytest.raises(ValidationError) as excinfo:
        DriverHint(
            factor_id="seg_a",
            delta=0.1,
            confidence="medium",  # not in CONFIDENCE_BANDS
            method="cooccurrence_v1",
        )
    assert "CONFIDENCE_BANDS" in str(excinfo.value)


# ---------------------------------------------------------------------------
# ClaimLike
# ---------------------------------------------------------------------------


def test_claim_like_happy_path_minimal() -> None:
    c = ClaimLike(claim_id="c1", text="A claim.", tag="inference")
    assert c.claim_id == "c1"
    assert c.tag == "inference"
    assert c.segment_refs == []
    assert c.evidence_refs == []
    assert c.confidence is None


def test_claim_like_happy_path_full() -> None:
    c = ClaimLike(
        claim_id="c2",
        text="A grounded claim.",
        tag="source_grounded",
        segment_refs=["seg_a", "seg_b"],
        evidence_refs=[
            EvidenceRef(ref_kind="seed_text_line", ref_id="seed_text:L4"),
        ],
        confidence=0.75,
    )
    assert c.confidence == 0.75
    assert len(c.evidence_refs) == 1


def test_claim_like_rejects_unknown_tag() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ClaimLike(claim_id="c1", text="x", tag="unknown_tag")
    assert "HYPOTHESIS_TAGS" in str(excinfo.value)


def test_claim_like_confidence_must_be_in_unit_interval() -> None:
    with pytest.raises(ValidationError):
        ClaimLike(claim_id="c1", text="x", tag="inference", confidence=1.5)
    with pytest.raises(ValidationError):
        ClaimLike(claim_id="c1", text="x", tag="inference", confidence=-0.1)


# ---------------------------------------------------------------------------
# ValidityFlag
# ---------------------------------------------------------------------------


def test_validity_flag_happy_path() -> None:
    flag = ValidityFlag(
        flag_id="vf_pl_01",
        kind="power_law_tail",
        severity="warning",
        message="Heavy-tailed distribution annotated on segment X.",
        method="distribution_assumptions_scan",
        claim_refs=["c1"],
        confidence="advisory",
    )
    assert flag.kind == "power_law_tail"
    assert flag.severity == "warning"
    assert flag.confidence == "advisory"


def test_validity_flag_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ValidityFlag(
            flag_id="vf_x",
            kind="bogus_kind",
            severity="info",
            message="m",
            method="x",
            confidence="low",
        )
    assert "VALIDITY_FLAG_KINDS" in str(excinfo.value)


def test_validity_flag_rejects_unknown_severity() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ValidityFlag(
            flag_id="vf_x",
            kind="observer_effect",
            severity="critical",
            message="m",
            method="x",
            confidence="low",
        )
    assert "SEVERITY_VARIANTS" in str(excinfo.value)


def test_validity_flag_rejects_unknown_confidence() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ValidityFlag(
            flag_id="vf_x",
            kind="observer_effect",
            severity="info",
            message="m",
            method="x",
            confidence="medium",
        )
    assert "CONFIDENCE_BANDS" in str(excinfo.value)


# ---------------------------------------------------------------------------
# GuardrailFinding
# ---------------------------------------------------------------------------


def test_guardrail_finding_happy_path_with_demotion() -> None:
    f = GuardrailFinding(
        finding_id="gf_01",
        claim_id="c1",
        kind="numeric_overclaim",
        severity="notice",
        demoted_from="simulated_observation",
        demoted_to="requires_validation",
        message="Numeric claim could not be verified.",
    )
    assert f.demoted_from == "simulated_observation"
    assert f.demoted_to == "requires_validation"


def test_guardrail_finding_happy_path_annotation_only() -> None:
    f = GuardrailFinding(
        finding_id="gf_02",
        claim_id="c2",
        kind="advisory_note",
        severity="info",
        message="Annotation only; claim left untouched.",
    )
    assert f.demoted_from is None
    assert f.demoted_to is None


def test_guardrail_finding_rejects_unknown_severity() -> None:
    with pytest.raises(ValidationError) as excinfo:
        GuardrailFinding(
            finding_id="gf_x",
            claim_id="c1",
            kind="x",
            severity="critical",
            message="m",
        )
    assert "SEVERITY_VARIANTS" in str(excinfo.value)


def test_guardrail_finding_rejects_off_taxonomy_demoted_from() -> None:
    """demoted_from must be a member of HYPOTHESIS_TAGS when set."""
    with pytest.raises(ValidationError) as excinfo:
        GuardrailFinding(
            finding_id="gf_bad_from",
            claim_id="c1",
            kind="x",
            severity="notice",
            demoted_from="speculative",  # not in HYPOTHESIS_TAGS
            demoted_to="requires_validation",
            message="m",
        )
    assert "HYPOTHESIS_TAGS" in str(excinfo.value)


def test_guardrail_finding_rejects_off_taxonomy_demoted_to() -> None:
    """demoted_to must be a member of HYPOTHESIS_TAGS when set."""
    with pytest.raises(ValidationError) as excinfo:
        GuardrailFinding(
            finding_id="gf_bad_to",
            claim_id="c1",
            kind="x",
            severity="notice",
            demoted_from="simulated_observation",
            demoted_to="provisional",  # not in HYPOTHESIS_TAGS
            message="m",
        )
    assert "HYPOTHESIS_TAGS" in str(excinfo.value)


# ---------------------------------------------------------------------------
# ReportGuardrailView
# ---------------------------------------------------------------------------


def test_report_guardrail_view_default_empty() -> None:
    v = ReportGuardrailView()
    assert v.flags == []
    assert v.findings == []
    assert v.driver_hints_by_claim == {}


def test_report_guardrail_view_happy_path_populated() -> None:
    flag = ValidityFlag(
        flag_id="vf_01",
        kind="simpson_aggregate_disagreement",
        severity="warning",
        message="Aggregate disagrees with per-segment direction.",
        method="simpson_check_v1",
        confidence="advisory",
    )
    finding = GuardrailFinding(
        finding_id="gf_01",
        claim_id="c1",
        kind="numeric_overclaim",
        severity="notice",
        message="m",
    )
    hint = DriverHint(
        factor_id="seg_alpha",
        delta=0.5,
        confidence="advisory",
        method="cooccurrence_v1",
    )
    view = ReportGuardrailView(
        flags=[flag],
        findings=[finding],
        driver_hints_by_claim={"c1": [hint]},
    )
    assert view.flags[0].flag_id == "vf_01"
    assert view.findings[0].finding_id == "gf_01"
    assert view.driver_hints_by_claim["c1"][0].factor_id == "seg_alpha"
