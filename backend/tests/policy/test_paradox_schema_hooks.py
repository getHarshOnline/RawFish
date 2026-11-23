"""Paradox-taxonomy schema-hook stubs.

Seven typed-field stubs that downstream runtime consumers attach to.
These tests pin:

* each field exists on the right model with the right default
* each field can be omitted at construction time (defaults are sensible)
* each field accepts a representative non-default value

No behavioural assertions here -- those land alongside the downstream
runtime consumers.
"""
from __future__ import annotations

from typing import Any, Dict

from app.policy import (
    AudienceSegment,
    Claim,
    RunControls,
    Scenario,
)
from app.policy import schema as schema_module
from app.policy.hypothesis_tags import HypothesisTag


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_segment_dict(**overrides: Any) -> Dict[str, Any]:
    """A minimal AudienceSegment dict that passes all field validators."""
    base: Dict[str, Any] = {
        "segment_id": "s_fans",
        "name": "Fans cohort",
        "type": "follower_cohort",
        "influence_level": 3,
        "vulnerability_level": 3,
        "compliance_propensity": 3,
        "trust_level": 3,
        "mobilization_capacity": 3,
        "what_changes_their_mind": ["new evidence emerges"],
    }
    base.update(overrides)
    return base


def _minimal_scenario_dict(**overrides: Any) -> Dict[str, Any]:
    """A minimal creator-persona Scenario dict."""
    base: Dict[str, Any] = {
        "persona": "creator",
        "title": "Minimal scenario",
        "summary": "A minimal creator scenario for schema tests.",
        "seed_text": "Post 1/1: a minimal seed for tests.",
        "stress_question": "Where might this misfire?",
        "stakeholders": [_minimal_segment_dict()],
        "assumptions": ["author is in good standing"],
        "success_criteria": ["no factual correction required"],
        "failure_modes_to_watch": ["misread out of context"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# AudienceSegment paradox hooks
# ---------------------------------------------------------------------------


def test_audience_segment_distribution_assumptions_default_none() -> None:
    """Power-Law / heavy-tails (kind=power_law_tail) hook field present, defaults to None."""
    assert "distribution_assumptions" in AudienceSegment.model_fields
    seg = AudienceSegment.model_validate(_minimal_segment_dict())
    assert seg.distribution_assumptions is None


def test_audience_segment_distribution_assumptions_accepts_dict() -> None:
    seg = AudienceSegment.model_validate(
        _minimal_segment_dict(distribution_assumptions={"reach": "pareto"})
    )
    assert seg.distribution_assumptions == {"reach": "pareto"}


def test_audience_segment_selection_frame_default_none() -> None:
    """Survivorship Bias (kind=survivorship_or_cohort_gap) hook field present, defaults to None."""
    assert "selection_frame" in AudienceSegment.model_fields
    seg = AudienceSegment.model_validate(_minimal_segment_dict())
    assert seg.selection_frame is None


def test_audience_segment_selection_frame_accepts_str() -> None:
    seg = AudienceSegment.model_validate(
        _minimal_segment_dict(selection_frame="engaged users only")
    )
    assert seg.selection_frame == "engaged users only"


def test_audience_segment_social_desirability_bias_default_none() -> None:
    """Hawthorne Effect (kind=observer_effect) hook field present, defaults to None."""
    assert "social_desirability_bias" in AudienceSegment.model_fields
    seg = AudienceSegment.model_validate(_minimal_segment_dict())
    assert seg.social_desirability_bias is None


def test_audience_segment_social_desirability_bias_accepts_float() -> None:
    seg = AudienceSegment.model_validate(
        _minimal_segment_dict(social_desirability_bias=0.42)
    )
    assert seg.social_desirability_bias == 0.42


def test_audience_segment_utility_params_default_none() -> None:
    """St. Petersburg Paradox (kind=utility_curve) hook field present, defaults to None."""
    assert "utility_params" in AudienceSegment.model_fields
    seg = AudienceSegment.model_validate(_minimal_segment_dict())
    assert seg.utility_params is None


def test_audience_segment_utility_params_accepts_dict() -> None:
    payload = {"risk_aversion": 0.3, "utility_curvature": "log"}
    seg = AudienceSegment.model_validate(
        _minimal_segment_dict(utility_params=payload)
    )
    assert seg.utility_params == payload


# ---------------------------------------------------------------------------
# Claim paradox hook
# ---------------------------------------------------------------------------


def test_claim_segment_refs_default_empty_list() -> None:
    """Simpson's Paradox (kind=simpson_aggregate_disagreement) hook field present, defaults to empty list.

    Claim now requires claim_id / text / tag (the full report-claim
    contract); this test continues to pin only the segment_refs paradox
    hook behaviour and supplies the other required fields with minimal
    placeholders.
    """
    assert "segment_refs" in Claim.model_fields
    claim = Claim.model_validate({
        "claim_id": "c1",
        "text": "placeholder",
        "tag": "simulated_observation",
    })
    assert claim.segment_refs == []


def test_claim_segment_refs_accepts_list() -> None:
    claim = Claim.model_validate({
        "claim_id": "c1",
        "text": "placeholder",
        "tag": "simulated_observation",
        "segment_refs": ["s_fans", "s_critics"],
    })
    assert claim.segment_refs == ["s_fans", "s_critics"]


# ---------------------------------------------------------------------------
# Scenario paradox hooks
# ---------------------------------------------------------------------------


def test_scenario_selection_criteria_default_none() -> None:
    """Berkson's Paradox (kind=selection_axis) hook field present, defaults to None."""
    assert "selection_criteria" in Scenario.model_fields
    scen = Scenario.model_validate(_minimal_scenario_dict())
    assert scen.selection_criteria is None


def test_scenario_selection_criteria_accepts_dict() -> None:
    payload = {"axes": ["influence", "engagement"]}
    scen = Scenario.model_validate(
        _minimal_scenario_dict(selection_criteria=payload)
    )
    assert scen.selection_criteria == payload


def test_scenario_excluded_cohorts_default_empty_list() -> None:
    """Berkson's Paradox (kind=selection_axis) hook field present, defaults to []."""
    assert "excluded_cohorts" in Scenario.model_fields
    scen = Scenario.model_validate(_minimal_scenario_dict())
    assert scen.excluded_cohorts == []


def test_scenario_excluded_cohorts_accepts_list() -> None:
    scen = Scenario.model_validate(
        _minimal_scenario_dict(excluded_cohorts=["lurker_cohort", "non_responder"])
    )
    assert scen.excluded_cohorts == ["lurker_cohort", "non_responder"]


# ---------------------------------------------------------------------------
# RunControls paradox hook
# ---------------------------------------------------------------------------


def test_run_controls_assumption_confirmation_ratio_default_none() -> None:
    """Confirmation Bias (kind=confirmation_bias) hook field present, defaults to None."""
    assert "assumption_confirmation_ratio" in RunControls.model_fields
    run = RunControls()
    assert run.assumption_confirmation_ratio is None


def test_run_controls_assumption_confirmation_ratio_accepts_float() -> None:
    run = RunControls(assumption_confirmation_ratio=0.85)
    assert run.assumption_confirmation_ratio == 0.85


# ---------------------------------------------------------------------------
# Full-construction smoke test: all 7 hook fields can be set together
# ---------------------------------------------------------------------------


def test_all_seven_paradox_hooks_construct_together() -> None:
    """Every paradox-hook field accepts a non-default value in concert."""
    seg = _minimal_segment_dict(
        distribution_assumptions={"reach": "pareto"},
        selection_frame="engaged users only",
        social_desirability_bias=0.25,
        utility_params={"risk_aversion": 0.4, "utility_curvature": "crra"},
    )
    scen_dict = _minimal_scenario_dict(
        stakeholders=[seg],
        selection_criteria={"axes": ["influence"]},
        excluded_cohorts=["lurker_cohort"],
        run={"assumption_confirmation_ratio": 0.5},
    )
    scen = Scenario.model_validate(scen_dict)

    stakeholder = scen.stakeholders[0]
    assert stakeholder.distribution_assumptions == {"reach": "pareto"}
    assert stakeholder.selection_frame == "engaged users only"
    assert stakeholder.social_desirability_bias == 0.25
    assert stakeholder.utility_params == {
        "risk_aversion": 0.4,
        "utility_curvature": "crra",
    }
    assert scen.selection_criteria == {"axes": ["influence"]}
    assert scen.excluded_cohorts == ["lurker_cohort"]
    assert scen.run.assumption_confirmation_ratio == 0.5

    # Claim is now the full report-claim model: claim_id / text / tag
    # are required. segment_refs continues to function as the
    # Simpson's-Paradox runtime hook (the only field this test pins).
    claim = Claim(
        claim_id="c1",
        text="Fans cohort engages with retention prompts.",
        tag=HypothesisTag.SIMULATED_OBSERVATION,
        segment_refs=[stakeholder.segment_id],
    )
    assert claim.segment_refs == ["s_fans"]


# ---------------------------------------------------------------------------
# Module surface: Claim is reachable from app.policy namespace
# ---------------------------------------------------------------------------


def test_claim_exported_from_app_policy() -> None:
    """Claim must be reachable via the top-level package re-export."""
    assert Claim is schema_module.Claim
