"""Pins the pure transparency post-processor.

Coverage:
  * Import-graph + AST-purity gates (no clock / env / fs / random /
    network / LLM / Flask / removed report-agent dependency).
  * Per-kind planted-trigger tests (9 kinds, falsifier-audit fold).
  * Deterministic ordering (same inputs -> same ids + order).
  * apply_transparency_flags returns a new Report and reuses the
    nested validated models (no Pydantic re-validation cost).
  * Renderer-inclusion gate: every canned message in the canonical
    set survives the banned-token regression helper.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from app.policy.hypothesis_tags import HypothesisTag
from app.policy.run_metadata import RunMetadata, ScaleCaps
from app.policy.schema import (
    AudienceSegment,
    Claim,
    DriftParams,
    Persona,
    PolicySurface,
    Report,
    RunControls,
    Scenario,
    Section,
    ValidityFlag,
)
from app.services.policy.language_guard import find_unhedged_banned_tokens
from app.services.policy.transparency_runtime import (
    R_TAIL,
    T_COHORT,
    T_CONF,
    T_DRIFT,
    T_MC,
    T_OBS,
    TRUST_HIGH,
    TRUST_LOW,
    apply_transparency_flags,
    derive_validity_flags,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _segment(
    segment_id: str = "s1",
    *,
    seg_type: str = "follower_cohort",
    distribution_assumptions: Optional[dict] = None,
    selection_frame: Optional[str] = None,
    social_desirability_bias: Optional[float] = None,
    utility_params: Optional[dict] = None,
    drift_delta: Optional[float] = None,
    influence_level: int = 3,
) -> AudienceSegment:
    drift_params = None
    if drift_delta is not None:
        drift_params = DriftParams(
            susceptibility=0.5,
            compromise_mu=0.1,
            epsilon=0.1,
            gain_pos=1.5,
            gain_neg=1.5,
            delta=drift_delta,
        )
    return AudienceSegment(
        segment_id=segment_id,
        name=f"Segment {segment_id}",
        type=seg_type,
        influence_level=influence_level,
        vulnerability_level=3,
        compliance_propensity=3,
        trust_level=3,
        mobilization_capacity=3,
        what_changes_their_mind=["new evidence emerges"],
        distribution_assumptions=distribution_assumptions,
        selection_frame=selection_frame,
        social_desirability_bias=social_desirability_bias,
        utility_params=utility_params,
        drift_params=drift_params,
    )


def _scenario(
    *,
    persona: Persona = Persona.CREATOR,
    scope: Optional[PolicySurface] = None,
    stakeholders: Optional[List[AudienceSegment]] = None,
    ensemble_n: int = 20,
    assumption_confirmation_ratio: Optional[float] = None,
    trust_baseline: Optional[float] = None,
    selection_criteria: Optional[dict] = None,
    excluded_cohorts: Optional[List[str]] = None,
    assumptions: Optional[List[str]] = None,
    failure_modes: Optional[List[str]] = None,
) -> Scenario:
    if stakeholders is None:
        stakeholders = [
            _segment("s1", seg_type="follower_cohort"),
            _segment(
                "s2",
                seg_type="lurker_cohort"
                if persona == Persona.CREATOR
                else "follower_cohort",
            ),
        ]
    return Scenario(
        persona=persona,
        scope=scope,
        title="Test scenario",
        summary="Test summary",
        seed_text="Test seed text body.",
        stress_question="Where might this misfire?",
        stakeholders=stakeholders,
        assumptions=assumptions or ["assumption alpha"],
        success_criteria=["criterion one"],
        failure_modes_to_watch=failure_modes or ["failure mode one"],
        run=RunControls(
            ensemble_n=ensemble_n,
            assumption_confirmation_ratio=assumption_confirmation_ratio,
        ),
        drift_horizon_rounds=3,
        trust_baseline=trust_baseline,
        selection_criteria=selection_criteria,
        excluded_cohorts=excluded_cohorts or [],
    )


def _run_metadata(persona: Persona = Persona.CREATOR) -> RunMetadata:
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
        scale_caps=ScaleCaps(
            ensemble_n=1, stakeholders=1, rounds=10, cap_value=500,
        ),
    )


def _report(
    *,
    persona: Persona = Persona.CREATOR,
    scope: Optional[PolicySurface] = None,
    executive_summary: Optional[List[Claim]] = None,
    sections_claims: Optional[List[Claim]] = None,
    field_validation_questions: Optional[List[str]] = None,
) -> Report:
    rm = _run_metadata(persona=persona)
    sections = [
        Section(
            section_id="sec_01",
            title="Engagement themes",
            claims=sections_claims if sections_claims is not None else [
                Claim(
                    claim_id="c_sec_01",
                    text="A safe inference about cohort engagement.",
                    tag=HypothesisTag.INFERENCE,
                ),
            ],
        ),
    ]
    return Report(
        report_id="report_sim_1_v1",
        simulation_id=rm.simulation_id,
        persona=persona,
        scope=scope,
        scenario_hash=rm.scenario_hash,
        seed_hash=rm.seed_hash,
        generated_at=datetime(2026, 6, 26, 12, 5, 0, tzinfo=timezone.utc),
        executive_summary=executive_summary if executive_summary is not None
        else [
            Claim(
                claim_id="c_exec_01",
                text="A safe exec-summary observation about engagement.",
                tag=HypothesisTag.SIMULATED_OBSERVATION,
            ),
        ],
        sections=sections,
        field_validation_questions=field_validation_questions or [],
        run_metadata=rm,
    )


# ---------------------------------------------------------------------------
# Module-level constant pins
# ---------------------------------------------------------------------------


def test_threshold_constants_are_canonical_values() -> None:
    """Tests assert against the imported symbols rather than re-
    spelling the literals, but the canonical numeric values are
    pinned here so a careless edit blowing up T_MC = 5 surfaces."""
    assert T_CONF == 0.85
    assert T_OBS == 0.5
    assert T_DRIFT == 0.4
    assert T_COHORT == 2
    assert T_MC == 20
    assert R_TAIL == 5.0
    assert TRUST_LOW == 0.15
    assert TRUST_HIGH == 0.85


# ---------------------------------------------------------------------------
# Purity (AST + import-graph)
# ---------------------------------------------------------------------------


_TRANSPARENCY_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "app" / "services" / "policy" / "transparency_runtime.py"
)


def _import_names(path: Path) -> set[str]:
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


def test_transparency_runtime_imports_are_pure() -> None:
    names = _import_names(_TRANSPARENCY_MODULE_PATH)
    allowed = {
        "__future__",
        "typing",
        "collections",
        "app.policy.schema",
        "app.policy.hypothesis_tags",
        # Pure regex/dataclass-only module; depended on for BANNED_PATTERNS
        # so falsifier-prompt scrubbing can re-use the canonical regex
        # contract instead of duplicating it. Importing it does not
        # weaken purity: language_guard itself imports only `re`,
        # `dataclasses`, and `typing` plus a TYPE_CHECKING-only schema
        # forward-ref.
        "app.services.policy.language_guard",
    }
    extras = names - allowed
    assert not extras, (
        f"transparency_runtime imports outside the audited allowlist: "
        f"{sorted(extras)!r}. Allowed: {sorted(allowed)!r}."
    )


def test_transparency_runtime_does_not_import_forbidden() -> None:
    """No clock / env / fs / random / network / LLM / Flask / legacy."""
    names = _import_names(_TRANSPARENCY_MODULE_PATH)
    forbidden = {
        "time",
        "datetime",
        "random",
        "secrets",
        "os",
        "pathlib",
        "socket",
        "urllib",
        "requests",
        "flask",
        "app.utils.llm_client",
        "app.seam.llm_patches",
        "app.services." + "report_agent",
    }
    for name in names:
        for bad in forbidden:
            assert name != bad and not name.startswith(bad + "."), (
                f"transparency_runtime imports forbidden module {name!r} "
                f"(matched against {bad!r})"
            )


def test_transparency_runtime_no_clock_env_or_fs_attr_access() -> None:
    """AST scan: no calls to datetime.now / time.time / os.environ /
    open / Path access in the module body."""
    src = _TRANSPARENCY_MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_attrs = {
        ("datetime", "now"),
        ("time", "time"),
        ("os", "environ"),
        ("os", "getenv"),
        ("random", "random"),
        ("Path", "read_text"),
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            pair = (node.value.id, node.attr)
            assert pair not in forbidden_attrs, (
                f"transparency_runtime uses forbidden attribute "
                f"{node.value.id}.{node.attr}"
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "open", (
                "transparency_runtime calls open() -- must stay fs-free"
            )


# ---------------------------------------------------------------------------
# Per-kind planted-trigger tests
# ---------------------------------------------------------------------------


def test_derive_power_law_tail_planted() -> None:
    seg = _segment(
        "s1", distribution_assumptions={"reach": "pareto"},
    )
    seg2 = _segment("s2", seg_type="lurker_cohort")
    scenario = _scenario(stakeholders=[seg, seg2])
    flags = derive_validity_flags(_report(), scenario)
    kinds = [f.kind for f in flags]
    assert kinds.count("power_law_tail") == 1


def test_derive_monte_carlo_coverage_planted() -> None:
    scenario = _scenario(ensemble_n=5)
    # Section claim with INFERENCE tag counts as aggregate-class.
    report = _report(
        sections_claims=[
            Claim(
                claim_id="c_agg",
                text="A safe inference about overall engagement.",
                tag=HypothesisTag.INFERENCE,
            ),
        ],
    )
    flags = derive_validity_flags(report, scenario)
    mc = [f for f in flags if f.kind == "monte_carlo_coverage"]
    assert len(mc) == 1
    assert "ensemble_n=5" in mc[0].message
    assert "N>=20" in mc[0].message


def test_derive_survivorship_or_cohort_gap_planted() -> None:
    scenario = _scenario(excluded_cohorts=["lurker_cohort"])
    flags = derive_validity_flags(_report(), scenario)
    kinds = [f.kind for f in flags]
    assert kinds.count("survivorship_or_cohort_gap") == 1


def test_derive_observer_effect_always_emitted() -> None:
    flags = derive_validity_flags(_report(), _scenario())
    oe = [f for f in flags if f.kind == "observer_effect"]
    assert len(oe) == 1
    assert oe[0].severity == "info"


def test_derive_observer_effect_severity_bumps_to_warning() -> None:
    seg = _segment("s1", social_desirability_bias=0.8)
    scenario = _scenario(stakeholders=[seg, _segment("s2", seg_type="lurker_cohort")])
    flags = derive_validity_flags(_report(), scenario)
    oe = [f for f in flags if f.kind == "observer_effect"]
    assert len(oe) == 1
    assert oe[0].severity == "warning"
    assert "s1" in oe[0].segment_refs


def test_derive_simpson_aggregate_disagreement_planted_inference() -> None:
    """An INFERENCE (aggregate-class) exec-summary claim with empty
    segment_refs in a scenario with 2 distinct stakeholder types fires
    one Simpson flag with that claim id attached."""
    report = _report(
        executive_summary=[
            Claim(
                claim_id="c_exec_01",
                text="Aggregate engagement is uneven across segments.",
                tag=HypothesisTag.INFERENCE,
                segment_refs=[],
            ),
        ],
    )
    scenario = _scenario(
        stakeholders=[
            _segment("s1", seg_type="follower_cohort"),
            _segment("s2", seg_type="lurker_cohort"),
        ],
    )
    flags = derive_validity_flags(report, scenario)
    s = [f for f in flags if f.kind == "simpson_aggregate_disagreement"]
    assert len(s) >= 1
    assert any("c_exec_01" in f.claim_refs for f in s)


def test_simpson_does_not_fire_on_non_aggregate_class_claims() -> None:
    """Per-segment ``simulated_observation`` and source-backed
    ``source_grounded`` claims with len(segment_refs)<=1 are the CORRECT
    shape (single-segment observations / source-backed citations) and
    MUST NOT trip the Simpson aggregate-disagreement flag. Only
    ``inference`` (aggregate-class) claims should fire."""
    non_aggregate_tags = [
        HypothesisTag.SIMULATED_OBSERVATION,
        HypothesisTag.SOURCE_GROUNDED,
        HypothesisTag.ASSUMPTION,
        HypothesisTag.REQUIRES_VALIDATION,
    ]
    scenario = _scenario(
        stakeholders=[
            _segment("s1", seg_type="follower_cohort"),
            _segment("s2", seg_type="lurker_cohort"),
        ],
    )
    for tag in non_aggregate_tags:
        report = _report(
            executive_summary=[
                Claim(
                    claim_id=f"c_{tag.value}_01",
                    text=f"A per-claim observation tagged {tag.value}.",
                    tag=tag,
                    segment_refs=[],  # single/empty -- but NOT aggregate
                ),
            ],
        )
        flags = derive_validity_flags(report, scenario)
        simpson_flags = [
            f for f in flags
            if f.kind == "simpson_aggregate_disagreement"
            and f"c_{tag.value}_01" in f.claim_refs
        ]
        assert not simpson_flags, (
            f"Simpson must not fire on a non-aggregate-class claim "
            f"(tag={tag.value!r}); got: {simpson_flags!r}"
        )


def test_derive_mean_reversion_planted_trust() -> None:
    scenario = _scenario(trust_baseline=0.9)
    flags = derive_validity_flags(_report(), scenario)
    assert sum(1 for f in flags if f.kind == "mean_reversion") == 1


def test_derive_mean_reversion_planted_delta() -> None:
    seg = _segment("s1", drift_delta=0.7)
    scenario = _scenario(stakeholders=[seg, _segment("s2", seg_type="lurker_cohort")])
    flags = derive_validity_flags(_report(), scenario)
    mr = [f for f in flags if f.kind == "mean_reversion"]
    assert len(mr) == 1
    assert "s1" in mr[0].segment_refs


def test_derive_utility_curve_planted() -> None:
    seg = _segment("s1", utility_params={"risk_aversion": 0.3})
    scenario = _scenario(stakeholders=[seg, _segment("s2", seg_type="lurker_cohort")])
    flags = derive_validity_flags(_report(), scenario)
    uc = [f for f in flags if f.kind == "utility_curve"]
    assert len(uc) == 1


def test_derive_confirmation_bias_planted_ratio() -> None:
    scenario = _scenario(assumption_confirmation_ratio=0.9)
    flags = derive_validity_flags(_report(), scenario)
    cb = [f for f in flags if f.kind == "confirmation_bias"]
    assert len(cb) == 1
    # Falsifier-audit content is folded into the prompt.
    assert cb[0].field_validation_prompt is not None


def test_falsifier_audit_folded_into_confirmation_bias() -> None:
    """An un-referenced ``what_changes_their_mind`` entry appears in
    the confirmation_bias.field_validation_prompt."""
    seg = _segment("s1")
    seg.what_changes_their_mind = ["a unique falsifier phrase xyz123"]
    scenario = _scenario(
        stakeholders=[seg, _segment("s2", seg_type="lurker_cohort")],
        assumption_confirmation_ratio=0.95,
    )
    report = _report(field_validation_questions=[])
    flags = derive_validity_flags(report, scenario)
    cb = [f for f in flags if f.kind == "confirmation_bias"]
    assert len(cb) == 1
    assert cb[0].field_validation_prompt is not None
    assert "xyz123" in cb[0].field_validation_prompt


def test_derive_selection_axis_planted_notice_for_multi_axis() -> None:
    scenario = _scenario(
        selection_criteria={"axes": ["influence", "engagement"]},
    )
    flags = derive_validity_flags(_report(), scenario)
    sa = [f for f in flags if f.kind == "selection_axis"]
    assert len(sa) == 1
    assert sa[0].severity == "notice"


def test_derive_selection_axis_single_axis_is_info() -> None:
    scenario = _scenario(
        selection_criteria={"axes": ["influence"]},
    )
    flags = derive_validity_flags(_report(), scenario)
    sa = [f for f in flags if f.kind == "selection_axis"]
    assert len(sa) == 1
    assert sa[0].severity == "info"


# ---------------------------------------------------------------------------
# Closed-taxonomy + deterministic ordering
# ---------------------------------------------------------------------------


def test_derive_emits_no_unknown_kinds() -> None:
    """Every emitted flag.kind sits in the 9-element allowlist (the
    schema Literal would reject otherwise; this is defense-in-depth)."""
    allowed = {
        "power_law_tail",
        "monte_carlo_coverage",
        "survivorship_or_cohort_gap",
        "observer_effect",
        "simpson_aggregate_disagreement",
        "mean_reversion",
        "utility_curve",
        "confirmation_bias",
        "selection_axis",
    }
    # Build a scenario that trips every kind.
    seg = _segment(
        "s1",
        distribution_assumptions={"reach": "pareto"},
        social_desirability_bias=0.8,
        utility_params={"risk_aversion": 0.3},
        drift_delta=0.7,
    )
    scenario = _scenario(
        stakeholders=[seg, _segment("s2", seg_type="lurker_cohort")],
        ensemble_n=5,
        trust_baseline=0.95,
        selection_criteria={"axes": ["influence", "engagement"]},
        excluded_cohorts=["other_cohort"],
        assumption_confirmation_ratio=0.95,
    )
    flags = derive_validity_flags(_report(), scenario)
    for f in flags:
        assert f.kind in allowed


def test_derive_deterministic_ordering_same_inputs() -> None:
    scenario = _scenario(
        stakeholders=[
            _segment("s1", distribution_assumptions={"reach": "pareto"}),
            _segment("s2", seg_type="lurker_cohort"),
        ],
        trust_baseline=0.95,
        selection_criteria={"axes": ["x", "y"]},
    )
    a = derive_validity_flags(_report(), scenario)
    b = derive_validity_flags(_report(), scenario)
    assert [f.flag_id for f in a] == [f.flag_id for f in b]
    assert [f.kind for f in a] == [f.kind for f in b]


def test_derive_deterministic_ordering_kind_then_id() -> None:
    """Flags come out sorted by (KIND_ORDER, flag_id)."""
    seg = _segment(
        "s1",
        distribution_assumptions={"reach": "pareto"},
        social_desirability_bias=0.8,
    )
    scenario = _scenario(
        stakeholders=[seg, _segment("s2", seg_type="lurker_cohort")],
        trust_baseline=0.95,
        selection_criteria={"axes": ["x", "y"]},
    )
    flags = derive_validity_flags(_report(), scenario)
    kind_order = [
        "power_law_tail",
        "monte_carlo_coverage",
        "survivorship_or_cohort_gap",
        "observer_effect",
        "simpson_aggregate_disagreement",
        "mean_reversion",
        "utility_curve",
        "confirmation_bias",
        "selection_axis",
    ]
    indices = [kind_order.index(f.kind) for f in flags]
    assert indices == sorted(indices), (
        f"flags must be sorted by kind order; got kinds={[f.kind for f in flags]}"
    )


# ---------------------------------------------------------------------------
# apply_transparency_flags semantics
# ---------------------------------------------------------------------------


def test_apply_transparency_flags_returns_new_report() -> None:
    original = _report()
    assert original.validity_flags == []
    scenario = _scenario()
    enriched = apply_transparency_flags(original, scenario)
    assert enriched is not original
    assert enriched.validity_flags  # at least observer_effect fires
    # The original report's validity_flags list MUST still be empty
    # (model_copy returns a new instance; the original is not mutated).
    assert original.validity_flags == []


def test_apply_transparency_flags_preserves_nested_run_metadata_value() -> None:
    """apply_transparency_flags re-validates the merged Report so the
    validity-flag hygiene validator fires at generate time. This
    rebuilds the nested Pydantic graph from the dumped payload, so
    `is` identity is NOT preserved -- but structural EQUALITY is.

    Identity preservation would require a ``model_copy(update=...)``
    shortcut that skips validators by design; trading identity for
    validator coverage is the correct tradeoff. Drift in
    derive_validity_flags that produces a malformed flag list now
    fails LOUDLY at generate time instead of silently slipping into a
    persisted artifact.
    """
    original = _report()
    scenario = _scenario()
    enriched = apply_transparency_flags(original, scenario)
    # Structural equality: every field on the embedded RunMetadata
    # matches verbatim across the round-trip.
    assert enriched.run_metadata == original.run_metadata
    assert enriched.run_metadata.simulation_id == original.run_metadata.simulation_id
    assert enriched.run_metadata.scenario_hash == original.run_metadata.scenario_hash
    assert enriched.run_metadata.seed_hash == original.run_metadata.seed_hash


# ---------------------------------------------------------------------------
# Renderer-inclusion + canned-message banned-token gate
# ---------------------------------------------------------------------------


def test_canned_messages_pass_banned_token_guard() -> None:
    """Every canned message in transparency_runtime._CANNED_MESSAGES
    survives the banned-token regression helper. This catches drift
    BEFORE the runtime render contract has a chance to fire on a real
    report."""
    from app.services.policy import transparency_runtime as tr
    for kind, message in tr._CANNED_MESSAGES.items():
        rendered = message.format(N=5, T_MC=T_MC)
        findings = find_unhedged_banned_tokens(rendered)
        assert findings == [], (
            f"canned message for {kind!r} carries an unhedged banned token: "
            f"{findings}"
        )


def test_apply_transparency_flags_then_render_contract_passes() -> None:
    """End-to-end: scenario tripping every kind -> apply -> render ->
    language guard returns no findings."""
    from app.services.policy.language_guard import (
        assert_report_render_contract,
    )
    seg = _segment(
        "s1",
        distribution_assumptions={"reach": "pareto"},
        social_desirability_bias=0.8,
        utility_params={"risk_aversion": 0.3},
        drift_delta=0.7,
    )
    scenario = _scenario(
        stakeholders=[seg, _segment("s2", seg_type="lurker_cohort")],
        ensemble_n=5,
        trust_baseline=0.95,
        selection_criteria={"axes": ["influence", "engagement"]},
        excluded_cohorts=["other_cohort"],
        assumption_confirmation_ratio=0.95,
    )
    report = _report()
    enriched = apply_transparency_flags(report, scenario)
    # Does not raise.
    assert_report_render_contract(enriched)


def test_renderer_includes_validity_section_when_flags_present() -> None:
    from app.services.policy.markdown_renderer import render_report
    seg = _segment("s1", distribution_assumptions={"reach": "pareto"})
    scenario = _scenario(
        stakeholders=[seg, _segment("s2", seg_type="lurker_cohort")],
    )
    enriched = apply_transparency_flags(_report(), scenario)
    md = render_report(enriched)
    assert "## Validity and transparency notes" in md
    assert "_power_law_tail_" in md


def test_renderer_omits_validity_section_when_flags_empty() -> None:
    from app.services.policy.markdown_renderer import render_report
    md = render_report(_report())
    assert "## Validity and transparency notes" not in md


# ---------------------------------------------------------------------------
# Falsifier-prompt sanitisation + Report-level validity_flags hygiene
# ---------------------------------------------------------------------------


def test_falsifier_prompt_scrubs_banned_token_in_raw_user_text() -> None:
    """If a scenario assumption contains a banned-token stem
    (e.g. "this will predict X"), the resulting field_validation_prompt
    must NOT carry that stem -- the scrubber replaces matches with
    ``[scrubbed]`` BEFORE the prompt is built so the rendered output
    is contract-safe regardless of hedge placement."""
    scenario = _scenario(
        assumption_confirmation_ratio=0.95,
        assumptions=[
            "This will predict the future for the audience.",
            "The team forecasts a clean rollout.",
        ],
    )
    flags = derive_validity_flags(_report(), scenario)
    cb = [f for f in flags if f.kind == "confirmation_bias"]
    assert len(cb) == 1
    prompt = cb[0].field_validation_prompt or ""
    # Banned stems are replaced; the hedge wrapper still present.
    assert "predict" not in prompt.lower(), prompt
    assert "forecast" not in prompt.lower(), prompt
    assert "[scrubbed]" in prompt, prompt


def test_falsifier_prompt_renders_clean_through_language_guard() -> None:
    """End-to-end: a scenario whose un-referenced assumptions contain
    banned-token stems renders into markdown that passes the language
    guard. Previously a hedged wrapper would have pardoned the raw
    user text; the scrub fix closes that hole."""
    from app.services.policy.language_guard import (
        assert_report_render_contract,
    )
    scenario = _scenario(
        assumption_confirmation_ratio=0.95,
        assumptions=[
            "This will predict adoption among Gen Z.",
            "Marketing forecasts that engagement will result.",
        ],
    )
    enriched = apply_transparency_flags(_report(), scenario)
    # Renders + asserts no unhedged banned tokens. Raises on violation.
    assert_report_render_contract(enriched)


def test_report_rejects_duplicate_validity_flag_ids() -> None:
    """Two ValidityFlags with the same flag_id trip the hygiene
    validator at Report.model_validate time."""
    import pytest
    from pydantic import ValidationError

    seg = _segment("s1", distribution_assumptions={"reach": "pareto"})
    scenario = _scenario(stakeholders=[seg, _segment("s2", seg_type="lurker_cohort")])
    base = apply_transparency_flags(_report(), scenario)
    # Drop in a duplicate by reusing an existing flag_id with a new kind
    # in the wrong slot. Use Report.model_validate to round-trip and
    # trigger the validator.
    dumped = base.model_dump(mode="python")
    if not dumped["validity_flags"]:
        # Defensive: planted scenario must produce at least one flag.
        raise AssertionError("planted scenario produced no flags")
    flags = dumped["validity_flags"]
    flags.append({**flags[0]})  # duplicate flag_id + duplicate kind
    dumped["validity_flags"] = flags
    with pytest.raises(ValidationError, match="duplicate flag_id"):
        Report.model_validate(dumped)


def test_report_rejects_out_of_canonical_order_validity_flags() -> None:
    """A confirmation_bias flag listed BEFORE a mean_reversion flag
    violates canonical kind ordering and trips the validator."""
    import pytest
    from pydantic import ValidationError

    # Build two flags in REVERSE canonical order. confirmation_bias is
    # kind index 7; mean_reversion is index 5. Listing confirmation
    # first puts the higher-index flag before the lower-index flag,
    # which is the violation.
    flag_late = ValidityFlag(
        flag_id="vf_confirmation_00",
        kind="confirmation_bias",
        severity="notice",
        message="placeholder confirmation hedge may need field check",
        claim_refs=[],
        segment_refs=[],
        source_fields=[],
        method="heuristic_v1",
        confidence="low",
    )
    flag_early = ValidityFlag(
        flag_id="vf_mean_reversion_00",
        kind="mean_reversion",
        severity="notice",
        message="drift may regress under these assumptions",
        claim_refs=[],
        segment_refs=[],
        source_fields=[],
        method="heuristic_v1",
        confidence="medium",
    )
    base = _report()
    dumped = base.model_dump(mode="python")
    dumped["validity_flags"] = [
        flag_late.model_dump(mode="python"),
        flag_early.model_dump(mode="python"),
    ]
    with pytest.raises(ValidationError, match="canonical"):
        Report.model_validate(dumped)


def test_report_rejects_dangling_claim_ref_in_validity_flag() -> None:
    """A ValidityFlag whose claim_refs points at a claim_id NOT present
    anywhere in the report (exec_summary / sections / evidence_gaps)
    trips the validator -- a dangling ref would render a bullet that
    points nowhere."""
    import pytest
    from pydantic import ValidationError

    flag = ValidityFlag(
        flag_id="vf_simpson_00",
        kind="simpson_aggregate_disagreement",
        severity="notice",
        message="aggregate may mask cohort directional disagreement",
        claim_refs=["c_does_not_exist"],
        segment_refs=[],
        source_fields=["executive_summary"],
        method="heuristic_v1",
        confidence="low",
    )
    base = _report()
    dumped = base.model_dump(mode="python")
    dumped["validity_flags"] = [flag.model_dump(mode="python")]
    with pytest.raises(ValidationError, match="dangling claim_ids"):
        Report.model_validate(dumped)


def test_report_rejects_empty_string_in_validity_flag_refs() -> None:
    """A ValidityFlag with an empty-string claim_ref trips the
    ref-hygiene validator."""
    import pytest
    from pydantic import ValidationError

    flag = ValidityFlag(
        flag_id="vf_observer_effect_00",
        kind="observer_effect",
        severity="info",
        message="observer effect may shift self-reported behaviour",
        claim_refs=[""],
        segment_refs=[],
        source_fields=[],
        method="heuristic_v1",
        confidence="medium",
    )
    base = _report()
    dumped = base.model_dump(mode="python")
    dumped["validity_flags"] = [flag.model_dump(mode="python")]
    with pytest.raises(ValidationError, match="empty/whitespace"):
        Report.model_validate(dumped)


def test_generator_strips_validity_flags_from_llm_payload() -> None:
    """The generator's _PROVENANCE_KEYS_STRIPPED_FROM_LLM_PAYLOAD list
    now includes ``validity_flags`` so an LLM that emits a malformed
    validity_flags array can NOT fail Report.model_validate before the
    runtime post-processor owns the field."""
    from app.services.policy.policy_report_generator import (
        _PROVENANCE_KEYS_STRIPPED_FROM_LLM_PAYLOAD,
    )
    assert "validity_flags" in _PROVENANCE_KEYS_STRIPPED_FROM_LLM_PAYLOAD


def test_report_rejects_same_kind_validity_flags_in_reversed_flag_id_order() -> None:
    """Two flags of the same kind in REVERSED flag_id order MUST trip
    the canonical-order validator. The runtime sorts by
    (kind_index, flag_id) so the schema validator MUST use the same
    tuple key -- a kind-index-only check would pass a list whose
    same-kind flag_ids were out of lexical order."""
    import pytest
    from pydantic import ValidationError

    flag_later = ValidityFlag(
        flag_id="vf_simpson_01",  # higher flag_id should come SECOND
        kind="simpson_aggregate_disagreement",
        severity="info",
        message="aggregate may diverge from per-segment under these assumptions",
        claim_refs=[],
        segment_refs=[],
        source_fields=[],
        method="heuristic_v1",
        confidence="low",
    )
    flag_earlier = ValidityFlag(
        flag_id="vf_simpson_00",  # lower flag_id should come FIRST
        kind="simpson_aggregate_disagreement",
        severity="info",
        message="aggregate may diverge from per-segment under these assumptions",
        claim_refs=[],
        segment_refs=[],
        source_fields=[],
        method="heuristic_v1",
        confidence="low",
    )
    base = _report()
    dumped = base.model_dump(mode="python")
    # Plant in REVERSED order (later flag_id first).
    dumped["validity_flags"] = [
        flag_later.model_dump(mode="python"),
        flag_earlier.model_dump(mode="python"),
    ]
    with pytest.raises(ValidationError, match="canonical"):
        Report.model_validate(dumped)


def test_claim_driver_hints_contribution_sum_invariant() -> None:
    """Per-claim driver_hints contributions MUST sum to 1.0 +/- 0.05.

    The runtime attributor normalises before returning, but a
    hand-edited or LLM-produced report could still ship hints whose
    sum drifts outside the band. The Claim-level validator fails
    loudly so the drift surfaces at the schema boundary, not silently
    at render time."""
    import pytest
    from pydantic import ValidationError
    from app.policy.schema import Claim as _C, DriverHint as _D

    # Sum = 0.5 -- out of band.
    with pytest.raises(ValidationError, match="must sum to 1.0"):
        _C(
            claim_id="c_bad_sum",
            text="An observation about engagement.",
            tag=HypothesisTag.SIMULATED_OBSERVATION,
            driver_hints=[
                _D(kind="segment", ref="s1", contribution=0.5,
                   method="cooccurrence_v1"),
            ],
        )
    # Sum = 1.0 -- accepted.
    ok = _C(
        claim_id="c_good_sum",
        text="An observation about engagement.",
        tag=HypothesisTag.SIMULATED_OBSERVATION,
        driver_hints=[
            _D(kind="segment", ref="s1", contribution=0.6,
               method="cooccurrence_v1"),
            _D(kind="prior", ref="prior_trust", contribution=0.4,
               method="cooccurrence_v1"),
        ],
    )
    assert len(ok.driver_hints) == 2
    # Sum = 0.96 -- within tolerance, accepted.
    edge = _C(
        claim_id="c_edge_sum",
        text="An observation about engagement.",
        tag=HypothesisTag.SIMULATED_OBSERVATION,
        driver_hints=[
            _D(kind="segment", ref="s1", contribution=0.96,
               method="cooccurrence_v1"),
        ],
    )
    assert edge.driver_hints[0].contribution == 0.96
