"""Per-(persona, scope) scale, drift, vocabulary, report-section + capability defaults.

Resolver helpers (``scale_defaults_for``, ``drift_defaults_for``, ``vocabulary_for``,
``report_sections_for``, ``capabilities_for``) raise ``KeyError`` on unknown keys.
Product persona splits drift into ``internal_org`` and ``public_policy`` (6 rows);
scale/vocab/sections keep product as one row (5 rows). :func:`resolve_product_drift_scope`
bridges product presets to a canonical scope. ``PERSONA_SEGMENT_TYPES`` lives in
:mod:`app.policy.schema` to avoid a circular import; an assertion at the bottom of this
file pins the two tables key-aligned at import time.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .schema import Persona, PolicySurface


# ---------------------------------------------------------------------------
# Per-persona default scale for live-LLM runs (agents x rounds = product):
#   creator                 30 x 2 = 60
#   video                   40 x 3 = 120
#   product                 50 x 3 = 150
#   policy.internal_org     50 x 4 = 200
#   policy.public_policy    80 x 5 = 400
# ensemble_n=5 is the variance-reduction multiplier applied to every row.
# ---------------------------------------------------------------------------
REAL_SCALE_DEFAULTS: Dict[Tuple[Persona, Optional[PolicySurface]], Dict[str, int]] = {
    (Persona.CREATOR, None): {"agents": 30, "rounds": 2, "ensemble_n": 5},
    (Persona.VIDEO, None): {"agents": 40, "rounds": 3, "ensemble_n": 5},
    (Persona.PRODUCT, None): {"agents": 50, "rounds": 3, "ensemble_n": 5},
    (Persona.POLICY, PolicySurface.INTERNAL_ORG): {
        "agents": 50,
        "rounds": 4,
        "ensemble_n": 5,
    },
    (Persona.POLICY, PolicySurface.PUBLIC_POLICY): {
        "agents": 80,
        "rounds": 5,
        "ensemble_n": 5,
    },
}


# ---------------------------------------------------------------------------
# Mock-mode scale defaults (reduced for fast demo loops):
#   creator                 12 / 2
#   video                   16 / 2
#   product                 20 / 2
#   policy.internal_org     24 / 3
#   policy.public_policy    32 / 3
# ensemble_n=5 across the board.
# ---------------------------------------------------------------------------
MOCK_SCALE_DEFAULTS: Dict[Tuple[Persona, Optional[PolicySurface]], Dict[str, int]] = {
    (Persona.CREATOR, None): {"agents": 12, "rounds": 2, "ensemble_n": 5},
    (Persona.VIDEO, None): {"agents": 16, "rounds": 2, "ensemble_n": 5},
    (Persona.PRODUCT, None): {"agents": 20, "rounds": 2, "ensemble_n": 5},
    (Persona.POLICY, PolicySurface.INTERNAL_ORG): {
        "agents": 24,
        "rounds": 3,
        "ensemble_n": 5,
    },
    (Persona.POLICY, PolicySurface.PUBLIC_POLICY): {
        "agents": 32,
        "rounds": 3,
        "ensemble_n": 5,
    },
}


# ---------------------------------------------------------------------------
# Per-(persona, scope) numeric DriftParams defaults.
#
# Dict keys match the schema field names on :class:`DriftParams` exactly so
# ``DriftParams(**drift_defaults_for(persona, scope))`` constructs without any
# translation step:
#   susceptibility, compromise_mu, epsilon, gain_pos, gain_neg, gain_ratio,
#   delta, citation_key.
#
# Note: this table splits the product persona into TWO rows
# (internal_org vs public_policy) even though the scale and report-section
# tables keep product as a single scope=None row. The drift dynamics
# genuinely differ across the two contexts -- see module docstring.
# ---------------------------------------------------------------------------
PERSONA_DRIFT_DEFAULTS: Dict[Tuple[Persona, Optional[PolicySurface]], Dict[str, Any]] = {
    (Persona.CREATOR, None): {
        "susceptibility": 0.50,
        "compromise_mu": 0.30,
        "epsilon": 0.60,
        "gain_pos": 1.0,
        "gain_neg": 2.25,
        "gain_ratio": 2.25,
        "delta": 0.05,
        "citation_key": "FJ1990 + KT1992",
    },
    (Persona.VIDEO, None): {
        "susceptibility": 0.50,
        "compromise_mu": 0.30,
        "epsilon": 0.55,
        "gain_pos": 1.0,
        "gain_neg": 2.25,
        "gain_ratio": 2.25,
        "delta": 0.05,
        "citation_key": "FJ1990 + KT1992",
    },
    (Persona.PRODUCT, PolicySurface.INTERNAL_ORG): {
        "susceptibility": 0.40,
        "compromise_mu": 0.20,
        "epsilon": 0.35,
        "gain_pos": 1.0,
        "gain_neg": 2.0,
        "gain_ratio": 2.0,
        "delta": 0.04,
        "citation_key": "FJ1990 + Baumeister2001",
    },
    # The source row labels this scope as "public" (not "public_policy");
    # we key it under PolicySurface.PUBLIC_POLICY because PolicySurface is the
    # only public-facing scope enum in the schema. Documented divergence.
    (Persona.PRODUCT, PolicySurface.PUBLIC_POLICY): {
        "susceptibility": 0.45,
        "compromise_mu": 0.25,
        "epsilon": 0.40,
        "gain_pos": 1.0,
        "gain_neg": 2.25,
        "gain_ratio": 2.25,
        "delta": 0.04,
        "citation_key": "FJ1990 + KT1992",
    },
    (Persona.POLICY, PolicySurface.INTERNAL_ORG): {
        "susceptibility": 0.25,
        "compromise_mu": 0.10,
        "epsilon": 0.25,
        "gain_pos": 1.0,
        "gain_neg": 3.0,
        "gain_ratio": 3.0,
        "delta": 0.02,
        "citation_key": "FJ1990 + Soroka2019",
    },
    (Persona.POLICY, PolicySurface.PUBLIC_POLICY): {
        "susceptibility": 0.30,
        "compromise_mu": 0.15,
        "epsilon": 0.30,
        "gain_pos": 1.0,
        "gain_neg": 2.5,
        "gain_ratio": 2.5,
        "delta": 0.02,
        "citation_key": "FJ1990 + Rozin2001",
    },
}


# Round-level drift globals (apply to every persona+scope row).
DRIFT_DW_PAIR_FRACTION: float = 0.3
DRIFT_VALENCE_NEUTRAL_BAND: float = 0.1


# ---------------------------------------------------------------------------
# Derived LLM-preamble drift_rules template strings per (persona, scope).
# ---------------------------------------------------------------------------
PERSONA_DRIFT_RULES: Dict[Tuple[Persona, Optional[PolicySurface]], str] = {
    (Persona.CREATOR, None): (
        "Stance drifts on engagement signals (likes, replies); reinforced by "
        "peer-creator amplification; hardened by mockery; softened by genuine "
        "engagement from creator"
    ),
    (Persona.VIDEO, None): (
        "Stance drifts on retention pattern; reinforced by comment validation; "
        "hardened by perceived bait-and-switch; softened by transparent "
        "acknowledgment"
    ),
    (Persona.PRODUCT, None): (
        "Stance drifts on perceived alignment with prior expectations; "
        "reinforced by community signals; hardened by perceived "
        "bait-and-switch; softened by acknowledgment + roadmap clarity"
    ),
    (Persona.POLICY, PolicySurface.INTERNAL_ORG): (
        "Stance drifts on perceived fairness signals (manager behavior, peer "
        "treatment); reinforced by peer concurrence in same cohort; hardened "
        "by perceived broken promises; softened by perceived management "
        "transparency + voice"
    ),
    (Persona.POLICY, PolicySurface.PUBLIC_POLICY): (
        "Stance drifts on perceived enforcement consistency + perceived elite "
        "acceptance; reinforced by trusted-source endorsement; hardened by "
        "perceived selective enforcement; softened by clear grievance redressal"
    ),
}


# ---------------------------------------------------------------------------
# Vocabulary swap: per-persona UI labels for the uniform schema field names.
# ---------------------------------------------------------------------------
_VOCAB_FIELDS = (
    "seed_text",
    "stress_question",
    "stakeholders",
    "jurisdiction",
    "assumptions",
    "success_criteria",
    "failure_modes_to_watch",
)
PERSONA_VOCABULARY: Dict[Tuple[Persona, Optional[PolicySurface]], Dict[str, str]] = {
    (Persona.CREATOR, None): {
        "seed_text": "Post / thread text",
        "stress_question": "What do you want to stress-test?",
        "stakeholders": "Audience cohorts",
        "jurisdiction": "Platform(s)",
        "assumptions": "Assumptions about your audience",
        "success_criteria": "What would 'this landed well' look like?",
        "failure_modes_to_watch": "What are you afraid might go wrong?",
    },
    (Persona.VIDEO, None): {
        "seed_text": "Script / outline / transcript",
        "stress_question": "What do you want to stress-test?",
        "stakeholders": "Audience cohorts",
        "jurisdiction": "Platform(s)",
        "assumptions": "Assumptions about your viewers",
        "success_criteria": "What would success look like?",
        "failure_modes_to_watch": "What are you afraid might go wrong?",
    },
    (Persona.PRODUCT, None): {
        "seed_text": "Announcement copy",
        "stress_question": "What's the launch risk you care about?",
        "stakeholders": "User segments",
        "jurisdiction": "Market / region",
        "assumptions": "Assumptions about your users / market",
        "success_criteria": "What does adoption look like?",
        "failure_modes_to_watch": "What launch failures worry you?",
    },
    (Persona.POLICY, PolicySurface.INTERNAL_ORG): {
        "seed_text": "Memo / handbook section",
        "stress_question": "What's the internal risk you care about?",
        "stakeholders": "Employee cohorts",
        "jurisdiction": "Organization / region",
        "assumptions": "Assumptions about your org",
        "success_criteria": "What does smooth rollout look like?",
        "failure_modes_to_watch": "What internal failures worry you?",
    },
    (Persona.POLICY, PolicySurface.PUBLIC_POLICY): {
        "seed_text": "Bill / draft regulation",
        "stress_question": "What's the policy-design risk you care about?",
        "stakeholders": "Stakeholder groups",
        "jurisdiction": "Jurisdiction",
        "assumptions": "Assumptions about implementation",
        "success_criteria": "What does successful implementation look like?",
        "failure_modes_to_watch": "What policy failures worry you?",
    },
}


# SSoT invariant -- every PERSONA_VOCABULARY entry must declare EXACTLY the
# keys in _VOCAB_FIELDS. Without this guard, adding a new vocab key to one
# persona block while forgetting the others would silently ship a
# half-populated UI. Fail loud at import.
def _assert_vocab_fields_invariant() -> None:
    """Module-init guard — see comment block above for rationale."""
    expected: set[str] = set(_VOCAB_FIELDS)
    for vkey, vocab in PERSONA_VOCABULARY.items():
        missing = expected - set(vocab.keys())
        extra = set(vocab.keys()) - expected
        assert not missing, (
            f"PERSONA_VOCABULARY[{vkey!r}] missing fields: {sorted(missing)}"
        )
        assert not extra, (
            f"PERSONA_VOCABULARY[{vkey!r}] has unknown fields: {sorted(extra)}"
        )


_assert_vocab_fields_invariant()


# ---------------------------------------------------------------------------
# Persona-specific report section list (titles + claim guidance).
# Section ordering matters and is preserved verbatim per persona+scope.
# ---------------------------------------------------------------------------
PERSONA_REPORT_SECTIONS: Dict[
    Tuple[Persona, Optional[PolicySurface]], List[Dict[str, str]]
] = {
    # creator
    (Persona.CREATOR, None): [
        {"title": "Executive summary",
         "claim_guidance": "3-5 claims, mixed tags, highest-priority risks first"},
        {"title": "Engagement themes",
         "claim_guidance": "What topics/angles agents engaged with most -- "
                           "`simulated_observation` heavy"},
        {"title": "Comment cluster risks",
         "claim_guidance": "Likely comment clusters that form -- `inference` "
                           "+ `simulated_observation`"},
        {"title": "Share triggers",
         "claim_guidance": "What might drive shares -- `inference`"},
        {"title": "Virality vs controversy ratio",
         "claim_guidance": "Estimated balance -- `inference` only; never "
                           "numeric point estimate"},
        {"title": "Drive-by hostile risks",
         "claim_guidance": "Likely external pile-on -- `inference` + "
                           "`requires_validation`"},
        {"title": "Algorithmic cohort signals",
         "claim_guidance": "What an algorithm might amplify -- `inference`"},
    ],
    # video
    (Persona.VIDEO, None): [
        {"title": "Executive summary", "claim_guidance": "3-5 claims"},
        {"title": "Retention drop points",
         "claim_guidance": "Hypothesized retention drop sections in script -- "
                           "`inference`"},
        {"title": "Comment themes by timestamp",
         "claim_guidance": "Cluster of comments per minute range -- "
                           "`simulated_observation`"},
        {"title": "Subscriber gain/loss signals",
         "claim_guidance": "Directional signals -- `inference`, never numeric"},
        {"title": "Controversy clusters",
         "claim_guidance": "Risky framings + likely backlash -- `inference` + "
                           "`requires_validation`"},
        {"title": "Skeptic-segment objections",
         "claim_guidance": "What the skeptic segment said -- "
                           "`simulated_observation`"},
    ],
    # product
    (Persona.PRODUCT, None): [
        {"title": "Executive summary", "claim_guidance": "3-5 claims"},
        {"title": "Adoption signals",
         "claim_guidance": "What looks positive -- `simulated_observation` "
                           "+ `inference`"},
        {"title": "Churn risks",
         "claim_guidance": "What might churn users -- `inference` + "
                           "`requires_validation`"},
        {"title": "Feature-request clusters",
         "claim_guidance": "What users asked for -- `simulated_observation`"},
        {"title": "Narrative drift",
         "claim_guidance": "How the story shifted in agent posts -- "
                           "`inference`"},
        {"title": "Competitor talking points",
         "claim_guidance": "Likely competitor responses -- `inference` + "
                           "`assumption`"},
        {"title": "Press posture",
         "claim_guidance": "Likely press framing -- `inference`"},
    ],
    # policy.internal_org
    (Persona.POLICY, PolicySurface.INTERNAL_ORG): [
        {"title": "Executive summary", "claim_guidance": "3-5 claims"},
        {"title": "Manager friction & comms gaps",
         "claim_guidance": "Specific friction surfaces -- `inference` + "
                           "`simulated_observation`"},
        {"title": "Attrition signals",
         "claim_guidance": "Which cohorts might leave -- `inference` + "
                           "`requires_validation`"},
        {"title": "Glassdoor risk",
         "claim_guidance": "Likely narrative on external channels -- "
                           "`inference` + `assumption`"},
        {"title": "DEI / equity impact",
         "claim_guidance": "Level / location / job-family fairness issues -- "
                           "`inference` + `requires_validation`"},
        {"title": "Comms-gap risks",
         "claim_guidance": "Where comms might fail -- `inference`"},
        {"title": "Coalition dynamics",
         "claim_guidance": "Likely informal coalitions forming -- `inference`"},
        {"title": "Suggested phasing",
         "claim_guidance": "Mitigation ideas -- `inference`"},
        {"title": "Evidence gaps",
         "claim_guidance": "What's unknown -- `requires_validation`"},
        {"title": "Field validation questions",
         "claim_guidance": "Survey items to validate before rollout"},
    ],
    # policy.public_policy
    (Persona.POLICY, PolicySurface.PUBLIC_POLICY): [
        {"title": "Executive summary", "claim_guidance": "3-5 claims"},
        {"title": "Stakeholder reaction map",
         "claim_guidance": "Per-segment stance summary -- "
                           "`simulated_observation` + `inference`"},
        {"title": "Support / opposition / swing groups",
         "claim_guidance": "Categorization -- `inference`"},
        {"title": "Implementation bottlenecks",
         "claim_guidance": "Specific operational frictions -- `inference` + "
                           "`assumption`"},
        {"title": "Administrative capacity risks",
         "claim_guidance": "Where capacity might be insufficient -- "
                           "`inference` + `requires_validation`"},
        {"title": "Equity / access risks",
         "claim_guidance": "Distributional concerns -- `inference` + "
                           "`requires_validation`"},
        {"title": "Perverse incentives & loopholes",
         "claim_guidance": "Specific gaming opportunities -- `inference`"},
        {"title": "Misinformation / narrative risks",
         "claim_guidance": "Likely false narratives -- `inference` + "
                           "`assumption`"},
        {"title": "Coalition dynamics",
         "claim_guidance": "Likely formal opposition coalitions -- `inference`"},
        {"title": "Suggested policy modifications",
         "claim_guidance": "Mitigation ideas -- `inference`"},
        {"title": "Evidence gaps",
         "claim_guidance": "What's unknown -- `requires_validation`"},
        {"title": "Field validation questions",
         "claim_guidance": "Specific field studies to run before rollout"},
    ],
}


# ---------------------------------------------------------------------------
# Capability hints surfaced in the /personas response. These are short
# string labels for the catalog endpoint; the canonical per-persona
# capability description lives in :data:`PERSONA_REPORT_SECTIONS`.
# ---------------------------------------------------------------------------
PERSONA_CAPABILITIES: Dict[Persona, List[str]] = {
    Persona.CREATOR: ["audience_simulation", "post_drafting", "stress_test"],
    Persona.VIDEO: ["audience_simulation", "watch_decision", "stress_test"],
    Persona.PRODUCT: ["adoption_modeling", "churn_signal", "stress_test"],
    Persona.POLICY: [
        "stakeholder_simulation",
        "compliance_drift",
        "counterfactual_variant",
        "stress_test",
    ],
}


# ---------------------------------------------------------------------------
# Human-readable persona label + the lead cohort question surfaced by the
# persona picker. These live here so the wire payload from GET /personas is
# the single source of truth -- the frontend keeps a defensive hardcoded
# fallback dict, but the canonical text is whatever this table emits.
# ---------------------------------------------------------------------------
PERSONA_LABELS: Dict[Persona, str] = {
    Persona.CREATOR: "Creator",
    Persona.VIDEO: "Video",
    Persona.PRODUCT: "Product",
    Persona.POLICY: "Policy",
}


PERSONA_COHORT_QUESTIONS: Dict[Persona, str] = {
    Persona.CREATOR: "How will my post land with my audience?",
    Persona.VIDEO: "Where will viewers drop off / cluster?",
    Persona.PRODUCT: "How will the announcement shift adoption?",
    Persona.POLICY: (
        "Who resists, where does it break, what perverse incentives form?"
    ),
}


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _normalize_key(
    persona: Persona, scope: Optional[PolicySurface]
) -> Tuple[Persona, Optional[PolicySurface]]:
    """Force scope to None for non-policy personas; pass through otherwise.

    The scale / vocab / report tables key non-policy rows by ``scope=None``,
    matching ``PERSONA_SEGMENT_TYPES`` in ``app.policy.schema``. This helper
    makes the lookup robust against callers that pass a stray scope for a
    creator/video/product persona.
    """
    if persona != Persona.POLICY:
        return (persona, None)
    return (persona, scope)


def scale_defaults_for(
    persona: Persona,
    scope: Optional[PolicySurface],
    mode: str,
) -> Dict[str, int]:
    """Resolve the scale block (agents, rounds, ensemble_n) for a persona.

    Parameters
    ----------
    persona
        Top-level persona discriminator.
    scope
        Required when ``persona == Persona.POLICY``; ignored otherwise.
    mode
        One of ``"mock"``, ``"graphiti"``, or ``"experimental_cloud"``
        (matches ``Config.ZEP_MODE``). The deprecated ``"real"`` spelling
        is accepted as an alias for ``"experimental_cloud"`` for one
        release; ``"graphiti"`` shares the cloud-tier scale row because
        the LLM-extraction cost profile matches.

    Raises
    ------
    KeyError
        On unknown persona/scope combination or unknown mode. The error is
        deliberately loud so call-sites that thread an unexpected combination
        through this function surface immediately rather than silently
        substituting a default.
    """
    normalized = (mode or "").strip().lower()
    # REAL_SCALE_DEFAULTS pre-dates the rename; both graphiti and
    # experimental_cloud share its row because both incur per-episode
    # LLM extraction cost (mock is the only zero-cost lane).
    _LLM_BACKED_MODES = {"graphiti", "experimental_cloud", "real"}
    if normalized == "mock":
        table = MOCK_SCALE_DEFAULTS
    elif normalized in _LLM_BACKED_MODES:
        table = REAL_SCALE_DEFAULTS
    else:
        raise KeyError(
            f"Unknown ZEP_MODE={mode!r}; scale defaults only defined for "
            "'mock', 'graphiti', and 'experimental_cloud'."
        )
    key = _normalize_key(persona, scope)
    if key not in table:
        raise KeyError(
            f"No scale defaults for persona={persona!r} scope={scope!r} "
            f"(mode={normalized!r})."
        )
    return dict(table[key])


# ---------------------------------------------------------------------------
# Product persona surface -> PolicySurface mapping.
#
# Product drift defaults split into TWO scope-keyed rows (INTERNAL_ORG,
# PUBLIC_POLICY), but :class:`Scenario` forbids ``scope`` on the product
# persona. Product presets therefore declare a free-form ``scope`` string
# (e.g. ``"b2b_saas"``, ``"consumer"``, ``"internal_org"``) which
# :func:`app.policy.preset_loader._resolve_scope` stashes on
# ``scenario.preset_overrides["domain_context"]["surface"]`` for non-policy
# personas.
#
# This helper bridges that gap: at /prepare time, we read the stashed
# ``surface`` and map it to a canonical :class:`PolicySurface` so
# :func:`drift_defaults_for` can resolve the correct product drift row.
#
# Mapping intent
# --------------
# Internal-org-flavoured surfaces → :attr:`PolicySurface.INTERNAL_ORG`:
#   - ``b2b_saas``           (paying-business / captive-account dynamics)
#   - ``internal_org``       (the canonical word; product internal-tools)
#   - ``internal_reorg``     (reorg-flavoured product preset)
#   - ``employee_facing``    (HR-tech / workforce-facing product)
#
# Public / consumer-facing surfaces → :attr:`PolicySurface.PUBLIC_POLICY`:
#   - ``consumer``           (general consumer product)
#   - ``public``             (public-facing launch)
#   - ``social_media``       (social platform feature launch)
#   - ``b2c``                (business-to-consumer)
#   - ``market``             (broad market launch)
#
# Default for unknown / None surface → :attr:`PolicySurface.PUBLIC_POLICY`.
# Rationale: a consumer-facing product launch has broader blast radius than an
# internal rollout; defaulting to PUBLIC keeps drift parameters on the more
# cautious side rather than understating risk for an unrecognised surface.
#
# Extend here if new product surface vocabulary is added in preset JSONs.
# ---------------------------------------------------------------------------
_PRODUCT_INTERNAL_SURFACES: frozenset[str] = frozenset({
    "b2b_saas",
    "internal_org",
    "internal_reorg",
    "employee_facing",
})
_PRODUCT_PUBLIC_SURFACES: frozenset[str] = frozenset({
    "consumer",
    "public",
    "social_media",
    "b2c",
    "market",
})


def resolve_product_drift_scope(surface: Optional[str]) -> PolicySurface:
    """Map a product preset's free-form ``surface`` tag to a canonical scope.

    See the module-level mapping table above for the recognised vocabulary.
    Unknown surface OR ``None`` defaults to :attr:`PolicySurface.PUBLIC_POLICY`
    (broader-impact assumption — see rationale comment).
    """
    if surface is None:
        return PolicySurface.PUBLIC_POLICY
    key = surface.strip().lower()
    if key in _PRODUCT_INTERNAL_SURFACES:
        return PolicySurface.INTERNAL_ORG
    if key in _PRODUCT_PUBLIC_SURFACES:
        return PolicySurface.PUBLIC_POLICY
    return PolicySurface.PUBLIC_POLICY


def drift_defaults_for(
    persona: Persona, scope: Optional[PolicySurface]
) -> Dict[str, Any]:
    """Resolve the numeric DriftParams row for a persona+scope.

    Note: unlike the scale tables, :data:`PERSONA_DRIFT_DEFAULTS` splits the
    product persona by scope. Callers MUST pass an explicit scope for
    ``Persona.PRODUCT``; passing ``None`` raises ``KeyError``.
    """
    # For policy: scope is required.
    # For creator/video: scope is forced to None.
    # For product: respect the caller's scope verbatim (table has both rows).
    if persona in (Persona.CREATOR, Persona.VIDEO):
        key: Tuple[Persona, Optional[PolicySurface]] = (persona, None)
    else:
        key = (persona, scope)
    if key not in PERSONA_DRIFT_DEFAULTS:
        raise KeyError(
            f"No drift defaults for persona={persona!r} scope={scope!r}; "
            "6 valid rows: creator, video, product/internal_org, "
            "product/public_policy, policy/internal_org, policy/public_policy."
        )
    return dict(PERSONA_DRIFT_DEFAULTS[key])


def drift_rules_for(
    persona: Persona, scope: Optional[PolicySurface]
) -> str:
    """Return the free-text drift_rules template string for a persona+scope."""
    key = _normalize_key(persona, scope)
    if key not in PERSONA_DRIFT_RULES:
        raise KeyError(
            f"No drift_rules template for persona={persona!r} scope={scope!r}."
        )
    return PERSONA_DRIFT_RULES[key]


def vocabulary_for(
    persona: Persona, scope: Optional[PolicySurface]
) -> Dict[str, str]:
    """Return the UI label mapping for a persona+scope."""
    key = _normalize_key(persona, scope)
    if key not in PERSONA_VOCABULARY:
        raise KeyError(
            f"No vocabulary for persona={persona!r} scope={scope!r}."
        )
    return dict(PERSONA_VOCABULARY[key])


def report_sections_for(
    persona: Persona, scope: Optional[PolicySurface]
) -> List[Dict[str, str]]:
    """Return the ordered report section list for a persona+scope."""
    key = _normalize_key(persona, scope)
    if key not in PERSONA_REPORT_SECTIONS:
        raise KeyError(
            f"No report sections for persona={persona!r} scope={scope!r}."
        )
    return [dict(section) for section in PERSONA_REPORT_SECTIONS[key]]


def capabilities_for(persona: Persona) -> List[str]:
    """Return the soft capability hint list for a persona."""
    if persona not in PERSONA_CAPABILITIES:
        raise KeyError(f"No capabilities entry for persona={persona!r}.")
    return list(PERSONA_CAPABILITIES[persona])


def label_for(persona: Persona) -> str:
    """Return the human-readable persona label (e.g. ``"Creator"``)."""
    if persona not in PERSONA_LABELS:
        raise KeyError(f"No label entry for persona={persona!r}.")
    return PERSONA_LABELS[persona]


def cohort_question_for(persona: Persona) -> str:
    """Return the lead cohort question surfaced by the persona picker."""
    if persona not in PERSONA_COHORT_QUESTIONS:
        raise KeyError(f"No cohort_question entry for persona={persona!r}.")
    return PERSONA_COHORT_QUESTIONS[persona]


# ---------------------------------------------------------------------------
# SSoT invariant -- PERSONA_SEGMENT_TYPES is authoritative in
# :mod:`app.policy.schema` (validator-coupling forces the split, see module
# docstring). Assert every (persona, scope) key in PERSONA_VOCABULARY is also
# declared in schema.PERSONA_SEGMENT_TYPES -- adding a vocab/scale row
# without the matching segment-types row would let Scenario validation
# silently accept segment types that have no canonical allowlist. Fail loud
# at import.
# ---------------------------------------------------------------------------
from .schema import PERSONA_SEGMENT_TYPES as _PERSONA_SEGMENT_TYPES_FROM_SCHEMA


def _assert_segment_types_parity() -> None:
    """Module-init guard — see comment block above for rationale."""
    for key in PERSONA_VOCABULARY.keys():
        assert key in _PERSONA_SEGMENT_TYPES_FROM_SCHEMA, (
            f"PERSONA_VOCABULARY has key {key!r} but schema.PERSONA_SEGMENT_TYPES "
            f"does not — segment-types/vocab tables must stay key-aligned."
        )


_assert_segment_types_parity()


__all__ = [
    "MOCK_SCALE_DEFAULTS",
    "REAL_SCALE_DEFAULTS",
    "PERSONA_DRIFT_DEFAULTS",
    "PERSONA_DRIFT_RULES",
    "PERSONA_VOCABULARY",
    "PERSONA_REPORT_SECTIONS",
    "PERSONA_CAPABILITIES",
    "PERSONA_LABELS",
    "PERSONA_COHORT_QUESTIONS",
    "DRIFT_DW_PAIR_FRACTION",
    "DRIFT_VALENCE_NEUTRAL_BAND",
    "scale_defaults_for",
    "drift_defaults_for",
    "resolve_product_drift_scope",
    "drift_rules_for",
    "vocabulary_for",
    "report_sections_for",
    "capabilities_for",
    "label_for",
    "cohort_question_for",
]
