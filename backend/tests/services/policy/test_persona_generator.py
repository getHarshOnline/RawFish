"""Determinism + OASIS-shape tests for :class:`PolicyPersonaGenerator`.

Pure-function generator tests; no Flask, no Config bootstrap, no LLM API
key required. The core contract pinned here: same Scenario in ->
identical profile dicts out (byte-stable JSON dump), with the
field-shape matching :meth:`OasisAgentProfile.to_reddit_format` /
:meth:`OasisAgentProfile.to_twitter_format` exactly.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from app.policy import load_scenario_from_dict
from app.services.policy.persona_generator import PolicyPersonaGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_segment(
    *,
    segment_id: str = "s_fans",
    name: str = "Fans cohort",
    seg_type: str = "follower_cohort",
    influence: int = 3,
    interests: List[str] | None = None,
) -> Dict[str, Any]:
    return {
        "segment_id": segment_id,
        "name": name,
        "type": seg_type,
        "interests": interests or [],
        "influence_level": influence,
        "vulnerability_level": 3,
        "compliance_propensity": 3,
        "trust_level": 3,
        "mobilization_capacity": 3,
        "what_changes_their_mind": ["new evidence emerges"],
    }


def _minimal_scenario(**overrides: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "persona": "creator",
        "title": "Minimal scenario",
        "summary": "A minimal creator scenario for persona-gen tests.",
        "seed_text": "Post 1/1: a minimal seed for tests.",
        "stress_question": "Where might this misfire?",
        "stakeholders": [_minimal_segment()],
        "assumptions": ["author is in good standing"],
        "success_criteria": ["no factual correction required"],
        "failure_modes_to_watch": ["misread out of context"],
    }
    data.update(overrides)
    return data


def _three_segment_creator_scenario() -> Dict[str, Any]:
    return _minimal_scenario(
        stakeholders=[
            _minimal_segment(
                segment_id="s_fans",
                name="Fans cohort",
                seg_type="follower_cohort",
                interests=["product launches", "behind the scenes"],
            ),
            _minimal_segment(
                segment_id="s_lurk",
                name="Quiet lurkers",
                seg_type="lurker_cohort",
            ),
            _minimal_segment(
                segment_id="s_algo",
                name="Algorithmic feed",
                seg_type="algorithmic_cohort",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# 1. Determinism -- byte-stable JSON for both platform outputs
# ---------------------------------------------------------------------------


def test_reddit_profiles_are_byte_stable_across_calls() -> None:
    scen_a = load_scenario_from_dict(_three_segment_creator_scenario())
    scen_b = load_scenario_from_dict(_three_segment_creator_scenario())
    gen = PolicyPersonaGenerator()

    a = gen.generate_reddit_profiles(scen_a)
    b = gen.generate_reddit_profiles(scen_b)

    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_twitter_profiles_are_byte_stable_across_calls() -> None:
    scen_a = load_scenario_from_dict(_three_segment_creator_scenario())
    scen_b = load_scenario_from_dict(_three_segment_creator_scenario())
    gen = PolicyPersonaGenerator()

    a = gen.generate_twitter_profiles(scen_a)
    b = gen.generate_twitter_profiles(scen_b)

    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_two_distinct_generator_instances_produce_identical_outputs() -> None:
    """The class carries no per-instance state that affects determinism."""
    scen = load_scenario_from_dict(_three_segment_creator_scenario())
    a = PolicyPersonaGenerator().generate_reddit_profiles(scen)
    b = PolicyPersonaGenerator().generate_reddit_profiles(scen)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------------------
# 2. OASIS shape parity -- one record per stakeholder, required fields
# ---------------------------------------------------------------------------


def test_one_reddit_profile_per_stakeholder() -> None:
    scen = load_scenario_from_dict(_three_segment_creator_scenario())
    profiles = PolicyPersonaGenerator().generate_reddit_profiles(scen)
    assert len(profiles) == len(scen.stakeholders)


def test_one_twitter_profile_per_stakeholder() -> None:
    scen = load_scenario_from_dict(_three_segment_creator_scenario())
    profiles = PolicyPersonaGenerator().generate_twitter_profiles(scen)
    assert len(profiles) == len(scen.stakeholders)


def test_reddit_profile_has_oasis_required_fields() -> None:
    """Reddit OASIS adapter requires user_id/username/name/bio/persona/karma/created_at."""
    scen = load_scenario_from_dict(_minimal_scenario())
    profile = PolicyPersonaGenerator().generate_reddit_profiles(scen)[0]
    for required in (
        "user_id",
        "username",
        "name",
        "bio",
        "persona",
        "karma",
        "created_at",
    ):
        assert required in profile, f"missing required Reddit field: {required}"


def test_twitter_profile_has_oasis_required_fields() -> None:
    scen = load_scenario_from_dict(_minimal_scenario())
    profile = PolicyPersonaGenerator().generate_twitter_profiles(scen)[0]
    for required in (
        "user_id",
        "username",
        "name",
        "bio",
        "persona",
        "friend_count",
        "follower_count",
        "statuses_count",
        "created_at",
    ):
        assert required in profile, f"missing required Twitter field: {required}"


def test_user_ids_are_0_based_dense() -> None:
    scen = load_scenario_from_dict(_three_segment_creator_scenario())
    profiles = PolicyPersonaGenerator().generate_reddit_profiles(scen)
    assert [p["user_id"] for p in profiles] == [0, 1, 2]


# ---------------------------------------------------------------------------
# 3. Determinism of trait values (no random.* leakage)
# ---------------------------------------------------------------------------


def test_age_mbti_country_are_stable_for_same_segment() -> None:
    """Critical: trait picks must not change across calls."""
    scen = load_scenario_from_dict(_three_segment_creator_scenario())
    a = PolicyPersonaGenerator().generate_reddit_profiles(scen)
    b = PolicyPersonaGenerator().generate_reddit_profiles(scen)
    for pa, pb in zip(a, b):
        assert pa["age"] == pb["age"]
        assert pa["mbti"] == pb["mbti"]
        assert pa["country"] == pb["country"]
        assert pa["karma"] == pb["karma"]


def test_username_is_stable_and_segment_id_derived() -> None:
    scen = load_scenario_from_dict(_three_segment_creator_scenario())
    profiles = PolicyPersonaGenerator().generate_reddit_profiles(scen)
    # username carries the segment_id prefix so an operator can trace back.
    for p, seg in zip(profiles, scen.stakeholders):
        assert p["username"].startswith(f"{seg.segment_id}_")


def test_created_at_is_deterministic_sentinel() -> None:
    """created_at must NOT leak wall-clock time (would defeat byte-stability)."""
    scen = load_scenario_from_dict(_minimal_scenario())
    profile = PolicyPersonaGenerator().generate_reddit_profiles(scen)[0]
    # Pinned to the sentinel declared on persona_generator._DETERMINISTIC_CREATED_AT.
    assert profile["created_at"] == "2026-01-01"


# ---------------------------------------------------------------------------
# 4. Distinct Scenarios yield distinct profile bytes
# ---------------------------------------------------------------------------


def test_different_segment_names_produce_different_profile_ids() -> None:
    scen_a = load_scenario_from_dict(
        _minimal_scenario(
            stakeholders=[
                _minimal_segment(segment_id="s1", name="Name A")
            ]
        )
    )
    scen_b = load_scenario_from_dict(
        _minimal_scenario(
            stakeholders=[
                _minimal_segment(segment_id="s1", name="Name B")
            ]
        )
    )
    a = PolicyPersonaGenerator().generate_reddit_profiles(scen_a)[0]
    b = PolicyPersonaGenerator().generate_reddit_profiles(scen_b)[0]
    assert a["username"] != b["username"]


# ---------------------------------------------------------------------------
# 5. Institutional segment types get the institutional placeholder values
# ---------------------------------------------------------------------------


def test_institutional_segment_uses_other_gender_and_isjt_mbti() -> None:
    scen = load_scenario_from_dict(
        _minimal_scenario(
            stakeholders=[
                _minimal_segment(
                    segment_id="s_plat",
                    name="Platform",
                    seg_type="platform",
                ),
            ]
        )
    )
    profile = PolicyPersonaGenerator().generate_reddit_profiles(scen)[0]
    assert profile["gender"] == "other"
    assert profile["mbti"] == "ISTJ"
    assert profile["age"] == 30


# ---------------------------------------------------------------------------
# 6. Policy persona path also works end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope,seg_type",
    [
        ("internal_org", "employee_cohort"),
        ("public_policy", "citizen_segment"),
    ],
)
def test_policy_persona_byte_stability(scope: str, seg_type: str) -> None:
    data = _minimal_scenario(
        persona="policy",
        scope=scope,
        stakeholders=[
            _minimal_segment(segment_id="s1", name="N1", seg_type=seg_type),
            _minimal_segment(segment_id="s2", name="N2", seg_type=seg_type),
        ],
    )
    scen_a = load_scenario_from_dict(data)
    scen_b = load_scenario_from_dict(data)
    a = PolicyPersonaGenerator().generate_reddit_profiles(scen_a)
    b = PolicyPersonaGenerator().generate_reddit_profiles(scen_b)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
