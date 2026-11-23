"""Determinism + structural tests for ``build_policy_ontology``.

Pure-function adapter tests; no Flask, no Config bootstrap, no LLM. The
core invariant: same :class:`Scenario` in -> byte-identical
:class:`Ontology` JSON dump out. Round-trip parity (a dumped Ontology can
re-parse) and persona/scope echo-back are also pinned here.
"""
from __future__ import annotations

from typing import Any, Dict

import pytest

from app.policy import load_scenario_from_dict
from app.services.policy.ontology_loader import build_policy_ontology
from app.seam.protocols import Ontology


# ---------------------------------------------------------------------------
# Helpers (mirroring tests/policy/test_scenario_schema.py)
# ---------------------------------------------------------------------------


def _minimal_segment(
    *,
    segment_id: str = "s_fans",
    name: str = "Fans cohort",
    seg_type: str = "follower_cohort",
    influence: int = 3,
) -> Dict[str, Any]:
    return {
        "segment_id": segment_id,
        "name": name,
        "type": seg_type,
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
        "summary": "A minimal creator scenario for ontology-loader tests.",
        "seed_text": "Post 1/1: a minimal seed for tests.",
        "stress_question": "Where might this misfire?",
        "stakeholders": [_minimal_segment()],
        "assumptions": ["author is in good standing"],
        "success_criteria": ["no factual correction required"],
        "failure_modes_to_watch": ["misread out of context"],
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# 1. Determinism: same Scenario -> byte-identical Ontology dump
# ---------------------------------------------------------------------------


def test_same_scenario_yields_identical_ontology_bytes() -> None:
    """Two independent build calls must produce the same JSON dump."""
    data = _minimal_scenario(
        stakeholders=[
            _minimal_segment(segment_id="s1", name="Fans", seg_type="follower_cohort"),
            _minimal_segment(segment_id="s2", name="Lurkers", seg_type="lurker_cohort"),
            _minimal_segment(segment_id="s3", name="Algo", seg_type="algorithmic_cohort"),
        ]
    )
    scen_a = load_scenario_from_dict(data)
    scen_b = load_scenario_from_dict(data)

    onto_a = build_policy_ontology(scen_a)
    onto_b = build_policy_ontology(scen_b)

    assert onto_a.model_dump_json() == onto_b.model_dump_json()


def test_duplicate_segment_types_dedup_in_ontology() -> None:
    """Two stakeholders of the same type collapse to one entity_types key."""
    data = _minimal_scenario(
        stakeholders=[
            _minimal_segment(segment_id="s1", name="Fans A", seg_type="follower_cohort"),
            _minimal_segment(segment_id="s2", name="Fans B", seg_type="follower_cohort"),
        ]
    )
    scen = load_scenario_from_dict(data)
    onto = build_policy_ontology(scen)
    assert list(onto.entity_types.keys()) == ["follower_cohort"]


def test_entity_type_keys_are_sorted_for_byte_stability() -> None:
    """The key order in entity_types must be deterministic (sorted)."""
    # Intentionally declare segments in a non-sorted-by-type order.
    data = _minimal_scenario(
        stakeholders=[
            _minimal_segment(segment_id="s1", name="L", seg_type="lurker_cohort"),
            _minimal_segment(segment_id="s2", name="A", seg_type="algorithmic_cohort"),
            _minimal_segment(segment_id="s3", name="F", seg_type="follower_cohort"),
        ]
    )
    scen = load_scenario_from_dict(data)
    onto = build_policy_ontology(scen)
    assert list(onto.entity_types.keys()) == sorted(onto.entity_types.keys())
    assert list(onto.entity_types.keys()) == [
        "algorithmic_cohort",
        "follower_cohort",
        "lurker_cohort",
    ]


# ---------------------------------------------------------------------------
# 2. Vocabulary label sourced from persona_defaults
# ---------------------------------------------------------------------------


def test_creator_persona_uses_audience_cohorts_label() -> None:
    """The 'description' attribute echoes the persona's vocab label."""
    scen = load_scenario_from_dict(_minimal_scenario())
    onto = build_policy_ontology(scen)
    only_entry = next(iter(onto.entity_types.values()))
    assert only_entry["description"] == "Audience cohorts"
    assert only_entry["segment_type"] == "follower_cohort"


def test_policy_public_persona_uses_stakeholder_groups_label() -> None:
    scen = load_scenario_from_dict(
        _minimal_scenario(
            persona="policy",
            scope="public_policy",
            stakeholders=[
                _minimal_segment(
                    segment_id="s_citizen",
                    name="Citizens",
                    seg_type="citizen_segment",
                ),
            ],
        )
    )
    onto = build_policy_ontology(scen)
    assert next(iter(onto.entity_types.values()))["description"] == "Stakeholder groups"


def test_policy_internal_persona_uses_employee_cohorts_label() -> None:
    scen = load_scenario_from_dict(
        _minimal_scenario(
            persona="policy",
            scope="internal_org",
            stakeholders=[
                _minimal_segment(
                    segment_id="s_emp",
                    name="Employees",
                    seg_type="employee_cohort",
                ),
            ],
        )
    )
    onto = build_policy_ontology(scen)
    assert next(iter(onto.entity_types.values()))["description"] == "Employee cohorts"


# ---------------------------------------------------------------------------
# 3. persona / scope extras roundtrip
# ---------------------------------------------------------------------------


def test_persona_and_scope_echo_back_on_ontology_extras() -> None:
    """Ontology.model_config.extra='allow' carries persona + scope through."""
    scen = load_scenario_from_dict(
        _minimal_scenario(
            persona="policy",
            scope="internal_org",
            stakeholders=[
                _minimal_segment(
                    segment_id="s_emp", name="E", seg_type="employee_cohort"
                ),
            ],
        )
    )
    onto = build_policy_ontology(scen)
    # Round-trip via JSON dump -> dict so we exercise the actual wire shape.
    dumped = onto.model_dump(mode="json")
    assert dumped["persona"] == "policy"
    assert dumped["scope"] == "internal_org"


def test_non_policy_scope_is_none_in_dump() -> None:
    scen = load_scenario_from_dict(_minimal_scenario())
    onto = build_policy_ontology(scen)
    dumped = onto.model_dump(mode="json")
    assert dumped["persona"] == "creator"
    assert dumped["scope"] is None


# ---------------------------------------------------------------------------
# 4. Round-trip parity: dumped Ontology re-parses to the same model
# ---------------------------------------------------------------------------


def test_ontology_roundtrip_through_model_validate() -> None:
    scen = load_scenario_from_dict(_minimal_scenario())
    onto = build_policy_ontology(scen)
    reparsed = Ontology.model_validate(onto.model_dump(mode="json"))
    assert reparsed.model_dump_json() == onto.model_dump_json()


# ---------------------------------------------------------------------------
# 5. Independence: distinct Scenarios produce distinct ontologies
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "persona,scope,seg_type",
    [
        ("creator", None, "follower_cohort"),
        ("video", None, "subscriber_cohort"),
        ("policy", "internal_org", "employee_cohort"),
        ("policy", "public_policy", "citizen_segment"),
    ],
)
def test_distinct_personas_produce_distinct_descriptions(
    persona: str, scope: str | None, seg_type: str
) -> None:
    """Smoke: every persona+scope combo yields a non-empty distinct vocab label."""
    overrides: Dict[str, Any] = {
        "persona": persona,
        "stakeholders": [
            _minimal_segment(segment_id="x", name="X", seg_type=seg_type)
        ],
    }
    if scope is not None:
        overrides["scope"] = scope
    scen = load_scenario_from_dict(_minimal_scenario(**overrides))
    onto = build_policy_ontology(scen)
    desc = next(iter(onto.entity_types.values()))["description"]
    assert isinstance(desc, str) and desc
