"""Report schema pins: ``EvidenceRef`` + ``DriverHint`` + ``Claim`` + ``Section`` + ``Report``.

Pinned guarantees:

* Every ``Claim.tag`` value MUST be one of the 5 canonical ``HypothesisTag``
  members. Any other string raises ``ValidationError`` at validation time.
* ``Claim.expected_risk_tags`` entries MUST all be in
  ``CANONICAL_EXPECTED_RISK_TAGS``; any unknown ``H-*`` string fails.
* ``EvidenceRef.ref_type`` is restricted to the 4 canonical source kinds
  (simulation_action / seed_text_line / agent_interview / external_source);
  any other ref_type fails.
* ``DriverHint.kind`` is restricted to the 4 canonical driver kinds
  (segment / prior / drift / assumption).
* ``DriverHint.contribution`` is bounded in [0, 1]; out-of-range fails.
* ``DriverHint.method`` is restricted to ``cooccurrence_v1`` (POC) or
  ``loo_v1`` (planned). Any other string fails validation.
* ``DriverHint.attribution_disclaimer`` defaults to the canonical
  "co-occurrence proxy, not causal" wording so reports cannot silently
  drop it.
* ``Section.section_id`` and ``Section.title`` are required non-empty.
* ``Report`` carries every canonical report field: ``report_id``,
  ``executive_summary``, ``evidence_gaps``, ``field_validation_questions``,
  ``confidence_notes``, ``source_limitations``, embedded ``RunMetadata``.
* ``Report.persona='policy'`` requires a non-None ``scope``; non-policy
  personas reject any non-None scope. Mirrors the Scenario invariant.
* ``model_config = extra='forbid'`` everywhere: unknown keys fail at
  ``model_validate_json`` time, never silently dropped.
* Round-trip through ``model_dump_json`` + ``model_validate_json`` is
  identity (Pydantic v2 serialises str-enum members as their string value).

The validators here are the second line of defense against accidental
taxonomy widening; the first line is the strict ``HypothesisTag`` /
``ExpectedRiskTag`` enums.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.policy.hypothesis_tags import (
    CANONICAL_EXPECTED_RISK_TAGS,
    HypothesisTag,
)
from app.policy.run_metadata import RunMetadata, ScaleCaps
from app.policy.schema import (
    Claim,
    DriverHint,
    EvidenceRef,
    Persona,
    PolicySurface,
    Report,
    Section,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _minimal_run_metadata(persona: Persona = Persona.CREATOR) -> RunMetadata:
    """Minimal valid RunMetadata snapshot for embedding in a Report."""
    return RunMetadata(
        simulation_id="sim_1",
        created_at=datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc),
        persona=persona,
        scenario_hash="a" * 64,
        seed_hash="b" * 64,
        prepared_at=datetime(2026, 6, 26, 11, 59, 0, tzinfo=timezone.utc),
        artifact_paths={"manifest.json": "manifest.json"},
        artifact_hashes={"manifest.json": "c" * 64},
        zep_mode="mock",
        scale_caps=ScaleCaps(ensemble_n=1, stakeholders=1, rounds=10, cap_value=500),
    )


def _minimal_section() -> Section:
    return Section(
        section_id="executive_summary",
        title="Executive summary",
        claims=[
            Claim(
                claim_id="c1",
                text="Fans cohort engages with retention prompts.",
                tag=HypothesisTag.SIMULATED_OBSERVATION,
            ),
        ],
    )


def _minimal_report(persona: Persona = Persona.CREATOR, scope=None) -> Report:
    return Report(
        report_id="report_sim_1_v1",
        simulation_id="sim_1",
        persona=persona,
        scope=scope,
        scenario_hash="a" * 64,
        seed_hash="b" * 64,
        generated_at=datetime(2026, 6, 26, 12, 5, 0, tzinfo=timezone.utc),
        sections=[_minimal_section()],
        run_metadata=_minimal_run_metadata(persona=persona),
    )


# ----------------------------------------------------------------------
# EvidenceRef
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref_type",
    ["simulation_action", "seed_text_line", "agent_interview", "external_source"],
)
def test_evidence_ref_accepts_all_4_canonical_types(ref_type: str) -> None:
    """The 4 canonical EvidenceRef source kinds all validate."""
    e = EvidenceRef.model_validate({"ref_type": ref_type, "ref_id": "abc"})
    assert e.ref_type == ref_type


@pytest.mark.parametrize(
    "bad_type",
    ["zep_node", "manifest_entry", "claim", "", "Simulation_Action"],
)
def test_evidence_ref_rejects_unknown_ref_type(bad_type: str) -> None:
    """Any ref_type outside the 4-element Literal fails validation."""
    with pytest.raises(ValidationError):
        EvidenceRef.model_validate({"ref_type": bad_type, "ref_id": "abc"})


def test_evidence_ref_rejects_empty_ref_id() -> None:
    """ref_id must be non-empty (min_length=1)."""
    with pytest.raises(ValidationError):
        EvidenceRef.model_validate(
            {"ref_type": "simulation_action", "ref_id": ""}
        )


def test_evidence_ref_rejects_unknown_field() -> None:
    """extra='forbid' rejects any unknown key."""
    with pytest.raises(ValidationError):
        EvidenceRef.model_validate({
            "ref_type": "simulation_action",
            "ref_id": "abc",
            "rogue": "x",
        })


# ----------------------------------------------------------------------
# DriverHint
# ----------------------------------------------------------------------


def test_driver_hint_minimal_construction() -> None:
    """Minimal valid DriverHint construction uses the canonical defaults."""
    h = DriverHint(
        kind="segment",
        ref="s_fans",
        contribution=0.5,
        method="cooccurrence_v1",
    )
    assert h.kind == "segment"
    assert h.attribution_confidence == "low"
    # Canonical disclaimer wording locked as the default; reports cannot
    # silently drop it.
    assert h.attribution_disclaimer == "co-occurrence proxy, not causal"


@pytest.mark.parametrize("kind", ["segment", "prior", "drift", "assumption"])
def test_driver_hint_accepts_all_4_canonical_kinds(kind: str) -> None:
    """The 4 canonical DriverHint kinds all validate."""
    h = DriverHint.model_validate({
        "kind": kind,
        "ref": "x",
        "contribution": 0.5,
        "method": "cooccurrence_v1",
    })
    assert h.kind == kind


@pytest.mark.parametrize(
    "bad_kind",
    ["segments", "Prior", "evidence", "", "model"],
)
def test_driver_hint_rejects_unknown_kind(bad_kind: str) -> None:
    """Any kind outside the 4-element Literal fails validation."""
    with pytest.raises(ValidationError):
        DriverHint.model_validate({
            "kind": bad_kind,
            "ref": "x",
            "contribution": 0.5,
            "method": "cooccurrence_v1",
        })


@pytest.mark.parametrize("bad_contribution", [-0.01, 1.01, -1.0, 2.0])
def test_driver_hint_contribution_must_be_in_unit_interval(bad_contribution: float) -> None:
    """Contributions outside [0, 1] fail validation."""
    with pytest.raises(ValidationError):
        DriverHint.model_validate({
            "kind": "segment",
            "ref": "x",
            "contribution": bad_contribution,
            "method": "cooccurrence_v1",
        })


def test_driver_hint_method_rejects_unknown_value() -> None:
    """Method discriminator only accepts ``cooccurrence_v1`` or ``loo_v1``."""
    with pytest.raises(ValidationError):
        DriverHint.model_validate({
            "kind": "segment",
            "ref": "x",
            "contribution": 0.5,
            "method": "shap_v1",
        })


def test_driver_hint_rejects_empty_ref() -> None:
    """ref must be non-empty (min_length=1)."""
    with pytest.raises(ValidationError):
        DriverHint.model_validate({
            "kind": "segment",
            "ref": "",
            "contribution": 0.5,
            "method": "cooccurrence_v1",
        })


def test_driver_hint_rejects_unknown_field() -> None:
    """extra='forbid' rejects any unknown key."""
    with pytest.raises(ValidationError):
        DriverHint.model_validate({
            "kind": "segment",
            "ref": "x",
            "contribution": 0.5,
            "method": "cooccurrence_v1",
            "weight": 0.7,  # old-shape field; must fail under new contract
        })


# ----------------------------------------------------------------------
# Claim
# ----------------------------------------------------------------------


def test_claim_minimal_construction() -> None:
    """Minimal valid Claim with the 3 required fields + default empties."""
    c = Claim(
        claim_id="c1",
        text="Retention drops at minute 4.",
        tag=HypothesisTag.INFERENCE,
    )
    assert c.claim_id == "c1"
    assert c.tag == HypothesisTag.INFERENCE
    assert c.segment_refs == []
    assert c.expected_risk_tags == []
    assert c.driver_hints == []
    assert c.evidence_refs == []
    # confidence is omit-when-absent (canonical contract).
    assert c.confidence is None


@pytest.mark.parametrize(
    "tag_str",
    [
        "simulated_observation",
        "inference",
        "source_grounded",
        "assumption",
        "requires_validation",
    ],
)
def test_claim_accepts_all_5_canonical_tags(tag_str: str) -> None:
    """Pydantic coerces canonical strings to HypothesisTag at validation."""
    c = Claim.model_validate({
        "claim_id": "c1",
        "text": "x",
        "tag": tag_str,
    })
    assert c.tag.value == tag_str


@pytest.mark.parametrize(
    "bad_tag",
    [
        "citation",        # non-canonical (plausible alt name)
        "conjecture",      # non-canonical (plausible alt name)
        "guess",           # arbitrary
        "",                # empty
        "Inference",       # case mismatch
    ],
)
def test_claim_rejects_non_canonical_tag(bad_tag: str) -> None:
    """Any tag string outside the canonical 5 must fail validation."""
    with pytest.raises(ValidationError):
        Claim.model_validate({
            "claim_id": "c1",
            "text": "x",
            "tag": bad_tag,
        })


def test_claim_accepts_canonical_expected_risk_tags() -> None:
    """All entries in expected_risk_tags must be canonical H-* values."""
    sample = sorted(CANONICAL_EXPECTED_RISK_TAGS)[:3]
    c = Claim.model_validate({
        "claim_id": "c1",
        "text": "x",
        "tag": "inference",
        "expected_risk_tags": sample,
    })
    assert c.expected_risk_tags == sample


@pytest.mark.parametrize(
    "bad_risk_tag",
    [
        "H-FAKE",
        "RETENTION",      # missing H- prefix
        "h-retention",    # case mismatch
        "",               # empty string
    ],
)
def test_claim_rejects_non_canonical_expected_risk_tag(bad_risk_tag: str) -> None:
    """Any expected_risk_tags entry outside CANONICAL_EXPECTED_RISK_TAGS fails."""
    with pytest.raises(ValidationError):
        Claim.model_validate({
            "claim_id": "c1",
            "text": "x",
            "tag": "inference",
            "expected_risk_tags": [bad_risk_tag],
        })


def test_claim_rejects_mixed_valid_and_invalid_risk_tags() -> None:
    """If any entry is invalid the whole list is rejected (no silent drop)."""
    with pytest.raises(ValidationError):
        Claim.model_validate({
            "claim_id": "c1",
            "text": "x",
            "tag": "inference",
            "expected_risk_tags": ["H-RETENTION", "H-FAKE"],
        })


def test_claim_confidence_accepts_unit_interval() -> None:
    """confidence in [0, 1] validates; None is omit-when-absent."""
    c = Claim.model_validate({
        "claim_id": "c1",
        "text": "x",
        "tag": "inference",
        "confidence": 0.65,
    })
    assert c.confidence == 0.65


@pytest.mark.parametrize("bad_conf", [-0.01, 1.01, -1.0, 2.0])
def test_claim_confidence_rejects_out_of_range(bad_conf: float) -> None:
    """confidence outside [0, 1] fails."""
    with pytest.raises(ValidationError):
        Claim.model_validate({
            "claim_id": "c1",
            "text": "x",
            "tag": "inference",
            "confidence": bad_conf,
        })


def test_claim_evidence_refs_must_be_typed_objects() -> None:
    """evidence_refs is List[EvidenceRef]; raw strings fail validation."""
    with pytest.raises(ValidationError):
        Claim.model_validate({
            "claim_id": "c1",
            "text": "x",
            "tag": "inference",
            "evidence_refs": ["actions.jsonl#L42"],
        })


def test_claim_evidence_refs_accept_typed_objects() -> None:
    """Properly-shaped EvidenceRef dicts validate."""
    c = Claim.model_validate({
        "claim_id": "c1",
        "text": "x",
        "tag": "inference",
        "evidence_refs": [
            {"ref_type": "simulation_action", "ref_id": "a-uuid-1"},
            {"ref_type": "seed_text_line", "ref_id": "14"},
        ],
    })
    assert len(c.evidence_refs) == 2
    assert c.evidence_refs[0].ref_type == "simulation_action"


def test_claim_requires_non_empty_text() -> None:
    """Empty text fails (min_length=1)."""
    with pytest.raises(ValidationError):
        Claim(claim_id="c1", text="", tag=HypothesisTag.INFERENCE)


def test_claim_requires_non_empty_claim_id() -> None:
    """Empty claim_id fails (min_length=1)."""
    with pytest.raises(ValidationError):
        Claim(claim_id="", text="x", tag=HypothesisTag.INFERENCE)


def test_claim_round_trip_through_json_is_identity() -> None:
    """Dump-then-load yields a structurally identical Claim."""
    c = Claim(
        claim_id="c1",
        text="Fans cohort engages with retention prompts.",
        tag=HypothesisTag.SIMULATED_OBSERVATION,
        segment_refs=["s_fans"],
        expected_risk_tags=["H-RETENTION", "H-PARASOCIAL"],
        confidence=0.7,
        driver_hints=[
            DriverHint(
                kind="segment",
                ref="s_fans",
                # Single-hint claim -> contribution=1.0 satisfies the
                # per-claim sum invariant (Claim._driver_hints_contribution_sum_in_band).
                contribution=1.0,
                method="cooccurrence_v1",
            ),
        ],
        evidence_refs=[
            EvidenceRef(ref_type="simulation_action", ref_id="a-uuid-1"),
        ],
    )
    blob = c.model_dump_json()
    c2 = Claim.model_validate_json(blob)
    assert c2.model_dump() == c.model_dump()


def test_claim_rejects_unknown_field() -> None:
    """extra='forbid' rejects any unknown key."""
    with pytest.raises(ValidationError):
        Claim.model_validate({
            "claim_id": "c1",
            "text": "x",
            "tag": "inference",
            "rogue_field": "should_not_be_here",
        })


# ----------------------------------------------------------------------
# Section
# ----------------------------------------------------------------------


def test_section_accepts_empty_claims_list() -> None:
    """A section with zero claims is structurally valid; the renderer
    surfaces an explicit "no claims emitted" line but does not drop the
    section."""
    s = Section(
        section_id="engagement_themes",
        title="Engagement themes",
        claims=[],
    )
    assert s.claims == []


def test_section_requires_non_empty_section_id() -> None:
    """Empty section_id fails (min_length=1)."""
    with pytest.raises(ValidationError):
        Section(section_id="", title="x", claims=[])


def test_section_requires_non_empty_title() -> None:
    """Empty title fails (min_length=1)."""
    with pytest.raises(ValidationError):
        Section(section_id="s", title="", claims=[])


def test_section_rejects_legacy_claim_guidance_field() -> None:
    """The old ReportSection.claim_guidance field is removed; a hand-edited
    report including it must fail under extra='forbid'."""
    with pytest.raises(ValidationError):
        Section.model_validate({
            "section_id": "s",
            "title": "x",
            "claim_guidance": "leftover field from a removed model shape",
            "claims": [],
        })


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------


def test_report_minimal_construction_creator_persona() -> None:
    """Creator persona requires no scope; minimal valid Report."""
    r = _minimal_report()
    assert r.report_id == "report_sim_1_v1"
    assert r.scope is None
    assert len(r.sections) == 1
    # Defaulted-empty Report fields all start as their empty value.
    assert r.executive_summary == []
    assert r.evidence_gaps == []
    assert r.field_validation_questions == []
    assert r.confidence_notes == ""
    assert r.source_limitations == []
    # Embedded RunMetadata snapshot is the matching shape.
    assert r.run_metadata.simulation_id == "sim_1"


def test_report_policy_persona_requires_scope() -> None:
    """Persona=policy without scope fails (mirrors Scenario invariant)."""
    with pytest.raises(ValidationError):
        Report(
            report_id="rid",
            simulation_id="sim_1",
            persona=Persona.POLICY,
            scope=None,
            scenario_hash="a" * 64,
            seed_hash="b" * 64,
            generated_at=datetime.now(timezone.utc),
            sections=[_minimal_section()],
            run_metadata=_minimal_run_metadata(persona=Persona.POLICY),
        )


def test_report_non_policy_persona_rejects_scope() -> None:
    """Creator/video/product with a non-None scope fails."""
    with pytest.raises(ValidationError):
        Report(
            report_id="rid",
            simulation_id="sim_1",
            persona=Persona.CREATOR,
            scope=PolicySurface.INTERNAL_ORG,
            scenario_hash="a" * 64,
            seed_hash="b" * 64,
            generated_at=datetime.now(timezone.utc),
            sections=[_minimal_section()],
            run_metadata=_minimal_run_metadata(),
        )


def test_report_sections_must_be_non_empty() -> None:
    """sections=[] fails (min_length=1)."""
    with pytest.raises(ValidationError):
        Report(
            report_id="rid",
            simulation_id="sim_1",
            persona=Persona.CREATOR,
            scenario_hash="a" * 64,
            seed_hash="b" * 64,
            generated_at=datetime.now(timezone.utc),
            sections=[],
            run_metadata=_minimal_run_metadata(),
        )


def test_report_rejects_empty_scenario_hash() -> None:
    """scenario_hash empty fails (min_length=1)."""
    with pytest.raises(ValidationError):
        Report(
            report_id="rid",
            simulation_id="sim_1",
            persona=Persona.CREATOR,
            scenario_hash="",
            seed_hash="b" * 64,
            generated_at=datetime.now(timezone.utc),
            sections=[_minimal_section()],
            run_metadata=_minimal_run_metadata(),
        )


def test_report_executive_summary_validates_each_claim() -> None:
    """Executive-summary claims go through the same Claim validator."""
    r = Report(
        report_id="rid",
        simulation_id="sim_1",
        persona=Persona.CREATOR,
        scenario_hash="a" * 64,
        seed_hash="b" * 64,
        generated_at=datetime.now(timezone.utc),
        executive_summary=[
            Claim(
                claim_id="exec_1",
                text="Engagement is concentrated in the first 5 minutes.",
                tag=HypothesisTag.SIMULATED_OBSERVATION,
            ),
        ],
        sections=[_minimal_section()],
        run_metadata=_minimal_run_metadata(),
    )
    assert len(r.executive_summary) == 1
    assert r.executive_summary[0].tag == HypothesisTag.SIMULATED_OBSERVATION


def test_report_evidence_gaps_validates_each_claim() -> None:
    """Evidence-gap claims go through the same Claim validator."""
    r = Report(
        report_id="rid",
        simulation_id="sim_1",
        persona=Persona.CREATOR,
        scenario_hash="a" * 64,
        seed_hash="b" * 64,
        generated_at=datetime.now(timezone.utc),
        sections=[_minimal_section()],
        evidence_gaps=[
            Claim(
                claim_id="gap_1",
                text="Actual response of trader cohort unknown.",
                tag=HypothesisTag.REQUIRES_VALIDATION,
            ),
        ],
        run_metadata=_minimal_run_metadata(),
    )
    assert r.evidence_gaps[0].tag == HypothesisTag.REQUIRES_VALIDATION


def test_report_field_validation_questions_are_plain_strings() -> None:
    """field_validation_questions is a list[str] (no provenance/tag)."""
    r = Report(
        report_id="rid",
        simulation_id="sim_1",
        persona=Persona.CREATOR,
        scenario_hash="a" * 64,
        seed_hash="b" * 64,
        generated_at=datetime.now(timezone.utc),
        sections=[_minimal_section()],
        field_validation_questions=[
            "Survey 30 traders before pricing rollout.",
            "Interview cohort leads about retention drivers.",
        ],
        run_metadata=_minimal_run_metadata(),
    )
    assert len(r.field_validation_questions) == 2


def test_report_round_trip_through_json_is_identity() -> None:
    """Full Report dump-then-load is structurally identical."""
    r = Report(
        report_id="report_sim_1_v1",
        simulation_id="sim_1",
        persona=Persona.POLICY,
        scope=PolicySurface.PUBLIC_POLICY,
        scenario_hash="a" * 64,
        seed_hash="b" * 64,
        generated_at=datetime(2026, 6, 26, 12, 5, 0, tzinfo=timezone.utc),
        executive_summary=[
            Claim(claim_id="e1", text="x", tag=HypothesisTag.INFERENCE),
        ],
        sections=[_minimal_section()],
        evidence_gaps=[
            Claim(claim_id="g1", text="y", tag=HypothesisTag.REQUIRES_VALIDATION),
        ],
        field_validation_questions=["Question 1.", "Question 2."],
        confidence_notes="Confidence narrative paragraph.",
        source_limitations=["Limitation 1.", "Limitation 2."],
        run_metadata=_minimal_run_metadata(persona=Persona.POLICY),
    )
    blob = r.model_dump_json()
    r2 = Report.model_validate_json(blob)
    assert r2.model_dump() == r.model_dump()


def test_report_rejects_unknown_field() -> None:
    """extra='forbid' rejects any unknown key in the persisted JSON."""
    with pytest.raises(ValidationError):
        Report.model_validate({
            "report_id": "rid",
            "simulation_id": "sim_1",
            "persona": "creator",
            "scenario_hash": "a" * 64,
            "seed_hash": "b" * 64,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sections": [{
                "section_id": "s",
                "title": "x",
                "claims": [],
            }],
            "run_metadata": _minimal_run_metadata().model_dump(mode="json"),
            "rogue_field": "should_not_be_here",
        })


# ----------------------------------------------------------------------
# Report header vs embedded run_metadata cross-field invariant
# ----------------------------------------------------------------------


def test_report_rejects_simulation_id_mismatch_with_embedded_run_metadata() -> None:
    """Header simulation_id must equal the embedded run_metadata's value.

    Without this check a hand-edited or LLM-produced report could ship
    ``simulation_id="sim_A"`` in the header while the embedded snapshot
    read ``"sim_B"`` -- "report A for run B" would silently validate.
    """
    rm = _minimal_run_metadata()  # simulation_id = "sim_1"
    with pytest.raises(ValidationError) as excinfo:
        Report(
            report_id="rid",
            simulation_id="sim_other",  # mismatch
            persona=Persona.CREATOR,
            scenario_hash="a" * 64,
            seed_hash="b" * 64,
            generated_at=datetime.now(timezone.utc),
            sections=[_minimal_section()],
            run_metadata=rm,
        )
    msg = str(excinfo.value)
    assert "simulation_id" in msg
    assert "'sim_other'" in msg
    assert "'sim_1'" in msg


def test_report_rejects_scenario_hash_mismatch_with_embedded_run_metadata() -> None:
    """Header scenario_hash must equal the embedded run_metadata's value."""
    rm = _minimal_run_metadata()  # scenario_hash = "a"*64
    with pytest.raises(ValidationError) as excinfo:
        Report(
            report_id="rid",
            simulation_id="sim_1",
            persona=Persona.CREATOR,
            scenario_hash="d" * 64,  # mismatch
            seed_hash="b" * 64,
            generated_at=datetime.now(timezone.utc),
            sections=[_minimal_section()],
            run_metadata=rm,
        )
    assert "scenario_hash" in str(excinfo.value)


def test_report_rejects_seed_hash_mismatch_with_embedded_run_metadata() -> None:
    """Header seed_hash must equal the embedded run_metadata's value."""
    rm = _minimal_run_metadata()  # seed_hash = "b"*64
    with pytest.raises(ValidationError) as excinfo:
        Report(
            report_id="rid",
            simulation_id="sim_1",
            persona=Persona.CREATOR,
            scenario_hash="a" * 64,
            seed_hash="e" * 64,  # mismatch
            generated_at=datetime.now(timezone.utc),
            sections=[_minimal_section()],
            run_metadata=rm,
        )
    assert "seed_hash" in str(excinfo.value)


def test_report_rejects_persona_mismatch_with_embedded_run_metadata() -> None:
    """Header persona must equal the embedded run_metadata's persona.

    A policy-report-for-creator-run mismatch would silently produce a
    file claiming the wrong persona's section list / vocabulary applied
    to the wrong run's data. Refused here.
    """
    rm = _minimal_run_metadata(persona=Persona.CREATOR)
    # Set up a policy-persona report (requires scope) with creator
    # run_metadata embedded -- the persona mismatch is the failure mode
    # under test; the scope discriminator is satisfied for the policy
    # header so the test isolates the persona check from the scope check.
    with pytest.raises(ValidationError) as excinfo:
        Report(
            report_id="rid",
            simulation_id="sim_1",
            persona=Persona.POLICY,  # mismatch (run_metadata is creator)
            scope=PolicySurface.PUBLIC_POLICY,
            scenario_hash="a" * 64,
            seed_hash="b" * 64,
            generated_at=datetime.now(timezone.utc),
            sections=[_minimal_section()],
            run_metadata=rm,
        )
    assert "persona" in str(excinfo.value)


def test_report_accepts_all_4_provenance_fields_matching() -> None:
    """Positive control: a Report whose header EXACTLY matches the
    embedded run_metadata validates cleanly. Pairs with the 4 mismatch
    tests above so the validator's behaviour is pinned in both
    directions."""
    r = _minimal_report()
    assert r.simulation_id == r.run_metadata.simulation_id
    assert r.scenario_hash == r.run_metadata.scenario_hash
    assert r.seed_hash == r.run_metadata.seed_hash
    assert r.persona == r.run_metadata.persona


def test_report_validity_flags_default_empty_list() -> None:
    """A Report constructed without a ``validity_flags`` key carries an
    empty list. The runtime post-processor is opt-in via
    :func:`apply_transparency_flags`; in-memory Report construction
    must not require the caller to spell out the field."""
    r = _minimal_report()
    assert r.validity_flags == []


def test_report_validity_flags_backward_compat_json() -> None:
    """A pre-existing persisted ``policy_report.json`` that does not
    carry the ``validity_flags`` key MUST still parse cleanly via
    :meth:`Report.model_validate_json`. Pydantic v2 fills the
    ``default_factory=list`` at validation time so the additive field
    is backward-compatible with on-disk fixtures predating it.
    """
    import json
    payload = {
        "report_id": "report_sim_1_v1",
        "simulation_id": "sim_1",
        "persona": "creator",
        "scenario_hash": "a" * 64,
        "seed_hash": "b" * 64,
        "generated_at": datetime(
            2026, 6, 26, 12, 5, 0, tzinfo=timezone.utc,
        ).isoformat(),
        "sections": [{
            "section_id": "executive_summary",
            "title": "Executive summary",
            "claims": [{
                "claim_id": "c1",
                "text": "Fans cohort engages with retention prompts.",
                "tag": "simulated_observation",
            }],
        }],
        "run_metadata": _minimal_run_metadata().model_dump(mode="json"),
    }
    # Deliberately omit the validity_flags key entirely.
    assert "validity_flags" not in payload
    reloaded = Report.model_validate_json(json.dumps(payload))
    assert reloaded.validity_flags == []


def test_report_rejects_legacy_run_metadata_ref_field() -> None:
    """The old ``run_metadata_ref: str`` field is removed; the embedded
    ``run_metadata`` snapshot replaces it. A hand-edited report carrying
    the old field must fail under extra='forbid'."""
    payload = {
        "report_id": "rid",
        "simulation_id": "sim_1",
        "persona": "creator",
        "scenario_hash": "a" * 64,
        "seed_hash": "b" * 64,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": [{
            "section_id": "s",
            "title": "x",
            "claims": [],
        }],
        "run_metadata": _minimal_run_metadata().model_dump(mode="json"),
        "run_metadata_ref": "run_metadata.json",  # legacy
    }
    with pytest.raises(ValidationError):
        Report.model_validate(payload)
