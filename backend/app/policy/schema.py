"""Scenario schema (Pydantic v2).

Defines :class:`Scenario` plus its nested models (AudienceSegment,
TraitMap, Priors, RunControls, Variant). Invariants enforced at
validation time:

* ``what_changes_their_mind`` is non-empty on every AudienceSegment
  (falsifier contract).
* ``persona`` is a discriminator: ``scope`` is required iff
  ``persona='policy'``; the allowed
  ``(persona, scope) -> AudienceSegment.type`` set lives in
  :data:`PERSONA_SEGMENT_TYPES` below.
* The five 1..5 propensity ints are ``strict=True`` per-field (no int
  <-> float coercion).
* Legacy aliases ``policy_text`` / ``policy_question`` parse, but
  canonical dump emits ``seed_text`` / ``stress_question``.
* ``drift_horizon_rounds`` is canonical at the Scenario root; a
  ``mode='before'`` lift accepts older persisted
  ``run.drift_horizon_rounds`` inputs.
* RFC 6902 patch ops on ``Variant.diff`` are restricted to
  ``add|remove|replace`` against a path allowlist.
* Cost guard:
  ``ensemble_n * len(stakeholders) * drift_horizon_rounds <= SCALE_CAP_POC``,
  relaxed to ``SCALE_CAP_LARGE`` when
  ``ValidationInfo.context['allow_expensive']`` is true. The schema
  itself never reads env vars; callers resolve and inject the flag.

All models use ``ConfigDict(populate_by_name=True, extra='forbid')``.
"""

from __future__ import annotations

import re
import warnings
from collections import defaultdict
from enum import Enum
from typing import (
    Annotated,
    Any,
    ClassVar,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    get_args,
)

from datetime import datetime

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from .hypothesis_tags import (
    CANONICAL_EXPECTED_RISK_TAGS,
    CANONICAL_HYPOTHESIS_TAGS,
    HypothesisTag,
)

# Platform-level methodological-guardrail taxonomies. The policy lane
# keeps its own richer Pydantic models (Claim / EvidenceRef /
# DriverHint / ValidityFlag below) but every taxonomy-bearing field on
# those models is checked at import time to live in lockstep with the
# platform frozensets, so the same hypothesis-tag / validity-flag /
# severity / evidence-ref allowlists govern both the policy lane and
# any other lane, including creator. Adding a value on one side without updating the other
# trips the lockstep assertion at module load -- the cross-lane
# contract cannot drift silently.
from ..simulation_platform.guardrails.taxonomy import (  # noqa: E402
    EVIDENCE_REF_KINDS as _PLATFORM_EVIDENCE_REF_KINDS,
    HYPOTHESIS_TAGS as _PLATFORM_HYPOTHESIS_TAGS,
    SEVERITY_VARIANTS as _PLATFORM_SEVERITY_VARIANTS,
    VALIDITY_FLAG_KINDS as _PLATFORM_VALIDITY_FLAG_KINDS,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Persona(str, Enum):
    """Top-level scenario persona discriminator."""

    CREATOR = "creator"
    VIDEO = "video"
    PRODUCT = "product"
    POLICY = "policy"


class PolicySurface(str, Enum):
    """Sub-discriminator that applies only when persona=policy."""

    INTERNAL_ORG = "internal_org"
    PUBLIC_POLICY = "public_policy"


class VariantKind(str, Enum):
    """Variant taxonomy."""

    BASELINE = "baseline"
    COUNTERFACTUAL = "counterfactual"
    SENSITIVITY = "sensitivity"


# ---------------------------------------------------------------------------
# Persona-aware segment type allowlist
# ---------------------------------------------------------------------------


# Keyed by ``(persona, scope)``. ``scope`` is ``None`` for non-policy personas.
PERSONA_SEGMENT_TYPES: Dict[Tuple[Persona, Optional[PolicySurface]], frozenset] = {
    (Persona.CREATOR, None): frozenset(
        {
            "follower_cohort",
            "lurker_cohort",
            "hostile_drive_by",
            "algorithmic_cohort",
            "peer_creator",
            "critic",
            "platform",
        }
    ),
    (Persona.VIDEO, None): frozenset(
        {
            "subscriber_cohort",
            "recommender_inflow",
            "hate_watcher",
            "skeptic_segment",
            "peer_creator",
            "critic",
            "platform",
        }
    ),
    (Persona.PRODUCT, None): frozenset(
        {
            "existing_user",
            "churned_user",
            "prospect",
            "competitor",
            "press_outlet",
            "influencer",
            "developer_relations",
            "support_channel",
            "sales_channel",
        }
    ),
    (Persona.POLICY, PolicySurface.INTERNAL_ORG): frozenset(
        {
            "employee_cohort",
            "manager_role",
            "executive_team",
            "hr_ops",
            "legal_counsel",
            "comms_team",
            "exiting_employee",
            "candidate_pipeline",
            "glassdoor_channel",
            "slack_channel",
        }
    ),
    (Persona.POLICY, PolicySurface.PUBLIC_POLICY): frozenset(
        {
            "citizen_segment",
            "beneficiary_group",
            "burdened_group",
            "implementing_agency",
            "intermediary",
            "business_segment",
            "civil_society_actor",
            "media_actor",
            "political_actor",
            "regulator",
            "legal_actor",
            "expert",
            "service_provider",
        }
    ),
}


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Cost-ceiling constants. The canonical home is
# :mod:`app.simulation_platform.scale_caps` (a leaf module any lane's
# admission gate can consume without pulling in the heavy
# ``app.policy.schema`` graph). They are re-exported here verbatim so
# existing ``from app.policy.schema import SCALE_CAP_POC,
# SCALE_CAP_LARGE`` call sites keep working without a sweep.
from ..simulation_platform.scale_caps import (  # noqa: E402
    SCALE_CAP_LARGE,
    SCALE_CAP_POC,
)


# RFC 6902 op allowlist. ``move|copy|test`` are not yet supported.
_ALLOWED_JSON_PATCH_OPS: frozenset = frozenset({"add", "remove", "replace"})


# Path allowlist regex for JSON Patch ops on Variant.diff. Two allowed
# shapes, both with strict single-segment tails (no nested mutation):
#
#   * ``/scenario/variants/<idx>/<leaf>`` where <leaf> is one of the
#     safe-to-edit ``Variant`` attributes
#     (``name``, ``description``, ``kind``, ``changed_assumptions``).
#     Sensitive tails are explicitly NOT reachable: ``variant_id``,
#     ``base_variant_id``, and ``diff`` would let a counterfactual
#     rewrite its own identity or splice in arbitrary further
#     patches; ``..`` would escape the variants scope entirely.
#   * ``/personas/<idx>/traits/<dotted-name>`` where <dotted-name> is
#     a single trait identifier (alphanumeric + dot/underscore).
#
# The regex pins both shapes simultaneously; any other path is rejected
# by ``JSONPatchOp._path_allowlisted`` below.
_JSON_PATCH_PATH_RE: re.Pattern = re.compile(
    r"^/scenario/variants/\d+/(?:name|description|kind|changed_assumptions)$"
    r"|^/personas/\d+/traits/[A-Za-z0-9_.]+$"
)


# Sentinel substrings whose presence anywhere in a JSONPatch path is
# forbidden defense-in-depth, regardless of regex shape. Catches
# escape attempts the leaf-name allowlist already blocks but worth
# rejecting at an outer perimeter so a future regex relaxation cannot
# silently re-open the hole.
_JSON_PATCH_FORBIDDEN_SUBSTRINGS: tuple = ("..", "/diff", "/base_variant_id", "/variant_id")


# Provenance source-type allowlist.
_PROVENANCE_SOURCE_TYPES: frozenset = frozenset(
    {"survey", "platform_data", "regulatory_filing", "user_upload", "synthetic"}
)


# Provenance license-class allowlist.
_PROVENANCE_LICENSE_CLASSES: frozenset = frozenset(
    {"public_domain", "permissive", "attribution_required", "share_alike", "proprietary"}
)


# Distribution-support allowlist.
_DISTRIBUTION_SUPPORTS: frozenset = frozenset(
    {"bernoulli", "beta", "categorical", "normal"}
)


# Stance allowlist for prior distributions.
_PRIOR_STANCES: frozenset = frozenset(
    {"support", "oppose", "neutral", "unsure", "mixed"}
)


# Provenance-id pattern.
_PROVENANCE_ID_RE: re.Pattern = re.compile(r"^prov_[A-Za-z0-9_-]{1,64}$")


# ---------------------------------------------------------------------------
# Base config: strict, no extras, populate-by-name
# ---------------------------------------------------------------------------


def _strict_config() -> ConfigDict:
    """Model-wide config used by every scenario-tree model.

    Note on ``strict``: we do NOT enable ``strict=True`` at the model level
    because that blocks the canonical string-to-enum coercion every YAML
    fixture relies on (``persona: creator`` is a YAML string, not a
    ``Persona`` instance). Instead, the locked numeric propensity fields
    on ``AudienceSegment`` get ``strict=True`` per-field via ``Field``,
    which is the idiomatic Pydantic v2 way to forbid silent
    ``int -> float`` coercion exactly where it matters.
    """

    return ConfigDict(
        populate_by_name=True,
        extra="forbid",
        use_enum_values=False,
    )


# ---------------------------------------------------------------------------
# Leaf models
# ---------------------------------------------------------------------------


class Clause(BaseModel):
    """A scenario clause (policy text, contract clause, post fragment, ...)."""

    model_config = _strict_config()

    clause_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    affects: List[str] = Field(default_factory=list)


class DriftParams(BaseModel):
    """Numeric opinion-drift parameters.

    Only the numeric parameters live here; the free-text ``drift_rules``
    string on AudienceSegment is derived from these numbers by the runtime
    drift module, not by the schema.
    """

    model_config = _strict_config()

    susceptibility: float = Field(..., ge=0.0, le=1.0)
    compromise_mu: float = Field(..., gt=0.0, le=0.5)
    epsilon: float = Field(..., ge=0.0, le=1.0)
    gain_pos: float = Field(..., ge=1.0, le=3.0)
    gain_neg: float = Field(..., ge=1.0, le=3.0)
    gain_ratio: Optional[float] = Field(default=None, ge=0.0)
    delta: float = Field(..., ge=0.0, le=1.0)
    citation_key: Optional[str] = Field(default=None)


# ---------------------------------------------------------------------------
# Prior provenance / distribution / segment-priors
# ---------------------------------------------------------------------------


class PriorProvenance(BaseModel):
    """Where a prior came from.

    Attribution string is REQUIRED when ``license_class`` is
    ``attribution_required`` or ``share_alike``; otherwise optional.
    """

    model_config = _strict_config()

    id: str = Field(..., pattern=_PROVENANCE_ID_RE.pattern)
    source_type: Literal[
        "survey", "platform_data", "regulatory_filing", "user_upload", "synthetic"
    ]
    source_url: Optional[str] = Field(default=None)
    fetched_at: Optional[str] = Field(default=None)
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    applied_to_segments: List[str] = Field(default_factory=list)
    license_class: Literal[
        "public_domain", "permissive", "attribution_required", "share_alike", "proprietary"
    ]
    attribution_string: Optional[str] = Field(default=None)
    redistribution_allowed: bool = Field(default=False)

    @model_validator(mode="after")
    def _attribution_conditional_required(self) -> "PriorProvenance":
        if self.license_class in {"attribution_required", "share_alike"} and not (
            self.attribution_string and self.attribution_string.strip()
        ):
            raise ValueError(
                f"PriorProvenance.attribution_string is required when "
                f"license_class={self.license_class!r}"
            )
        return self


class PriorDistribution(BaseModel):
    """A single prior distribution over some dimension.

    ``params`` carries the distribution-specific numeric parameters (for
    example, ``{'alpha': 2.0, 'beta': 5.0}`` for ``support='beta'``). The
    per-support shape of ``params`` is not validated at this layer; the
    runtime consumer is responsible for that check.
    """

    model_config = _strict_config()

    dimension: str = Field(..., min_length=1)
    support: Literal["bernoulli", "beta", "categorical", "normal"]
    params: Dict[str, Any] = Field(..., min_length=1)
    stance: Literal["support", "oppose", "neutral", "unsure", "mixed"]
    weight: float = Field(..., ge=0.0, le=1.0)
    provenance_id: str = Field(..., pattern=_PROVENANCE_ID_RE.pattern)


class AudienceSegmentPriors(BaseModel):
    """Per-segment prior bundle.

    Sum-to-1 invariant: within each ``dimension`` the ``weight`` values of all
    priors must sum to 1.0 (+/- 0.01). Multiple dimensions can coexist; the
    invariant is checked per-dimension.
    """

    model_config = _strict_config()

    priors: List[PriorDistribution] = Field(default_factory=list)
    override_llm_default: bool = Field(default=True)
    free_text_context: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _weights_sum_to_one_per_dimension(self) -> "AudienceSegmentPriors":
        groups: Dict[str, float] = defaultdict(float)
        for p in self.priors:
            groups[p.dimension] += p.weight
        for dim, total in groups.items():
            if abs(total - 1.0) > 0.01:
                raise ValueError(
                    f"AudienceSegmentPriors weights for dimension={dim!r} must "
                    f"sum to 1.0 +/- 0.01 (got {total:.4f})"
                )
        return self


# ---------------------------------------------------------------------------
# Trait map (OCEAN + Schwartz + parasocial)
# ---------------------------------------------------------------------------


class OceanScores(BaseModel):
    """OCEAN Big-Five scores in [0, 1]."""

    model_config = _strict_config()

    openness: float = Field(..., ge=0.0, le=1.0)
    conscientiousness: float = Field(..., ge=0.0, le=1.0)
    extraversion: float = Field(..., ge=0.0, le=1.0)
    agreeableness: float = Field(..., ge=0.0, le=1.0)
    neuroticism: float = Field(..., ge=0.0, le=1.0)


class SchwartzScores(BaseModel):
    """Schwartz 10 basic values in [0, 1]."""

    model_config = _strict_config()

    self_direction: float = Field(..., ge=0.0, le=1.0)
    stimulation: float = Field(..., ge=0.0, le=1.0)
    hedonism: float = Field(..., ge=0.0, le=1.0)
    achievement: float = Field(..., ge=0.0, le=1.0)
    power: float = Field(..., ge=0.0, le=1.0)
    security: float = Field(..., ge=0.0, le=1.0)
    conformity: float = Field(..., ge=0.0, le=1.0)
    tradition: float = Field(..., ge=0.0, le=1.0)
    benevolence: float = Field(..., ge=0.0, le=1.0)
    universalism: float = Field(..., ge=0.0, le=1.0)


class TraitMap(BaseModel):
    """Typed trait map for an AudienceSegment.

    The typed fields are closed-world; unknown free-form extensions go into
    ``freeform: Dict[str, str]`` so scenario authors can attach arbitrary
    string metadata without weakening the typed surface.
    """

    model_config = _strict_config()

    ocean: Optional[OceanScores] = None
    values: Optional[SchwartzScores] = None
    parasocial_intensity: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    attention_budget: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    partisan_lean: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    press_cycle_sensitivity: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    workflow_friction_tolerance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    freeform: Dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# JSON Patch ops
# ---------------------------------------------------------------------------


class JSONPatchOp(BaseModel):
    """RFC 6902 op restricted to add|remove|replace + path allowlist.

    ``move|copy|test`` are not supported. The path regex pins mutations to
    scenario variants and persona trait dotted paths.
    """

    model_config = _strict_config()

    op: str = Field(...)
    path: str = Field(...)
    value: Optional[Any] = None

    @field_validator("op")
    @classmethod
    def _op_allowlisted(cls, v: str) -> str:
        if v not in _ALLOWED_JSON_PATCH_OPS:
            raise ValueError(
                f"JSONPatchOp.op={v!r} not allowed; "
                f"allowed: {sorted(_ALLOWED_JSON_PATCH_OPS)}"
            )
        return v

    @field_validator("path")
    @classmethod
    def _path_allowlisted(cls, v: str) -> str:
        # Defense-in-depth substring check FIRST: catches escape
        # attempts (``..``) and sensitive-tail keys (``/diff``,
        # ``/base_variant_id``, ``/variant_id``) regardless of the
        # outer regex shape.
        for forbidden in _JSON_PATCH_FORBIDDEN_SUBSTRINGS:
            if forbidden in v:
                raise ValueError(
                    f"JSONPatchOp.path={v!r} contains forbidden "
                    f"substring {forbidden!r}; sensitive tails and "
                    "parent-directory escapes are rejected."
                )
        if not _JSON_PATCH_PATH_RE.match(v):
            raise ValueError(
                f"JSONPatchOp.path={v!r} not on allowlist; must match "
                "/scenario/variants/<idx>/(name|description|kind|changed_assumptions) "
                "or /personas/<idx>/traits/<dotted_name>"
            )
        return v

    @model_validator(mode="after")
    def _value_matches_op_arity(self) -> "JSONPatchOp":
        """RFC 6902 op-arity contract.

        * ``add`` / ``replace`` REQUIRE a ``value`` (the bytes being
          written into the document).
        * ``remove`` FORBIDS ``value`` (the op is path-only; an
          accidental value would silently misrepresent the intent).

        The base field type carries ``value: Optional[Any] = None`` so
        the wire shape stays uniform; the contract is enforced here
        per-op so a malformed payload trips a ValidationError at
        request-shape time, not later at apply time.
        """
        if self.op in {"add", "replace"} and self.value is None:
            raise ValueError(
                f"JSONPatchOp(op={self.op!r}) requires a non-None 'value'; "
                "RFC 6902 add/replace cannot be value-less."
            )
        if self.op == "remove" and self.value is not None:
            raise ValueError(
                "JSONPatchOp(op='remove') must NOT carry a 'value' field; "
                "RFC 6902 remove is path-only."
            )
        return self


class RunControls(BaseModel):
    """Reproducibility + scale knobs.

    ``drift_horizon_rounds`` lives on the Scenario root, not here. A
    ``model_validator(mode='before')`` on Scenario lifts the legacy
    ``run.drift_horizon_rounds`` to the root when the root field is absent so
    older preset/state.json blobs continue to load. RunControls itself no
    longer carries the field.
    """

    model_config = _strict_config()

    tier: Literal["sanity", "report", "publication"] = Field(default="sanity")
    run_seed: int = Field(default=42)
    ensemble_n: int = Field(default=5, ge=1, le=20)
    t_structure: float = Field(default=0.2, ge=0.0, le=2.0)
    t_narrative: float = Field(default=0.7, ge=0.0, le=2.0)
    ensemble_seeds: Optional[List[int]] = None
    persona_salts: Optional[Dict[str, str]] = None
    signal_schedule: Dict[str, Any] = Field(default_factory=dict)
    # Confirmation Bias paradox hook: ratio in ``[0.0, 1.0]`` of report
    # claims that confirm the scenario's stated assumptions; downstream
    # gate refuses a 100%-confirmation report unless tagged
    # H_OVERCONFIDENCE. Consumed by the transparency post-processor at ``app.services.policy.transparency_runtime`` to emit per-flag validity notes on the persisted ``Report.validity_flags`` list.
    assumption_confirmation_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class AudienceSegment(BaseModel):
    """A stakeholder segment.

    The five locked ``1..5`` int fields live here; they are behavioural
    propensities, NOT trait_schema entries. Persona-aware ``type``
    validation does NOT run here -- it requires parent Scenario context
    (persona + scope) and is enforced in ``Scenario._validate_segment_types``.
    """

    model_config = _strict_config()

    segment_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)

    interests: List[str] = Field(default_factory=list)
    incentives: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    likely_support_reasons: List[str] = Field(default_factory=list)
    likely_opposition_reasons: List[str] = Field(default_factory=list)
    information_channels: List[str] = Field(default_factory=list)

    # Five locked 1..5 ints. ``strict=True`` rejects silent int<->float
    # coercion (``1.5`` is not truncated to ``1``; ``3.0`` is rejected).
    influence_level: int = Field(..., ge=1, le=5, strict=True)
    vulnerability_level: int = Field(..., ge=1, le=5, strict=True)
    compliance_propensity: int = Field(..., ge=1, le=5, strict=True)
    trust_level: int = Field(..., ge=1, le=5, strict=True)
    mobilization_capacity: int = Field(..., ge=1, le=5, strict=True)

    drift_rules: Optional[str] = None
    drift_params: Optional[DriftParams] = None
    # Hawthorne Effect paradox hook: observer-effect bias in
    # ``[0.0, 1.0]``; sits next to ``drift_params`` because the runtime
    # drift consumer applies it. Consumed by the transparency
    # post-processor at ``app.services.policy.transparency_runtime``
    # to emit observer-effect validity flags on ``Report.validity_flags``.
    social_desirability_bias: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    traits: Optional[TraitMap] = None
    trait_schema_ref: Optional[str] = None
    audience_priors: Optional[AudienceSegmentPriors] = None
    # Power-Law / heavy-tails paradox hook: per-segment distribution-shape
    # annotations. ``audience_priors`` already carries numeric priors;
    # this is a separate hook for declaring tail-shape assumptions
    # (e.g. ``{"reach": "pareto"}``). Consumed by the transparency
    # post-processor at ``app.services.policy.transparency_runtime``
    # to emit power-law-tail validity flags on ``Report.validity_flags``.
    distribution_assumptions: Optional[Dict[str, Any]] = Field(default=None)
    # Survivorship Bias paradox hook: free-text label naming the selection
    # frame this segment was drawn from (e.g. "engaged users only").
    # Consumed by the transparency post-processor at ``app.services.policy.transparency_runtime`` to emit per-flag validity notes on the persisted ``Report.validity_flags`` list.
    selection_frame: Optional[str] = Field(default=None)
    # St. Petersburg Paradox hook: utility-curve parameters
    # (e.g. ``{"risk_aversion": 0.3, "utility_curvature": "log"}``).
    # Consumed by the transparency post-processor at ``app.services.policy.transparency_runtime`` to emit per-flag validity notes on the persisted ``Report.validity_flags`` list.
    utility_params: Optional[Dict[str, Any]] = Field(default=None)

    # Falsifier contract: at least one entry required.
    what_changes_their_mind: List[str] = Field(..., min_length=1)
    notes: Optional[str] = None


class Variant(BaseModel):
    """Scenario variant (baseline | counterfactual | sensitivity).

    ``kind=counterfactual`` requires ``base_variant_id`` (causal diff
    integrity).
    """

    model_config = _strict_config()

    variant_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    changed_assumptions: List[str] = Field(default_factory=list)
    kind: VariantKind = Field(default=VariantKind.BASELINE)
    base_variant_id: Optional[str] = None
    diff: List[JSONPatchOp] = Field(default_factory=list)

    @model_validator(mode="after")
    def _counterfactual_requires_base(self) -> "Variant":
        if self.kind == VariantKind.COUNTERFACTUAL and not self.base_variant_id:
            raise ValueError(
                "Variant.kind=counterfactual requires non-empty base_variant_id "
                "(causal diff integrity)"
            )
        return self


# ---------------------------------------------------------------------------
# EvidenceRef -- typed pointer into a run artifact backing a claim.
# ---------------------------------------------------------------------------


class EvidenceRef(BaseModel):
    """Typed evidence pointer attached to a :class:`Claim`.

    ``ref_type`` is restricted to a small Literal so adding a new
    evidence source (e.g. ``zep_node`` once Graphiti emits typed entity
    nodes) is a schema-level breaking change visible at the boundary,
    not a silent extension. The renderer maps each type to a stable link
    shape in the rendered markdown:

    * ``simulation_action`` -> ``actions.jsonl#<action UUID>``;
      ``ref_id`` is the bare UUID.
    * ``seed_text_line`` -> ``seed_text:L<line number>``; ``ref_id``
      is the FULL key (``"seed_text:L<n>"``) as stored in
      :class:`EvidenceCatalog.seed_text_line`. Storing the full key
      keeps :meth:`EvidenceCatalog.contains` a direct membership
      check and prevents the renderer from accidentally
      double-prefixing the value (the renderer still accepts the older
      bare-line-number form; new code should emit the full key).
    * ``agent_interview`` -> ``interviews/<interview UUID>.json``;
      ``ref_id`` is the bare UUID (file stem).
    * ``external_source`` -> the URL verbatim; ``ref_id`` IS the URL.
    """

    model_config = _strict_config()

    ref_type: Literal[
        "simulation_action",
        "seed_text_line",
        "agent_interview",
        "external_source",
    ]
    # Free-form pointer body whose shape is constrained by ref_type.
    # Min length 1 so a typo like ``ref_id=""`` fails at validation
    # rather than rendering as a broken link.
    ref_id: Annotated[str, Field(min_length=1)]


# ---------------------------------------------------------------------------
# DriverHint -- per-claim attribution hint (co-occurrence proxy at POC).
# ---------------------------------------------------------------------------


class DriverHint(BaseModel):
    """One attribution hint for why a claim was emitted.

    Heuristic, not causal. The POC method is ``cooccurrence_v1`` (cheap:
    segment-intersection weighted by global OAT swing); ``loo_v1`` is
    the higher-cost leave-one-out method. ``attribution_disclaimer`` is a
    literal string the renderer surfaces verbatim alongside every hint
    so a reader cannot accidentally treat a co-occurrence proxy as
    causal attribution.

    Per-claim contributions sum to ~1.0 +/- 0.05 across all hints for a
    given claim. This sum invariant is checked by the report generator
    at claim-construction time, NOT here -- the invariant is across the
    set of DriverHints for a claim, not a property of a single hint.
    """

    model_config = _strict_config()

    # What kind of driver this hint references. The 4 values correspond
    # to the four contribution sources the OAT runner can attribute to:
    # a stakeholder segment, a prior distribution, a drift parameter
    # path, or an explicit scenario assumption.
    kind: Literal["segment", "prior", "drift", "assumption"]
    # Identifier whose shape depends on ``kind``: segment_id for
    # segments, prior_id for priors, drift parameter path (e.g.
    # ``drift_params.gain_pos``) for drift, or assumption index for
    # assumption. Min length 1 so an empty pointer fails validation.
    ref: Annotated[str, Field(min_length=1)]
    # Per-claim contribution in [0, 1]. Across all hints for a single
    # claim these sum to ~1.0 +/- 0.05 (invariant enforced by the
    # generator, not the per-hint model).
    contribution: Annotated[float, Field(ge=0.0, le=1.0)]
    # Method discriminator. Literal (not Enum) because adding a new
    # method must be a schema-level breaking change visible at the
    # boundary.
    method: Literal["cooccurrence_v1", "loo_v1"]
    attribution_confidence: Literal["low", "medium", "high"] = "low"
    # Literal disclaimer string surfaced verbatim by the renderer
    # alongside every hint. Default value pins the canonical wording so
    # the message cannot drift across runs; consumers comparing reports
    # can diff the value and immediately see if a non-default disclaimer
    # was overridden.
    attribution_disclaimer: str = "co-occurrence proxy, not causal"


# ---------------------------------------------------------------------------
# Claim -- one report assertion with provenance.
# ---------------------------------------------------------------------------


class Claim(BaseModel):
    """One report claim with strict canonical-tag provenance.

    Every claim carries exactly one :class:`HypothesisTag` value -- the
    5-tag epistemic taxonomy is the product's non-negotiable contract.
    ``tag`` is typed as the enum so Pydantic refuses any string outside
    the canonical 5 at validation time. A model-level validator
    additionally checks the serialised string lives in
    :data:`CANONICAL_HYPOTHESIS_TAGS` so a future enum refactor cannot
    silently widen the contract.

    ``segment_refs`` is the Simpson's-Paradox runtime hook: per-segment
    claim emission alongside any aggregate claim lets the runtime
    compute an aggregation-disagreement flag.

    ``expected_risk_tags`` are scenario-level risk hypotheses the
    scenario author declared the simulation should stress-test. A claim
    that names one or more of them is asserting "this claim speaks to
    that risk hypothesis." Stored as plain strings on the wire (Pydantic
    v2 serialises str-enums as their value) but every entry MUST be a
    member of :data:`CANONICAL_EXPECTED_RISK_TAGS` -- the validator
    below enforces that. This is NOT defensive duplication of the
    :class:`ScenarioPreset` boundary check; reports may be hand-edited
    or arrive from an LLM, and the boundary check at preset-load time
    does not see those code paths.

    ``confidence`` is an optional numeric in [0, 1] that the renderer
    surfaces only when present (omit-when-absent is the contract --
    the UI must not invent a value).

    ``evidence_refs`` carries typed :class:`EvidenceRef` objects rather
    than free-form strings so the renderer can dispatch on
    ``ref_type`` to emit the right link shape per source kind.
    """

    model_config = _strict_config()

    claim_id: Annotated[str, Field(min_length=1)]
    text: Annotated[str, Field(min_length=1)]
    # Typed as the enum so Pydantic emits a ValidationError for any
    # string outside the 5 canonical values. The string form is what
    # ships on the JSON wire (Pydantic v2 serialises str-enum members as
    # their value, not the enum object).
    tag: HypothesisTag
    # Typed evidence pointers (see :class:`EvidenceRef`). Empty list is
    # correct for pure ``assumption`` / ``inference`` claims that draw
    # on no specific run artifact.
    evidence_refs: List[EvidenceRef] = Field(default_factory=list)
    # Simpson's Paradox hook: segment_ids this claim references. The
    # runtime gate forces per-segment claim emission alongside any
    # aggregate claim and computes an aggregation_disagreement_flag.
    segment_refs: List[str] = Field(default_factory=list)
    # Optional numeric confidence in [0, 1]. Renderer surfaces only when
    # present; omit-when-absent (no default 0.0 / None-as-zero) so an
    # unspecified confidence renders as nothing rather than "0%".
    confidence: Optional[Annotated[float, Field(ge=0.0, le=1.0)]] = None
    # Co-occurrence driver hints (see :class:`DriverHint` above for the
    # cooccurrence_v1 heuristic). Empty list is correct for claims with
    # no driver attribution (e.g. pure ``assumption`` tags). Renderer
    # surfaces these inline with each claim alongside the
    # ``attribution_confidence`` label.
    driver_hints: List[DriverHint] = Field(default_factory=list)
    # Author-declared risk hypotheses this claim addresses. Validated
    # against the canonical 17-value allowlist by the validator below.
    expected_risk_tags: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _driver_hints_contribution_sum_in_band(self) -> "Claim":
        """Enforce the per-claim driver contribution-sum invariant.

        :class:`DriverHint` documents ~1.0 +/- 0.05 as the per-claim
        sum. The runtime attributor normalises before returning, but
        a hand-edited or LLM-produced report could still ship hints
        whose sum drifts outside the band. Catching it here fails
        loudly at the schema boundary -- not silently at render time
        -- so a future drift in the attribution path surfaces in the
        right place. Empty list is exempt (no hints to sum).
        """
        if not self.driver_hints:
            return self
        total = sum(h.contribution for h in self.driver_hints)
        if abs(total - 1.0) > 0.05:
            raise ValueError(
                f"Claim.driver_hints contributions must sum to 1.0 +/- 0.05; "
                f"got sum={total:.4f} across {len(self.driver_hints)} hint(s) "
                f"on claim_id={self.claim_id!r}."
            )
        return self

    @model_validator(mode="after")
    def _tag_is_canonical(self) -> "Claim":
        """Refuse any tag value outside the canonical 5 (defense-in-depth).

        Pydantic v2 already rejects non-enum values at field validation
        time, so the only way to land here with a non-canonical value is
        if the enum module itself was edited to widen the set without
        updating the frozen ``CANONICAL_HYPOTHESIS_TAGS`` constant. This
        check is the second line of defense so the enum + frozen set
        cannot drift apart silently.
        """
        if self.tag.value not in CANONICAL_HYPOTHESIS_TAGS:
            raise ValueError(
                f"Claim.tag={self.tag!r} is not in the canonical "
                f"hypothesis-tag taxonomy "
                f"({sorted(CANONICAL_HYPOTHESIS_TAGS)!r}). "
                "Editing HypothesisTag without updating "
                "CANONICAL_HYPOTHESIS_TAGS is the only way to reach this "
                "error -- the two must stay in lockstep."
            )
        return self

    @field_validator("expected_risk_tags")
    @classmethod
    def _expected_risk_tags_are_canonical(cls, value: List[str]) -> List[str]:
        """Every entry must be a member of CANONICAL_EXPECTED_RISK_TAGS.

        Reports may be hand-edited or produced by an LLM; the
        ScenarioPreset boundary's check does not see those code paths.
        Validating here closes that gap. Empty list is allowed (the
        common case for claims that don't name a specific risk).
        """
        unknown = [t for t in value if t not in CANONICAL_EXPECTED_RISK_TAGS]
        if unknown:
            raise ValueError(
                f"Claim.expected_risk_tags contains values outside the "
                f"canonical ExpectedRiskTag allowlist: {sorted(set(unknown))!r}. "
                f"Allowed values (sample): "
                f"{sorted(CANONICAL_EXPECTED_RISK_TAGS)[:5]!r}... "
                f"({len(CANONICAL_EXPECTED_RISK_TAGS)} total)."
            )
        return value


# ---------------------------------------------------------------------------
# Section -- one persona-aware report section.
# ---------------------------------------------------------------------------


class Section(BaseModel):
    """One section of a rendered report.

    ``section_id`` is a stable identifier (typically a slug of the
    title) the renderer uses as the HTML anchor for cross-references
    from elsewhere in the report. ``title`` is human-facing.
    ``claims`` can be empty -- a section with zero claims is valid
    (the runtime had nothing to say in that section for this scenario)
    and the renderer surfaces an explicit "no claims emitted" line
    rather than dropping the section entirely. The per-persona
    ``claim_guidance`` lives only on the runtime prompt (in
    :data:`app.policy.persona_defaults.PERSONA_REPORT_SECTIONS`) and is
    deliberately NOT persisted into the report JSON to keep that file
    a pure report-output artifact.
    """

    model_config = _strict_config()

    section_id: Annotated[str, Field(min_length=1)]
    title: Annotated[str, Field(min_length=1)]
    claims: List[Claim] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ValidityFlag -- runtime-consumer transparency note attached to a Report.
# ---------------------------------------------------------------------------


class ValidityFlag(BaseModel):
    """One transparency/validity note attached to a rendered report.

    The 9-element ``kind`` Literal is the closed taxonomy the runtime
    post-processor emits against the canonical paradox-hook schema
    fields (heavy-tails, Monte-Carlo coverage, survivorship/cohort gap,
    observer effect, Simpson aggregate disagreement, mean-reversion,
    St-Petersburg utility, confirmation bias / falsifier audit,
    Berkson selection axis). Adding a 10th kind is a schema-level
    breaking change visible at the boundary.

    Optional ``field_validation_prompt`` is a one-line prompt the
    renderer surfaces under the bullet so a human reviewer has a
    concrete follow-up; for ``confirmation_bias`` this carries the
    un-referenced falsifier list (folds the falsifier-audit panel
    into the confirmation-bias flag without introducing a 10th kind).
    """

    model_config = _strict_config()

    flag_id: Annotated[str, Field(min_length=1, max_length=64)]
    kind: Literal[
        "power_law_tail",
        "monte_carlo_coverage",
        "survivorship_or_cohort_gap",
        "observer_effect",
        "simpson_aggregate_disagreement",
        "mean_reversion",
        "utility_curve",
        "confirmation_bias",
        "selection_axis",
    ]
    severity: Literal["info", "notice", "warning"]
    message: Annotated[str, Field(min_length=1, max_length=400)]
    claim_refs: List[str] = Field(default_factory=list)
    segment_refs: List[str] = Field(default_factory=list)
    source_fields: List[str] = Field(default_factory=list)
    method: Annotated[str, Field(min_length=1)]
    confidence: Literal["low", "medium", "high"] = "low"
    field_validation_prompt: Optional[str] = Field(default=None, max_length=240)


# ---------------------------------------------------------------------------
# Report -- top-level report shape.
# ---------------------------------------------------------------------------


class Report(BaseModel):
    """Top-level policy report shape persisted to ``policy_report.json``.

    JSON-first schema: ``executive_summary`` + persona-specific
    ``sections`` + ``evidence_gaps`` + ``field_validation_questions`` +
    ``confidence_notes`` + ``source_limitations`` + an embedded
    :class:`app.policy.run_metadata.RunMetadata` snapshot.

    Strict ``extra='forbid'`` so any unknown key in a hand-edited or
    LLM-generated report trips a ValidationError at read time. The
    embedded ``run_metadata`` snapshot duplicates the on-disk
    ``run_metadata.json`` artifact deliberately: a report is a
    self-contained provenance bundle, and a reader should not have to
    chase a separate file to know which run produced which claims.
    """

    model_config = _strict_config()

    # Stable identifier; the generator typically derives this from
    # ``simulation_id`` + the report version so re-runs that change
    # claims (but reuse simulation_id) get a distinct report_id.
    report_id: Annotated[str, Field(min_length=1)]
    simulation_id: Annotated[str, Field(min_length=1)]
    persona: Persona
    scope: Optional[PolicySurface] = None
    scenario_hash: Annotated[str, Field(min_length=1)]
    seed_hash: Annotated[str, Field(min_length=1)]
    # Wall-clock generated-at; captured by the report GENERATOR exactly
    # once at construction time (NOT by the renderer; the renderer is a
    # pure consumer of the persisted Report). Used for staleness
    # comparison against ``RunMetadata.prepared_at`` (prepare-vs-generate
    # delta) and as the stable identifier in any deterministic markdown
    # filename the renderer emits.
    generated_at: datetime
    # 3-5 claims summarising the report's key findings. Stored as a
    # list of Claim objects with the same canonical-tag contract as
    # section claims; the renderer surfaces them at the top of the
    # markdown before the per-section breakdown.
    executive_summary: List[Claim] = Field(default_factory=list)
    # Per-persona sections in render order. Required + non-empty: a
    # report with no sections is structurally invalid (the persona
    # section list lives in persona_defaults and always has at least
    # one section per persona+scope row).
    sections: Annotated[List[Section], Field(min_length=1)]
    # Open questions demanding real-world evidence before action.
    # Typically all carry ``tag='requires_validation'`` but the model
    # does NOT enforce that uniformly because a section's evidence-gap
    # may also be flagged with other tags (e.g. ``assumption`` if a
    # held assumption is the limiting factor). The renderer surfaces
    # these in a dedicated "Evidence gaps" section.
    evidence_gaps: List[Claim] = Field(default_factory=list)
    # Concrete questions a human reviewer could ask to validate the
    # report's claims with real-world data. Plain strings (no
    # provenance/tag); the rendered markdown emits them as a bullet
    # list under "Field validation questions".
    field_validation_questions: List[str] = Field(default_factory=list)
    # Narrative paragraph (multi-line) on overall confidence + caveats.
    # Rendered verbatim under "Confidence and limitations".
    confidence_notes: str = ""
    # Bulleted list of source limitations (e.g. "seed_text is a draft;
    # actual policy text may differ", "trader association leadership
    # not interviewed"). Rendered verbatim under "Confidence and
    # limitations" beneath ``confidence_notes``.
    source_limitations: List[str] = Field(default_factory=list)
    # Transparency / validity flags emitted by the runtime
    # post-processor against the paradox-hook schema fields. Additive:
    # default is an empty list so pre-existing persisted policy_report
    # JSONs (which do not carry the key) parse cleanly under
    # ``extra='forbid'`` -- Pydantic v2 fills the default at
    # ``model_validate_json`` time. The renderer suppresses the
    # transparency section entirely when this list is empty so
    # unaffected fixtures keep their byte stability.
    validity_flags: List[ValidityFlag] = Field(default_factory=list)
    # Embedded run-metadata snapshot. Forward reference (annotation
    # string) to avoid the schema -> run_metadata -> schema circular
    # import at module-load time; the import + ``model_rebuild()`` at
    # the bottom of this module resolves it.
    run_metadata: "RunMetadata"

    @model_validator(mode="after")
    def _scope_required_for_policy(self) -> "Report":
        """Mirror the Scenario-level invariant: scope iff persona=policy."""
        if self.persona == Persona.POLICY and self.scope is None:
            raise ValueError(
                "Report.persona='policy' requires a non-None scope "
                "(matches the Scenario.persona discriminator contract)."
            )
        if self.persona != Persona.POLICY and self.scope is not None:
            raise ValueError(
                f"Report.scope={self.scope!r} is only valid for "
                "persona='policy'; non-policy personas must leave it None."
            )
        return self

    @model_validator(mode="after")
    def _header_matches_embedded_run_metadata(self) -> "Report":
        """Cross-field invariant: the 4 provenance fields the Report header
        duplicates from the embedded ``run_metadata`` MUST match exactly.

        Without this check a hand-edited or LLM-produced report could
        silently ship with ``simulation_id="sim_A"`` in the header while
        the embedded ``run_metadata.simulation_id`` read ``"sim_B"`` --
        i.e. "report A for run B". The duplicate fields exist so the
        renderer can show the same identifiers in two places (header +
        run-metadata block) without dereferencing, but the contract is
        that they describe the SAME run. Mismatches raise here.

        Only the 4 fields the embedded model also carries are checked
        (simulation_id, scenario_hash, seed_hash, persona). Scope is
        Report-only (RunMetadata does not carry scope) so its
        persona-discriminator invariant lives in the validator above.
        """
        mismatches: List[str] = []
        if self.simulation_id != self.run_metadata.simulation_id:
            mismatches.append(
                f"simulation_id: header={self.simulation_id!r} vs "
                f"run_metadata={self.run_metadata.simulation_id!r}"
            )
        if self.scenario_hash != self.run_metadata.scenario_hash:
            mismatches.append(
                f"scenario_hash: header={self.scenario_hash!r} vs "
                f"run_metadata={self.run_metadata.scenario_hash!r}"
            )
        if self.seed_hash != self.run_metadata.seed_hash:
            mismatches.append(
                f"seed_hash: header={self.seed_hash!r} vs "
                f"run_metadata={self.run_metadata.seed_hash!r}"
            )
        if self.persona != self.run_metadata.persona:
            mismatches.append(
                f"persona: header={self.persona!r} vs "
                f"run_metadata={self.run_metadata.persona!r}"
            )
        if mismatches:
            raise ValueError(
                "Report header fields disagree with the embedded "
                "run_metadata snapshot (they must describe the same run): "
                + "; ".join(mismatches)
            )
        return self

    @model_validator(mode="after")
    def _validity_flags_are_well_formed(self) -> "Report":
        """Cross-flag invariants: uniqueness + canonical ordering + ref hygiene.

        Three checks run against the populated ``validity_flags`` list:

        1. ``flag_id`` is unique across the report. Duplicates would let
           a hand-edited report carry two flags with the same id, which
           the renderer would surface twice with identical anchor text.
        2. The list is ordered by canonical ``kind`` (matching the
           ``Literal`` declaration order on :class:`ValidityFlag`).
           Identical inputs MUST produce identical bytes -- a renderer
           that walks the list verbatim cannot recover from a shuffle.
           Ties within a kind preserve insertion order (which the
           runtime post-processor builds deterministically).
        3. ``claim_refs`` / ``segment_refs`` are non-empty strings AND
           every claim_ref resolves to an actual claim_id in the report
           (executive_summary or sections[*].claims) -- a dangling ref
           would render a bullet that points nowhere.
        """
        flags = self.validity_flags
        if not flags:
            return self

        # 1. Uniqueness of flag_id.
        ids = [f.flag_id for f in flags]
        if len(ids) != len(set(ids)):
            seen: set[str] = set()
            dupes = sorted({i for i in ids if i in seen or seen.add(i)})  # type: ignore[func-returns-value]
            raise ValueError(
                "Report.validity_flags has duplicate flag_id values "
                f"({dupes!r}); each flag must carry a unique id."
            )

        # 2. Canonical ordering by kind. The Literal declaration order
        #    on ValidityFlag.kind is the canonical sort key.
        canonical_kinds = (
            "power_law_tail",
            "monte_carlo_coverage",
            "survivorship_or_cohort_gap",
            "observer_effect",
            "simpson_aggregate_disagreement",
            "mean_reversion",
            "utility_curve",
            "confirmation_bias",
            "selection_axis",
        )
        kind_index = {k: i for i, k in enumerate(canonical_kinds)}
        # The runtime post-processor sorts by (kind_index, flag_id) so
        # same-kind flags land in lexical flag_id order. The validator
        # must compare the same tuple key -- the previous version only
        # compared kind_index, which would let two flags of the same
        # kind appear in REVERSED flag_id order (e.g.
        # ``vf_simpson_01`` before ``vf_simpson_00``) and still pass.
        actual_keys = [
            (kind_index.get(f.kind, len(canonical_kinds)), f.flag_id)
            for f in flags
        ]
        if actual_keys != sorted(actual_keys):
            actual_seq = [(f.kind, f.flag_id) for f in flags]
            raise ValueError(
                "Report.validity_flags is not in canonical "
                "(kind, flag_id) order. Expected kinds in "
                f"{list(canonical_kinds)!r} sequence with "
                "lexical flag_id ordering within each kind; "
                f"got sequence: {actual_seq!r}."
            )

        # 3. Reference hygiene: no empty strings; every claim_ref must
        #    resolve to an actual claim_id present in the report.
        known_claim_ids: set[str] = set()
        for c in self.executive_summary:
            known_claim_ids.add(c.claim_id)
        for sec in self.sections:
            for c in sec.claims:
                known_claim_ids.add(c.claim_id)
        for sec in self.evidence_gaps:
            known_claim_ids.add(sec.claim_id)

        for flag in flags:
            if any(not r or not r.strip() for r in flag.claim_refs):
                raise ValueError(
                    f"Report.validity_flags[{flag.flag_id!r}].claim_refs "
                    "contains an empty/whitespace entry; ref hygiene "
                    "requires non-empty strings."
                )
            if any(not r or not r.strip() for r in flag.segment_refs):
                raise ValueError(
                    f"Report.validity_flags[{flag.flag_id!r}].segment_refs "
                    "contains an empty/whitespace entry; ref hygiene "
                    "requires non-empty strings."
                )
            dangling = [r for r in flag.claim_refs if r not in known_claim_ids]
            if dangling:
                raise ValueError(
                    f"Report.validity_flags[{flag.flag_id!r}].claim_refs "
                    f"contains dangling claim_ids not present in the "
                    f"report: {sorted(dangling)!r}."
                )
        return self


# ---------------------------------------------------------------------------
# ReportLLMDraft -- the LLM-owned subset of :class:`Report`.
# ---------------------------------------------------------------------------


class ReportLLMDraft(BaseModel):
    """Strict-schema shape the LLM is permitted to emit for a Report.

    A :class:`Report` carries TWO kinds of fields:

      * **LLM-owned**: ``executive_summary``, ``sections``,
        ``evidence_gaps``, ``field_validation_questions``,
        ``confidence_notes``, ``source_limitations`` -- claims and
        narrative the model produces from the scenario prompt.
      * **Caller-owned / runtime-owned**: ``report_id``,
        ``simulation_id``, ``persona``, ``scope``, ``scenario_hash``,
        ``seed_hash``, ``generated_at``, ``run_metadata`` (provenance
        merged in by the generator from the caller-supplied
        :class:`RunMetadata`) plus ``validity_flags`` (populated by
        the transparency post-processor AFTER schema validation).

    Sending the full :class:`Report` schema to the provider with
    ``response_format={"type":"json_schema", "strict": true}`` would
    force the LLM to either fabricate the caller-owned fields or fail
    every call -- both unacceptable. This draft model is what the
    strict-schema preflight should target; the route then strips any
    echoed provenance keys (see
    ``policy_report_generator._PROVENANCE_KEYS_STRIPPED_FROM_LLM_PAYLOAD``),
    merges the caller-supplied provenance + ``generated_at`` clock,
    and re-validates the resulting dict against the full
    :class:`Report` for the final cross-field invariants
    (header-vs-embedded-run_metadata, scope-iff-policy,
    validity_flags hygiene).

    Strict ``extra='forbid'`` so an LLM that echoes a provenance key
    nevertheless trips a ValidationError at decode time -- matching
    the route's already-defensive strip step but surfacing the
    violation as a per-retry signal to the structured-output client
    instead of silently overwriting.

    Mirrors the LLM-owned subset of :class:`Report` field-for-field
    (same defaults, same min_length on ``sections``) so a payload
    valid against the draft is, by construction, structurally
    compatible with the full :class:`Report` once provenance is
    attached.
    """

    model_config = _strict_config()

    executive_summary: List[Claim] = Field(default_factory=list)
    sections: Annotated[List[Section], Field(min_length=1)]
    evidence_gaps: List[Claim] = Field(default_factory=list)
    field_validation_questions: List[str] = Field(default_factory=list)
    confidence_notes: str = ""
    source_limitations: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Scenario root model
# ---------------------------------------------------------------------------


class Scenario(BaseModel):
    """Top-level scenario.

    Legacy aliases ``policy_text`` and ``policy_question`` parse via
    ``AliasChoices`` but canonical serialisation always emits the
    persona-neutral ``seed_text`` / ``stress_question``. A
    ``DeprecationWarning`` fires the first time legacy keys are seen in the
    raw input.

    Root-level fields beyond the basics:
    * ``drift_horizon_rounds`` -- canonical location for this field.
    * ``drift_sensitivity_preset`` -- per-segment scaling applied by the
      runtime drift module.
    * ``trust_baseline`` in [0, 1].
    * ``expected_risk_tags`` -- author-declared risk hypotheses; the allowlist
      is enforced upstream at the :class:`ScenarioPreset` boundary (see
      ``app.policy.hypothesis_tags.ExpectedRiskTag``), so the lowered
      Scenario carries a plain ``List[str]`` runtime payload.
    """

    model_config = _strict_config()

    # Discriminators
    persona: Persona
    scope: Optional[PolicySurface] = None

    title: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    jurisdiction: Optional[str] = None

    seed_text: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("seed_text", "policy_text"),
        serialization_alias="seed_text",
    )
    stress_question: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("stress_question", "policy_question"),
        serialization_alias="stress_question",
    )

    clauses: List[Clause] = Field(default_factory=list)
    stakeholders: List[AudienceSegment] = Field(..., min_length=1)
    assumptions: List[str] = Field(..., min_length=1)
    success_criteria: List[str] = Field(..., min_length=1)
    failure_modes_to_watch: List[str] = Field(..., min_length=1)
    variants: List[Variant] = Field(default_factory=list)

    # Additive metadata / preset wiring.
    prior_bundle_id: Optional[str] = None
    preset_id: Optional[str] = None
    preset_overrides: Dict[str, Any] = Field(default_factory=dict)
    run: RunControls = Field(default_factory=RunControls)

    # Canonical root fields lifted out of RunControls / preset metadata.
    drift_horizon_rounds: int = Field(default=3, ge=1, le=20)
    drift_sensitivity_preset: Optional[
        Literal["conservative", "default", "volatile"]
    ] = None
    trust_baseline: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    expected_risk_tags: List[str] = Field(default_factory=list)
    # Berkson's Paradox hook: dict describing the axes the stakeholder
    # set was selected on (e.g. ``{"axes": ["influence", "engagement"]}``).
    # Downstream renders a selection-axis flag on aggregate claims when
    # ``len(axes) > 1``. Consumed by the transparency post-processor at ``app.services.policy.transparency_runtime`` to emit per-flag validity notes on the persisted ``Report.validity_flags`` list.
    selection_criteria: Optional[Dict[str, Any]] = Field(default=None)
    # Berkson's Paradox hook: cohorts deliberately excluded from the
    # stakeholder set so reports can name what's missing. Consumed by
    # the transparency post-processor at
    # ``app.services.policy.transparency_runtime`` to emit
    # survivorship/cohort-gap validity flags on ``Report.validity_flags``.
    excluded_cohorts: List[str] = Field(default_factory=list)

    # Field paths that emit a DeprecationWarning when present in raw input.
    _LEGACY_FIELD_MAP: ClassVar[Dict[str, str]] = {
        "policy_text": "seed_text",
        "policy_question": "stress_question",
    }

    # ------------------------------------------------------------------
    # Pre-validation: detect legacy keys + lift legacy run.drift_horizon_rounds
    # ------------------------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _warn_on_legacy_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for legacy, canonical in cls._LEGACY_FIELD_MAP.items():
            if legacy in data:
                warnings.warn(
                    (
                        f"Scenario field {legacy!r} is deprecated; use "
                        f"{canonical!r} instead (legacy alias will be "
                        "removed at v1.0)."
                    ),
                    DeprecationWarning,
                    stacklevel=2,
                )
        return data

    @model_validator(mode="before")
    @classmethod
    def _lift_legacy_run_drift_horizon_rounds(cls, data: Any) -> Any:
        """Accept older ``scenario.run.drift_horizon_rounds`` inputs.

        The canonical location is the Scenario root field; the legacy location
        is silently lifted so older preset JSONs and persisted state.json blobs
        continue to load. On dump only the root field is emitted because
        ``RunControls`` no longer declares the field.
        """
        if not isinstance(data, dict):
            return data
        run = data.get("run")
        if not isinstance(run, dict):
            return data
        legacy = run.pop("drift_horizon_rounds", None)
        if legacy is None:
            return data
        if "drift_horizon_rounds" not in data or data["drift_horizon_rounds"] is None:
            data["drift_horizon_rounds"] = legacy
            warnings.warn(
                "Scenario.run.drift_horizon_rounds is deprecated; the field "
                "has moved to Scenario.drift_horizon_rounds. Lifting the "
                "older value transparently.",
                DeprecationWarning,
                stacklevel=2,
            )
        return data

    # ------------------------------------------------------------------
    # Cross-field validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _scope_discriminator(self) -> "Scenario":
        if self.persona == Persona.POLICY and self.scope is None:
            raise ValueError("scope is required when persona=policy")
        if self.persona != Persona.POLICY and self.scope is not None:
            raise ValueError(
                f"scope is forbidden when persona={self.persona.value}"
            )
        return self

    @model_validator(mode="after")
    def _validate_segment_types(self) -> "Scenario":
        """Persona-aware ``AudienceSegment.type`` allowlist.

        Lookup key is ``(persona, scope)`` where scope is ``None`` for non-policy.
        """
        key = (self.persona, self.scope if self.persona == Persona.POLICY else None)
        valid_types = PERSONA_SEGMENT_TYPES.get(key)
        if valid_types is None:
            # Defensive: an unknown persona/scope combo shouldn't reach here
            # because the discriminator validator already ran.
            raise ValueError(
                f"No segment-type allowlist registered for "
                f"persona={self.persona.value} scope={self.scope}"
            )
        for idx, seg in enumerate(self.stakeholders):
            if seg.type not in valid_types:
                scope_repr = self.scope.value if self.scope else None
                raise ValueError(
                    f"stakeholders[{idx}].type={seg.type!r} not valid for "
                    f"persona={self.persona.value} scope={scope_repr}; "
                    f"allowed: {sorted(valid_types)}"
                )
        return self

    @model_validator(mode="after")
    def _enforce_scale_cap(self, info: ValidationInfo) -> "Scenario":
        """Cost guard: ensemble_n * stakeholders * drift_horizon_rounds <= cap.

        Cap is ``SCALE_CAP_POC`` unless the validation context carries
        ``allow_expensive=True`` (env override:
        ``POLICY_ALLOW_EXPENSIVE=true``), in which case it relaxes to
        ``SCALE_CAP_LARGE``. The schema does NOT read environment variables;
        callers MUST pass the context (see ``scenario_loader`` and the
        generic policy dispatch).
        """
        allow_expensive = bool((info.context or {}).get("allow_expensive", False))
        cap = SCALE_CAP_LARGE if allow_expensive else SCALE_CAP_POC
        total = (
            self.run.ensemble_n
            * len(self.stakeholders)
            * self.drift_horizon_rounds
        )
        if total > cap:
            raise ValueError(
                f"Scenario exceeds scale cap "
                f"(ensemble_n={self.run.ensemble_n} * "
                f"stakeholders={len(self.stakeholders)} * "
                f"drift_horizon_rounds={self.drift_horizon_rounds} = "
                f"{total} > {cap}; allow_expensive={allow_expensive})"
            )
        return self

    @model_validator(mode="after")
    def _counterfactual_base_exists(self) -> "Scenario":
        """A counterfactual variant's ``base_variant_id`` must exist."""
        known_ids = {v.variant_id for v in self.variants}
        for v in self.variants:
            if (
                v.kind == VariantKind.COUNTERFACTUAL
                and v.base_variant_id is not None
                and v.base_variant_id not in known_ids
            ):
                raise ValueError(
                    f"Variant {v.variant_id!r} references unknown "
                    f"base_variant_id={v.base_variant_id!r}"
                )
        return self


# ---------------------------------------------------------------------------
# Module-init SSoT invariants
# ---------------------------------------------------------------------------
# The frozensets above (_PROVENANCE_SOURCE_TYPES / _PROVENANCE_LICENSE_CLASSES /
# _DISTRIBUTION_SUPPORTS / _PRIOR_STANCES) are the canonical allowlists; the
# inline Literal[...] re-spellings on PriorProvenance / PriorDistribution must
# stay in lockstep with them. Pydantic v2 requires the literal arg list to be
# spelled inline (PEP 586 forbids star-unpack into Literal), so we enforce the
# alignment at import time -- any drift now blows up the process boot.
_assert_pairs = (
    (set(get_args(PriorProvenance.model_fields["source_type"].annotation)),
     set(_PROVENANCE_SOURCE_TYPES),
     "_PROVENANCE_SOURCE_TYPES"),
    (set(get_args(PriorProvenance.model_fields["license_class"].annotation)),
     set(_PROVENANCE_LICENSE_CLASSES),
     "_PROVENANCE_LICENSE_CLASSES"),
    (set(get_args(PriorDistribution.model_fields["support"].annotation)),
     set(_DISTRIBUTION_SUPPORTS),
     "_DISTRIBUTION_SUPPORTS"),
    (set(get_args(PriorDistribution.model_fields["stance"].annotation)),
     set(_PRIOR_STANCES),
     "_PRIOR_STANCES"),
)
for _literal_set, _frozenset_set, _name in _assert_pairs:
    if _literal_set != _frozenset_set:
        raise AssertionError(
            f"SSoT drift: schema Literal for {_name} = {_literal_set!r} but "
            f"frozenset = {_frozenset_set!r} -- update one to match the other."
        )
del _assert_pairs


# Cross-lane platform-taxonomy lockstep. The policy lane's Pydantic
# Literal[...] declarations for hypothesis tags / validity-flag kinds /
# severity / evidence-ref types MUST match the platform frozensets in
# :mod:`app.simulation_platform.guardrails.taxonomy` value-for-value.
# Editing one side without the other trips this assertion at import
# time -- the contract between the policy lane and any other lane that
# emits guardrails cannot drift silently.
_platform_lockstep_pairs = (
    (
        set(CANONICAL_HYPOTHESIS_TAGS),
        set(_PLATFORM_HYPOTHESIS_TAGS),
        "HYPOTHESIS_TAGS",
    ),
    (
        set(get_args(ValidityFlag.model_fields["kind"].annotation)),
        set(_PLATFORM_VALIDITY_FLAG_KINDS),
        "VALIDITY_FLAG_KINDS",
    ),
    (
        set(get_args(ValidityFlag.model_fields["severity"].annotation)),
        set(_PLATFORM_SEVERITY_VARIANTS),
        "SEVERITY_VARIANTS",
    ),
    (
        set(get_args(EvidenceRef.model_fields["ref_type"].annotation)),
        set(_PLATFORM_EVIDENCE_REF_KINDS),
        "EVIDENCE_REF_KINDS",
    ),
)
for _policy_set, _platform_set, _taxonomy_name in _platform_lockstep_pairs:
    if _policy_set != _platform_set:
        raise AssertionError(
            f"Platform taxonomy drift: policy schema for {_taxonomy_name} = "
            f"{sorted(_policy_set)!r} but platform frozenset = "
            f"{sorted(_platform_set)!r} -- update one to match the other "
            "(see app.simulation_platform.guardrails.taxonomy)."
        )
del _platform_lockstep_pairs


# ---------------------------------------------------------------------------
# Forward reference resolution: Report.run_metadata
# ---------------------------------------------------------------------------
#
# ``Report.run_metadata`` is annotated as the string "RunMetadata" so the
# module load order can resolve the platform metadata model without a true
# circular import. Pydantic v2's
# ``model_rebuild()`` re-evaluates the forward ref now that the symbol is
# in scope.
from .run_metadata import RunMetadata  # noqa: E402  (intentional deferred import)

Report.model_rebuild()
