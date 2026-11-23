"""Pure-model tests for ``app.policy.priors.loader.parse_priors``.

No Flask, no fs. Exercises the YAML / JSON parity contract, syntax
error surfaces, and surfaces the structured ``{loc, msg, code}`` shape
of each underlying :class:`AudienceSegmentPriors` validation failure.
"""

from __future__ import annotations

import json

import pytest

from app.policy.priors import PriorValidationError, parse_priors
from app.policy.schema import AudienceSegmentPriors


def _good_bundle_dict() -> dict:
    """A canonical bundle whose weights sum to 1.0 across one dimension."""
    return {
        "priors": [
            {
                "dimension": "stance",
                "support": "bernoulli",
                "params": {"p": 0.6},
                "stance": "support",
                "weight": 0.6,
                "provenance_id": "prov_sample_a",
            },
            {
                "dimension": "stance",
                "support": "bernoulli",
                "params": {"p": 0.4},
                "stance": "oppose",
                "weight": 0.4,
                "provenance_id": "prov_sample_b",
            },
        ],
        "override_llm_default": True,
        "free_text_context": "test context",
    }


def test_parse_priors_yaml_and_json_parity_round_trip():
    """The same canonical dict serialised as YAML or JSON yields the same
    AudienceSegmentPriors model_dump()."""
    data = _good_bundle_dict()
    json_text = json.dumps(data)
    # Hand-written YAML equivalent (yaml.safe_dump would also work but we
    # want to prove the loader accepts arbitrary user-authored YAML).
    yaml_text = """
priors:
  - dimension: stance
    support: bernoulli
    params: {p: 0.6}
    stance: support
    weight: 0.6
    provenance_id: prov_sample_a
  - dimension: stance
    support: bernoulli
    params: {p: 0.4}
    stance: oppose
    weight: 0.4
    provenance_id: prov_sample_b
override_llm_default: true
free_text_context: "test context"
""".strip()

    parsed_json = parse_priors(json_text, "json")
    parsed_yaml = parse_priors(yaml_text, "yaml")

    assert isinstance(parsed_json, AudienceSegmentPriors)
    assert isinstance(parsed_yaml, AudienceSegmentPriors)
    assert parsed_json.model_dump() == parsed_yaml.model_dump()


def test_parse_priors_invalid_yaml_syntax_surfaces_yaml_syntax_code():
    bad = "priors: [unbalanced"
    with pytest.raises(PriorValidationError) as ei:
        parse_priors(bad, "yaml")
    err = ei.value
    assert err.errors[0]["code"] == "yaml_syntax"
    assert err.errors[0]["loc"] == "<root>"


def test_parse_priors_invalid_json_syntax_surfaces_json_syntax_code():
    bad = "{not-valid-json"
    with pytest.raises(PriorValidationError) as ei:
        parse_priors(bad, "json")
    err = ei.value
    assert err.errors[0]["code"] == "json_syntax"
    assert err.errors[0]["loc"] == "<root>"


def test_parse_priors_bad_weight_sum_per_dimension_surfaces_loc():
    """Weights that don't sum to 1.0 surface the model_validator error."""
    data = _good_bundle_dict()
    data["priors"][0]["weight"] = 0.9  # 0.9 + 0.4 = 1.3 -> reject
    with pytest.raises(PriorValidationError) as ei:
        parse_priors(json.dumps(data), "json")
    err = ei.value
    assert any("must sum to 1.0" in e["msg"] for e in err.errors)


def test_parse_priors_unknown_support_rejected():
    data = _good_bundle_dict()
    data["priors"][0]["support"] = "lognormal"
    with pytest.raises(PriorValidationError) as ei:
        parse_priors(json.dumps(data), "json")
    err = ei.value
    # The loc points at the offending field. Pydantic v2 surfaces a
    # ``literal_error`` for Literal mismatches.
    assert any(
        e["loc"].startswith("priors[0].support") and e["code"] == "literal_error"
        for e in err.errors
    )


def test_parse_priors_empty_params_rejected():
    data = _good_bundle_dict()
    data["priors"][0]["params"] = {}
    with pytest.raises(PriorValidationError) as ei:
        parse_priors(json.dumps(data), "json")
    err = ei.value
    assert any(e["loc"].startswith("priors[0].params") for e in err.errors)


def test_parse_priors_unknown_stance_rejected():
    data = _good_bundle_dict()
    data["priors"][0]["stance"] = "ambivalent"
    with pytest.raises(PriorValidationError) as ei:
        parse_priors(json.dumps(data), "json")
    err = ei.value
    assert any(
        e["loc"].startswith("priors[0].stance") and e["code"] == "literal_error"
        for e in err.errors
    )


def test_parse_priors_bad_provenance_id_pattern_rejected():
    data = _good_bundle_dict()
    data["priors"][0]["provenance_id"] = "not-a-provenance"
    with pytest.raises(PriorValidationError) as ei:
        parse_priors(json.dumps(data), "json")
    err = ei.value
    assert any(
        e["loc"].startswith("priors[0].provenance_id")
        and e["code"] == "string_pattern_mismatch"
        for e in err.errors
    )


def test_parse_priors_top_level_non_mapping_rejected():
    """A YAML list at the root is a structural mismatch, not a syntax error."""
    with pytest.raises(PriorValidationError) as ei:
        parse_priors("[1, 2, 3]", "json")
    err = ei.value
    assert err.errors[0]["code"] == "not_mapping"
    assert err.errors[0]["loc"] == "<root>"


def test_parse_priors_unknown_top_level_field_rejected_extra_forbid():
    """``extra='forbid'`` on the model rejects any unknown top-level key."""
    data = _good_bundle_dict()
    data["unknown_top_key"] = "x"
    with pytest.raises(PriorValidationError) as ei:
        parse_priors(json.dumps(data), "json")
    err = ei.value
    assert any("unknown_top_key" in e["loc"] for e in err.errors)
