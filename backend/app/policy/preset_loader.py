"""Load and lower scenario presets into strict Scenarios.

Public surface: :func:`load_preset` (single JSON -> ``ScenarioPreset``),
:func:`load_all_presets` (directory discovery, ``lru_cache``-d -- call
``cache_clear`` in tests), :func:`apply_preset` (pure, deterministic
lowering to a strict :class:`Scenario`).

Lowering rules: rich preset knobs (OCEAN, parasocial, trust_baseline,
``drift_sensitivity_preset``, ``domain_context``) are preserved while the
body validates through the canonical strict schema;
``drift_horizon_rounds`` is promoted to the Scenario root;
``drift_sensitivity_preset`` also lands under
``preset_overrides['domain_context']`` so the runtime drift module can
multiply per-segment :class:`DriftParams`; free-form scope tags outside
``{policy} x {internal_org, public_policy}`` are stashed under
``domain_context['surface']``.

No I/O, no env reads; ``allow_expensive`` is plumbed via keyword and
forwarded through :class:`ValidationInfo.context` (the schema itself
never touches the env).
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from .preset_schema import (
    PresetSegmentOverride,
    ScenarioPreset,
)
from .schema import (
    PERSONA_SEGMENT_TYPES,
    Persona,
    PolicySurface,
    Scenario,
)
from .scenario_loader import ScenarioValidationError, _wrap_validation_error


logger = logging.getLogger(__name__)


# Canonical on-disk location for the 10 preset JSONs.
# Resolved as ``<repo>/scenarios/presets`` (sibling of ``backend/``) so the
# preset files live with the documentation set rather than buried inside the
# Python package. The path is computed by walking up from this module:
#   backend/app/policy/preset_loader.py  →  parents[3] == RawFish/  →  /scenarios/presets
PRESETS_DIR: Path = Path(__file__).resolve().parents[3] / "scenarios" / "presets"


# ---------------------------------------------------------------------------
# Loader (single file + directory)
# ---------------------------------------------------------------------------


def load_preset(path: str | Path) -> ScenarioPreset:
    """Parse a single preset JSON file into a :class:`ScenarioPreset`.

    Raises
    ------
    FileNotFoundError
        When ``path`` does not exist on disk.
    ScenarioValidationError
        When the file fails :class:`ScenarioPreset` validation. The error
        message is prefixed with ``preset@<path>`` so the location surfaces
        in tracebacks.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Preset file not found: {p}")

    with p.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    try:
        return ScenarioPreset.model_validate(data)
    except ValidationError as e:
        wrapped = _wrap_validation_error(e)
        # Prefix every formatted error with the preset path so callers can
        # locate the offending file without re-parsing.
        prefixed = [f"preset@{p}: {line}" for line in wrapped.errors]
        raise ScenarioValidationError(
            f"Preset validation failed for {p}:\n  - " + "\n  - ".join(prefixed),
            prefixed,
            wrapped.raw_errors,
        ) from e


@lru_cache(maxsize=1)
def load_all_presets() -> Dict[str, ScenarioPreset]:
    """Discover every ``*.json`` preset under :data:`PRESETS_DIR`.

    Returns a ``{preset_id: ScenarioPreset}`` mapping. Cached for the lifetime
    of the process; call ``load_all_presets.cache_clear()`` in tests that
    write fresh preset files into a different location and need rediscovery.

    A duplicate ``preset_id`` across files raises :class:`ValueError`
    immediately — collisions would otherwise silently shadow whichever file
    was loaded last.
    """
    if not PRESETS_DIR.exists():
        logger.debug("PRESETS_DIR=%s missing; returning empty preset map", PRESETS_DIR)
        return {}

    result: Dict[str, ScenarioPreset] = {}
    for path in sorted(PRESETS_DIR.glob("**/*.json")):
        preset = load_preset(path)
        if preset.preset_id in result:
            raise ValueError(
                f"Duplicate preset_id={preset.preset_id!r} found at {path}; "
                "every preset JSON must declare a unique preset_id."
            )
        result[preset.preset_id] = preset
    return result


# ---------------------------------------------------------------------------
# Internal helpers (private; not part of the public surface)
# ---------------------------------------------------------------------------


def _stress_question_for(persona: str, topic: str) -> str:
    """Deterministic derivation of the stress_question prompt.

    Format: ``"What may go wrong for {persona} and which cohorts will resist?"``.
    The persona slug is interpolated as-is so it reads naturally
    (``creator``, ``video``, ``product``, ``policy``); the topic is not
    substituted (the topic surfaces in ``seed_text``).
    """
    return f"What may go wrong for {persona} and which cohorts will resist?"


def _resolve_scope(
    persona: str, scope: Optional[str], domain_context: Dict[str, Any]
) -> tuple[Optional[PolicySurface], Dict[str, Any]]:
    """Promote scope to :class:`PolicySurface` only for the canonical policy pair.

    Mutates a fresh copy of ``domain_context`` — never mutates the caller's
    dict — to add ``surface`` for non-policy or non-canonical scope tags.
    Returns the ``(PolicySurface|None, updated_domain_context)`` pair.
    """
    ctx = dict(domain_context)
    if persona == Persona.POLICY.value and scope in {
        PolicySurface.INTERNAL_ORG.value,
        PolicySurface.PUBLIC_POLICY.value,
    }:
        return (PolicySurface(scope), ctx)
    if scope:
        # Non-canonical scope: stash on domain_context['surface'] so the
        # runtime drift module and dashboards can read it without
        # re-parsing the preset.
        ctx.setdefault("surface", scope)
    return (None, ctx)


def _default_segment_type(
    persona: str, policy_scope: Optional[PolicySurface]
) -> Optional[str]:
    """Pick a deterministic default ``type`` for a segment when author omits it.

    Strategy: first alphabetically-sorted entry from
    :data:`PERSONA_SEGMENT_TYPES` for the resolved (persona, scope) key.
    Returns ``None`` if no allowlist row exists (the schema's
    ``_validate_segment_types`` will then raise loud).
    """
    persona_enum = Persona(persona)
    key = (persona_enum, policy_scope if persona_enum == Persona.POLICY else None)
    allowed = PERSONA_SEGMENT_TYPES.get(key)
    if not allowed:
        return None
    return sorted(allowed)[0]


def _segment_to_stakeholder(
    seg: PresetSegmentOverride,
    *,
    persona: str,
    policy_scope: Optional[PolicySurface],
    known_provenance_ids: set[str],
) -> Dict[str, Any]:
    """Lower a :class:`PresetSegmentOverride` into a raw stakeholder dict.

    Field renames: ``id`` -> ``segment_id``, ``label`` -> ``name``.
    Inference: ``type`` defaults to the first entry of
    :data:`PERSONA_SEGMENT_TYPES` for the resolved (persona, scope). Priors
    get synthesised ``provenance_id`` strings of the form
    ``prov_<segment_id>_<dimension>`` when authors leave them implicit;
    the synthesised id MUST already exist in ``known_provenance_ids`` for
    the schema's provenance reference contract — callers are expected to
    pre-populate provenance entries OR the loader synthesises the id and
    leaves resolution to :class:`AudienceSegmentPriors` (the schema only
    enforces a pattern match, not cross-reference resolution).
    """
    out: Dict[str, Any] = {
        "segment_id": seg.id,
        "name": seg.label or seg.id,
    }

    if seg.type:
        out["type"] = seg.type
    else:
        inferred = _default_segment_type(persona, policy_scope)
        if inferred is not None:
            out["type"] = inferred

    # Optional list fields — only emit when the author provided them, so the
    # schema's ``default_factory=list`` semantics survive untouched.
    for opt in (
        "interests",
        "incentives",
        "constraints",
        "likely_support_reasons",
        "likely_opposition_reasons",
        "information_channels",
    ):
        v = getattr(seg, opt)
        if v is not None:
            out[opt] = v

    # Five locked 1..5 ints -- REQUIRED by the schema. If the
    # author omits any, we forward sane neutrals (3) so the resulting
    # Scenario validates; preset JSONs almost always set these explicitly.
    out["influence_level"] = seg.influence_level if seg.influence_level is not None else 3
    out["vulnerability_level"] = (
        seg.vulnerability_level if seg.vulnerability_level is not None else 3
    )
    out["compliance_propensity"] = (
        seg.compliance_propensity if seg.compliance_propensity is not None else 3
    )
    out["trust_level"] = seg.trust_level if seg.trust_level is not None else 3
    out["mobilization_capacity"] = (
        seg.mobilization_capacity if seg.mobilization_capacity is not None else 3
    )

    if seg.drift_rules:
        out["drift_rules"] = seg.drift_rules
    if seg.drift_params:
        out["drift_params"] = dict(seg.drift_params)
    if seg.traits:
        # Forward to TraitMap.model_validate via Scenario.model_validate; the
        # schema will raise if any sub-key is malformed.
        out["traits"] = dict(seg.traits)
    if seg.trait_schema_ref:
        out["trait_schema_ref"] = seg.trait_schema_ref

    # Priors: author shape -> AudienceSegmentPriors shape. We synthesise
    # provenance_id strings when the author omits them; the synthesised id
    # follows the schema's `prov_` pattern so PriorDistribution accepts it.
    if seg.priors or seg.audience_priors:
        bundle: Dict[str, Any]
        if seg.audience_priors:
            bundle = dict(seg.audience_priors)
        else:
            bundle = {}
        if seg.priors:
            priors_out: List[Dict[str, Any]] = []
            for prior in seg.priors:
                prior_copy = dict(prior)
                if "provenance_id" not in prior_copy or not prior_copy["provenance_id"]:
                    dim = prior_copy.get("dimension", "unknown")
                    synth = f"prov_{seg.id}_{dim}"
                    prior_copy["provenance_id"] = synth
                # Cross-link enforcement: a prior's provenance_id MUST
                # resolve to an entry in
                # ``preset_overrides.provenance[]``. Without this, an author
                # can typo a provenance_id and the preset silently lowers to
                # a Scenario whose priors point at non-existent provenance.
                pid = prior_copy["provenance_id"]
                if pid not in known_provenance_ids:
                    raise ScenarioValidationError(
                        f"segment {seg.id!r}: prior.provenance_id={pid!r} "
                        f"has no matching provenance entry in "
                        f"preset_overrides.provenance (known: "
                        f"{sorted(known_provenance_ids)})",
                        [f"segment[{seg.id}].priors.provenance_id={pid}"],
                        [],
                    )
                priors_out.append(prior_copy)
            bundle["priors"] = priors_out
        out["audience_priors"] = bundle

    # Falsifier contract.
    out["what_changes_their_mind"] = list(seg.what_changes_their_mind or ["TBD"])
    if seg.notes:
        out["notes"] = seg.notes

    return out


def _merge_run_controls(
    base: Dict[str, Any], overrides: Dict[str, Any]
) -> Dict[str, Any]:
    """Shallow merge of run-controls dicts, with override taking precedence.

    Only keys that the override explicitly sets (non-None) win; this is the
    canonical "preset overrides defaults, defaults survive when override
    omits the key" semantics used everywhere in this loader.
    """
    merged = dict(base)
    for k, v in overrides.items():
        if v is not None:
            merged[k] = v
    return merged


# ---------------------------------------------------------------------------
# Public apply_preset
# ---------------------------------------------------------------------------


def apply_preset(
    preset: ScenarioPreset,
    base: Optional[Scenario] = None,
    *,
    allow_expensive: bool = False,
) -> Scenario:
    """Lower a :class:`ScenarioPreset` into a strict :class:`Scenario`.

    Steps:

    1. Build scaffold dict; map persona; only emit :class:`PolicySurface`
       when ``persona == 'policy'`` AND ``scope`` is in
       {``internal_org``, ``public_policy``}. Else stash on
       ``domain_context['surface']``.
    2. Derive ``title`` / ``summary`` / ``seed_text`` / ``stress_question``
       from ``topic`` deterministically (see :func:`_stress_question_for`);
       author-pinned values win over derived ones.
    3. Lower each :class:`PresetSegmentOverride` to a stakeholder dict
       (rename id/label, infer type, copy traits/priors).
    4. Promote :class:`PresetRunOverrides` into ``RunControls`` fields.
    5. Copy ``drift_horizon_rounds`` / ``trust_baseline`` /
       ``drift_sensitivity_preset`` / ``expected_risk_tags`` to the
       Scenario root.
    6. Stash the provenance list under
       ``preset_overrides['provenance']`` (sidecar -- a future typed
       RunMetadata bag will take over).
    7. Final ``Scenario.model_validate(merged, context={'allow_expensive': ...})``
       -- wrapped in :class:`ScenarioValidationError` with the preset_id
       prefixed on every loc.

    ``base`` (optional): when provided, ``base.model_dump(mode='json')``
    seeds the scaffold; preset values override base values key-by-key
    (shallow merge, except for ``audience_segments`` which is fully
    replaced when the preset declares any segments).
    """
    # Step 1 — scaffold + scope resolution.
    if base is not None:
        merged: Dict[str, Any] = base.model_dump(mode="json", by_alias=True)
    else:
        merged = {}

    merged["persona"] = preset.persona

    body = preset.preset_overrides.scenario
    domain_context = dict(body.domain_context or {})
    policy_scope, domain_context = _resolve_scope(
        preset.persona, preset.scope, domain_context
    )
    if policy_scope is not None:
        merged["scope"] = policy_scope.value
    else:
        # Non-policy or non-canonical scope: ensure no stale scope leaks
        # through from the base scenario.
        merged.pop("scope", None)

    # Step 2 — topic / title / summary / seed_text / stress_question.
    topic = body.topic
    title = body.title or topic[:80]
    summary = body.summary or topic
    seed_text = body.seed_text or topic
    stress_question = body.stress_question or _stress_question_for(
        preset.persona, topic
    )
    merged["title"] = title
    merged["summary"] = summary
    merged["seed_text"] = seed_text
    merged["stress_question"] = stress_question
    if body.jurisdiction is not None:
        merged["jurisdiction"] = body.jurisdiction

    # Step 3 — audience_segments -> stakeholders.
    # Pre-compute the known provenance ids so segment priors can
    # cross-reference them. Used as a soft set: the schema enforces
    # pattern match, not full cross-reference resolution.
    known_provenance_ids = {p.id for p in preset.preset_overrides.provenance}
    if body.audience_segments:
        merged["stakeholders"] = [
            _segment_to_stakeholder(
                seg,
                persona=preset.persona,
                policy_scope=policy_scope,
                known_provenance_ids=known_provenance_ids,
            )
            for seg in body.audience_segments
        ]

    # Optional schema fields the author can pin.
    if body.clauses:
        merged["clauses"] = list(body.clauses)
    if body.assumptions is not None:
        merged["assumptions"] = list(body.assumptions)
    if body.success_criteria is not None:
        merged["success_criteria"] = list(body.success_criteria)
    if body.failure_modes_to_watch is not None:
        merged["failure_modes_to_watch"] = list(body.failure_modes_to_watch)
    if body.variants:
        merged["variants"] = list(body.variants)

    # Step 4 — RunControls overrides.
    run_overrides = body.run.model_dump(exclude_none=True)
    base_run = merged.get("run", {}) if isinstance(merged.get("run"), dict) else {}
    merged_run = _merge_run_controls(base_run, run_overrides)
    if merged_run:
        merged["run"] = merged_run

    # Step 5 — Scenario root fields.
    if body.drift_horizon_rounds is not None:
        merged["drift_horizon_rounds"] = body.drift_horizon_rounds
    if body.trust_baseline is not None:
        merged["trust_baseline"] = body.trust_baseline
    if body.drift_sensitivity_preset is not None:
        merged["drift_sensitivity_preset"] = body.drift_sensitivity_preset
        # Also park on domain_context for the runtime drift module.
        domain_context.setdefault(
            "drift_sensitivity_preset", body.drift_sensitivity_preset
        )

    # ExpectedRiskTag enums coerce to their string ``.value`` so the lowered
    # Scenario carries plain strings (it does not import the enum).
    merged["expected_risk_tags"] = [
        t.value if hasattr(t, "value") else str(t)
        for t in preset.expected_risk_tags
    ]

    # Step 6 — preset metadata sidecar.
    preset_overrides_sidecar: Dict[str, Any] = (
        dict(merged.get("preset_overrides", {}))
        if isinstance(merged.get("preset_overrides"), dict)
        else {}
    )
    if preset.preset_overrides.provenance:
        preset_overrides_sidecar["provenance"] = [
            p.model_dump(mode="json") for p in preset.preset_overrides.provenance
        ]
    if domain_context:
        preset_overrides_sidecar["domain_context"] = domain_context
    if preset.region:
        preset_overrides_sidecar["region"] = preset.region
    if preset.description:
        preset_overrides_sidecar["description"] = preset.description
    if preset_overrides_sidecar:
        merged["preset_overrides"] = preset_overrides_sidecar

    merged["preset_id"] = preset.preset_id

    # Step 7 — final strict validation with allow_expensive context.
    ctx = {"allow_expensive": bool(allow_expensive)}
    try:
        return Scenario.model_validate(merged, context=ctx)
    except ValidationError as e:
        wrapped = _wrap_validation_error(e)
        prefixed = [
            f"preset[{preset.preset_id}]: {line}" for line in wrapped.errors
        ]
        raise ScenarioValidationError(
            f"apply_preset failed for preset_id={preset.preset_id!r}:\n  - "
            + "\n  - ".join(prefixed),
            prefixed,
            wrapped.raw_errors,
        ) from e


__all__ = [
    "PRESETS_DIR",
    "load_preset",
    "load_all_presets",
    "apply_preset",
]
