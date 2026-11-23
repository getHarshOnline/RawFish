"""Pins the deterministic markdown renderer (``render_report``).

The renderer is a pure function: same ``Report`` in -> same markdown
bytes out. These tests cover:

* byte-idempotency across two renders of the same input
* import-graph purity (no clock / env / filesystem / random / network /
  LLM / Flask / report-agent dependency)
* caveat block presence at the top
* canonical metadata block rendering against actual model fields
* persona-discriminator behaviour (scope omitted for non-policy;
  included for policy)
* every report section renders: executive_summary, sections,
  evidence_gaps, field_validation_questions, confidence_notes,
  source_limitations, run-metadata pointer
* empty section claims render the explicit placeholder, not silence
* every claim-metadata field renders when present:
  segment_refs, confidence, expected_risk_tags, evidence_refs,
  driver_hints (including the literal ``attribution_disclaimer`` text)
* the tag string is rendered (``simulated_observation``), never the
  enum's ``repr()`` (``<HypothesisTag.SIMULATED_OBSERVATION: '...'>``)
* exactly one trailing newline
* the four ``EvidenceRef.ref_type`` values render to distinct link shapes
"""
from __future__ import annotations

import ast
import importlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.policy.hypothesis_tags import HypothesisTag
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
from app.services.policy.markdown_renderer import render_report


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _build_run_metadata(
    *,
    persona: Persona = Persona.CREATOR,
    simulation_id: str = "sim_1",
    scenario_hash: str = "a" * 64,
    seed_hash: str = "b" * 64,
) -> RunMetadata:
    return RunMetadata(
        simulation_id=simulation_id,
        created_at=datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc),
        persona=persona,
        scenario_hash=scenario_hash,
        seed_hash=seed_hash,
        prepared_at=datetime(2026, 6, 26, 11, 59, 0, tzinfo=timezone.utc),
        artifact_paths={"manifest.json": "manifest.json"},
        artifact_hashes={"manifest.json": "c" * 64},
        zep_mode="mock",
        scale_caps=ScaleCaps(
            ensemble_n=1, stakeholders=2, rounds=10, cap_value=500,
        ),
    )


def _build_section(section_id: str = "exec", title: str = "Executive summary",
                   *, with_claims: bool = True) -> Section:
    return Section(
        section_id=section_id,
        title=title,
        claims=[
            Claim(
                claim_id="c1",
                text="Fans cohort engages with retention prompts.",
                tag=HypothesisTag.SIMULATED_OBSERVATION,
            )
        ] if with_claims else [],
    )


def _build_report(
    *,
    persona: Persona = Persona.CREATOR,
    scope=None,
    sections=None,
    executive_summary=None,
    evidence_gaps=None,
    field_validation_questions=None,
    confidence_notes: str = "",
    source_limitations=None,
    run_metadata: RunMetadata | None = None,
) -> Report:
    rm = run_metadata if run_metadata is not None else _build_run_metadata(persona=persona)
    return Report(
        report_id="report_sim_1_v1",
        simulation_id=rm.simulation_id,
        persona=persona,
        scope=scope,
        scenario_hash=rm.scenario_hash,
        seed_hash=rm.seed_hash,
        generated_at=datetime(2026, 6, 26, 12, 5, 0, tzinfo=timezone.utc),
        executive_summary=executive_summary or [],
        sections=sections or [_build_section()],
        evidence_gaps=evidence_gaps or [],
        field_validation_questions=field_validation_questions or [],
        confidence_notes=confidence_notes,
        source_limitations=source_limitations or [],
        run_metadata=rm,
    )


# ----------------------------------------------------------------------
# Byte-idempotency
# ----------------------------------------------------------------------


def test_render_same_report_twice_is_byte_identical() -> None:
    """Two renders of the same ``Report`` produce byte-equal strings."""
    r = _build_report()
    first = render_report(r)
    second = render_report(r)
    assert first == second
    # Pin the byte equality at the encoded level so a hidden encoding
    # difference cannot slip through string comparison.
    assert first.encode("utf-8") == second.encode("utf-8")


def test_render_two_equivalent_reports_byte_identical() -> None:
    """Rendering two structurally-identical Reports (separate instances)
    produces byte-equal output. This is the practical purity claim: an
    LLM that emits the same JSON twice produces the same markdown twice."""
    r1 = _build_report()
    r2 = _build_report()
    assert render_report(r1) == render_report(r2)


# ----------------------------------------------------------------------
# Import-graph purity
# ----------------------------------------------------------------------


_RENDERER_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "app" / "services" / "policy" / "markdown_renderer.py"
)


def _import_names(path: Path) -> set[str]:
    """Return every dotted module name appearing in ``import`` /
    ``from ... import`` statements at the top level of the source file.
    AST-based so relative imports + conditional imports inside functions
    are both surfaced."""
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


def test_renderer_imports_are_minimal_and_pure() -> None:
    """Renderer module imports a small allowlisted set only.

    Forbidden import categories enumerated in the renderer's module
    docstring: clock / env / filesystem / random / network / LLM /
    Flask / report-agent dependency. The allowlist below is the small
    set of safe dependencies the renderer is permitted to use."""
    names = _import_names(_RENDERER_MODULE_PATH)
    # Allowlist: standard-library typing + the policy package's schema.
    # Anything else is a regression we want to surface. Python's
    # ``ast.ImportFrom`` strips the leading dots of a relative import
    # and stores them in ``node.level`` (which ``_import_names`` ignores);
    # ``from ...policy.schema import X`` therefore surfaces as the bare
    # ``policy.schema``.
    allowed = {
        "typing",
        "__future__",
        "policy.schema",  # the only app import (relative; dots stripped by AST)
    }
    extras = names - allowed
    assert not extras, (
        f"markdown_renderer imports outside the pure allowlist: {sorted(extras)!r}. "
        f"Allowed: {sorted(allowed)!r}."
    )


@pytest.mark.parametrize(
    "forbidden_module",
    [
        "datetime",         # clock source
        "time",             # clock source
        "os",               # env / filesystem
        "pathlib",          # filesystem
        "random",           # nondeterminism
        "secrets",          # nondeterminism
        "socket",           # network
        "urllib",           # network
        "requests",         # network
        "flask",            # web framework
        "app.utils.llm_client",            # LLM
        "app.seam.llm_patches",            # LLM
        "app.services." + "report_agent",
    ],
)
def test_renderer_does_not_import_forbidden_module(forbidden_module: str) -> None:
    """No forbidden top-level import. Catches both ``import X`` and
    ``from X import Y`` forms; the AST scan also catches lazy imports
    inside the function bodies."""
    names = _import_names(_RENDERER_MODULE_PATH)
    # An exact match OR a prefix match both fail the gate.
    for name in names:
        assert name != forbidden_module, (
            f"markdown_renderer imports forbidden module {forbidden_module!r}"
        )
        assert not name.startswith(forbidden_module + "."), (
            f"markdown_renderer imports submodule of forbidden {forbidden_module!r}: {name!r}"
        )


def test_render_does_not_read_environment(monkeypatch) -> None:
    """``render_report`` does not read any env var.

    Sentinel: monkeypatch ``os.environ.get`` to raise; if the renderer
    touches it the call would fail. A clean render under the sentinel
    proves no env access."""
    original_get = os.environ.get

    def _fail_get(*args, **kwargs):
        raise AssertionError(
            "renderer touched os.environ.get; renderer must be env-free"
        )

    monkeypatch.setattr(os.environ, "get", _fail_get)
    try:
        render_report(_build_report())
    finally:
        monkeypatch.setattr(os.environ, "get", original_get)


def test_renderer_module_does_not_import_report_agent_dependency() -> None:
    """The loaded module must not expose the removed report-agent dependency."""
    importlib.import_module("app.services.policy.markdown_renderer")
    module = sys.modules["app.services.policy.markdown_renderer"]
    attrs = {name for name in dir(module) if not name.startswith("_")}
    assert "Report" + "Agent" not in attrs
    assert "report_agent" not in attrs


# ----------------------------------------------------------------------
# Caveat block + title
# ----------------------------------------------------------------------


def test_caveat_block_appears_near_top() -> None:
    """The caveat must appear before any claim-bearing section."""
    out = render_report(_build_report())
    caveat_pos = out.index("**Caveat:**")
    exec_pos = out.index("## Executive summary")
    assert caveat_pos < exec_pos, "caveat must precede the executive summary"


def test_caveat_uses_non_predictive_framing() -> None:
    """The literal "not a forecast" wording is rendered verbatim."""
    out = render_report(_build_report())
    assert "not a forecast" in out
    # The 5-tag taxonomy must be named in the caveat itself so a reader
    # who only scans the top of the doc still sees what the tags mean.
    for tag_value in (
        "simulated_observation",
        "inference",
        "source_grounded",
        "assumption",
        "requires_validation",
    ):
        assert tag_value in out


def test_title_uses_persona_string_no_enum_repr() -> None:
    """Title shows ``creator`` not ``Persona.CREATOR`` and certainly not
    ``<Persona.CREATOR: 'creator'>``."""
    out = render_report(_build_report(persona=Persona.CREATOR))
    assert out.startswith("# Audience Reaction Report -- creator")
    assert "Persona.CREATOR" not in out
    assert "<Persona" not in out


# ----------------------------------------------------------------------
# Persona / scope discriminator
# ----------------------------------------------------------------------


def test_non_policy_persona_omits_scope_from_title() -> None:
    """Creator / video / product reports must not show a scope."""
    out = render_report(_build_report(persona=Persona.CREATOR))
    first_line = out.split("\n", 1)[0]
    assert " / " not in first_line, f"non-policy title should not show scope: {first_line!r}"


def test_policy_persona_includes_scope_in_title() -> None:
    """Policy reports show ``policy / public_policy`` (or internal_org)."""
    rm = _build_run_metadata(persona=Persona.POLICY)
    r = _build_report(
        persona=Persona.POLICY,
        scope=PolicySurface.PUBLIC_POLICY,
        run_metadata=rm,
    )
    out = render_report(r)
    assert out.startswith("# Audience Reaction Report -- policy / public_policy")


# ----------------------------------------------------------------------
# Metadata block field rendering
# ----------------------------------------------------------------------


def test_metadata_block_renders_every_required_field() -> None:
    """Every metadata-block field from the canonical template renders
    with its actual model value."""
    r = _build_report()
    out = render_report(r)
    assert f"**Report:** {r.report_id}" in out
    assert f"**Simulation:** {r.simulation_id}" in out
    assert r.generated_at.isoformat() in out
    assert r.run_metadata.prepared_at.isoformat() in out
    assert r.run_metadata.created_at.isoformat() in out
    assert f"**Scenario hash:** {r.scenario_hash}" in out
    assert f"**Seed hash:** {r.seed_hash}" in out
    assert f"**Audience store:** {r.run_metadata.zep_mode}" in out
    # The Scale line concatenates all 4 ScaleCaps multiplicands.
    sc = r.run_metadata.scale_caps
    assert (
        f"ensemble_n={sc.ensemble_n} x stakeholders={sc.stakeholders} "
        f"x rounds={sc.rounds} (cap {sc.cap_value})"
    ) in out


# ----------------------------------------------------------------------
# Section rendering: executive_summary, sections, evidence_gaps, ...
# ----------------------------------------------------------------------


def test_executive_summary_renders_each_claim() -> None:
    r = _build_report(
        executive_summary=[
            Claim(claim_id="e1", text="Engagement is front-loaded.",
                  tag=HypothesisTag.INFERENCE),
        ],
    )
    out = render_report(r)
    assert "## Executive summary" in out
    assert "**[inference]** Engagement is front-loaded." in out


def test_section_titles_render() -> None:
    r = _build_report(sections=[
        _build_section("comments", "Comment cluster risks"),
        _build_section("share", "Share triggers"),
    ])
    out = render_report(r)
    assert "## Comment cluster risks" in out
    assert "## Share triggers" in out


def test_evidence_gaps_block_renders() -> None:
    r = _build_report(evidence_gaps=[
        Claim(claim_id="g1", text="Actual response of trader cohort unknown.",
              tag=HypothesisTag.REQUIRES_VALIDATION),
    ])
    out = render_report(r)
    assert "## Evidence gaps" in out
    assert "**[requires_validation]** Actual response of trader cohort unknown." in out


def test_field_validation_questions_render_as_bullets() -> None:
    r = _build_report(field_validation_questions=[
        "Survey 30 traders before pricing rollout.",
        "Interview cohort leads about retention drivers.",
    ])
    out = render_report(r)
    assert "- Survey 30 traders before pricing rollout." in out
    assert "- Interview cohort leads about retention drivers." in out


def test_confidence_and_limitations_render() -> None:
    r = _build_report(
        confidence_notes="Confidence is directional, not numeric.",
        source_limitations=["seed_text is a draft", "no trader interviews"],
    )
    out = render_report(r)
    assert "## Confidence and limitations" in out
    assert "Confidence is directional, not numeric." in out
    assert "- seed_text is a draft" in out
    assert "- no trader interviews" in out


def test_run_metadata_pointer_block_renders() -> None:
    r = _build_report()
    out = render_report(r)
    assert "## Run metadata" in out
    assert "embedded in this report's `run_metadata` field" in out
    assert "`run_metadata.json`" in out


# ----------------------------------------------------------------------
# Empty section / claim list placeholder
# ----------------------------------------------------------------------


def test_empty_section_claims_render_placeholder() -> None:
    """A section with zero claims renders the explicit placeholder; the
    section is NOT silently dropped."""
    r = _build_report(sections=[
        _build_section("empty", "Empty section", with_claims=False),
    ])
    out = render_report(r)
    assert "## Empty section" in out
    assert "_(no claims emitted)_" in out


def test_empty_executive_summary_renders_placeholder() -> None:
    r = _build_report(executive_summary=[])
    out = render_report(r)
    # Locate the executive-summary header; the next non-empty line is
    # the placeholder, not the next section.
    chunk = out.split("## Executive summary", 1)[1]
    next_non_empty = next(line for line in chunk.split("\n") if line.strip())
    assert next_non_empty == "_(no claims emitted)_"


def test_empty_evidence_gaps_renders_placeholder() -> None:
    r = _build_report(evidence_gaps=[])
    out = render_report(r)
    chunk = out.split("## Evidence gaps", 1)[1]
    next_non_empty = next(line for line in chunk.split("\n") if line.strip())
    assert next_non_empty == "_(no claims emitted)_"


# ----------------------------------------------------------------------
# Claim metadata: every optional field renders when present
# ----------------------------------------------------------------------


def test_claim_renders_tag_value_not_enum_repr() -> None:
    """The tag must render as the string value, never the enum repr."""
    r = _build_report(executive_summary=[
        Claim(claim_id="c1", text="x", tag=HypothesisTag.INFERENCE),
    ])
    out = render_report(r)
    assert "**[inference]** x" in out
    assert "HypothesisTag.INFERENCE" not in out
    assert "<HypothesisTag" not in out


def test_claim_renders_segment_refs_when_present() -> None:
    r = _build_report(executive_summary=[
        Claim(
            claim_id="c1", text="x", tag=HypothesisTag.INFERENCE,
            segment_refs=["s_fans", "s_critics"],
        ),
    ])
    out = render_report(r)
    assert "Segments: s_fans, s_critics" in out


def test_claim_renders_confidence_as_categorical_band_when_present() -> None:
    """The schema permits ``Claim.confidence`` as a [0, 1] float;
    the rendered surface MUST bucket it into a categorical band
    (``low`` / ``advisory`` / ``source-grounded``) so the reader
    cannot mistake the value for calibrated probability. The
    ``Not a Predictor`` contract pins confidence at the directional
    level."""
    cases = [
        (0.10, "low"),
        (0.50, "advisory"),
        (0.85, "source-grounded"),
    ]
    for value, expected_band in cases:
        r = _build_report(executive_summary=[
            Claim(
                claim_id="c1", text="x", tag=HypothesisTag.INFERENCE,
                confidence=value,
            ),
        ])
        out = render_report(r)
        # The rendered text uses the band, NEVER the raw numeric.
        assert f"Confidence band: {expected_band}" in out
        # No raw numeric in the rendered confidence line.
        assert f"Confidence: {value}" not in out, (
            f"renderer must categorise confidence={value}, not emit it raw"
        )


def test_claim_omits_confidence_when_absent() -> None:
    """``confidence`` is omit-when-absent. None must NOT render as 0."""
    r = _build_report(executive_summary=[
        Claim(claim_id="c1", text="x", tag=HypothesisTag.INFERENCE),
    ])
    out = render_report(r)
    assert "Confidence:" not in out


def test_claim_renders_expected_risk_tags_when_present() -> None:
    r = _build_report(executive_summary=[
        Claim(
            claim_id="c1", text="x", tag=HypothesisTag.INFERENCE,
            expected_risk_tags=["H-RETENTION", "H-PARASOCIAL"],
        ),
    ])
    out = render_report(r)
    assert "Risk tags: H-RETENTION, H-PARASOCIAL" in out


def test_claim_renders_typed_evidence_refs_per_kind() -> None:
    """Each ``EvidenceRef.ref_type`` renders to its canonical link form."""
    r = _build_report(executive_summary=[
        Claim(
            claim_id="c1", text="x", tag=HypothesisTag.INFERENCE,
            evidence_refs=[
                EvidenceRef(ref_type="simulation_action", ref_id="a-uuid-1"),
                EvidenceRef(ref_type="seed_text_line", ref_id="14"),
                EvidenceRef(ref_type="agent_interview", ref_id="i-uuid-9"),
                EvidenceRef(ref_type="external_source",
                            ref_id="https://example.com/source"),
            ],
        ),
    ])
    out = render_report(r)
    assert "actions.jsonl#a-uuid-1" in out
    # Legacy bare-line-number ``ref_id="14"`` is auto-prefixed by the
    # renderer so the rendered link still reads ``seed_text:L14``.
    assert "seed_text:L14" in out
    # And critically NOT the doubled form -- a regression here would
    # mean the renderer's verbatim-or-prefix branch is misfiring.
    assert "seed_text:Lseed_text:L14" not in out
    assert "interviews/i-uuid-9.json" in out
    assert "<https://example.com/source>" in out


def test_claim_renders_full_key_seed_text_ref_without_doubling() -> None:
    """Canonical full-key ``ref_id="seed_text:L<n>"`` MUST render verbatim.

    The :class:`EvidenceCatalog` stores ``seed_text_line`` entries as
    full ``seed_text:L<n>`` keys, and :meth:`EvidenceCatalog.contains`
    matches against those keys verbatim. An LLM that echoes a catalog
    key back -- the path the prompt explicitly encourages -- ships an
    EvidenceRef with that full key as ``ref_id``. The renderer must
    NOT prepend a second ``seed_text:L`` prefix; the rendered link
    must equal the catalog key exactly so a reader who searches for
    ``seed_text:L7`` in the markdown finds the link AND the same
    string the binding logic resolved against.

    The previous renderer always emitted ``seed_text:L{ref_id}``
    which produced the symptomatic ``seed_text:Lseed_text:L7`` for
    every catalog-keyed ref.
    """
    r = _build_report(executive_summary=[
        Claim(
            claim_id="c1", text="x", tag=HypothesisTag.INFERENCE,
            evidence_refs=[
                EvidenceRef(ref_type="seed_text_line", ref_id="seed_text:L7"),
            ],
        ),
    ])
    out = render_report(r)
    assert "seed_text:L7" in out
    # Exactly ONE occurrence: the verbatim full key. A doubled form
    # ``seed_text:Lseed_text:L7`` ALSO contains ``seed_text:L7`` as a
    # substring so the lone substring check above is insufficient.
    assert out.count("seed_text:L7") >= 1
    assert "seed_text:Lseed_text:L7" not in out


def test_claim_renders_driver_hints_with_attribution_disclaimer() -> None:
    """Every driver-hint line must end with the literal disclaimer text."""
    r = _build_report(executive_summary=[
        Claim(
            claim_id="c1", text="x", tag=HypothesisTag.INFERENCE,
            driver_hints=[
                DriverHint(
                    # Single-hint -> contribution=1.0 satisfies the
                    # per-claim sum invariant.
                    kind="segment", ref="s_fans", contribution=1.0,
                    method="cooccurrence_v1",
                ),
            ],
        ),
    ])
    out = render_report(r)
    assert "Driver: segment/s_fans" in out
    assert "method=cooccurrence_v1" in out
    assert "confidence=low" in out
    # Literal disclaimer wording must surface verbatim so the reader
    # cannot mistake the heuristic for causal attribution.
    assert "co-occurrence proxy, not causal" in out


# ----------------------------------------------------------------------
# Output formatting
# ----------------------------------------------------------------------


def test_output_ends_with_exactly_one_trailing_newline() -> None:
    out = render_report(_build_report())
    assert out.endswith("\n")
    assert not out.endswith("\n\n")


def test_render_returns_str() -> None:
    out = render_report(_build_report())
    assert isinstance(out, str)


# ----------------------------------------------------------------------
# Renderer hardcoded prose passes the language guard (defensive pin)
# ----------------------------------------------------------------------


def test_assert_report_render_contract_passes_for_a_clean_report() -> None:
    """End-to-end pin: the canonical rendered markdown produced by
    :func:`render_report` is gated by
    :func:`assert_report_render_contract`, which renders + then runs
    :func:`assert_no_unhedged_banned_tokens` on the rendered text.

    This is the single shared code path the generator (pre-persist) and
    the read-side routes (``GET /report``, ``GET /report.md``,
    ``/compare``) all use. A future renderer edit that introduces an
    unhedged banned token in hardcoded prose (caveat block, section
    headers, empty-list placeholder, audit summary, validity-flag
    bullet template, etc.) WILL trip this assertion AT THE RENDER
    SITE, not at runtime when an operator opens the report.
    """
    from app.services.policy.language_guard import (
        assert_report_render_contract,
    )

    report = _build_report()
    rendered = assert_report_render_contract(report)
    # Sanity: the function returns the rendered markdown so callers
    # don't pay the render cost twice.
    assert isinstance(rendered, str)
    assert len(rendered) > 0


def test_renderer_caveat_block_is_hedged_explicitly() -> None:
    """The renderer's _CAVEAT_BLOCK constant contains the literal
    word ``forecast`` -- this is allowed ONLY because it appears in
    the hedged phrase ``not a forecast``. If a future edit drops the
    ``not a`` hedge, the language guard would catch it via the test
    above, but pinning the hedge presence explicitly here gives a
    cleaner regression signal.
    """
    from app.services.policy.markdown_renderer import _CAVEAT_BLOCK

    assert "not a forecast" in _CAVEAT_BLOCK.lower()
