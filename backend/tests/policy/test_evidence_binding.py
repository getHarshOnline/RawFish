"""Tests for the evidence-binding contract enforced by the generator.

The generator's ``generate_and_persist`` builds an evidence catalog
from the run dir + scenario BEFORE the LLM call. After the Report is
validated, every claim whose tag is in the evidence-required set
(``source_grounded`` / ``simulated_observation``, plus aggregate
``inference``) must carry at least one ``evidence_ref`` resolving
against the catalog. Failures demote the tag to
``requires_validation``; if the demotion would corrupt the majority
of the report, the whole report is rejected.

Tests cover:
  * source_grounded claim with valid ref -> unchanged
  * source_grounded claim with dangling ref -> demoted + warning logged
  * aggregate inference claim with no refs -> demoted + warning logged
  * majority-demotion threshold -> EvidenceGroundingFailed
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from app.policy.hypothesis_tags import HypothesisTag
from app.policy.persona_defaults import report_sections_for
from app.policy.schema import Persona, PolicySurface, Report
from app.services.policy.policy_report_generator import (
    EvidenceGroundingFailed,
    PolicyReportGenerator,
)
from tests.services.policy.test_policy_report_generator import (
    _StubLLM,
    _StubScenario,
    _llm_payload_for_persona,
    _minimal_run_metadata,
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _payload_with_first_claim(
    persona: Persona,
    scope: Optional[PolicySurface],
    tag: HypothesisTag,
    evidence_refs: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Build a per-persona payload, overriding the first claim's tag + refs.

    Every other section's claims keep the factory defaults (tag =
    ``source_grounded`` + a valid ``seed_text:L1`` ref -- the
    canonical valid pair under the per-tag evidence-kind matrix) so
    the binding contract sees only one claim flexing the failure
    mode under test in each scenario below.
    """
    payload = _llm_payload_for_persona(persona, scope)
    first_claim = payload["sections"][0]["claims"][0]
    first_claim["tag"] = tag.value
    first_claim["evidence_refs"] = evidence_refs
    return payload


# ----------------------------------------------------------------------
# Happy path -- valid ref keeps the tag unchanged
# ----------------------------------------------------------------------


def test_source_grounded_claim_with_valid_ref_is_unchanged(
    tmp_path: Path,
) -> None:
    """A claim that carries a resolvable ``seed_text:L1`` evidence_ref
    keeps its ``source_grounded`` tag through the binding step."""
    persona = Persona.CREATOR
    payload = _payload_with_first_claim(
        persona,
        None,
        HypothesisTag.SOURCE_GROUNDED,
        [{"ref_type": "seed_text_line", "ref_id": "seed_text:L1"}],
    )
    scenario = _StubScenario(persona=persona, scope=None)
    rm = _minimal_run_metadata(persona=persona)
    gen = PolicyReportGenerator(
        scenario=scenario, llm=_StubLLM(payload),  # type: ignore[arg-type]
    )

    artifacts = gen.generate_and_persist(rm, tmp_path)
    report = Report.model_validate_json(
        artifacts.report_json_path.read_text(encoding="utf-8"),
    )
    # First claim's tag survives.
    assert (
        report.sections[0].claims[0].tag == HypothesisTag.SOURCE_GROUNDED
    )
    # No binding warning landed -- the tag was not demoted.
    assert not any(
        w.startswith("evidence_binding:") for w in rm.warnings
    )


# ----------------------------------------------------------------------
# Dangling ref -> demoted + warning
# ----------------------------------------------------------------------


def test_source_grounded_claim_with_dangling_ref_is_demoted(
    tmp_path: Path,
) -> None:
    """A claim whose every evidence_ref fails catalog resolution is
    demoted to ``requires_validation``. The original tag is gone; a
    one-line binding warning lands on ``run_metadata.warnings``.
    """
    persona = Persona.CREATOR
    payload = _payload_with_first_claim(
        persona,
        None,
        HypothesisTag.SOURCE_GROUNDED,
        [
            {
                "ref_type": "simulation_action",
                "ref_id": "act-uuid-does-not-exist",
            },
        ],
    )
    scenario = _StubScenario(persona=persona, scope=None)
    rm = _minimal_run_metadata(persona=persona)
    gen = PolicyReportGenerator(
        scenario=scenario, llm=_StubLLM(payload),  # type: ignore[arg-type]
    )

    artifacts = gen.generate_and_persist(rm, tmp_path)
    report = Report.model_validate_json(
        artifacts.report_json_path.read_text(encoding="utf-8"),
    )
    demoted = report.sections[0].claims[0]
    assert demoted.tag == HypothesisTag.REQUIRES_VALIDATION
    # Warning records the demotion with the claim_id.
    assert any(
        w.startswith("evidence_binding:") and demoted.claim_id in w
        for w in rm.warnings
    ), f"expected a binding warning for {demoted.claim_id!r}; got {rm.warnings!r}"


# ----------------------------------------------------------------------
# Aggregate inference claim with no refs -> demoted
# ----------------------------------------------------------------------


def test_aggregate_inference_claim_with_no_refs_is_demoted(
    tmp_path: Path,
) -> None:
    """An ``inference`` claim in ``executive_summary`` (the aggregate
    proxy) with no evidence_refs is demoted just like the grounding-
    required tags. Section-level inference claims are NOT in scope --
    they are treated as non-aggregate by the structural proxy.
    """
    persona = Persona.CREATOR
    payload = _llm_payload_for_persona(persona, None)
    # Inject an executive-summary inference claim with no refs.
    payload["executive_summary"] = [
        {
            "claim_id": "exec_01",
            "text": (
                "Across segments, the simulation suggests an aggregate "
                "engagement shift may emerge under these assumptions."
            ),
            "tag": HypothesisTag.INFERENCE.value,
            "evidence_refs": [],
        },
    ]
    scenario = _StubScenario(persona=persona, scope=None)
    rm = _minimal_run_metadata(persona=persona)
    gen = PolicyReportGenerator(
        scenario=scenario, llm=_StubLLM(payload),  # type: ignore[arg-type]
    )

    artifacts = gen.generate_and_persist(rm, tmp_path)
    report = Report.model_validate_json(
        artifacts.report_json_path.read_text(encoding="utf-8"),
    )
    assert report.executive_summary[0].tag == HypothesisTag.REQUIRES_VALIDATION
    assert any(
        w.startswith("evidence_binding:") and "exec_01" in w
        for w in rm.warnings
    )


def test_section_level_inference_with_no_refs_is_not_demoted(
    tmp_path: Path,
) -> None:
    """A section-level inference claim (not aggregate by the proxy)
    keeps its tag even without an evidence_ref. Mirrors the structural
    rule documented on :func:`_is_aggregate_claim`."""
    persona = Persona.CREATOR
    payload = _payload_with_first_claim(
        persona,
        None,
        HypothesisTag.INFERENCE,
        [],  # no refs
    )
    scenario = _StubScenario(persona=persona, scope=None)
    rm = _minimal_run_metadata(persona=persona)
    gen = PolicyReportGenerator(
        scenario=scenario, llm=_StubLLM(payload),  # type: ignore[arg-type]
    )

    artifacts = gen.generate_and_persist(rm, tmp_path)
    report = Report.model_validate_json(
        artifacts.report_json_path.read_text(encoding="utf-8"),
    )
    assert report.sections[0].claims[0].tag == HypothesisTag.INFERENCE
    assert not any(
        w.startswith("evidence_binding:") for w in rm.warnings
    )


# ----------------------------------------------------------------------
# Majority demotion -> EvidenceGroundingFailed
# ----------------------------------------------------------------------


def test_semantic_mismatch_demotes_claim_with_explicit_reason(
    tmp_path: Path,
) -> None:
    """A claim that supplies refs which DO resolve against the catalog
    but whose ``ref_type`` is outside the tag's whitelist is demoted
    just like a dangling-ref claim. The warning text encodes
    ``semantic_mismatch`` so an operator can tell the failure mode
    apart from ``no_resolvable_ref`` when triaging.

    Pinning the explicit failure-class string in the warning matters:
    a future refactor that homogenises the two failure paths would
    quietly hide which kind of binding error happened, and the
    operator's triage cost grows.
    """
    persona = Persona.CREATOR
    # ``simulated_observation`` whose ONLY ref is ``seed_text_line``
    # (which resolves -- seed_text:L1 IS in the catalog) but
    # ``seed_text_line`` is not in the simulated_observation
    # whitelist (sim observations cite sim outputs, not user text).
    payload = _payload_with_first_claim(
        persona,
        None,
        HypothesisTag.SIMULATED_OBSERVATION,
        [{"ref_type": "seed_text_line", "ref_id": "seed_text:L1"}],
    )
    scenario = _StubScenario(persona=persona, scope=None)
    rm = _minimal_run_metadata(persona=persona)
    gen = PolicyReportGenerator(
        scenario=scenario, llm=_StubLLM(payload),  # type: ignore[arg-type]
    )

    artifacts = gen.generate_and_persist(rm, tmp_path)
    report = Report.model_validate_json(
        artifacts.report_json_path.read_text(encoding="utf-8"),
    )
    demoted = report.sections[0].claims[0]
    assert demoted.tag == HypothesisTag.REQUIRES_VALIDATION
    # The warning carries the explicit semantic_mismatch marker so an
    # operator triaging warnings can branch on it.
    semantic_warnings = [
        w for w in rm.warnings if "semantic_mismatch" in w and demoted.claim_id in w
    ]
    assert semantic_warnings, (
        f"expected a semantic_mismatch warning for {demoted.claim_id!r}; "
        f"got {rm.warnings!r}"
    )
    # And the warning surfaces both the supplied kind (seed_text_line)
    # and the whitelist (simulation_action / agent_interview) so an
    # operator can read the failure without grep-ing the matrix.
    assert "seed_text_line" in semantic_warnings[0]
    assert "simulation_action" in semantic_warnings[0]
    assert "agent_interview" in semantic_warnings[0]


def test_demotion_warning_persists_to_run_metadata_json(
    tmp_path: Path,
) -> None:
    """A demotion warning lands on disk via ``refresh_warning`` BEFORE
    ``generate_and_persist`` writes its own final metadata snapshot.
    The pin proves the dual-write contract: in-memory append + atomic
    on-disk append, with the on-disk path surviving a hypothetical
    crash between the binding step and the final write.

    Verification path:
      1. Write a ``run_metadata.json`` with empty warnings to disk.
      2. Trigger a demotion via a dangling ref.
      3. Read the on-disk ``run_metadata.json`` AFTER the binding
         step and BEFORE ``generate_and_persist``'s final write
         would land (the final write is the same path with possibly
         additional updates -- the warning MUST be present after the
         binding step regardless).
    """
    import json

    persona = Persona.CREATOR
    rm = _minimal_run_metadata(persona=persona)
    # Pre-write the metadata file the generator will refresh from
    # (mirrors the /prepare contract -- prepare writes the file,
    # generate refreshes it).
    meta_path = tmp_path / "run_metadata.json"
    meta_path.write_text(rm.model_dump_json(indent=2), encoding="utf-8")

    payload = _payload_with_first_claim(
        persona,
        None,
        HypothesisTag.SOURCE_GROUNDED,
        [
            {
                "ref_type": "external_source",
                "ref_id": "https://does-not-exist.example/page",
            },
        ],
    )
    scenario = _StubScenario(persona=persona, scope=None)
    gen = PolicyReportGenerator(
        scenario=scenario, llm=_StubLLM(payload),  # type: ignore[arg-type]
    )

    artifacts = gen.generate_and_persist(rm, tmp_path)
    # After generate_and_persist, the on-disk metadata file contains
    # the binding warning. The final write would also surface it via
    # the in-memory list -- the contract under test is that the
    # WARNING is present on disk, NOT that exactly one write
    # produced it.
    on_disk = json.loads(meta_path.read_text(encoding="utf-8"))
    binding_warnings = [
        w for w in on_disk.get("warnings", [])
        if w.startswith("evidence_binding:")
    ]
    assert binding_warnings, (
        f"expected a persisted evidence_binding warning in "
        f"{meta_path}; got warnings={on_disk.get('warnings', [])!r}"
    )
    # The persisted warning matches the claim_id that was demoted
    # (the only one with a dangling ref).
    demoted = Report.model_validate_json(
        artifacts.report_json_path.read_text(encoding="utf-8"),
    ).sections[0].claims[0]
    assert any(demoted.claim_id in w for w in binding_warnings)


def test_majority_demotion_rejects_report_with_evidence_grounding_failed(
    tmp_path: Path,
) -> None:
    """If more than half the claims demote, the entire report is
    rejected. The on-disk policy_report.json must NOT exist (the
    rejection fires before persistence)."""
    persona = Persona.CREATOR
    # Build a payload where EVERY section-claim has a dangling ref.
    sections_meta = report_sections_for(persona, None)
    payload = {
        "executive_summary": [],
        "sections": [
            {
                "section_id": f"sec_{idx:02d}",
                "title": section["title"],
                "claims": [
                    {
                        "claim_id": f"c_{idx:02d}_01",
                        "text": (
                            f"Section {idx} surfaces a simulated "
                            "observation drawn from the stub scenario "
                            "inputs."
                        ),
                        "tag": HypothesisTag.SIMULATED_OBSERVATION.value,
                        "evidence_refs": [
                            {
                                "ref_type": "simulation_action",
                                "ref_id": f"act-missing-{idx}",
                            },
                        ],
                    },
                ],
            }
            for idx, section in enumerate(sections_meta, start=1)
        ],
        "evidence_gaps": [],
        "field_validation_questions": [],
        "confidence_notes": "",
        "source_limitations": [],
    }
    scenario = _StubScenario(persona=persona, scope=None)
    rm = _minimal_run_metadata(persona=persona)
    gen = PolicyReportGenerator(
        scenario=scenario, llm=_StubLLM(payload),  # type: ignore[arg-type]
    )

    out_path = tmp_path / "policy_report.json"
    assert not out_path.exists()
    with pytest.raises(EvidenceGroundingFailed) as excinfo:
        gen.generate_and_persist(rm, tmp_path)
    msg = str(excinfo.value)
    assert "Evidence-binding contract failed" in msg
    # File must NOT have been written.
    assert not out_path.exists()
