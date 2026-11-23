"""``PersonaSpec.use_case_id`` self-describing contract.

Pins:
  - A persona constructed inside a CatalogPayload without an explicit
    ``use_case_id`` inherits the envelope's id (backfill).
  - A persona that ships an explicit ``use_case_id`` matching the
    envelope is accepted unchanged.
  - A mismatch between the spec's explicit id and the envelope's id
    raises ValidationError (no silent envelope-membership smuggling).
  - The default :class:`FakeUseCase` from tests_helpers produces a
    catalog whose persona row is self-describing after construction.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.simulation_platform.catalog import PersonaSpec
from app.simulation_platform.tests_helpers import build_fake_use_case
from tests._helpers import catalog_payload


def _make_spec(**overrides) -> PersonaSpec:
    """Shorthand: build a minimal PersonaSpec; overrides win.

    ``base`` is constructed as a plain ``dict`` (typed by pyright as
    ``dict[str, str]``) and then merged with arbitrary ``overrides``;
    spreading into ``PersonaSpec(**base)`` loses per-field type info,
    so the constructor call is annotated to suppress the resulting
    union-mismatch noise without weakening the model itself.
    """
    base = dict(
        persona_id="creator",
        label="Creator",
        cohort_question="How will my post land?",
    )
    base.update(overrides)
    return PersonaSpec(**base)  # type: ignore[arg-type]


def test_persona_use_case_id_backfilled_from_envelope():
    spec = _make_spec()
    assert spec.use_case_id is None
    payload = catalog_payload(use_case_id="lane_a", personas=[spec])
    # Backfilled in-place after the envelope validator runs.
    assert payload.personas[0].use_case_id == "lane_a"
    # The original handle reflects the same mutation (validator
    # mutates the spec instance attached to the envelope, not a copy).
    assert spec.use_case_id == "lane_a"


def test_persona_use_case_id_passthrough_when_matching():
    spec = _make_spec(use_case_id="lane_a")
    payload = catalog_payload(use_case_id="lane_a", personas=[spec])
    assert payload.personas[0].use_case_id == "lane_a"


def test_persona_use_case_id_mismatch_raises():
    spec = _make_spec(use_case_id="other_lane")
    with pytest.raises(ValidationError) as excinfo:
        catalog_payload(use_case_id="lane_a", personas=[spec])
    msg = str(excinfo.value)
    assert "other_lane" in msg
    assert "lane_a" in msg


def test_persona_use_case_id_explicit_when_constructed_standalone():
    """Ad-hoc specs (no envelope) should set the id themselves to stay
    self-describing in downstream consumers (debug logs, fixtures).
    """
    spec = _make_spec(use_case_id="standalone_lane")
    assert spec.use_case_id == "standalone_lane"


def test_fake_use_case_catalog_self_describes():
    uc = build_fake_use_case(uid="fake_lane")
    cat = uc.catalog()
    assert cat.use_case_id == "fake_lane"
    assert cat.personas[0].use_case_id == "fake_lane"


def test_multiple_personas_backfilled_uniformly():
    specs = [
        _make_spec(persona_id="a", label="A", cohort_question="?"),
        _make_spec(persona_id="b", label="B", cohort_question="?"),
        _make_spec(persona_id="c", label="C", cohort_question="?", use_case_id="lane_a"),
    ]
    payload = catalog_payload(use_case_id="lane_a", personas=specs)
    for spec in payload.personas:
        assert spec.use_case_id == "lane_a"
