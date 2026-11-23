"""Pins the pure prompt-builder helpers used by the policy report generator.

These tests cover:
  * determinism of :func:`render_section_list_for_prompt`
  * 5-tag taxonomy enumeration completeness in the system prompt
  * the system prompt is paraphrased (does NOT verbatim-quote any
    plan-doc paragraph)
  * per-persona section list resolution (creator vs policy.public_policy
    produce different enumerations)
  * import-graph purity (mirrors the renderer's purity test): the module
    only imports from a small allowlisted set; no clock / env / fs /
    network / LLM / Flask / report-agent dependency.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List

from app.policy.hypothesis_tags import HypothesisTag
from app.policy.persona_defaults import report_sections_for
from app.policy.schema import Persona, PolicySurface
from app.services.policy.prompt_builder import (
    build_system_prompt,
    build_user_prompt,
    render_section_list_for_prompt,
)


# ----------------------------------------------------------------------
# render_section_list_for_prompt
# ----------------------------------------------------------------------


def test_render_section_list_for_prompt_is_deterministic() -> None:
    """Same input list -> byte-identical output string across two calls."""
    sections: List[Dict[str, str]] = [
        {"title": "Executive summary", "claim_guidance": "3-5 claims"},
        {
            "title": "Engagement themes",
            "claim_guidance": "What topics agents engaged with most",
        },
        {"title": "Closing notes", "claim_guidance": ""},
    ]
    first = render_section_list_for_prompt(sections)
    second = render_section_list_for_prompt(sections)
    assert first == second
    # Spot-check the numbering + format.
    assert first.splitlines()[0].startswith("1. Executive summary -- ")
    assert first.splitlines()[1].startswith("2. Engagement themes -- ")
    # Missing claim_guidance falls back to title-only (no trailing ' -- ').
    assert first.splitlines()[2] == "3. Closing notes"


def test_render_section_list_for_prompt_matches_ssot_for_creator() -> None:
    """The helper consumes the SSoT dicts as-is (no key remapping)."""
    sections = report_sections_for(Persona.CREATOR, None)
    rendered = render_section_list_for_prompt(sections)
    # Every SSoT title must appear verbatim in the rendered block.
    for section in sections:
        assert section["title"] in rendered


# ----------------------------------------------------------------------
# build_system_prompt
# ----------------------------------------------------------------------


def test_build_system_prompt_includes_all_5_canonical_tags() -> None:
    """Every HypothesisTag value appears in the rendered system prompt.

    Iterating the enum (rather than hardcoding 5 strings) ties this test
    to the canonical enum -- adding a 6th tag would surface here too.
    """
    sections = report_sections_for(Persona.CREATOR, None)
    section_text = render_section_list_for_prompt(sections)
    prompt = build_system_prompt(Persona.CREATOR, None, section_text)
    for tag in HypothesisTag:
        assert tag.value in prompt, (
            f"HypothesisTag value {tag.value!r} missing from system prompt"
        )


def test_build_system_prompt_paraphrases_template() -> None:
    """The prompt body must NOT verbatim-quote any plan-doc paragraph.

    We check against sentinel substrings drawn from the source planning
    paragraph the prompt is conceptually derived from. The prompt
    expresses the same contract in code-vocabulary terms, so the
    planning-doc sentences themselves must not appear.
    """
    sections = report_sections_for(Persona.POLICY, PolicySurface.PUBLIC_POLICY)
    section_text = render_section_list_for_prompt(sections)
    prompt = build_system_prompt(
        Persona.POLICY, PolicySurface.PUBLIC_POLICY, section_text
    )
    # Verbatim sentinel fragments from the source paragraph the prompt
    # is paraphrasing. If any of these substrings appears, the prompt
    # has been copy-pasted rather than paraphrased.
    forbidden_verbatim = [
        "You are a policy / audience reaction report generator.",
        "an analyst inference drawn from simulated observations",
        "a restated fact from the input seed_text / seed material",
        "an open question demanding real-world evidence before action",
        "Return only the JSON object. No surrounding prose.",
    ]
    for fragment in forbidden_verbatim:
        assert fragment not in prompt, (
            f"System prompt verbatim-quotes plan-doc sentence: {fragment!r}"
        )


def test_build_system_prompt_per_persona_changes_section_list() -> None:
    """Creator vs policy.public_policy produce different section blocks."""
    creator_sections = report_sections_for(Persona.CREATOR, None)
    creator_text = render_section_list_for_prompt(creator_sections)
    creator_prompt = build_system_prompt(Persona.CREATOR, None, creator_text)

    policy_sections = report_sections_for(
        Persona.POLICY, PolicySurface.PUBLIC_POLICY
    )
    policy_text = render_section_list_for_prompt(policy_sections)
    policy_prompt = build_system_prompt(
        Persona.POLICY, PolicySurface.PUBLIC_POLICY, policy_text
    )

    assert creator_prompt != policy_prompt
    # Spot-check: a policy-only section title appears in the policy
    # prompt but not the creator prompt, and vice versa.
    assert "Stakeholder reaction map" in policy_prompt
    assert "Stakeholder reaction map" not in creator_prompt
    assert "Engagement themes" in creator_prompt
    assert "Engagement themes" not in policy_prompt
    # Context line carries the correct discriminator on each side.
    assert "persona=creator scope=n/a" in creator_prompt
    assert "persona=policy scope=public_policy" in policy_prompt


def test_build_system_prompt_includes_banned_and_hedge_guidance() -> None:
    """The prompt must instruct the LLM about the banned phrases + hedges.

    The prompt itself may carry the literal banned words because it is
    INSTRUCTING the LLM not to emit them unhedged; the banned-token
    regression gate scans rendered REPORT markdown, not the system
    prompt.
    """
    sections = report_sections_for(Persona.CREATOR, None)
    section_text = render_section_list_for_prompt(sections)
    prompt = build_system_prompt(Persona.CREATOR, None, section_text)
    # A representative banned word and a representative hedge word.
    assert "predict" in prompt
    assert "forecast" in prompt
    assert "hypothesis" in prompt
    assert "may" in prompt


# ----------------------------------------------------------------------
# build_user_prompt
# ----------------------------------------------------------------------


class _StubSegment:
    def __init__(self, segment_id: str, name: str, seg_type: str) -> None:
        self.segment_id = segment_id
        self.name = name
        self.type = seg_type


class _StubScenario:
    """Duck-typed Scenario stand-in for prompt-builder tests.

    Carries only the attributes ``build_user_prompt`` reads. Using a
    stub avoids the SCALE_CAP_POC validation noise that a full Scenario
    instance would otherwise add to this purely lexical test.
    """

    def __init__(
        self,
        *,
        persona: Persona,
        scope: "PolicySurface | None",
    ) -> None:
        self.persona = persona
        self.scope = scope
        self.title = "Test title"
        self.summary = "Short summary"
        self.jurisdiction = "TestLand"
        self.seed_text = "Seed body."
        self.stress_question = "What breaks first?"
        self.assumptions = ["Assumption A", "Assumption B"]
        self.success_criteria = ["Success X"]
        self.failure_modes_to_watch = ["Failure mode Y"]
        self.stakeholders = [
            _StubSegment("s_one", "Cohort one", "follower_cohort"),
        ]


class _StubRunMetadata:
    def __init__(self) -> None:
        self.simulation_id = "sim_test_001"
        self.scenario_hash = "abc123"
        self.seed_hash = "def456"


def test_build_user_prompt_returns_system_then_user_message_pair() -> None:
    scenario = _StubScenario(persona=Persona.CREATOR, scope=None)
    run_metadata = _StubRunMetadata()
    messages = build_user_prompt(scenario, run_metadata)
    assert isinstance(messages, list)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    # User message carries the scenario context + provenance fields.
    user = messages[1]["content"]
    assert "Test title" in user
    assert "Seed body." in user
    assert "What breaks first?" in user
    assert "Cohort one" in user
    assert "sim_test_001" in user
    assert "abc123" in user
    assert "def456" in user


# ----------------------------------------------------------------------
# Import-graph purity (mirrors markdown_renderer test)
# ----------------------------------------------------------------------


_PROMPT_BUILDER_MODULE_PATH = (
    Path(__file__).resolve().parents[3]
    / "app" / "services" / "policy" / "prompt_builder.py"
)


def _import_names(path: Path) -> set[str]:
    """Return every dotted module name appearing in ``import`` /
    ``from ... import`` statements anywhere in the source file.

    Mirrors the helper in ``tests/policy/test_markdown_renderer.py``.
    AST-based so lazy imports inside function bodies are surfaced too.
    Relative-import dots are stripped by ``ast.ImportFrom`` -- a
    ``from ...policy.schema import X`` therefore surfaces as the bare
    ``policy.schema``.
    """
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


def test_prompt_builder_imports_are_minimal_and_pure() -> None:
    """Prompt builder imports a small allowlisted set only.

    Forbidden import categories mirror the renderer's purity contract:
    no clock / env / filesystem / random / network / LLM / Flask /
    report-agent dependency. The allowlist below is the small set of safe
    dependencies the prompt builder is permitted to use.
    """
    names = _import_names(_PROMPT_BUILDER_MODULE_PATH)
    allowed = {
        "typing",
        "__future__",
        "policy.schema",
        "policy.persona_defaults",
        "policy.hypothesis_tags",
        "policy_core.scope_context",
    }
    extras = names - allowed
    assert not extras, (
        f"prompt_builder imports outside the pure allowlist: "
        f"{sorted(extras)!r}. Allowed: {sorted(allowed)!r}."
    )


def test_prompt_builder_does_not_import_forbidden_modules() -> None:
    """No forbidden top-level OR lazy import. Catches both ``import X`` and
    ``from X import Y`` forms; the AST scan also catches lazy imports
    inside function bodies."""
    forbidden = [
        "datetime",
        "time",
        "os",
        "pathlib",
        "random",
        "secrets",
        "socket",
        "urllib",
        "requests",
        "flask",
        "app.utils.llm_client",
        "app.seam.llm_patches",
        "app.services." + "report_agent",
    ]
    names = _import_names(_PROMPT_BUILDER_MODULE_PATH)
    for name in names:
        for bad in forbidden:
            assert name != bad and not name.startswith(bad + "."), (
                f"prompt_builder imports forbidden module {name!r} "
                f"(matched against {bad!r})"
            )
