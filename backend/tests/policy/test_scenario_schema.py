"""Scenario schema + loader tests.

Pure Pydantic / loader tests; no Flask or Config bootstrap. Covers
every :class:`Scenario` invariant: persona/scope discriminator, allowed
``AudienceSegment.type`` per persona, falsifier contract on
``what_changes_their_mind``, TraitMap typed + freeform escape, prior
provenance, ``drift_horizon_rounds`` at the root, and the HypothesisTag
taxonomy boundary.
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from app.policy import (
    AudienceSegmentPriors,
    OceanScores,
    PriorDistribution,
    PriorProvenance,
    Scenario,
    SchwartzScores,
    TraitMap,
    Variant,
    VariantKind,
    load_scenario_from_dict,
    load_scenario_from_yaml,
)
from app.policy import schema as schema_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_GLOB_ROOT = REPO_ROOT / "examples" / "policy"


def _minimal_segment(
    *,
    segment_id: str = "s_fans",
    seg_type: str = "follower_cohort",
    influence: int = 3,
    vulnerability: int = 3,
) -> Dict[str, Any]:
    """A minimal AudienceSegment dict that passes all field validators."""
    return {
        "segment_id": segment_id,
        "name": "Fans cohort",
        "type": seg_type,
        "influence_level": influence,
        "vulnerability_level": vulnerability,
        "compliance_propensity": 3,
        "trust_level": 3,
        "mobilization_capacity": 3,
        "what_changes_their_mind": ["new evidence emerges"],
    }


def _minimal_scenario(**overrides: Any) -> Dict[str, Any]:
    """Build a minimal creator-persona scenario dict.

    Uses ``follower_cohort`` (a valid type for persona=creator) by default.
    Callers can override any top-level key via ``overrides``.
    """
    data: Dict[str, Any] = {
        "persona": "creator",
        "title": "Minimal scenario",
        "summary": "A minimal creator scenario for schema tests.",
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
# 1. round-trip
# ---------------------------------------------------------------------------


def test_valid_scenario_roundtrip() -> None:
    data = _minimal_scenario()
    scen = load_scenario_from_dict(data)
    dumped = scen.model_dump(by_alias=True, mode="python")
    reloaded = load_scenario_from_dict(dumped)
    # Compare via dump (Pydantic enums make __eq__ surprising across reloads).
    assert reloaded.model_dump(by_alias=True, mode="python") == dumped


# ---------------------------------------------------------------------------
# 2. missing seed_text
# ---------------------------------------------------------------------------


def test_missing_seed_text_rejected() -> None:
    data = _minimal_scenario()
    del data["seed_text"]
    with pytest.raises(Exception) as ei:
        load_scenario_from_dict(data)
    msg = str(ei.value)
    assert "seed_text" in msg or "policy_text" in msg


# ---------------------------------------------------------------------------
# 3. missing stress_question
# ---------------------------------------------------------------------------


def test_missing_stress_question_rejected() -> None:
    data = _minimal_scenario()
    del data["stress_question"]
    with pytest.raises(Exception) as ei:
        load_scenario_from_dict(data)
    msg = str(ei.value)
    assert "stress_question" in msg or "policy_question" in msg


# ---------------------------------------------------------------------------
# 4. legacy aliases parse, canonical names emitted
# ---------------------------------------------------------------------------


def test_legacy_aliases_accepted() -> None:
    data = _minimal_scenario()
    data["policy_text"] = data.pop("seed_text")
    data["policy_question"] = data.pop("stress_question")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        scen = load_scenario_from_dict(data)

    # Legacy fields populate canonical attributes.
    assert scen.seed_text == data["policy_text"]
    assert scen.stress_question == data["policy_question"]

    # Canonical serialisation emits seed_text / stress_question, not legacy keys.
    dumped = scen.model_dump(by_alias=True, mode="python")
    assert "seed_text" in dumped
    assert "stress_question" in dumped
    assert "policy_text" not in dumped
    assert "policy_question" not in dumped

    # DeprecationWarning was raised at least once.
    assert any(
        issubclass(w.category, DeprecationWarning)
        and ("policy_text" in str(w.message) or "policy_question" in str(w.message))
        for w in caught
    ), f"expected DeprecationWarning, got: {[str(w.message) for w in caught]}"


# ---------------------------------------------------------------------------
# 5. persona=policy requires scope
# ---------------------------------------------------------------------------


def test_policy_persona_requires_scope() -> None:
    data = _minimal_scenario(
        persona="policy",
        stakeholders=[_minimal_segment(seg_type="employee_cohort")],
    )
    # No scope -> reject.
    with pytest.raises(Exception) as ei:
        load_scenario_from_dict(data)
    assert "scope" in str(ei.value)


# ---------------------------------------------------------------------------
# 6. non-policy persona rejects scope
# ---------------------------------------------------------------------------


def test_non_policy_persona_rejects_scope() -> None:
    data = _minimal_scenario(scope="internal_org")
    with pytest.raises(Exception) as ei:
        load_scenario_from_dict(data)
    assert "scope" in str(ei.value)


# ---------------------------------------------------------------------------
# 7. all five fixtures load
# ---------------------------------------------------------------------------


_FIXTURE_PATHS = sorted(FIXTURE_GLOB_ROOT.glob("*/*.yaml"))


def test_fixture_corpus_count() -> None:
    """Sanity check: the glob must find exactly the 5 scenario fixtures."""
    assert len(_FIXTURE_PATHS) == 5, (
        f"expected 5 fixtures under {FIXTURE_GLOB_ROOT}, "
        f"got {len(_FIXTURE_PATHS)}: {_FIXTURE_PATHS}"
    )


@pytest.mark.parametrize("fixture_path", _FIXTURE_PATHS, ids=lambda p: p.name)
def test_all_five_fixtures_load(fixture_path: Path) -> None:
    scen = load_scenario_from_yaml(fixture_path)
    assert isinstance(scen, Scenario)
    # Smoke: round-trip dump must also be re-loadable.
    reloaded = load_scenario_from_dict(scen.model_dump(by_alias=True, mode="python"))
    assert isinstance(reloaded, Scenario)


# ---------------------------------------------------------------------------
# 8. AudienceSegment 1..5 bounds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [0, 6, -1, 100])
def test_audience_segment_bounds_reject_out_of_range(bad_value: int) -> None:
    data = _minimal_scenario(
        stakeholders=[_minimal_segment(influence=bad_value)],
    )
    with pytest.raises(Exception):
        load_scenario_from_dict(data)


def test_audience_segment_bounds_accept_middle() -> None:
    data = _minimal_scenario(
        stakeholders=[_minimal_segment(influence=3, vulnerability=3)],
    )
    scen = load_scenario_from_dict(data)
    assert scen.stakeholders[0].influence_level == 3
    assert scen.stakeholders[0].vulnerability_level == 3


def test_audience_segment_vulnerability_bounds_rejected() -> None:
    data = _minimal_scenario(
        stakeholders=[_minimal_segment(vulnerability=6)],
    )
    with pytest.raises(Exception):
        load_scenario_from_dict(data)


# ---------------------------------------------------------------------------
# 9. counterfactual requires base_variant_id
# ---------------------------------------------------------------------------


def test_counterfactual_requires_base_variant_id() -> None:
    with pytest.raises(ValidationError) as ei:
        Variant(
            variant_id="v_cf",
            name="Counterfactual A",
            description="Some counterfactual without a base.",
            kind=VariantKind.COUNTERFACTUAL,
        )
    assert "base_variant_id" in str(ei.value)


def test_counterfactual_with_base_variant_id_accepted() -> None:
    v = Variant(
        variant_id="v_cf",
        name="Counterfactual A",
        description="Counterfactual with a base.",
        kind=VariantKind.COUNTERFACTUAL,
        base_variant_id="v_base",
    )
    assert v.base_variant_id == "v_base"


# ---------------------------------------------------------------------------
# 10. Variant.diff op allowlist (replace/add/remove allowed, others not)
# ---------------------------------------------------------------------------


def test_variant_diff_add_accepted() -> None:
    v = Variant.model_validate({
        "variant_id": "v_sens",
        "name": "Sensitivity A",
        "description": "Sensitivity tweak.",
        "kind": VariantKind.SENSITIVITY.value,
        "diff": [
            {
                "op": "add",
                "path": "/scenario/variants/0/changed_assumptions",
                "value": ["new assumption"],
            }
        ],
    })
    assert v.diff[0].op == "add"


def test_variant_diff_replace_accepted() -> None:
    v = Variant.model_validate({
        "variant_id": "v_sens",
        "name": "Sensitivity B",
        "description": "Sensitivity tweak.",
        "kind": VariantKind.SENSITIVITY.value,
        "diff": [
            {
                "op": "replace",
                "path": "/scenario/variants/0/name",
                "value": "renamed",
            }
        ],
    })
    assert v.diff[0].op == "replace"


@pytest.mark.parametrize("bad_op", ["move", "copy", "test"])
def test_variant_diff_disallowed_ops_rejected(bad_op: str) -> None:
    with pytest.raises(ValidationError) as ei:
        Variant.model_validate({
            "variant_id": "v_sens",
            "name": "Sensitivity",
            "description": "Sensitivity.",
            "kind": VariantKind.SENSITIVITY.value,
            "diff": [
                {
                    "op": bad_op,
                    "path": "/scenario/variants/0",
                    "value": None,
                }
            ],
        })
    assert "op" in str(ei.value)


# ---------------------------------------------------------------------------
# 11. TraitMap: typed OCEAN/Schwartz/parasocial + freeform escape
# ---------------------------------------------------------------------------


def test_trait_map_accepts_ocean_and_parasocial() -> None:
    seg = _minimal_segment()
    seg["traits"] = {
        "ocean": {
            "openness": 0.6,
            "conscientiousness": 0.5,
            "extraversion": 0.4,
            "agreeableness": 0.7,
            "neuroticism": 0.3,
        },
        "parasocial_intensity": 0.42,
    }
    data = _minimal_scenario(stakeholders=[seg])
    scen = load_scenario_from_dict(data)
    tm = scen.stakeholders[0].traits
    assert isinstance(tm, TraitMap)
    assert isinstance(tm.ocean, OceanScores)
    assert tm.ocean.openness == 0.6
    assert tm.parasocial_intensity == 0.42


def test_trait_map_accepts_schwartz() -> None:
    seg = _minimal_segment()
    seg["traits"] = {
        "values": {
            k: 0.5 for k in [
                "self_direction", "stimulation", "hedonism", "achievement",
                "power", "security", "conformity", "tradition",
                "benevolence", "universalism",
            ]
        }
    }
    data = _minimal_scenario(stakeholders=[seg])
    scen = load_scenario_from_dict(data)
    tm = scen.stakeholders[0].traits
    assert tm is not None, "TraitMap should be populated from the dict input"
    assert isinstance(tm.values, SchwartzScores)


def test_trait_map_unknown_top_level_key_rejected_unless_in_freeform() -> None:
    """Unknown keys at the TraitMap top level fail; freeform dict accepts them."""
    seg = _minimal_segment()
    seg["traits"] = {"definitely_not_a_real_trait_key": "x"}
    data = _minimal_scenario(stakeholders=[seg])
    with pytest.raises(Exception):
        load_scenario_from_dict(data)

    # Same value tucked into freeform: accepted.
    seg2 = _minimal_segment()
    seg2["traits"] = {"freeform": {"definitely_not_a_real_trait_key": "x"}}
    data2 = _minimal_scenario(stakeholders=[seg2])
    scen = load_scenario_from_dict(data2)
    tm2 = scen.stakeholders[0].traits
    assert tm2 is not None, "TraitMap should be populated from the dict input"
    assert tm2.freeform == {
        "definitely_not_a_real_trait_key": "x"
    }


def test_trait_schema_ref_accepted() -> None:
    seg = _minimal_segment()
    seg["trait_schema_ref"] = "schemas/trait/v1#creator"
    data = _minimal_scenario(stakeholders=[seg])
    scen = load_scenario_from_dict(data)
    assert scen.stakeholders[0].trait_schema_ref == "schemas/trait/v1#creator"


def test_trait_key_allowlist_was_deleted() -> None:
    """The flat allowlist + _validate_trait_keys validator are gone."""
    assert not hasattr(schema_module, "TRAIT_KEY_ALLOWLIST")
    assert not hasattr(Scenario, "_validate_trait_keys")


# ---------------------------------------------------------------------------
# 12. PriorProvenance / PriorDistribution / AudienceSegmentPriors
# ---------------------------------------------------------------------------


def _valid_provenance(prov_id: str = "prov_anes_2024_01") -> Dict[str, Any]:
    return {
        "id": prov_id,
        "source_type": "survey",
        "license_class": "permissive",
    }


def test_provenance_id_pattern_enforced() -> None:
    with pytest.raises(ValidationError):
        PriorProvenance(id="not-allowed-id", source_type="survey",
                        license_class="permissive")
    p = PriorProvenance(**_valid_provenance())
    assert p.id.startswith("prov_")


def test_provenance_attribution_required_when_license_demands_it() -> None:
    with pytest.raises(ValidationError) as ei:
        PriorProvenance(
            id="prov_x", source_type="survey",
            license_class="attribution_required",
        )
    assert "attribution_string" in str(ei.value)

    p = PriorProvenance(
        id="prov_x", source_type="survey",
        license_class="share_alike",
        attribution_string="Author 2024",
    )
    assert p.attribution_string == "Author 2024"


def test_prior_distribution_shape() -> None:
    pd = PriorDistribution(
        dimension="support_for_proposal",
        support="beta",
        params={"alpha": 2.0, "beta": 5.0},
        stance="support",
        weight=1.0,
        provenance_id="prov_anes_2024_01",
    )
    assert pd.dimension == "support_for_proposal"
    assert pd.support == "beta"
    assert pd.params == {"alpha": 2.0, "beta": 5.0}


def test_audience_segment_priors_sum_to_one_per_dimension() -> None:
    # OK: two dimensions each summing to 1.
    AudienceSegmentPriors(
        priors=[
            PriorDistribution(dimension="d1", support="bernoulli",
                              params={"p": 0.6}, stance="support",
                              weight=0.6, provenance_id="prov_a"),
            PriorDistribution(dimension="d1", support="bernoulli",
                              params={"p": 0.4}, stance="oppose",
                              weight=0.4, provenance_id="prov_a"),
            PriorDistribution(dimension="d2", support="bernoulli",
                              params={"p": 0.5}, stance="neutral",
                              weight=1.0, provenance_id="prov_b"),
        ]
    )

    # Reject: d1 weights only sum to 0.7.
    with pytest.raises(ValidationError) as ei:
        AudienceSegmentPriors(
            priors=[
                PriorDistribution(dimension="d1", support="bernoulli",
                                  params={"p": 0.6}, stance="support",
                                  weight=0.7, provenance_id="prov_a"),
            ]
        )
    assert "d1" in str(ei.value)


# ---------------------------------------------------------------------------
# 13. Strict-int contract on AudienceSegment (float 3.0 rejected)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "influence_level",
        "vulnerability_level",
        "trust_level",
        "compliance_propensity",
        "mobilization_capacity",
    ],
)
def test_segment_strict_int_rejects_float(field: str) -> None:
    seg = _minimal_segment()
    seg[field] = 3.0  # strict=True rejects float even for whole-number value.
    data = _minimal_scenario(stakeholders=[seg])
    with pytest.raises(Exception):
        load_scenario_from_dict(data)


# ---------------------------------------------------------------------------
# 14. RunControls: tier + t_structure + t_narrative + ensemble_n le=20
# ---------------------------------------------------------------------------


def test_run_controls_default_tier_and_temperatures() -> None:
    data = _minimal_scenario()
    scen = load_scenario_from_dict(data)
    assert scen.run.tier == "sanity"
    assert scen.run.t_structure == 0.2
    assert scen.run.t_narrative == 0.7


def test_run_controls_rejects_unknown_tier() -> None:
    data = _minimal_scenario(run={"tier": "production"})
    with pytest.raises(Exception):
        load_scenario_from_dict(data)


def test_run_controls_ensemble_n_capped_at_20() -> None:
    data = _minimal_scenario(run={"ensemble_n": 21})
    with pytest.raises(Exception):
        load_scenario_from_dict(data)
    scen = load_scenario_from_dict(_minimal_scenario(run={"ensemble_n": 20}))
    assert scen.run.ensemble_n == 20


def test_run_controls_temperature_default_removed() -> None:
    """temperature_default was replaced by t_structure / t_narrative."""
    data = _minimal_scenario(run={"temperature_default": 0.5})
    with pytest.raises(Exception):
        load_scenario_from_dict(data)


# ---------------------------------------------------------------------------
# 15. drift_horizon_rounds at Scenario root + older-input lift
# ---------------------------------------------------------------------------


def test_drift_horizon_rounds_root_default_three() -> None:
    scen = load_scenario_from_dict(_minimal_scenario())
    assert scen.drift_horizon_rounds == 3


def test_drift_horizon_rounds_root_value_preserved() -> None:
    scen = load_scenario_from_dict(_minimal_scenario(drift_horizon_rounds=7))
    assert scen.drift_horizon_rounds == 7
    dumped = scen.model_dump(by_alias=True, mode="python")
    assert dumped["drift_horizon_rounds"] == 7
    # RunControls dump no longer carries the field.
    assert "drift_horizon_rounds" not in dumped["run"]


def test_drift_horizon_rounds_legacy_run_value_lifted() -> None:
    """Legacy run.drift_horizon_rounds is lifted to the Scenario root.

    On dump the root field is the only place the value lives.
    """
    data = _minimal_scenario(run={"drift_horizon_rounds": 5})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        scen = load_scenario_from_dict(data)
    assert scen.drift_horizon_rounds == 5
    dumped = scen.model_dump(by_alias=True, mode="python")
    assert dumped["drift_horizon_rounds"] == 5
    assert "drift_horizon_rounds" not in dumped["run"]
    assert any(
        issubclass(w.category, DeprecationWarning)
        and "drift_horizon_rounds" in str(w.message)
        for w in caught
    )


def test_drift_horizon_rounds_root_wins_when_both_present() -> None:
    data = _minimal_scenario(
        drift_horizon_rounds=4,
        run={"drift_horizon_rounds": 9},
    )
    scen = load_scenario_from_dict(data)
    # Root explicitly set => legacy lift is a no-op.
    assert scen.drift_horizon_rounds == 4


# ---------------------------------------------------------------------------
# 16. Scenario root additions: drift_sensitivity_preset / trust_baseline /
#     expected_risk_tags
# ---------------------------------------------------------------------------


def test_scenario_root_additions_accepted() -> None:
    data = _minimal_scenario(
        drift_sensitivity_preset="volatile",
        trust_baseline=0.65,
        expected_risk_tags=["adoption", "backlash"],
    )
    scen = load_scenario_from_dict(data)
    assert scen.drift_sensitivity_preset == "volatile"
    assert scen.trust_baseline == 0.65
    assert scen.expected_risk_tags == ["adoption", "backlash"]


def test_scenario_root_drift_sensitivity_preset_literal_enforced() -> None:
    data = _minimal_scenario(drift_sensitivity_preset="extreme")
    with pytest.raises(Exception):
        load_scenario_from_dict(data)


# ---------------------------------------------------------------------------
# 17. Scale cap overridable via validation context
# ---------------------------------------------------------------------------


def test_scale_cap_overridable_via_env_context() -> None:
    """ensemble_n * len(stakeholders) * drift_horizon_rounds = 1000 triggers
    the cap when allow_expensive is False, validates when True."""
    big_segments = [
        _minimal_segment(segment_id=f"s_{i}", seg_type="follower_cohort")
        for i in range(10)
    ]
    data = _minimal_scenario(
        stakeholders=big_segments,
        drift_horizon_rounds=20,
        run={"ensemble_n": 5},  # 10 * 20 * 5 = 1000 > SCALE_CAP_POC (500)
    )

    # Without override: rejected.
    with pytest.raises(Exception) as ei:
        load_scenario_from_dict(data, allow_expensive=False)
    assert "scale cap" in str(ei.value)

    # With explicit override (mirrors POLICY_ALLOW_EXPENSIVE=true): accepted.
    scen = load_scenario_from_dict(data, allow_expensive=True)
    assert scen.run.ensemble_n == 5
    assert scen.drift_horizon_rounds == 20


def test_scale_cap_context_passes_through_model_validate_direct() -> None:
    """Direct Scenario.model_validate call must accept context kwarg."""
    big_segments = [
        _minimal_segment(segment_id=f"s_{i}", seg_type="follower_cohort")
        for i in range(10)
    ]
    payload = _minimal_scenario(
        stakeholders=big_segments,
        drift_horizon_rounds=20,
        run={"ensemble_n": 5},
    )
    with pytest.raises(ValidationError):
        Scenario.model_validate(payload, context={"allow_expensive": False})
    scen = Scenario.model_validate(payload, context={"allow_expensive": True})
    assert scen.drift_horizon_rounds == 20


# ---------------------------------------------------------------------------
# 18. HypothesisTag does not live on app.policy.schema
# ---------------------------------------------------------------------------


def test_no_new_tag_values_introduced() -> None:
    """HypothesisTag belongs in app.policy.hypothesis_tags; the scenario
    schema module may import it (e.g. for the Claim model) but must not
    redeclare it. The check below pins that any HypothesisTag attribute
    on the schema module IS the canonical one via identity comparison,
    not a fork."""
    from app.policy.hypothesis_tags import HypothesisTag as CanonicalHypothesisTag
    schema_tag = getattr(schema_module, "HypothesisTag", None)
    if schema_tag is not None:
        assert schema_tag is CanonicalHypothesisTag, (
            "HypothesisTag on schema module must be the canonical import "
            "from app.policy.hypothesis_tags, not a redeclared fork"
        )


# ---------------------------------------------------------------------------
# 19. SSoT invariant: module frozensets match inline Literals
# ---------------------------------------------------------------------------
from typing import get_args  # noqa: E402


def test_provenance_source_type_literal_matches_frozenset() -> None:
    """PriorProvenance.source_type Literal must equal _PROVENANCE_SOURCE_TYPES."""
    literal_values = set(
        get_args(PriorProvenance.model_fields["source_type"].annotation)
    )
    assert literal_values == set(schema_module._PROVENANCE_SOURCE_TYPES)


def test_provenance_license_class_literal_matches_frozenset() -> None:
    literal_values = set(
        get_args(PriorProvenance.model_fields["license_class"].annotation)
    )
    assert literal_values == set(schema_module._PROVENANCE_LICENSE_CLASSES)


def test_distribution_support_literal_matches_frozenset() -> None:
    literal_values = set(
        get_args(PriorDistribution.model_fields["support"].annotation)
    )
    assert literal_values == set(schema_module._DISTRIBUTION_SUPPORTS)


def test_prior_stance_literal_matches_frozenset() -> None:
    literal_values = set(
        get_args(PriorDistribution.model_fields["stance"].annotation)
    )
    assert literal_values == set(schema_module._PRIOR_STANCES)
