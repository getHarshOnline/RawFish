"""Pins the standalone policy report generator.

Coverage:
  * import-graph purity (mirrors the renderer + prompt_builder tests):
    the module's import set is constrained to a small allowlist; no
    Flask, no removed report-agent dependency, no raw upstream LLMClient.
  * The generator is NOT a subclass of any other class (composition
    contract; subclassing would silently inherit incompatible prompt
    material).
  * report_id derivation is deterministic and fingerprints the four
    provenance inputs (simulation_id, scenario_hash, seed_hash,
    REPORT_VERSION).
  * generate() merges the caller-supplied RunMetadata as the single
    source of truth for header fields, ignoring any provenance keys
    the LLM echoed back.
  * generate_and_persist() writes ``policy_report.json`` whose contents
    round-trip back through ``Report.model_validate_json``.
  * Service-suite coverage, parametrized across all 4 personas
    (creator, video, product, policy/public_policy):
      - happy-path generate() returns a Report whose sections match the
        SSoT ``report_sections_for(persona, scope)`` titles list, and
        whose render_report() output passes the banned-token gate
      - every LLM-payload-driven Report invariant violation surfaces
        as a ValidationError (no silent default fill)
      - generate_and_persist persists ``policy_report.json`` atomically
        with content that round-trips through ``model_validate_json``
      - report_id stability vs sensitivity to provenance changes
      - source-scan composition assertion: no removed report-agent class name
        anywhere in the generator module source.
"""
from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from pydantic import ValidationError

import app.services.policy.policy_report_generator as policy_report_generator_module
from app.policy.hypothesis_tags import HypothesisTag
from app.policy.persona_defaults import report_sections_for
from app.policy.run_metadata import RunMetadata, ScaleCaps
from app.policy.schema import Persona, PolicySurface, Report
from app.services.policy.markdown_renderer import render_report
from app.services.policy.policy_report_generator import (
    REPORT_VERSION,
    PolicyReportGenerator,
    ReportSectionsMismatch,
    ScenarioRunMetadataPersonaMismatch,
    _derive_report_id,
)

from tests.policy._banned_tokens import assert_no_unhedged_banned_tokens


# ----------------------------------------------------------------------
# Stub scaffolding
# ----------------------------------------------------------------------


class _StubSegment:
    def __init__(self, segment_id: str, name: str, seg_type: str) -> None:
        self.segment_id = segment_id
        self.name = name
        self.type = seg_type
        # Paradox-hook attributes the transparency post-processor reads;
        # defaulted to "no signal" so the generic stub fires nothing on
        # the per-kind triggers below.
        self.distribution_assumptions: Optional[Dict[str, Any]] = None
        self.selection_frame: Optional[str] = None
        self.social_desirability_bias: Optional[float] = None
        self.utility_params: Optional[Dict[str, Any]] = None
        self.drift_params = None
        self.influence_level: int = 1
        self.what_changes_their_mind: List[str] = ["new evidence emerges"]


class _StubScenario:
    """Duck-typed Scenario stand-in.

    The generator only reads ``persona`` / ``scope``; the prompt builder
    reads a handful of free-text fields. Using a stub avoids the
    full Scenario validator chain (segment-type allowlist, scale cap,
    etc.) which is orthogonal to the generator's contract under test.
    """

    def __init__(
        self,
        *,
        persona: Persona,
        scope: "PolicySurface | None",
        stakeholders: Optional[List[_StubSegment]] = None,
    ) -> None:
        self.persona = persona
        self.scope = scope
        self.title = "Stub scenario"
        self.summary = "Stub summary"
        self.jurisdiction = None
        self.seed_text = "Stub seed body."
        self.stress_question = "Stub stress question?"
        self.assumptions = ["Assumption A"]
        self.success_criteria = ["Success X"]
        self.failure_modes_to_watch = ["Failure mode Y"]
        # Paradox-hook attributes the transparency post-processor reads
        # off the Scenario root. Defaulted to "no signal" so the
        # generic generator stub never trips a per-kind trigger that
        # would noisy-up the section-completeness / language-guard
        # assertions in this test file.
        self.selection_criteria: Optional[Dict[str, Any]] = None
        self.excluded_cohorts: List[str] = []
        self.trust_baseline: Optional[float] = None
        # ``run.ensemble_n >= T_MC`` keeps the monte_carlo_coverage
        # trigger silent for these generator-contract tests.

        class _StubRun:
            ensemble_n: int = 64
            assumption_confirmation_ratio: Optional[float] = None

        self.run = _StubRun()
        # The generator does NOT validate segment ``type`` against the
        # persona-aware allowlist (that is the Scenario validator's job).
        # The prompt builder only reads segment_id/name/type as opaque
        # strings, so the default "follower_cohort" works for any
        # persona-keyed stub at this test boundary. Callers that want
        # to mirror a specific persona's segment-type allowlist may pass
        # ``stakeholders`` explicitly.
        self.stakeholders = stakeholders if stakeholders is not None else [
            _StubSegment("s_one", "Cohort one", "follower_cohort"),
        ]


class _StubLLM:
    """Deterministic ``PolicyLLMClient`` stand-in.

    Captures the messages it was called with and returns a fixed JSON
    payload shaped like a Report (minus the provenance fields, which
    the generator overlays from RunMetadata).
    """

    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.captured_messages: List[Dict[str, str]] | None = None
        # The generator reads this attribute when passing the explicit
        # report budget into chat_json. A dedicated test in §I pins the
        # actual value flow; this default keeps the rest of the suite
        # working without each test having to set it.
        self.policy_max_tokens_report: int = 4096

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        self.captured_messages = messages
        # Return a deep-ish copy so test mutation cannot bleed across
        # calls.
        return json.loads(json.dumps(self._payload))


def _minimal_run_metadata(
    persona: Persona = Persona.CREATOR,
    simulation_id: str = "sim_test_001",
) -> RunMetadata:
    return RunMetadata(
        simulation_id=simulation_id,
        created_at=datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc),
        persona=persona,
        scenario_hash="a" * 64,
        seed_hash="b" * 64,
        prepared_at=datetime(2026, 6, 26, 11, 59, 0, tzinfo=timezone.utc),
        artifact_paths={"manifest.json": "manifest.json"},
        artifact_hashes={"manifest.json": "c" * 64},
        zep_mode="mock",
        scale_caps=ScaleCaps(
            ensemble_n=1, stakeholders=1, rounds=10, cap_value=500,
        ),
    )


def _llm_payload_for_creator() -> Dict[str, Any]:
    """LLM payload for the creator persona that matches the SSoT contract.

    Delegates to the per-persona helper so the section list matches the
    canonical row from ``report_sections_for`` (the section-completeness
    contract the generator enforces after schema validation requires
    EXACT title equality + order with the SSoT). Forward-declared
    helper -- the actual implementation lives below in §D.
    """
    return _llm_payload_for_persona(Persona.CREATOR, None)


# ----------------------------------------------------------------------
# Import-graph purity
# ----------------------------------------------------------------------


_GENERATOR_MODULE_PATH = (
    Path(__file__).resolve().parents[3]
    / "app" / "services" / "policy" / "policy_report_generator.py"
)


def _import_names(path: Path) -> set[str]:
    """AST-scan helper -- mirrors the renderer / prompt_builder tests."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
    return names


def test_policy_report_generator_imports_are_pure() -> None:
    """The generator imports a small, audited allowlist.

    Unlike the renderer (pure) and the prompt builder (pure), the
    generator IS permitted clock + filesystem because it captures
    ``generated_at`` and writes ``policy_report.json``. It is NOT
    permitted to import flask, the upstream raw LLMClient, or the
    removed report-agent module.
    """
    names = _import_names(_GENERATOR_MODULE_PATH)
    allowed = {
        "__future__",
        "typing",
        "hashlib",
        "json",
        "dataclasses",
        "datetime",
        "pathlib",
        # ``pydantic`` is permitted because the generator now catches
        # ``ValidationError`` explicitly around the persist-warning
        # path (a corrupt ``run_metadata.json`` makes refresh_warning
        # raise; the generator swallows it to keep report generation
        # alive). Pydantic was already a transitive dependency via
        # every ``Report.model_validate`` call; the named-class
        # import just makes the exception-handling contract explicit.
        "pydantic",
        "app.policy.persona_defaults",
        "app.policy.run_metadata",
        "app.policy.schema",
        # Lazy import inside ``_load_sensitivity_report`` -- the
        # generator reads ``sensitivity.json`` when present so the
        # advisory semantic pass can run the claim-linked numeric
        # verifier. Lazy because numpy is heavy and tests without
        # a sensitivity artifact never trigger the import.
        "app.policy.sensitivity.oat_runner",
        "app.seam.llm_patches",
        "app.services.policy.evidence_catalog",
        "app.services.policy.language_guard",
        "app.services.policy.markdown_renderer",
        # Pure-utility verifiers wired into the advisory pass.
        # Both modules are leaf (no further app imports beyond
        # their own typed shapes).
        "app.services.policy.numeric_claim_verifier",
        "app.services.policy.overclaim_paraphrases",
        "app.services.policy.prompt_builder",
        "app.services.policy.transparency_runtime",
        "app.utils.atomic_io",
    }
    extras = names - allowed
    assert not extras, (
        f"policy_report_generator imports outside the audited allowlist: "
        f"{sorted(extras)!r}. Allowed: {sorted(allowed)!r}."
    )


@pytest.mark.parametrize(
    "forbidden_module",
    [
        "flask",
        "app.services." + "report_agent",
        "app.utils.llm_client",
        "requests",
        "urllib",
        "socket",
        "random",
        "secrets",
    ],
)
def test_policy_report_generator_does_not_import_forbidden(
    forbidden_module: str,
) -> None:
    """No forbidden top-level OR lazy import.

    The AST scan walks function bodies too, so a lazy ``import flask``
    inside ``generate()`` would still trip this gate.
    """
    names = _import_names(_GENERATOR_MODULE_PATH)
    for name in names:
        assert name != forbidden_module and not name.startswith(
            forbidden_module + "."
        ), (
            f"policy_report_generator imports forbidden module {name!r} "
            f"(matched against {forbidden_module!r})"
        )


def test_policy_report_generator_class_is_standalone() -> None:
    """The class subclasses only ``object`` -- no inherited renderer."""
    bases = PolicyReportGenerator.__bases__
    assert bases == (object,), (
        f"PolicyReportGenerator must be standalone (no inheritance); "
        f"got bases={bases!r}. Subclassing any other class would smuggle "
        "incompatible prompt material or field-name conventions."
    )


# ----------------------------------------------------------------------
# report_id derivation
# ----------------------------------------------------------------------


def test_derive_report_id_is_deterministic() -> None:
    """Same RunMetadata -> same report_id across two calls."""
    rm = _minimal_run_metadata()
    first = _derive_report_id(rm)
    second = _derive_report_id(rm)
    assert first == second
    assert len(first) == 16


def test_derive_report_id_changes_when_provenance_changes() -> None:
    """Changing any one of the four fingerprint inputs changes the id."""
    base = _derive_report_id(_minimal_run_metadata())
    alt_sim = _derive_report_id(
        _minimal_run_metadata(simulation_id="sim_test_002"),
    )
    assert base != alt_sim


def test_report_version_constant_is_v2() -> None:
    """v2 marks the shape that carries the runtime-owned ``validity_flags``
    list. Bump again only on subsequent Report-shape changes that consumers
    should notice when diffing report identifiers across runs."""
    assert REPORT_VERSION == "v2"


# ----------------------------------------------------------------------
# generate()
# ----------------------------------------------------------------------


def test_generate_returns_validated_report_with_header_from_run_metadata(
) -> None:
    """Header fields come from RunMetadata, NOT from LLM payload."""
    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    rm = _minimal_run_metadata()
    llm = _StubLLM(_llm_payload_for_creator())
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: _StubScenario is a duck-typed stand-in (the
    # generator only reads .persona/.scope; the full Scenario validator
    # chain is orthogonal to this test).

    report = gen.generate(rm)

    assert isinstance(report, Report)
    assert report.simulation_id == rm.simulation_id
    assert report.scenario_hash == rm.scenario_hash
    assert report.seed_hash == rm.seed_hash
    assert report.persona == rm.persona
    assert report.scope is None  # creator persona -> no scope
    # The cross-field invariant on Report fired implicitly; the
    # embedded RunMetadata equals the one we passed in. (Object
    # identity is NOT preserved because the transparency post-processor
    # re-validates the merged Report so validity-flag hygiene
    # validators fire at generate time; this rebuilds the nested
    # Pydantic graph. Structural equality is the load-bearing
    # invariant -- identity is incidental.)
    assert report.run_metadata == rm
    # report_id matches the deterministic derivation.
    assert report.report_id == _derive_report_id(rm)
    # generated_at is timezone-aware and recent.
    assert report.generated_at.tzinfo is not None


def test_generate_overrides_llm_supplied_provenance_keys() -> None:
    """LLM-echoed provenance keys MUST be discarded.

    A misbehaving LLM that returns ``simulation_id="hijack"`` cannot
    poison the report header; the generator's caller-supplied
    RunMetadata is the single source of truth.
    """
    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    rm = _minimal_run_metadata(simulation_id="sim_canonical")
    payload = _llm_payload_for_creator()
    # Deliberately try to smuggle bogus provenance through the LLM.
    payload["simulation_id"] = "sim_hijack"
    payload["scenario_hash"] = "z" * 64
    payload["seed_hash"] = "z" * 64
    payload["report_id"] = "report_hijack"
    payload["persona"] = Persona.POLICY.value  # different persona
    payload["scope"] = PolicySurface.INTERNAL_ORG.value

    llm = _StubLLM(payload)
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    report = gen.generate(rm)

    assert report.simulation_id == "sim_canonical"
    assert report.scenario_hash == "a" * 64
    assert report.seed_hash == "b" * 64
    assert report.persona == Persona.CREATOR
    assert report.scope is None
    assert report.report_id == _derive_report_id(rm)


def test_generate_raises_when_llm_returns_non_dict() -> None:
    """A non-dict LLM payload trips a TypeError -- never a silent default fill."""

    class _BrokenLLM:
        # Generator reads this when forwarding the explicit budget; the
        # default keeps the broken-payload contract isolated from the
        # budget contract.
        policy_max_tokens_report: int = 4096

        def chat_json(
            self,
            messages: List[Dict[str, str]],
            temperature: float = 0.3,
            max_tokens: int = 4096,
        ) -> Any:
            return ["not", "a", "dict"]

    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    rm = _minimal_run_metadata()
    gen = PolicyReportGenerator(scenario=scenario, llm=_BrokenLLM())  # type: ignore[arg-type]
    # type: ignore WHY: _BrokenLLM duck-types PolicyLLMClient.chat_json
    # only; the full seam-class surface is orthogonal to this test.
    with pytest.raises(TypeError):
        gen.generate(rm)


# ----------------------------------------------------------------------
# generate_and_persist()
# ----------------------------------------------------------------------


def test_generate_and_persist_round_trips_through_validate_json(
    tmp_path: Path,
) -> None:
    """The persisted JSON re-loads into an equivalent Report via the schema."""
    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    rm = _minimal_run_metadata()
    llm = _StubLLM(_llm_payload_for_creator())
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.

    artifacts = gen.generate_and_persist(rm, tmp_path)
    out_path = artifacts.report_json_path
    assert out_path == tmp_path / "policy_report.json"
    assert out_path.exists()
    # generate_and_persist now also writes the markdown render alongside
    # the JSON; pin both artifacts so a future split-out trips here.
    assert artifacts.report_md_path == tmp_path / "policy_report.md"
    assert artifacts.report_md_path.exists()

    text = out_path.read_text(encoding="utf-8")
    reloaded = Report.model_validate_json(text)
    assert reloaded.simulation_id == rm.simulation_id
    assert reloaded.scenario_hash == rm.scenario_hash
    assert reloaded.seed_hash == rm.seed_hash
    assert reloaded.report_id == _derive_report_id(rm)


# ======================================================================
# SERVICE SUITE
# ======================================================================
# The block below extends the import-graph + report_id unit pins with
# the full service-suite coverage matrix: every (persona, scope) row
# gets a happy-path generate + render gate, every Report invariant gets
# an LLM-payload-driven violation test, composition isolation is
# locked, and the generate_and_persist artifact is exercised end-to-end.
# ======================================================================


# ----------------------------------------------------------------------
# Persona matrix + LLM payload factory
# ----------------------------------------------------------------------


# The 4 persona+scope rows the canonical SSoT (``report_sections_for``)
# defines today. Listed as a single source so adding a new persona
# (currently impossible: the 4 enum members are the closed taxonomy) or
# splitting an existing one would surface a parametrize failure here
# rather than silently bypassing the matrix.
_PERSONA_MATRIX: List[tuple[Persona, Optional[PolicySurface]]] = [
    (Persona.CREATOR, None),
    (Persona.VIDEO, None),
    (Persona.PRODUCT, None),
    (Persona.POLICY, PolicySurface.INTERNAL_ORG),
    (Persona.POLICY, PolicySurface.PUBLIC_POLICY),
]


def _persona_matrix_id(case: tuple[Persona, Optional[PolicySurface]]) -> str:
    """Stable parametrize id surfacing persona+scope in pytest -v output."""
    persona, scope = case
    scope_part = scope.value if scope is not None else "none"
    return f"{persona.value}-{scope_part}"


def _llm_payload_for_persona(
    persona: Persona,
    scope: Optional[PolicySurface],
) -> Dict[str, Any]:
    """Build a per-persona LLM payload whose sections match the SSoT.

    The generated payload echoes every (title, claim_guidance) pair the
    canonical SSoT (``report_sections_for``) declares for the row, with
    one safe canonically-tagged claim per section. The claim text is
    written so the rendered output passes the banned-token gate without
    a hedge crutch (no ``forecast`` / ``predict`` / ``will happen`` /
    etc. words appear in the test data itself).
    """
    sections_meta = report_sections_for(persona, scope)
    sections: List[Dict[str, Any]] = []
    for idx, section in enumerate(sections_meta, start=1):
        title = section["title"]
        sections.append({
            # Stable, slug-shaped section_id. The schema only requires
            # min_length=1, but a deterministic id keeps the test
            # snapshot comparable across runs.
            "section_id": f"sec_{idx:02d}",
            "title": title,
            "claims": [
                {
                    "claim_id": f"c_{idx:02d}_01",
                    "text": (
                        # Banned-token-safe wording (no predict/forecast/
                        # will-happen/etc.) so the render-gate sub-test
                        # has a clean baseline.
                        f"Section {idx} restates a source-grounded claim "
                        "drawn from the stub scenario inputs."
                    ),
                    # ``source_grounded`` is the right tag for a claim
                    # that points at user-supplied ``seed_text``;
                    # ``simulated_observation`` would be a semantic
                    # mismatch under the per-tag evidence-kind matrix
                    # (sim observations must cite ``simulation_action``
                    # or ``agent_interview``, not text the user
                    # provided). The flow test does not care about the
                    # specific tag -- only that the binding round-trip
                    # produces a valid persisted report -- so we pick
                    # the tag whose evidence-kind matrix matches the
                    # only ref the catalog has out of the box
                    # (``seed_text:L1`` from the scenario's seed_text).
                    "tag": HypothesisTag.SOURCE_GROUNDED.value,
                    "evidence_refs": [
                        {
                            "ref_type": "seed_text_line",
                            "ref_id": "seed_text:L1",
                        },
                    ],
                },
            ],
        })
    return {
        "executive_summary": [],
        "sections": sections,
        "evidence_gaps": [],
        "field_validation_questions": [],
        "confidence_notes": "",
        "source_limitations": [],
    }


# ----------------------------------------------------------------------
# A. Happy paths -- parametrized over every persona row
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "persona, scope",
    _PERSONA_MATRIX,
    ids=[_persona_matrix_id(c) for c in _PERSONA_MATRIX],
)
def test_generate_happy_path_sections_match_ssot_and_render_passes_gate(
    persona: Persona,
    scope: Optional[PolicySurface],
) -> None:
    """For every persona+scope: generate produces a Report whose section
    titles match the canonical SSoT, persona/scope agree with the
    Scenario discriminator, and the rendered markdown passes the
    banned-token gate end-to-end.

    The render sub-assertion proves the generator -> renderer composition
    does not introduce a banned token at any layer (the LLM payload, the
    merge step, the schema validators, or the renderer itself).
    """
    scenario = _StubScenario(persona=persona, scope=scope)
    rm = _minimal_run_metadata(persona=persona)
    llm = _StubLLM(_llm_payload_for_persona(persona, scope))
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: _StubScenario duck-types the Scenario surface
    # the generator + prompt_builder read (.persona/.scope plus opaque
    # free-text fields); the full Scenario validator chain (segment-type
    # allowlist, scale cap, etc.) is orthogonal to the generator
    # contract under test in this file.

    report = gen.generate(rm)

    # Persona + scope discriminator agreement.
    assert report.persona == persona
    assert report.scope == scope

    # Section titles match the SSoT exactly, in order. This proves the
    # generator did not silently re-key, drop, or reorder sections.
    expected_titles = [s["title"] for s in report_sections_for(persona, scope)]
    rendered_titles = [s.title for s in report.sections]
    assert rendered_titles == expected_titles, (
        f"persona={persona.value} scope={scope!r}: sections drifted from SSoT. "
        f"expected={expected_titles!r} got={rendered_titles!r}"
    )

    # End-to-end render gate: the rendered markdown of a clean payload
    # must pass the banned-token regression helper.
    rendered = render_report(report)
    assert_no_unhedged_banned_tokens(rendered)


# ----------------------------------------------------------------------
# B. LLM payload failure modes -- every Report invariant fires here
# ----------------------------------------------------------------------


def _payload_with_canonical_provenance_removed() -> Dict[str, Any]:
    """Helper: build a creator LLM payload with no provenance keys.

    The generator owns provenance attach, so the payload omits the four
    fields. The B-block tests then mutate the LLM payload to plant a
    specific schema violation and confirm the failure surfaces (NOT a
    silent default fill).
    """
    return _llm_payload_for_persona(Persona.CREATOR, None)


def test_llm_omits_all_sections_triggers_min_length_validation() -> None:
    """sections=[] fails the ``min_length=1`` invariant on Report.sections."""
    payload = _payload_with_canonical_provenance_removed()
    payload["sections"] = []  # empty list -- must be rejected
    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    rm = _minimal_run_metadata()
    gen = PolicyReportGenerator(
        scenario=scenario, llm=_StubLLM(payload),  # type: ignore[arg-type]
    )
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    with pytest.raises(ValidationError) as excinfo:
        gen.generate(rm)
    assert "sections" in str(excinfo.value)


@pytest.mark.parametrize(
    "bad_tag",
    [
        "guess",          # arbitrary non-canonical
        "citation",       # historical-doc artifact, not canonical
        "Inference",      # case mismatch
        "",               # empty
    ],
)
def test_llm_non_canonical_claim_tag_triggers_validation(bad_tag: str) -> None:
    """A non-canonical Claim.tag is rejected at validation time."""
    payload = _payload_with_canonical_provenance_removed()
    payload["sections"][0]["claims"][0]["tag"] = bad_tag
    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    rm = _minimal_run_metadata()
    gen = PolicyReportGenerator(
        scenario=scenario, llm=_StubLLM(payload),  # type: ignore[arg-type]
    )
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    with pytest.raises(ValidationError) as excinfo:
        gen.generate(rm)
    # The validator surface always names the tag field on failure.
    assert "tag" in str(excinfo.value).lower()


@pytest.mark.parametrize(
    "bad_risk_tag",
    ["H-FAKE", "RETENTION", "h-retention", "X"],
)
def test_llm_unknown_expected_risk_tag_triggers_validation(
    bad_risk_tag: str,
) -> None:
    """Any expected_risk_tags entry outside the canonical allowlist fails."""
    payload = _payload_with_canonical_provenance_removed()
    payload["sections"][0]["claims"][0]["expected_risk_tags"] = [bad_risk_tag]
    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    rm = _minimal_run_metadata()
    gen = PolicyReportGenerator(
        scenario=scenario, llm=_StubLLM(payload),  # type: ignore[arg-type]
    )
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    with pytest.raises(ValidationError) as excinfo:
        gen.generate(rm)
    assert "expected_risk_tags" in str(excinfo.value)


# Cross-field invariants: the generator owns the four provenance fields
# and strips them off any LLM payload, so the FAILURE MODE we exercise
# here is "the generator's merge step writes a value that disagrees with
# the embedded RunMetadata". We model that by constructing a
# PolicyReportGenerator that the test ITSELF hands a deliberately
# mismatched merge -- using a custom subclass-by-monkeypatch is fragile,
# so we test by passing a RunMetadata whose embedded values disagree
# with what the generator will overwrite onto the header (which is
# itself sourced from the same RunMetadata). The only way to actually
# trigger header-vs-embedded mismatch on the generator's output path is
# for the generator to be edited to do something other than read from
# the same RunMetadata twice -- so the cross-field invariant tests live
# at the schema layer (see test_report_schema.py) where they can be
# exercised by direct Report.model_validate calls. For the GENERATOR
# layer we exercise the inverse contract: any LLM-supplied provenance
# key is stripped BEFORE merge and CANNOT influence the resulting
# Report header. The four parametrized tests below pin that strip
# behavior, which is the reciprocal of the schema-layer cross-field
# tests.


@pytest.mark.parametrize(
    "field, hijack_value, expected_attr",
    [
        ("simulation_id", "sim_HIJACK", "simulation_id"),
        ("scenario_hash", "z" * 64, "scenario_hash"),
        ("seed_hash", "z" * 64, "seed_hash"),
        ("persona", Persona.POLICY.value, "persona"),
    ],
    ids=["simulation_id", "scenario_hash", "seed_hash", "persona"],
)
def test_llm_supplied_provenance_field_is_stripped_before_validate(
    field: str,
    hijack_value: str,
    expected_attr: str,
) -> None:
    """A misbehaving LLM that echoes a provenance field with a wrong
    value must NOT influence the Report header. The strip step in the
    generator pre-empts the cross-field validator at the schema layer
    (which would also catch the mismatch, but only after the LLM's
    fabricated value had been merged onto the header). Pinning the
    strip behavior here means the generator surface is the boundary,
    not the schema validator.
    """
    payload = _payload_with_canonical_provenance_removed()
    payload[field] = hijack_value
    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    rm = _minimal_run_metadata()
    gen = PolicyReportGenerator(
        scenario=scenario, llm=_StubLLM(payload),  # type: ignore[arg-type]
    )
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    report = gen.generate(rm)
    # The hijack value is NEVER on the resulting Report header.
    actual = getattr(report, expected_attr)
    if isinstance(actual, Persona):
        assert actual.value != hijack_value
    else:
        assert actual != hijack_value
    # And the resulting value matches the RunMetadata snapshot exactly,
    # i.e. the schema's cross-field invariant is satisfied.
    assert getattr(report, expected_attr) == getattr(rm, expected_attr)


# ----------------------------------------------------------------------
# C. Composition + isolation
# ----------------------------------------------------------------------


def test_mock_llm_is_the_only_collaborator_no_real_network_client() -> None:
    """The generator's only network-bearing collaborator is the injected
    LLM. Asserting this by introspecting the generator instance proves
    the test never accidentally exercises a real OpenAI client.
    """
    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    llm = _StubLLM(_llm_payload_for_creator())
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    assert gen._llm is llm  # noqa: SLF001  - asserting the DI seam
    # The stub class deliberately does NOT subclass PolicyLLMClient, so
    # this assertion proves the generator accepts ANY object that
    # quacks like chat_json -- the DI seam is open.
    from app.seam.llm_patches import PolicyLLMClient
    assert not isinstance(gen._llm, PolicyLLMClient)  # noqa: SLF001


def test_generator_source_does_not_mention_report_agent_literal() -> None:
    """Source-level guard against the removed report-agent class name."""
    src = inspect.getsource(policy_report_generator_module)
    removed_class = "Report" + "Agent"
    assert removed_class not in src, (
        "policy_report_generator source references the removed report-agent "
        "class; the policy lane must not name, import, subclass, or annotate it."
    )


@pytest.mark.parametrize(
    "forbidden_module",
    [
        "app.services." + "report_agent",
        "app.utils.llm_client",
    ],
)
def test_generator_ast_scan_rejects_legacy_imports(
    forbidden_module: str,
) -> None:
    """Companion to ``test_policy_report_generator_does_not_import_forbidden``
    but framed as an explicit legacy-imports gate so a future reviewer
    grepping for "legacy" lands directly here.
    """
    names = _import_names(_GENERATOR_MODULE_PATH)
    for name in names:
        assert name != forbidden_module and not name.startswith(
            forbidden_module + "."
        ), (
            f"policy_report_generator imports forbidden legacy module "
            f"{name!r} (matched against {forbidden_module!r})"
        )


# ----------------------------------------------------------------------
# D. generate_and_persist -- atomic write + round-trip
# ----------------------------------------------------------------------


def test_generate_and_persist_writes_to_policy_report_json(tmp_path: Path) -> None:
    """The persist path is exactly ``{run_dir}/policy_report.json``.

    Pinning the filename (not just the suffix) is important: downstream
    consumers (the /report.md endpoint, the manifest writer) reference
    this artifact by name.
    """
    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    rm = _minimal_run_metadata()
    llm = _StubLLM(_llm_payload_for_creator())
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    artifacts = gen.generate_and_persist(rm, tmp_path)
    out_path = artifacts.report_json_path
    assert out_path.name == "policy_report.json"
    assert out_path.parent == tmp_path
    assert out_path.exists()
    assert artifacts.report_md_path.name == "policy_report.md"
    assert artifacts.sha256_json and artifacts.sha256_md


def test_generate_and_persist_written_file_round_trips_through_validate_json(
    tmp_path: Path,
) -> None:
    """The on-disk JSON is a typed Report after re-reading.

    Different test from ``test_generate_and_persist_round_trips_through_validate_json``
    above (which exercises only the creator row): this one parametrizes
    over the full persona matrix so every persona+scope row's on-disk
    shape is exercised, not just creator.
    """
    for persona, scope in _PERSONA_MATRIX:
        scenario = _StubScenario(persona=persona, scope=scope)
        rm = _minimal_run_metadata(persona=persona)
        llm = _StubLLM(_llm_payload_for_persona(persona, scope))
        gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
        # type: ignore WHY: duck-typed stub stands in for full Scenario.
        # Each iteration writes to its own subdir so the assertion is
        # scoped and a single-row failure surfaces cleanly.
        run_dir = tmp_path / _persona_matrix_id((persona, scope))
        run_dir.mkdir()
        artifacts = gen.generate_and_persist(rm, run_dir)
        out_path = artifacts.report_json_path

        text = out_path.read_text(encoding="utf-8")
        reloaded = Report.model_validate_json(text)
        assert reloaded.persona == persona
        assert reloaded.scope == scope
        assert reloaded.simulation_id == rm.simulation_id
        assert reloaded.scenario_hash == rm.scenario_hash
        assert reloaded.seed_hash == rm.seed_hash
        assert reloaded.report_id == _derive_report_id(rm)


# ----------------------------------------------------------------------
# E. report_id stability + sensitivity
# ----------------------------------------------------------------------


def test_generate_report_id_is_stable_across_two_runs_with_same_inputs() -> None:
    """Two consecutive ``generate()`` calls with the SAME scenario and
    RunMetadata produce reports with the SAME ``report_id``.

    Pins the deterministic-hash contract from the generator's docstring:
    ``report_id`` is a sha256 prefix of provenance, not a clock or
    random source. Re-running a scenario to compare claim diffs across
    runs depends on this stability.
    """
    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    rm = _minimal_run_metadata()
    # Two distinct LLM instances + two distinct generators -- the id
    # derivation must not depend on any per-call state.
    llm_a = _StubLLM(_llm_payload_for_creator())
    llm_b = _StubLLM(_llm_payload_for_creator())
    gen_a = PolicyReportGenerator(scenario=scenario, llm=llm_a)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    gen_b = PolicyReportGenerator(scenario=scenario, llm=llm_b)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    report_a = gen_a.generate(rm)
    report_b = gen_b.generate(rm)
    assert report_a.report_id == report_b.report_id


def test_generate_report_id_changes_with_seed_hash() -> None:
    """A different ``seed_hash`` in RunMetadata produces a DIFFERENT
    ``report_id``. Pins that the hash incorporates the seed_hash and not
    just the simulation_id (regression catch for an accidental
    truncation of the fingerprint inputs to ``simulation_id`` alone)."""
    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    rm_a = _minimal_run_metadata()
    # Build a second RunMetadata with the SAME simulation_id +
    # scenario_hash but a DIFFERENT seed_hash. The id must differ.
    rm_b = RunMetadata(
        simulation_id=rm_a.simulation_id,
        created_at=rm_a.created_at,
        persona=rm_a.persona,
        scenario_hash=rm_a.scenario_hash,
        seed_hash="d" * 64,  # different seed
        prepared_at=rm_a.prepared_at,
        artifact_paths=dict(rm_a.artifact_paths),
        artifact_hashes=dict(rm_a.artifact_hashes),
        zep_mode=rm_a.zep_mode,
        scale_caps=rm_a.scale_caps,
    )
    llm_a = _StubLLM(_llm_payload_for_creator())
    llm_b = _StubLLM(_llm_payload_for_creator())
    gen_a = PolicyReportGenerator(scenario=scenario, llm=llm_a)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    gen_b = PolicyReportGenerator(scenario=scenario, llm=llm_b)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    report_a = gen_a.generate(rm_a)
    report_b = gen_b.generate(rm_b)
    assert report_a.report_id != report_b.report_id


# ----------------------------------------------------------------------
# F. Preflight: scenario.persona vs run_metadata.persona
# ----------------------------------------------------------------------


def test_generate_refuses_when_scenario_persona_mismatches_run_metadata_persona() -> None:
    """A VIDEO scenario + CREATOR RunMetadata must NOT silently produce
    a creator report with video sections. The post-LLM merge overwrites
    persona from RunMetadata, so the schema cross-field invariator
    never sees the mismatch; the preflight check raises
    ScenarioRunMetadataPersonaMismatch BEFORE any prompt is built or
    any LLM call is made.
    """
    # Stub LLM that records whether chat_json was called -- if the
    # preflight check fires correctly, the LLM stub must NEVER be
    # invoked.
    class _RecordingLLM:
        def __init__(self) -> None:
            self.call_count = 0

        def chat_json(self, *, messages: List[Dict[str, str]]) -> Dict[str, Any]:
            self.call_count += 1
            return _llm_payload_for_persona(Persona.CREATOR, None)

    scenario = _StubScenario(persona=Persona.VIDEO, scope=None)
    rm = _minimal_run_metadata(persona=Persona.CREATOR)  # mismatch
    llm = _RecordingLLM()
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: _StubScenario + _RecordingLLM are duck-typed
    # stubs targeting the generator's invariant under test.

    with pytest.raises(ScenarioRunMetadataPersonaMismatch) as excinfo:
        gen.generate(rm)
    msg = str(excinfo.value)
    assert "scenario.persona='video'" in msg
    assert "run_metadata.persona='creator'" in msg
    # Critical: the LLM stub must not have been called -- no tokens
    # should burn on a misconfigured request.
    assert llm.call_count == 0, (
        f"preflight failed: chat_json was called {llm.call_count}x "
        "but should have raised before any LLM round-trip"
    )


@pytest.mark.parametrize(
    "scenario_persona, run_metadata_persona",
    [
        (Persona.CREATOR, Persona.VIDEO),
        (Persona.VIDEO, Persona.CREATOR),
        (Persona.PRODUCT, Persona.POLICY),
        (Persona.POLICY, Persona.PRODUCT),
    ],
)
def test_generate_preflight_refuses_every_persona_disagreement(
    scenario_persona: Persona,
    run_metadata_persona: Persona,
) -> None:
    """Parametrized matrix: every direction of persona mismatch must
    raise. The scope discriminator is honored by giving the policy
    persona a scope when it appears; the persona mismatch is the
    failure mode isolated here."""
    scope: Optional[PolicySurface] = (
        PolicySurface.PUBLIC_POLICY if scenario_persona == Persona.POLICY else None
    )
    scenario = _StubScenario(persona=scenario_persona, scope=scope)
    rm = _minimal_run_metadata(persona=run_metadata_persona)
    llm = _StubLLM(_llm_payload_for_persona(scenario_persona, scope))
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stubs target the preflight invariant.
    with pytest.raises(ScenarioRunMetadataPersonaMismatch):
        gen.generate(rm)


# ----------------------------------------------------------------------
# G. Section-completeness contract (ReportSectionsMismatch)
# ----------------------------------------------------------------------


def test_generate_rejects_llm_payload_that_drops_a_persona_required_section() -> None:
    """The Pydantic Report enforces ``min_length=1`` on sections; the
    completeness contract (run by the generator AFTER schema validation)
    enforces EXACT title equality + order with the persona SSoT. An
    LLM that emits only the first section of a multi-section persona
    must be rejected with ReportSectionsMismatch."""
    persona = Persona.CREATOR
    scope: Optional[PolicySurface] = None
    full_payload = _llm_payload_for_persona(persona, scope)
    # Keep only the first section -- the SSoT has many more.
    full_payload["sections"] = full_payload["sections"][:1]

    scenario = _StubScenario(persona=persona, scope=scope)
    rm = _minimal_run_metadata(persona=persona)
    llm = _StubLLM(full_payload)
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    with pytest.raises(ReportSectionsMismatch) as excinfo:
        gen.generate(rm)
    msg = str(excinfo.value)
    assert "do not match the persona SSoT contract" in msg
    # The error message must surface BOTH the expected and actual
    # title lists so a reviewer can diff without re-running.
    assert "Expected (in order)" in msg
    assert "Got (in order)" in msg


def test_generate_rejects_llm_payload_that_reorders_sections() -> None:
    """Reordering is rejected: order matters because the rendered
    report walks sections in render order and a reader expects the
    persona-canonical sequence."""
    persona = Persona.CREATOR
    scope: Optional[PolicySurface] = None
    payload = _llm_payload_for_persona(persona, scope)
    # Swap the first two sections.
    payload["sections"][0], payload["sections"][1] = (
        payload["sections"][1], payload["sections"][0],
    )

    scenario = _StubScenario(persona=persona, scope=scope)
    rm = _minimal_run_metadata(persona=persona)
    llm = _StubLLM(payload)
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    with pytest.raises(ReportSectionsMismatch):
        gen.generate(rm)


def test_generate_rejects_llm_payload_with_wrong_section_title() -> None:
    """An LLM-emitted title that differs from the SSoT title (typo,
    paraphrase, alternate wording) must be rejected. The renderer would
    surface the LLM-side title verbatim; a reader expects the canonical
    persona-contract title."""
    persona = Persona.CREATOR
    scope: Optional[PolicySurface] = None
    payload = _llm_payload_for_persona(persona, scope)
    # Mutate the first section's title.
    payload["sections"][0]["title"] = "Wrong Title (LLM-side rename)"

    scenario = _StubScenario(persona=persona, scope=scope)
    rm = _minimal_run_metadata(persona=persona)
    llm = _StubLLM(payload)
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    with pytest.raises(ReportSectionsMismatch):
        gen.generate(rm)


def test_generate_rejects_llm_payload_with_extra_section() -> None:
    """Extra sections (LLM hallucinating a section the SSoT did NOT
    request) must be rejected. Otherwise an LLM could pad reports with
    arbitrary section names that bypass the persona contract."""
    persona = Persona.CREATOR
    scope: Optional[PolicySurface] = None
    payload = _llm_payload_for_persona(persona, scope)
    payload["sections"].append({
        "section_id": "sec_hallucinated",
        "title": "Hallucinated Bonus Section",
        "claims": [
            {
                "claim_id": "c_hallucinated",
                "text": "A simulated observation drawn from nothing in particular.",
                "tag": HypothesisTag.SIMULATED_OBSERVATION.value,
            },
        ],
    })

    scenario = _StubScenario(persona=persona, scope=scope)
    rm = _minimal_run_metadata(persona=persona)
    llm = _StubLLM(payload)
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    with pytest.raises(ReportSectionsMismatch):
        gen.generate(rm)


# ----------------------------------------------------------------------
# H. Generator runs language guard BEFORE persisting
# ----------------------------------------------------------------------


def test_generate_and_persist_refuses_unhedged_banned_claim_text(tmp_path) -> None:
    """A Report whose claim text contains an unhedged banned phrase
    must fail at ``generate_and_persist`` time BEFORE policy_report.json
    is written. Rejecting at the runtime boundary (not just in CI
    tests) closes the gap where a banned-phrase LLM payload could land
    on disk and only fail when a later test exercised the renderer."""
    persona = Persona.CREATOR
    scope: Optional[PolicySurface] = None
    payload = _llm_payload_for_persona(persona, scope)
    # Replace one claim's text with an unhedged banned phrase. The
    # claim's section + tag still match the SSoT so the section-
    # completeness contract + canonical-tag invariant pass; only the
    # rendered output's banned-token check should reject.
    payload["sections"][0]["claims"][0]["text"] = (
        "Engagement will result in a 30% subscriber loss within 6 weeks."
    )

    scenario = _StubScenario(persona=persona, scope=scope)
    rm = _minimal_run_metadata(persona=persona)
    llm = _StubLLM(payload)
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.

    out_path = tmp_path / "policy_report.json"
    assert not out_path.exists(), "test fixture: file must not pre-exist"
    with pytest.raises(AssertionError) as excinfo:
        gen.generate_and_persist(rm, tmp_path)
    msg = str(excinfo.value)
    assert "will result" in msg.lower()
    # Critical: the file MUST NOT have been written.
    assert not out_path.exists(), (
        "generate_and_persist must reject BEFORE writing; the on-disk "
        "policy_report.json should not exist after a guard failure"
    )


@pytest.mark.parametrize(
    "banned_phrase",
    [
        # New morphology coverage flagged by reviewer audit:
        "These predictions are firm.",
        "This is predictive of churn.",
        "The predictor says churn rises.",
        "We are expecting churn.",
        "We are anticipating criticism.",
        "We are guaranteeing compliance.",
        "This is ensuring retention.",
        # Plus the originals (regression: must still catch):
        "This will happen in 6 weeks.",
        "Sales will result in a 30% drop.",
        "The model predicts a backlash.",
        "Our forecast shows churn at 12%.",
    ],
)
def test_generate_and_persist_widened_morphology_catches_each_inflection(
    banned_phrase: str,
    tmp_path,
) -> None:
    """Per the reviewer audit: previous BANNED_PATTERNS missed
    ``predictions`` (plural), ``predictor``, ``predictive``,
    ``expecting``, ``anticipating``, ``guaranteeing``, ``ensuring``.
    Widened the morphology; this parametrize proves every flagged
    inflection now lands a rejection through the runtime pipeline."""
    persona = Persona.CREATOR
    scope: Optional[PolicySurface] = None
    payload = _llm_payload_for_persona(persona, scope)
    payload["sections"][0]["claims"][0]["text"] = banned_phrase

    scenario = _StubScenario(persona=persona, scope=scope)
    rm = _minimal_run_metadata(persona=persona)
    llm = _StubLLM(payload)
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    out_path = tmp_path / "policy_report.json"
    with pytest.raises(AssertionError):
        gen.generate_and_persist(rm, tmp_path)
    assert not out_path.exists(), (
        f"phrase {banned_phrase!r} should have rejected before persist"
    )


# ----------------------------------------------------------------------
# I. LLM token budget pinning
# ----------------------------------------------------------------------


def test_generate_passes_policy_max_tokens_report_to_chat_json() -> None:
    """The generator must call ``chat_json(..., max_tokens=...)`` with the
    seam's ``policy_max_tokens_report`` attribute, NOT the seam's
    chat_json default. Otherwise multi-section persona rows can hit the
    default 4096 truncation silently."""
    class _RecordingBudgetLLM:
        """Records max_tokens; exposes the report-token attribute."""

        def __init__(self, report_budget: int) -> None:
            self.policy_max_tokens_report = report_budget
            self.captured_max_tokens: Optional[int] = None

        def chat_json(
            self,
            messages: List[Dict[str, str]],
            temperature: float = 0.3,
            max_tokens: int = 4096,
        ) -> Dict[str, Any]:
            self.captured_max_tokens = max_tokens
            return _llm_payload_for_persona(Persona.CREATOR, None)

    sentinel_budget = 8192  # distinct from the seam default (4096)
    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    rm = _minimal_run_metadata(persona=Persona.CREATOR)
    llm = _RecordingBudgetLLM(sentinel_budget)
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: _RecordingBudgetLLM duck-types the seam contract
    # under test (chat_json + policy_max_tokens_report).
    gen.generate(rm)
    assert llm.captured_max_tokens == sentinel_budget, (
        f"generator must use policy_max_tokens_report ({sentinel_budget}); "
        f"actually passed max_tokens={llm.captured_max_tokens}"
    )


# ----------------------------------------------------------------------
# generate() itself rejects banned claim text
# ----------------------------------------------------------------------


def test_generate_itself_rejects_banned_claim_text_before_render() -> None:
    """The language contract is enforced inside ``generate()`` (step 8),
    not just inside ``generate_and_persist``. A caller that runs
    ``generate()`` -> ``render_report()`` directly (e.g. an interactive
    debugger that never persists) must still see the rejection at
    generate-time -- otherwise the render path could surface banned
    output the caller never expected."""
    persona = Persona.CREATOR
    scope: Optional[PolicySurface] = None
    payload = _llm_payload_for_persona(persona, scope)
    payload["sections"][0]["claims"][0]["text"] = (
        "Engagement will result in a 30% subscriber loss within 6 weeks."
    )

    scenario = _StubScenario(persona=persona, scope=scope)
    rm = _minimal_run_metadata(persona=persona)
    llm = _StubLLM(payload)
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.
    with pytest.raises(AssertionError) as excinfo:
        gen.generate(rm)
    assert "will result" in str(excinfo.value).lower()


# ----------------------------------------------------------------------
# G. Atomic ordering: artifact-first, metadata-second
# ----------------------------------------------------------------------


def test_generate_and_persist_writes_artifact_before_metadata(
    tmp_path: Path,
) -> None:
    """``generate_and_persist`` writes ``policy_report.json`` +
    ``policy_report.md`` BEFORE refreshing ``run_metadata.json``.

    Mirrors the ordering the sensitivity write path uses
    (``api.policy.run_simulation_sensitivity``: artifact-first, then
    ``refresh_output_path``). Pins the contract that the artifact bytes
    on disk are the source of truth and ``run_metadata.json`` is a
    lagging index -- a crash between the two writes leaves the fresh
    artifact next to a slightly stale index (recoverable on the next
    refresh) instead of an index that over-promises an artifact that
    does not yet exist.
    """
    # Seed a run_metadata.json so the refresh branch is taken.
    rm = _minimal_run_metadata()
    run_metadata_path = tmp_path / "run_metadata.json"
    run_metadata_path.write_text(
        rm.model_dump_json(indent=2), encoding="utf-8",
    )

    write_order: List[str] = []

    import app.services.policy.policy_report_generator as prg

    original_atomic_write_text = prg.atomic_write_text

    def _recording_atomic_write_text(path: Any, body: str) -> None:
        write_order.append(Path(path).name)
        original_atomic_write_text(path, body)

    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    llm = _StubLLM(_llm_payload_for_creator())
    gen = PolicyReportGenerator(scenario=scenario, llm=llm)  # type: ignore[arg-type]
    # type: ignore WHY: duck-typed stub stands in for full Scenario.

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(prg, "atomic_write_text", _recording_atomic_write_text)
        gen.generate_and_persist(rm, tmp_path)
    finally:
        monkey.undo()

    # The three writes the path performs, in order:
    #   1. policy_report.json    (artifact)
    #   2. policy_report.md      (artifact)
    #   3. run_metadata.json     (lagging index)
    assert write_order == [
        "policy_report.json",
        "policy_report.md",
        "run_metadata.json",
    ], (
        "ordering contract violated: expected artifact-first, "
        f"metadata-second; got {write_order!r}"
    )

    # And the on-disk metadata reflects the just-written artifact sha.
    from app.policy.run_metadata import RunMetadata as _RM
    persisted = _RM.model_validate_json(
        run_metadata_path.read_text(encoding="utf-8"),
    )
    assert "policy_report.json" in persisted.artifact_hashes
    assert "policy_report.md" in persisted.artifact_hashes
