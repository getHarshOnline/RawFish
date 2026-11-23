"""``ReportLLMDraft`` strict-schema contract pins.

The structured-output preflight sends ``ReportLLMDraft.model_json_schema()``
to the provider with ``strict=true`` and validates each response against
the draft model BEFORE the route's provenance merge. The contract here
pins exactly which keys the draft accepts so that:

  * The LLM is never asked to emit caller-owned provenance
    (``report_id`` / ``simulation_id`` / ``persona`` / ``scope`` /
    ``scenario_hash`` / ``seed_hash`` / ``generated_at`` /
    ``run_metadata``) -- those come from the caller-supplied
    :class:`RunMetadata` snapshot.
  * The LLM is never asked to emit runtime-owned ``validity_flags``
    -- those are populated by the transparency post-processor AFTER
    schema validation.
  * A draft that validates cleanly remains structurally compatible
    with the full :class:`Report` once provenance is attached. This
    round-trip pin is the contract that lets the generator's
    second-pass ``Report.model_validate(merged)`` succeed without
    duplicating draft-level validation.

If the LLM-owned subset of the Report schema ever drifts (a new
field added, an existing field made required, a min_length raised),
these tests fail loudly and the structured-output preflight contract
is force-updated alongside.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.policy.hypothesis_tags import HypothesisTag
from app.policy.run_metadata import RunMetadata, ScaleCaps
from app.policy.schema import (
    Persona,
    Report,
    ReportLLMDraft,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _minimal_section_payload() -> dict:
    """Wire-shape (dict) of a minimal valid section.

    Wire-shape NOT model-instance: the draft enters this module from
    the LLM as ``json.loads(content)`` -- a dict -- so the test
    surface mirrors that path.
    """
    return {
        "section_id": "executive_summary",
        "title": "Executive summary",
        "claims": [
            {
                "claim_id": "c1",
                "text": "Fans cohort engages with retention prompts.",
                "tag": HypothesisTag.SIMULATED_OBSERVATION.value,
            },
        ],
    }


def _minimal_draft_payload() -> dict:
    """Wire-shape dict that is a valid :class:`ReportLLMDraft`."""
    return {
        "sections": [_minimal_section_payload()],
    }


def _minimal_run_metadata(persona: Persona = Persona.CREATOR) -> RunMetadata:
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


# ----------------------------------------------------------------------
# Happy path: minimal valid draft.
# ----------------------------------------------------------------------


def test_minimal_draft_validates_with_only_sections() -> None:
    """``sections`` is the only non-defaulted field. Everything else
    defaults to an empty list / empty string."""
    draft = ReportLLMDraft.model_validate(_minimal_draft_payload())
    assert draft.executive_summary == []
    assert len(draft.sections) == 1
    assert draft.sections[0].section_id == "executive_summary"
    assert draft.evidence_gaps == []
    assert draft.field_validation_questions == []
    assert draft.confidence_notes == ""
    assert draft.source_limitations == []


def test_draft_validates_full_llm_owned_payload() -> None:
    """All six LLM-owned fields populated -- mirrors a realistic LLM emit."""
    payload = {
        "executive_summary": [
            {
                "claim_id": "exec_1",
                "text": "Retention prompts land with the core cohort.",
                "tag": HypothesisTag.SIMULATED_OBSERVATION.value,
            },
        ],
        "sections": [_minimal_section_payload()],
        "evidence_gaps": [
            {
                "claim_id": "gap_1",
                "text": "No interview data from churned subscribers yet.",
                "tag": HypothesisTag.REQUIRES_VALIDATION.value,
            },
        ],
        "field_validation_questions": [
            "Survey churned subscribers about prompt visibility.",
        ],
        "confidence_notes": "Limited to one observation window; interpret accordingly.",
        "source_limitations": [
            "Seed text is a draft.",
            "No interview data for the churned cohort.",
        ],
    }
    draft = ReportLLMDraft.model_validate(payload)
    assert len(draft.executive_summary) == 1
    assert draft.executive_summary[0].claim_id == "exec_1"
    assert len(draft.evidence_gaps) == 1
    assert draft.field_validation_questions == [
        "Survey churned subscribers about prompt visibility.",
    ]
    assert draft.confidence_notes.startswith("Limited to one observation")
    assert draft.source_limitations == [
        "Seed text is a draft.",
        "No interview data for the churned cohort.",
    ]


# ----------------------------------------------------------------------
# sections is the only structural constraint the draft enforces.
# ----------------------------------------------------------------------


def test_draft_requires_sections_field() -> None:
    with pytest.raises(ValidationError) as exc:
        ReportLLMDraft.model_validate({})
    msg = str(exc.value)
    assert "sections" in msg


def test_draft_rejects_empty_sections_list() -> None:
    """``min_length=1`` mirrors the Report contract -- a draft with
    no sections cannot become a valid Report no matter what
    provenance is merged in, so we fail fast at draft time."""
    with pytest.raises(ValidationError) as exc:
        ReportLLMDraft.model_validate({"sections": []})
    assert "at least 1 item" in str(exc.value)


# ----------------------------------------------------------------------
# extra='forbid' rejects every caller-owned and runtime-owned key.
# This is the security/correctness property that prevents the LLM
# from silently overriding provenance.
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden_key,sample_value",
    [
        ("report_id", "report_attempted_override"),
        ("simulation_id", "sim_attempted_override"),
        ("persona", Persona.POLICY.value),
        ("scope", "internal_org"),
        ("scenario_hash", "f" * 64),
        ("seed_hash", "e" * 64),
        ("generated_at", "2026-06-28T00:00:00+00:00"),
        ("run_metadata", {"simulation_id": "x"}),
        ("validity_flags", []),
    ],
)
def test_draft_rejects_caller_or_runtime_owned_keys(
    forbidden_key: str, sample_value: object,
) -> None:
    """A draft that echoes ANY provenance or runtime-owned key trips
    ``extra='forbid'`` at decode time. Each key MUST be rejected
    individually so a partial echo (LLM emits only ``simulation_id``
    but not the rest) still fails fast.

    Without this pin, an LLM emitting a fabricated ``simulation_id``
    would silently succeed against the draft schema, the route's
    later provenance merge would overwrite it, and the symptom would
    only surface if a future refactor relied on the LLM's value.
    Failing at decode time keeps the trust boundary obvious."""
    payload = _minimal_draft_payload() | {forbidden_key: sample_value}
    with pytest.raises(ValidationError) as exc:
        ReportLLMDraft.model_validate(payload)
    msg = str(exc.value)
    # Pydantic v2 reports unknown keys as "Extra inputs are not permitted".
    assert "not permitted" in msg or "extra" in msg.lower()
    assert forbidden_key in msg


def test_draft_rejects_arbitrary_unknown_key() -> None:
    """``extra='forbid'`` is the contract -- not a per-key allowlist
    -- so an LLM that invents a brand-new key (e.g. ``confidence``)
    is rejected too."""
    payload = _minimal_draft_payload() | {"confidence": "high"}
    with pytest.raises(ValidationError) as exc:
        ReportLLMDraft.model_validate(payload)
    assert "confidence" in str(exc.value)


# ----------------------------------------------------------------------
# Compatibility: a valid draft + merged provenance round-trips through
# the full Report.model_validate. This pins that the draft remains a
# structural subset of the Report -- if a future Report field gets a
# stricter shape (e.g. min_length on confidence_notes), this test
# fails until the draft is updated to match.
# ----------------------------------------------------------------------


def test_draft_payload_round_trips_to_full_report_after_provenance_merge() -> None:
    rm = _minimal_run_metadata(persona=Persona.CREATOR)
    draft_dict = _minimal_draft_payload()
    # Mirror exactly what PolicyReportGenerator does: take the
    # draft-shaped dict, attach the 8 provenance fields, run
    # Report.model_validate.
    merged = dict(draft_dict)
    merged["report_id"] = "report_sim_1_v1"
    merged["simulation_id"] = rm.simulation_id
    merged["persona"] = rm.persona
    merged["scope"] = None
    merged["scenario_hash"] = rm.scenario_hash
    merged["seed_hash"] = rm.seed_hash
    merged["generated_at"] = datetime(2026, 6, 26, 12, 5, 0, tzinfo=timezone.utc)
    merged["run_metadata"] = rm
    report = Report.model_validate(merged)
    assert report.simulation_id == "sim_1"
    assert report.persona == Persona.CREATOR
    assert report.validity_flags == []
    assert len(report.sections) == 1


# ----------------------------------------------------------------------
# JSON Schema export: the draft's published schema -- the exact bytes
# the structured-output preflight ships to the provider -- MUST list
# ``sections`` as required and MUST NOT list any of the
# provenance / runtime-owned keys. Pinning this directly keeps the
# wire-side contract green even if Pydantic internals shift.
# ----------------------------------------------------------------------


def test_draft_json_schema_lists_only_llm_owned_fields() -> None:
    schema = ReportLLMDraft.model_json_schema()
    props = set(schema.get("properties", {}).keys())
    assert props == {
        "executive_summary",
        "sections",
        "evidence_gaps",
        "field_validation_questions",
        "confidence_notes",
        "source_limitations",
    }, f"unexpected ReportLLMDraft properties on the wire: {sorted(props)}"


def test_draft_json_schema_marks_sections_as_required() -> None:
    schema = ReportLLMDraft.model_json_schema()
    assert "sections" in schema.get("required", []), (
        "ReportLLMDraft must require ``sections`` in its published "
        "JSON Schema -- the strict-schema preflight relies on the "
        "provider enforcing the same min_length contract."
    )


def test_draft_json_schema_does_not_advertise_provenance() -> None:
    """Belt-and-braces vs the extra='forbid' test above: even if the
    draft model accidentally widened to allow a provenance key, the
    JSON Schema sent to the provider MUST NOT advertise it.

    The wire contract is the source of truth the model provider
    enforces -- if the provider sees ``simulation_id`` in the
    schema, the model will dutifully emit one, and the route's
    strip step would have to silently overwrite it. Pinning the
    wire schema is the only way to keep the LLM from being asked
    to emit a field it cannot legitimately know."""
    schema = ReportLLMDraft.model_json_schema()
    advertised = set(schema.get("properties", {}).keys())
    forbidden = {
        "report_id",
        "simulation_id",
        "persona",
        "scope",
        "scenario_hash",
        "seed_hash",
        "generated_at",
        "run_metadata",
        "validity_flags",
    }
    leaked = advertised & forbidden
    assert not leaked, (
        f"ReportLLMDraft JSON Schema leaks provenance / runtime keys "
        f"the LLM must not be asked to emit: {sorted(leaked)}"
    )
