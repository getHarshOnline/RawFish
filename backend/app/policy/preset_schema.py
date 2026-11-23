"""Typed envelope for scenario presets.

:class:`ScenarioPreset` is an author-friendly wrapper around the strict
:class:`app.policy.schema.Scenario`: it accepts aliases (``topic`` for
``seed_text`` / ``stress_question``; ``id`` / ``label`` for
``segment_id`` / ``name``), preset-only metadata (``description``,
``expected_risk_tags``, provenance), and rich knobs (OCEAN, parasocial,
``trust_baseline``, ``drift_sensitivity_preset``, ``domain_context``).
:func:`apply_preset` (in :mod:`.preset_loader`) lowers a wrapper into a
strict Scenario.

Scope is free-form ``str`` at this layer; only the policy persona's
``internal_org`` / ``public_policy`` are promoted to
:class:`PolicySurface`. Other tags flow into ``domain_context['surface']``.
``expected_risk_tags`` validates against
:class:`~app.policy.hypothesis_tags.ExpectedRiskTag` so author typos
fail at the wrapper boundary. All models set ``extra='forbid'`` so typos
surface immediately rather than silently dropping data.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .hypothesis_tags import ExpectedRiskTag
from .schema import Persona, PriorProvenance, _strict_config


# ---------------------------------------------------------------------------
# Module-level regex constants
# ---------------------------------------------------------------------------


# preset_id pattern: ``<persona>/<slug>`` where slug is lowercase ascii
# alphanumerics + underscores, 4-40 chars long.
#
# The persona alternation is BUILT from the canonical
# :class:`app.policy.schema.Persona` enum so adding a 5th persona member
# anywhere updates the preset_id allowlist automatically. Each value is
# ``re.escape``'d defensively even though current persona values are pure
# ASCII letters -- the cost is zero and a future persona slug with a regex
# metacharacter cannot quietly corrupt the pattern.
_PERSONA_VALUES_FOR_REGEX: str = "|".join(re.escape(p.value) for p in Persona)
_PRESET_ID_PATTERN: str = rf"^({_PERSONA_VALUES_FOR_REGEX})/[a-z0-9_]{{4,40}}$"


# ---------------------------------------------------------------------------
# Run-overrides block
# ---------------------------------------------------------------------------


class PresetRunOverrides(BaseModel):
    """Run-controls overrides surfaced at preset-authoring time.

    Maps 1:1 onto :class:`app.policy.schema.RunControls` fields the loader
    promotes; ``drift_horizon_rounds`` is intentionally NOT here because the
    canonical location is :attr:`Scenario.drift_horizon_rounds` at the root.
    Authors set rounds on the scenario body, not under run.
    """

    model_config = _strict_config()

    tier: Optional[Literal["sanity", "report", "publication"]] = None
    ensemble_n: Optional[int] = Field(default=None, ge=1, le=20)
    run_seed: Optional[int] = None
    t_structure: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    t_narrative: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    ensemble_seeds: Optional[List[int]] = None
    persona_salts: Optional[Dict[str, str]] = None


# ---------------------------------------------------------------------------
# Per-segment override (author-friendly shape)
# ---------------------------------------------------------------------------


class PresetSegmentOverride(BaseModel):
    """Per-segment overrides authored against an upstream scaffold segment.

    Author-friendly aliases: ``id`` lowers to ``segment_id``; ``label``
    lowers to ``name``. ``type`` is optional -- if omitted the loader picks
    the first allowed entry from :data:`app.policy.schema.PERSONA_SEGMENT_TYPES`
    for the resolved (persona, scope).

    ``traits`` is a free-form dict so authors can write OCEAN / Schwartz /
    parasocial keys without learning the typed sub-model layout; the loader
    feeds it to :meth:`TraitMap.model_validate`.

    ``priors`` is a list of plain prior dicts; the loader synthesises
    ``provenance_id`` per ``(segment_id, dimension)`` if the author leaves
    it implicit.
    """

    model_config = _strict_config()

    id: str = Field(..., min_length=1)
    label: Optional[str] = Field(default=None, min_length=1)
    type: Optional[str] = Field(default=None, min_length=1)
    weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    interests: Optional[List[str]] = None
    incentives: Optional[List[str]] = None
    constraints: Optional[List[str]] = None
    likely_support_reasons: Optional[List[str]] = None
    likely_opposition_reasons: Optional[List[str]] = None
    information_channels: Optional[List[str]] = None

    influence_level: Optional[int] = Field(default=None, ge=1, le=5, strict=True)
    vulnerability_level: Optional[int] = Field(default=None, ge=1, le=5, strict=True)
    compliance_propensity: Optional[int] = Field(default=None, ge=1, le=5, strict=True)
    trust_level: Optional[int] = Field(default=None, ge=1, le=5, strict=True)
    mobilization_capacity: Optional[int] = Field(default=None, ge=1, le=5, strict=True)

    drift_rules: Optional[str] = None
    drift_params: Optional[Dict[str, Any]] = None
    drift_sensitivity_preset: Optional[
        Literal["conservative", "default", "volatile"]
    ] = None
    traits: Optional[Dict[str, Any]] = None
    trait_schema_ref: Optional[str] = None
    priors: Optional[List[Dict[str, Any]]] = None
    audience_priors: Optional[Dict[str, Any]] = None

    what_changes_their_mind: Optional[List[str]] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Author-facing scenario body (lowers to Scenario)
# ---------------------------------------------------------------------------


class PresetScenarioBody(BaseModel):
    """Author-friendly scenario body that lowers to :class:`Scenario`.

    Notable aliases:

    * ``topic`` is the single author-facing string; the loader derives
      ``title``, ``summary``, ``seed_text``, and ``stress_question`` from
      it deterministically (see :func:`apply_preset`).
    * ``audience_segments`` carries :class:`PresetSegmentOverride` rows
      with ``id``/``label`` instead of ``segment_id``/``name``.
    * ``domain_context`` is a free-form dict that travels through the
      loader and is stashed on the resulting Scenario (``preset_overrides``
      bag) -- non-policy scope strings end up under
      ``domain_context['surface']``.
    """

    model_config = _strict_config()

    topic: str = Field(..., min_length=1)
    title: Optional[str] = Field(default=None, min_length=1)
    summary: Optional[str] = Field(default=None, min_length=1)
    seed_text: Optional[str] = Field(default=None, min_length=1)
    stress_question: Optional[str] = Field(default=None, min_length=1)
    jurisdiction: Optional[str] = None

    clauses: List[Dict[str, Any]] = Field(default_factory=list)
    audience_segments: List[PresetSegmentOverride] = Field(default_factory=list)
    assumptions: Optional[List[str]] = None
    success_criteria: Optional[List[str]] = None
    failure_modes_to_watch: Optional[List[str]] = None
    variants: List[Dict[str, Any]] = Field(default_factory=list)

    domain_context: Dict[str, Any] = Field(default_factory=dict)

    # Root-level Scenario fields the author can pin directly.
    drift_horizon_rounds: Optional[int] = Field(default=None, ge=1, le=20)
    drift_sensitivity_preset: Optional[
        Literal["conservative", "default", "volatile"]
    ] = None
    trust_baseline: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    run: PresetRunOverrides = Field(default_factory=PresetRunOverrides)


# ---------------------------------------------------------------------------
# Wrapper bundle (scenario body + provenance sidecar)
# ---------------------------------------------------------------------------


class PresetOverrides(BaseModel):
    """The override bundle attached to a preset.

    ``scenario`` carries the author-friendly body. ``provenance`` carries a
    list of :class:`PriorProvenance` entries that downstream priors reference
    by id (the loader synthesises ``provenance_id`` strings if priors omit
    them).
    """

    model_config = _strict_config()

    scenario: PresetScenarioBody
    provenance: List[PriorProvenance] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level ScenarioPreset envelope
# ---------------------------------------------------------------------------


class ScenarioPreset(BaseModel):
    """Top-level typed envelope persisted as a preset JSON.

    Attributes
    ----------
    preset_id
        ``<persona>/<slug>``; pattern pinned by :data:`_PRESET_ID_PATTERN`.
    description
        One-line author-facing description.
    persona
        Free-form persona string (validated by the schema discriminator
        when the loader lowers to :class:`Scenario`).
    scope
        Free-form scope tag. Only promoted to a strict
        :class:`app.policy.schema.PolicySurface` when ``persona == 'policy'``
        AND ``scope`` is in {``internal_org``, ``public_policy``}. Otherwise
        stashed on ``domain_context['surface']``.
    region
        Optional ISO-3166 alpha-2 hint or free-form region string surfaced
        in dashboards.
    expected_risk_tags
        Non-empty list of author-declared risk hypotheses; each value must
        be a member of :class:`~app.policy.hypothesis_tags.ExpectedRiskTag`
        (e.g. ``H-RETENTION``). Propagated as a plain ``List[str]`` onto
        ``Scenario.expected_risk_tags`` at the root.
    preset_overrides
        The :class:`PresetOverrides` bundle that lowers to a Scenario.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    preset_id: str = Field(..., pattern=_PRESET_ID_PATTERN)
    description: str = Field(..., min_length=1)
    # The persona discriminator uses the canonical Persona enum directly
    # instead of a duplicated Literal so adding/renaming a persona is a
    # one-place change. Pydantic v2 accepts ``str, Enum`` values both as
    # enum members and as their string values, so existing preset JSONs
    # that author ``"persona": "creator"`` continue to parse unchanged.
    persona: Persona
    scope: Optional[str] = Field(default=None, min_length=1)
    region: Optional[str] = Field(default=None, min_length=1)
    expected_risk_tags: List[ExpectedRiskTag] = Field(..., min_length=1)
    preset_overrides: PresetOverrides


__all__ = [
    "ScenarioPreset",
    "PresetOverrides",
    "PresetScenarioBody",
    "PresetRunOverrides",
    "PresetSegmentOverride",
]
