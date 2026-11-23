"""Tiny deterministic policy ontology adapter.

The policy path builds its ontology without a model call.
:func:`build_policy_ontology` synthesises an :class:`app.seam.protocols.Ontology` directly from the
validated :class:`Scenario` -- specifically, from
``Scenario.stakeholders[].type`` (persona-aware allowlist enforced
upstream) plus the per-(persona, scope) vocabulary in
:mod:`app.policy.persona_defaults`.

Contract:

* Pure function. No LLM, no I/O, no env reads. Same Scenario in -> same
  Ontology bytes out (verified by ``test_ontology_loader.py``).
* The Ontology's ``entity_types`` is keyed by the segment ``type`` strings
  declared on ``Scenario.stakeholders[]``; each value is a small JSON-schema
  fragment carrying the persona-aware vocabulary label as ``description``.
  Both ``MockZepStorage.set_ontology`` and
  ``GraphitiAudienceStore.set_ontology`` accept this shape -- the only hard
  requirement on the inner dict is JSON-serialisability.
* ``Ontology.model_config.extra='allow'`` (see ``app.seam.protocols``) lets
  us thread through two book-keeping extras without expanding the typed
  surface: ``persona`` (string) and ``scope`` (string or None). They are
  informational only; the audience-store backends ignore unknown keys.

Why no model call? A content-derived ontology would vary between runs. The
policy lane needs a deterministic, persona-validated ontology.
"""
from __future__ import annotations

from typing import Any, Dict

from ...policy.persona_defaults import vocabulary_for
from ...policy.schema import Scenario
from ...policy_core.scope_context import scenario_policy_scope, vocabulary_scope
from ...seam.protocols import Ontology


def build_policy_ontology(scenario: Scenario) -> Ontology:
    """Synthesise a deterministic :class:`Ontology` from a Scenario.

    The entity-type set is the deduplicated, sorted list of
    ``segment.type`` strings on ``Scenario.stakeholders[]`` -- the
    persona-aware allowlist is already enforced by
    :func:`Scenario._validate_segment_types` upstream, so this loader
    trusts those strings without re-checking them.

    Each entry's JSON-schema fragment is:

        {
            "description": "<persona-aware stakeholders label>",
            "segment_type": "<segment.type>"
        }

    The description string is sourced from
    :func:`app.policy.persona_defaults.vocabulary_for` so the label
    matches whatever the UI surfaces ("Audience cohorts",
    "Stakeholder groups", "Employee cohorts", etc.). The
    ``segment_type`` echo is informational; backends ignore it.

    Determinism: ``sorted()`` on the type set + ``str`` enum dump make
    the output byte-stable across two calls for the same Scenario.
    """
    vocab = vocabulary_for(scenario.persona, vocabulary_scope(scenario))
    stakeholders_label = vocab["stakeholders"]

    # Sort to lock byte order; dedupe to handle scenarios with two segments
    # of the same type (e.g. two follower_cohort entries).
    type_names = sorted({seg.type for seg in scenario.stakeholders})

    entity_types: Dict[str, Dict[str, Any]] = {
        type_name: {
            "description": stakeholders_label,
            "segment_type": type_name,
        }
        for type_name in type_names
    }

    effective_scope = scenario_policy_scope(scenario)
    # ``Ontology.model_config`` declares ``extra='allow'`` so persona/scope ride
    # through as untyped extras. We construct via ``model_validate`` rather than
    # the typed constructor kwargs because pyright cannot see ``extra='allow'``
    # at call-sites (it only inspects the declared fields).
    payload: Dict[str, Any] = {
        "entity_types": entity_types,
        "persona": scenario.persona.value,
        "scope": effective_scope.value if effective_scope is not None else None,
    }
    return Ontology.model_validate(payload)


__all__ = ["build_policy_ontology"]
