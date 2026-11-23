"""Conformance + end-to-end test suite for the creator adapter.

Pins:

* The adapter satisfies :class:`app.simulation_platform.use_case.SimulationUseCase`
  structurally (mirrors the policy adapter conformance check).
* :meth:`use_case_id`, :meth:`catalog`, :meth:`artifact_names` publish
  well-formed metadata that does NOT collide with the policy adapter's
  artifact filenames.
* :meth:`validate_scenario` accepts a well-formed payload and rejects
  one whose ``stakeholders[].type`` is outside the persona-keyed
  allowlist.
* The prepare -> generate end-to-end path runs against a deterministic
  stub LLM for two fixtures (video creator + newsletter writer) so the
  per-persona section list resolution + section-completeness contract
  fire across more than one persona row.
* :meth:`run_sensitivity` raises :class:`NotImplementedError` (the
  canonical "not supported" signal the platform suite recognises).

The 5-fixture-pattern from
``tests/policy/test_five_fixture_proof.py`` is folded into a smaller
2-fixture parametrised matrix here because (a) this adapter ships with
no on-disk preset JSONs to load (``load_presets`` returns an empty
list, see ``CreatorUseCase.load_presets`` docstring)
and (b) two fixtures is enough to prove the per-persona dispatch is
real rather than a single-persona happy path.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from app.creator.adapter import (
    CREATOR_USE_CASE,
    CreatorUseCase,
    hash_scenario,
    hash_seed_text,
)
from app.creator.catalog import (
    CREATOR_SEGMENT_TYPES,
    CreatorPersona,
)
from app.creator.defaults import report_sections_for
from app.creator.report_generator import (
    CANONICAL_CLAIM_TAGS,
    CreatorReport,
    CreatorReportLLMDraft,
    CreatorScenario,
    ReportSectionsMismatch,
)
from app.simulation_platform.registry import get, register
from app.simulation_platform.use_case import SimulationUseCase
from tests._helpers import make_app


# ----------------------------------------------------------------------
# Stub LLM
# ----------------------------------------------------------------------


class _StubLLM:
    """Deterministic ``chat_json`` stand-in.

    Captures the messages and returns the supplied payload verbatim
    each call. Mirrors the policy ``_StubLLM`` so any future shared
    LLM Protocol exposes the same surface for both adapters.
    """

    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.captured_messages: List[Dict[str, str]] | None = None
        self.policy_max_tokens_report: int = 4096

    def chat_json(
        self,
        messages: Any,
        *,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        del max_tokens
        self.captured_messages = list(messages)
        # Deep copy through json so test mutation cannot bleed across calls.
        return json.loads(json.dumps(self._payload))


# ----------------------------------------------------------------------
# Fixtures: scenario + matching payload builders
# ----------------------------------------------------------------------


def _build_scenario_for_persona(persona: CreatorPersona) -> CreatorScenario:
    """Construct a minimal but persona-correct CreatorScenario.

    Uses the first segment-type listed for the persona so the
    allowlist check passes; the scenario body is enough to satisfy
    every ``min_length=1`` constraint without dragging in fixture
    files this use case does not yet ship.
    """
    segment_type = CREATOR_SEGMENT_TYPES[persona][0]
    return CreatorScenario.model_validate({
        "persona": persona.value,
        "title": f"Conformance fixture for {persona.value}",
        "summary": "End-to-end conformance scenario.",
        "seed_text": "Draft body for the conformance fixture.",
        "stress_question": "How does this draft land with the cohorts?",
        "stakeholders": [
            {
                "segment_id": "s_one",
                "name": "Returning audience",
                "type": segment_type,
                "what_changes_their_mind": ["new evidence emerges"],
            },
        ],
        "assumptions": ["assumption one"],
        "success_criteria": ["success one"],
        "failure_modes_to_watch": ["failure mode one"],
    })


def _llm_payload_for_persona(persona: CreatorPersona) -> Dict[str, Any]:
    """Build a stub payload whose sections match the per-persona SSoT.

    Mirrors the policy ``_llm_payload_for_persona`` helper: one safe
    canonically-tagged claim per section, deterministic claim_ids so
    test snapshots stay byte-stable.
    """
    sections_meta = report_sections_for(persona)
    sections: List[Dict[str, Any]] = []
    for idx, section in enumerate(sections_meta, start=1):
        sections.append({
            "section_id": f"sec_{idx:02d}",
            "title": section["title"],
            "claims": [
                {
                    "claim_id": f"c_{idx:02d}_01",
                    "text": (
                        f"Section {idx} surfaces a simulated observation "
                        "drawn from the scenario inputs."
                    ),
                    "tag": "simulated_observation",
                    "segment_refs": ["s_one"],
                },
            ],
        })
    return {
        "executive_summary": [],
        "sections": sections,
        "evidence_gaps": [],
        "field_validation_questions": [],
    }


def _loose_llm_payload_for_persona(persona: CreatorPersona) -> Dict[str, Any]:
    """Payload shaped like common real local-model JSON drift.

    It keeps the same section titles/content, but omits structural ids
    and uses ``claim`` / ``provenance`` aliases the draft normalizer is
    allowed to repair. The helper deliberately does not change titles
    so title-contract failures can be tested separately.
    """
    payload = _llm_payload_for_persona(persona)
    loose = json.loads(json.dumps(payload))
    loose["executive_summary"] = [
        {
            "claim": "Returning viewers may understand the core promise.",
            "provenance": "inference",
            "segment_refs": "s_one",
        }
    ]
    for section in loose["sections"]:
        section.pop("section_id", None)
        for claim in section.get("claims", []):
            text = claim.pop("text")
            tag = claim.pop("tag")
            claim.pop("claim_id", None)
            claim["claim"] = text
            claim["provenance"] = tag
    loose["evidence_gaps"] = [
        {
            "claim": "Interview data is still needed before publishing.",
            "provenance": "requires_validation",
        }
    ]
    return loose


# ----------------------------------------------------------------------
# A. Identity / Protocol conformance
# ----------------------------------------------------------------------


def test_singleton_satisfies_protocol_structurally() -> None:
    """``isinstance(CREATOR_USE_CASE, SimulationUseCase)``
    passes. ``SimulationUseCase`` is ``@runtime_checkable`` so missing
    any Protocol method would fail this check before the rest of the
    suite even ran."""
    assert isinstance(
        CREATOR_USE_CASE, SimulationUseCase
    )


def test_use_case_id_is_creator() -> None:
    """The identity method returns the canonical adapter id."""
    assert CREATOR_USE_CASE.use_case_id() == "creator"


def test_catalog_has_three_personas_with_well_formed_specs() -> None:
    """The catalog publishes one PersonaSpec per CreatorPersona and
    each spec carries the minimum metadata the wizard needs."""
    catalog = CREATOR_USE_CASE.catalog()
    assert catalog.use_case_id == "creator"
    assert catalog.lane_manifest.lane_id == "creator"
    assert len(catalog.personas) == len(CreatorPersona)

    published_ids = {p.persona_id for p in catalog.personas}
    expected_ids = {p.value for p in CreatorPersona}
    assert published_ids == expected_ids

    for persona in catalog.personas:
        assert persona.persona_id
        assert persona.label
        assert persona.cohort_question
        # No scope dimension for this use case.
        assert persona.scopes is None
        # Segment-type allowlist published under the None scope key.
        assert None in persona.segment_types_by_scope
        assert len(persona.segment_types_by_scope[None]) > 0


def test_artifact_names_keys_and_distinct_from_policy(monkeypatch) -> None:
    """Logical-id keys publish creator artifacts plus the generic
    statistical settings artifact.

    Creator-specific filenames must not collide with the policy adapter's;
    ``statistical_settings`` is intentionally shared across RawFish lanes.
    """
    names = CREATOR_USE_CASE.artifact_names()
    assert set(names.keys()) == {"report_json", "prepare", "statistical_settings"}
    assert names["statistical_settings"] == "statistical_settings.json"
    for logical_id, filename in names.items():
        assert filename, (
            f"artifact_names()[{logical_id!r}] must be non-empty"
        )

    make_app(monkeypatch)
    policy_names_by_lane = [
        get("internal_policy").artifact_names(),
        get("public_policy").artifact_names(),
    ]
    for policy_names in policy_names_by_lane:
        overlap_ids = (set(policy_names) & set(names)) - {"statistical_settings"}
        for logical_id in overlap_ids:
            assert policy_names[logical_id] != names[logical_id], (
                f"filename collision on logical_id {logical_id!r}: "
                f"policy uses {policy_names[logical_id]!r}, "
                f"creator uses {names[logical_id]!r}"
            )


def test_app_factory_registers_both_use_cases(monkeypatch) -> None:
    """Booting the app factory publishes BOTH adapters into the
    registry under their declared ids. Proves the wiring in
    ``app/__init__.py`` is real, not just an import side-effect."""
    make_app(monkeypatch)
    # Look both up through the registry (not the module singletons) so
    # we exercise the registry round-trip on the registered instances.
    internal_policy = get("internal_policy")
    public_policy = get("public_policy")
    creator = get("creator")
    assert isinstance(internal_policy, SimulationUseCase)
    assert isinstance(public_policy, SimulationUseCase)
    assert isinstance(creator, SimulationUseCase)
    # The creator adapter the registry holds is the module singleton --
    # repeated create_app() calls in the same process do not rebuild
    # it (idempotency contract).
    assert creator is CREATOR_USE_CASE


def test_third_party_register_does_not_shadow_creator_adapter() -> None:
    """A third adapter can register alongside; the registry's
    idempotency contract holds for the creator adapter when
    re-registered."""
    register(CREATOR_USE_CASE)
    assert get("creator") is CREATOR_USE_CASE


# ----------------------------------------------------------------------
# B. Authoring inputs
# ----------------------------------------------------------------------


def test_validate_scenario_accepts_well_formed_payload() -> None:
    """A well-formed payload validates and round-trips through the
    persona-aware segment-type allowlist check."""
    scenario = CREATOR_USE_CASE.validate_scenario({
        "persona": "video_creator",
        "title": "Test video",
        "summary": "Test summary",
        "seed_text": "Body",
        "stress_question": "Will it land?",
        "stakeholders": [
            {
                "segment_id": "s_one",
                "name": "Loyal audience",
                "type": "loyal_subscriber",
                "what_changes_their_mind": ["new evidence emerges"],
            },
        ],
    })
    assert isinstance(scenario, CreatorScenario)
    assert scenario.persona == CreatorPersona.VIDEO_CREATOR


def test_validate_scenario_rejects_off_allowlist_segment_type() -> None:
    """A stakeholder type outside the persona-keyed allowlist fails the
    segment-type check (NOT the leaf model -- the leaf only enforces
    min_length=1 on the string)."""
    with pytest.raises(ValueError) as excinfo:
        CREATOR_USE_CASE.validate_scenario({
            "persona": "video_creator",
            "title": "Test",
            "summary": "Summary",
            "seed_text": "Body",
            "stress_question": "Question?",
            "stakeholders": [
                {
                    "segment_id": "s_one",
                    "name": "Bad cohort",
                    # Policy segment type -- must NOT pass for a creator
                    # persona; proves the allowlist is enforced.
                    "type": "employee_cohort",
                    "what_changes_their_mind": ["evidence"],
                },
            ],
        })
    assert "not valid for persona" in str(excinfo.value)


def test_parse_seed_returns_draft_dict() -> None:
    """parse_seed echoes the canonical input fields into a draft dict."""
    draft = CREATOR_USE_CASE.parse_seed(
        seed_text="seed",
        stress_question="stress",
        persona="video_creator",
        scope=None,
    )
    assert isinstance(draft, dict)
    assert draft["persona"] == "video_creator"
    assert draft["seed_text"] == "seed"
    assert draft["stress_question"] == "stress"


def test_load_presets_returns_empty_list() -> None:
    """No authored presets ship today; the list is empty but iterable."""
    presets = CREATOR_USE_CASE.load_presets()
    assert isinstance(presets, list)
    assert presets == []


# ----------------------------------------------------------------------
# C. Prepare -> generate end-to-end (two fixtures)
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "persona",
    [
        CreatorPersona.VIDEO_CREATOR,
        CreatorPersona.NEWSLETTER_WRITER,
    ],
    ids=[p.value for p in (
        CreatorPersona.VIDEO_CREATOR, CreatorPersona.NEWSLETTER_WRITER,
    )],
)
def test_prepare_then_generate_end_to_end(
    persona: CreatorPersona, tmp_path: Any,
) -> None:
    """Per-persona: write_prepare_artifacts -> generate_report runs
    cleanly against a stub LLM, the prepare artifact is on disk, and
    the generated report's sections match the SSoT.

    This is the structural analog of the policy 5-fixture proof,
    folded to 2 fixtures because (a) this use case has no preset JSONs
    on disk yet and (b) two fixtures is enough to prove per-persona
    dispatch is real.
    """
    adapter = CREATOR_USE_CASE
    scenario = _build_scenario_for_persona(persona)

    # Prepare-phase side-effect.
    run_dir = tmp_path / "run-001"
    run_dir.mkdir()
    prepare_manifest = adapter.write_prepare_artifacts(
        scenario=scenario, run_dir=run_dir,
    )
    assert "artifacts" in prepare_manifest
    prepare_path = run_dir / "creator_prepare.json"
    assert prepare_path.exists()
    # The persisted prepare artifact round-trips back through the model.
    reloaded_scenario = CreatorScenario.model_validate_json(
        prepare_path.read_text(encoding="utf-8"),
    )
    assert reloaded_scenario.persona == persona

    # Report phase: build a run-metadata-shaped stub and invoke the
    # generator directly (not via the adapter's generate_report, which
    # would construct a real PolicyLLMClient).
    from app.creator.report_generator import (
        CreatorReportGenerator,
    )

    scenario_hash = hash_scenario(scenario)
    seed_hash = hash_seed_text(scenario.seed_text)
    llm = _StubLLM(_llm_payload_for_persona(persona))
    gen = CreatorReportGenerator(
        scenario=scenario, llm=llm,
    )
    report = gen.generate(
        simulation_id="sim_creator_test",
        scenario_hash=scenario_hash,
        seed_hash=seed_hash,
        run_persona=persona,
    )
    assert isinstance(report, CreatorReport)
    assert report.persona == persona
    assert report.simulation_id == "sim_creator_test"

    # Section-completeness contract: titles match the SSoT exactly.
    expected_titles = [s["title"] for s in report_sections_for(persona)]
    actual_titles = [s.title for s in report.sections]
    assert actual_titles == expected_titles, (
        f"persona={persona.value}: sections drifted. "
        f"expected={expected_titles!r} got={actual_titles!r}"
    )

    # Persistence path also lands a report file under run_dir.
    out = gen.generate_and_persist(
        simulation_id="sim_creator_test",
        scenario_hash=scenario_hash,
        seed_hash=seed_hash,
        run_persona=persona,
        run_dir=run_dir,
    )
    assert out.exists()
    # Round-trip the JSON.
    reloaded_report = CreatorReport.model_validate_json(
        out.read_text(encoding="utf-8"),
    )
    assert reloaded_report.persona == persona


def test_real_llm_draft_aliases_are_normalized_without_fake_content() -> None:
    """Common local-model key drift is repaired before strict validation.

    The normalizer only adds structural ids and maps existing content;
    it does not synthesize missing section titles or claim text.
    """
    persona = CreatorPersona.VIDEO_CREATOR
    scenario = _build_scenario_for_persona(persona)
    payload = _loose_llm_payload_for_persona(persona)

    # The strict-output wrapper validates this draft before the
    # generator sees it; pin that boundary explicitly.
    CreatorReportLLMDraft.model_validate(payload)

    from app.creator.report_generator import (
        CreatorReportGenerator,
    )

    scenario_hash = hash_scenario(scenario)
    seed_hash = hash_seed_text(scenario.seed_text)
    gen = CreatorReportGenerator(
        scenario=scenario,
        llm=_StubLLM(payload),
    )
    report = gen.generate(
        simulation_id="sim_creator_loose",
        scenario_hash=scenario_hash,
        seed_hash=seed_hash,
        run_persona=persona,
    )

    assert report.executive_summary[0].claim_id == "exec_01"
    assert report.executive_summary[0].text == (
        "Returning viewers may understand the core promise."
    )
    assert report.executive_summary[0].tag == "inference"
    assert report.executive_summary[0].segment_refs == ["s_one"]
    assert report.sections[0].section_id == "sec_01"
    assert report.sections[0].claims[0].claim_id == "c_01_01"
    assert report.sections[0].claims[0].text == (
        "Section 1 surfaces a simulated observation "
        "drawn from the scenario inputs."
    )


def test_real_llm_normalizer_does_not_mask_wrong_section_titles() -> None:
    """Structural alias repair must not hide section-contract drift."""
    persona = CreatorPersona.VIDEO_CREATOR
    scenario = _build_scenario_for_persona(persona)
    payload = _loose_llm_payload_for_persona(persona)
    payload["sections"][1]["title"] = "Not the expected section"

    from app.creator.report_generator import (
        CreatorReportGenerator,
    )

    scenario_hash = hash_scenario(scenario)
    seed_hash = hash_seed_text(scenario.seed_text)
    gen = CreatorReportGenerator(
        scenario=scenario,
        llm=_StubLLM(payload),
    )
    with pytest.raises(ReportSectionsMismatch):
        gen.generate(
            simulation_id="sim_creator_bad_titles",
            scenario_hash=scenario_hash,
            seed_hash=seed_hash,
            run_persona=persona,
        )


def test_generated_creator_report_hedges_unhedged_banned_claims() -> None:
    """Creator reports keep content but add sentence-local hedges.

    This pins the live-smoke failure mode where a real local model used
    "expected" / "will result" in unhedged claim prose. The shared
    language guard stays strict; the generator makes uncertainty
    explicit before the report reaches that guard.
    """
    from app.creator.adapter import _creator_render_markdown
    from app.creator.report_generator import (
        CreatorReportGenerator,
    )
    from app.services.policy.language_guard import (
        assert_no_unhedged_banned_tokens,
    )

    persona = CreatorPersona.VIDEO_CREATOR
    scenario = _build_scenario_for_persona(persona)
    payload = _llm_payload_for_persona(persona)
    payload["sections"][0]["claims"][0]["text"] = (
        "The short-form video is expected to resonate. "
        "It will result in stronger comments."
    )

    scenario_hash = hash_scenario(scenario)
    seed_hash = hash_seed_text(scenario.seed_text)
    gen = CreatorReportGenerator(
        scenario=scenario,
        llm=_StubLLM(payload),
    )
    report = gen.generate(
        simulation_id="sim_creator_hedged",
        scenario_hash=scenario_hash,
        seed_hash=seed_hash,
        run_persona=persona,
    )

    text = report.sections[0].claims[0].text
    assert "Under these assumptions, the short-form video is expected" in text
    assert "Under these assumptions, it will result" in text
    assert_no_unhedged_banned_tokens(_creator_render_markdown(report))


# ----------------------------------------------------------------------
# D. Optional follow-on
# ----------------------------------------------------------------------


def test_run_sensitivity_raises_not_implemented() -> None:
    """The canonical "not supported" opt-out for use cases that lack a
    sensitivity sweep."""
    with pytest.raises(NotImplementedError):
        CREATOR_USE_CASE.run_sensitivity(
            simulation_id="sim_x", body={},
        )


# ----------------------------------------------------------------------
# E. Type construction: every persona has a valid segment type listed
# ----------------------------------------------------------------------


def test_canonical_tags_are_a_closed_taxonomy() -> None:
    """The five hypothesis tags this adapter accepts match the policy
    adapter's taxonomy (closed contract; widening MUST be intentional)."""
    assert CANONICAL_CLAIM_TAGS == frozenset({
        "source_grounded",
        "simulated_observation",
        "inference",
        "assumption",
        "requires_validation",
    })


def test_persona_coerce_rejects_unsupported_type() -> None:
    """``_coerce_run_persona`` rejects values that are neither a
    :class:`CreatorPersona` nor a valid string -- a sloppy caller
    passing a policy Persona enum is detected immediately."""
    adapter = CreatorUseCase()
    with pytest.raises(ValueError):
        adapter._coerce_run_persona(object())
    with pytest.raises(ValueError):
        adapter._coerce_run_persona("not_a_valid_persona")
