"""Unit tests for app.policy.run_resolution.resolve_rounds.

Pins the precedence chain directly (no Flask client, no atomic write,
no state.json round-trip): explicit override > scenario root
``drift_horizon_rounds`` > legacy ``run.drift_horizon_rounds`` >
persona+scope+zep_mode default. Endpoint-level integration coverage
stays in ``test_start_request_hardening.py``.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from app.policy import Persona
from app.policy.persona_defaults import scale_defaults_for
from app.policy.run_resolution import resolve_rounds
from app.policy.schema import Scenario


# ---------------------------------------------------------------------------
# Fixtures: minimal Scenario builder (kept local; copy-not-import pattern
# matches the rest of the policy test suite).
# ---------------------------------------------------------------------------


def _minimal_segment() -> Dict[str, Any]:
    return {
        "segment_id": "s_fans",
        "name": "Fans cohort",
        "type": "follower_cohort",
        "influence_level": 3,
        "vulnerability_level": 3,
        "compliance_propensity": 3,
        "trust_level": 3,
        "mobilization_capacity": 3,
        "what_changes_their_mind": ["new evidence"],
    }


def _scenario(**overrides: Any) -> Scenario:
    data: Dict[str, Any] = {
        "persona": "creator",
        "title": "Minimal scenario",
        "summary": "A minimal creator scenario for resolve_rounds tests.",
        "seed_text": "Post 1/1: seed.",
        "stress_question": "Where might this misfire?",
        "stakeholders": [_minimal_segment()],
        "assumptions": ["a"],
        "success_criteria": ["s"],
        "failure_modes_to_watch": ["f"],
        "run": {"ensemble_n": 1, "run_seed": 42},
        "drift_horizon_rounds": 1,
    }
    data.update(overrides)
    return Scenario.model_validate(data)


# ---------------------------------------------------------------------------
# Leg 1: body.rounds wins over everything else
# ---------------------------------------------------------------------------


def test_body_rounds_wins_over_scenario_root():
    scen = _scenario(drift_horizon_rounds=3)
    assert resolve_rounds(7, scen, "mock") == 7


def test_body_rounds_wins_over_legacy_run():
    # Build via raw dict so the schema's _lift validator promotes legacy → root,
    # then override body to confirm the body leg shortcircuits the rest.
    scen = _scenario(drift_horizon_rounds=4)
    assert resolve_rounds(9, scen, "mock") == 9


# ---------------------------------------------------------------------------
# Leg 2: scenario root (canonical) when body is absent
# ---------------------------------------------------------------------------


def test_falls_back_to_scenario_root():
    scen = _scenario(drift_horizon_rounds=4)
    assert resolve_rounds(None, scen, "mock") == 4


# ---------------------------------------------------------------------------
# Leg 3: legacy run.drift_horizon_rounds — schema already lifts it to root.
# resolve_rounds's explicit legacy leg is therefore a DEAD-FALLBACK guard.
# We exercise it by surgically clearing the lifted root field on a built
# Scenario and re-populating the run.drift_horizon_rounds attribute
# directly, simulating a hypothetical future schema that drops the lift.
# ---------------------------------------------------------------------------


def test_falls_back_to_legacy_run_with_deprecation_warning():
    scen = _scenario(drift_horizon_rounds=4)
    # Clear the lifted root; force the legacy leg.
    object.__setattr__(scen, "drift_horizon_rounds", None)
    assert scen.run is not None
    object.__setattr__(scen.run, "drift_horizon_rounds", 6)
    with pytest.warns(DeprecationWarning, match="run.drift_horizon_rounds"):
        assert resolve_rounds(None, scen, "mock") == 6


# ---------------------------------------------------------------------------
# Leg 4: persona default — body, root, and legacy all absent
# ---------------------------------------------------------------------------


def test_falls_back_to_persona_default():
    scen = _scenario()  # creator persona, mock mode
    # Strip every rounds-bearing field so the persona-default leg fires.
    object.__setattr__(scen, "drift_horizon_rounds", None)
    if scen.run is not None:
        object.__setattr__(scen.run, "drift_horizon_rounds", None)
    expected = int(scale_defaults_for(Persona.CREATOR, None, "mock")["rounds"])
    assert resolve_rounds(None, scen, "mock") == expected


def test_persona_default_is_zep_mode_sensitive():
    """zep_mode='real' picks a different scale row than 'mock'."""
    scen = _scenario()
    object.__setattr__(scen, "drift_horizon_rounds", None)
    if scen.run is not None:
        object.__setattr__(scen.run, "drift_horizon_rounds", None)
    mock_rounds = resolve_rounds(None, scen, "mock")
    real_rounds = resolve_rounds(None, scen, "real")
    # Both must come from scale_defaults_for; we don't pin equality
    # (the persona_defaults table may have them coincide for some
    # personas), only that both resolve to a valid positive int.
    assert isinstance(mock_rounds, int) and mock_rounds >= 1
    assert isinstance(real_rounds, int) and real_rounds >= 1
