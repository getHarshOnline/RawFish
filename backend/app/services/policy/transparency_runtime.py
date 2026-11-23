"""Pure transparency post-processor for the policy :class:`Report`.

Scans the validated :class:`Report` + source :class:`Scenario` and emits
a deterministic list of :class:`ValidityFlag` notes against the
9-element canonical taxonomy (heavy-tails / Monte-Carlo coverage /
survivorship-or-cohort gap / observer effect / Simpson aggregate
disagreement / mean-reversion / utility curve / confirmation bias /
selection axis). The falsifier-audit content is folded into the
confirmation-bias flag via its ``field_validation_prompt`` rather than
introducing a 10th kind.

Purity contract (mirrors the markdown renderer + prompt builder):
  * No clock reads (``datetime.now`` / ``time.time``).
  * No environment reads (``os.environ``).
  * No filesystem reads or writes.
  * No random sources.
  * No network calls.
  * No LLM client construction or invocation.
  * No Flask or web framework imports.
  * No dependency on the report-agent module.

Allowed imports (allowlist pinned by the unit tests):
  * stdlib ``typing`` / ``collections`` / ``__future__``
  * :mod:`app.policy.schema` (Report / Scenario / ValidityFlag types)

Determinism contract:
  * Same ``(report, scenario)`` -> identical list (ids + order).
  * Output sorted by ``(_KIND_ORDER.index(kind), flag_id)``.
  * ``flag_id`` is ``f"vf_{kind}_{n:02d}"`` where ``n`` is the 0-based
    emission index per kind (iteration follows scenario.stakeholders
    declared order, then executive_summary -> sections[*].claims
    declared order).

Wording posture:
  * Every canned message in :data:`_CANNED_MESSAGES` is authored so
    its banned-adjacent verbs (if any) sit in a sentence carrying a
    hedge registered in
    :data:`app.services.policy.language_guard.HEDGE_PATTERNS`
    (``may`` / ``might`` / ``could`` / ``appears`` / ``hypotheses``
    / ``under these assumptions``). The shared
    ``assert_report_render_contract`` runs after this post-processor
    so any drift in either the canned messages or the renderer's
    surrounding template fails report generation before disk write.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from app.policy.schema import (
    Claim,
    Report,
    Scenario,
    ValidityFlag,
)
from app.policy.hypothesis_tags import HypothesisTag
from app.services.policy.language_guard import BANNED_PATTERNS


# ---------------------------------------------------------------------------
# Module-level thresholds (named constants so reviewers can grep them; the
# unit tests import these symbols rather than re-spelling the literals so
# the canonical values stay in lockstep with the test expectations).
# ---------------------------------------------------------------------------

# Confirmation-bias trigger ratio: ``run.assumption_confirmation_ratio``
# >= this value flags the report (proxy for "too many claims confirm
# the stated assumptions").
T_CONF: float = 0.85

# Observer-effect severity-bump threshold against
# ``stakeholder.social_desirability_bias``.
T_OBS: float = 0.5

# Per-segment drift_params.delta threshold for the mean-reversion flag.
T_DRIFT: float = 0.4

# Min stakeholder count below which the cohort-gap flag fires.
T_COHORT: int = 2

# ensemble_n threshold for monte_carlo_coverage. A sanity-tier run
# below this count emits a coverage-gap flag whenever an aggregate-
# class claim is present (the "report" tier supplies stratified-
# bootstrap coverage at or above this value).
T_MC: int = 20

# Cohort-size ratio above which the heavy-tail flag fires (based on the
# stakeholder influence_level spread).
R_TAIL: float = 5.0

# Extreme trust_baseline bounds for the mean-reversion flag.
TRUST_LOW: float = 0.15
TRUST_HIGH: float = 0.85


# ---------------------------------------------------------------------------
# Canonical kind ordering. Matches the 9-kind taxonomy enumerated on
# the ValidityFlag schema (heavy-tails -> Monte-Carlo coverage ->
# survivorship -> observer effect -> Simpson -> mean-reversion ->
# utility -> confirmation bias -> selection axis).
# ---------------------------------------------------------------------------

_KIND_ORDER: Tuple[str, ...] = (
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


def _scrub_banned_tokens(text: str) -> str:
    """Replace every ``BANNED_PATTERNS`` match in ``text`` with ``[scrubbed]``.

    Used to sanitise raw user/scenario strings BEFORE they are folded
    into a ``ValidityFlag.field_validation_prompt`` (or any other
    rendered surface). The language guard works sentence-locally and
    pardons any sentence carrying a hedge -- so a hedged wrapper
    sentence around raw user text would let banned-token-bearing user
    text reach the rendered output unchallenged. Scrubbing at the
    source closes that hole regardless of hedge placement.

    Pure: walks the shared compiled-regex list and emits a literal
    constant for each match. Idempotent (no banned token left after
    one pass).
    """
    if not text:
        return text
    out = text
    for pattern in BANNED_PATTERNS:
        out = pattern.sub("[scrubbed]", out)
    return out


# Per (persona_key, scope_key) -> canonical "silent cohort" segment-type
# whose absence implies a survivorship/cohort gap. Keyed by the persona
# string values so the lookup never imports the Persona enum (kept
# decoupled from the discriminator enum representation).
_SILENT_COHORTS_BY_PERSONA: Dict[Tuple[str, Optional[str]], str] = {
    ("creator", None): "lurker_cohort",
    ("video", None): "skeptic_segment",
    ("product", None): "churned_user",
    ("policy", "public_policy"): "burdened_group",
    ("policy", "internal_org"): "exiting_employee",
}


# Substrings matched (case-insensitive) on per-segment
# ``distribution_assumptions`` values to detect heavy-tail declarations.
_HEAVY_TAIL_KEYWORDS: Tuple[str, ...] = (
    "pareto",
    "heavy_tail",
    "power_law",
    "lognormal",
)


# Canned per-kind message templates. Every sentence is authored so its
# banned-adjacent verbs sit in a sentence carrying a hedge registered
# in language_guard.HEDGE_PATTERNS (may / might / could / appears /
# hypotheses / under these assumptions / ...). The
# ``monte_carlo_coverage`` template carries two format placeholders
# (``{N}`` and ``{T_MC}``) that the emit helper fills in.
_CANNED_MESSAGES: Dict[str, str] = {
    "power_law_tail": (
        "Heavy-tailed distribution assumption present; the point "
        "estimate may not be the modal outcome -- needs field check."
    ),
    "monte_carlo_coverage": (
        "Aggregate claims emitted under ensemble_n={N}; this sample "
        "may under-cover the distribution and is not a confidence "
        "interval. The report tier (N>={T_MC}) could supply "
        "stratified-bootstrap coverage."
    ),
    "survivorship_or_cohort_gap": (
        "Selection frame or excluded cohorts shape the stakeholder "
        "set; cohorts not represented may behave differently -- needs "
        "field check."
    ),
    "observer_effect_info": (
        "Audiences who know they are observed, measured, or simulated "
        "about may behave differently; surfaced as a hypothesis-shaped "
        "caveat under these assumptions."
    ),
    "observer_effect_warning": (
        "One or more segments carry a high social_desirability_bias; "
        "self-reported stances may need extra caution -- needs field "
        "check."
    ),
    "simpson_aggregate_disagreement": (
        "Aggregate claim spans multiple segments without per-segment "
        "breakdown; per-segment direction may disagree with the "
        "aggregate under these assumptions."
    ),
    "mean_reversion": (
        "Extreme trust_baseline or drift delta present; stances may "
        "revert under the drift engine independent of any "
        "intervention."
    ),
    "utility_curve": (
        "Segment utility_params present; unbounded payoffs may render "
        "as hypothesis-shaped notes rather than linear value claims."
    ),
    "confirmation_bias": (
        "Most claims appear to confirm stated assumptions; "
        "un-referenced falsifiers may be listed in the field-check "
        "below."
    ),
    "selection_axis": (
        "Stakeholder set was selected on the named axes; correlations "
        "within this subpopulation may not hold in the broader "
        "audience."
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flag_id(kind: str, n: int) -> str:
    """Deterministic flag id: ``vf_{kind}_{n:02d}``."""
    return f"vf_{kind}_{n:02d}"


def _all_claims(report: Report) -> List[Claim]:
    """Iterate claims in canonical order: executive_summary, then
    sections[*].claims, then evidence_gaps. Each section iterated in
    declared order; claims within a section also in declared order."""
    out: List[Claim] = []
    out.extend(report.executive_summary)
    for sec in report.sections:
        out.extend(sec.claims)
    out.extend(report.evidence_gaps)
    return out


def _aggregate_class_claims(report: Report) -> List[Claim]:
    """Aggregate-class claims = exec-summary claims plus any
    section claim tagged ``inference``. Used by the monte_carlo_coverage
    trigger."""
    out: List[Claim] = list(report.executive_summary)
    for sec in report.sections:
        for c in sec.claims:
            if c.tag == HypothesisTag.INFERENCE:
                out.append(c)
    return out


def _heavy_tail_in_distribution(value: object) -> bool:
    """Return True iff any string value inside a
    ``distribution_assumptions`` dict matches a heavy-tail keyword
    (case-insensitive substring)."""
    if not isinstance(value, str):
        return False
    lowered = value.lower()
    return any(k in lowered for k in _HEAVY_TAIL_KEYWORDS)


# ---------------------------------------------------------------------------
# Per-kind emitters
# ---------------------------------------------------------------------------


def _emit_power_law_tail(scenario: Scenario) -> List[ValidityFlag]:
    """Heavy-tail / power-law flag (kind=``power_law_tail``).

    Trigger: any per-segment ``distribution_assumptions`` value matches
    a heavy-tail keyword, OR (with >=3 stakeholders) the
    influence_level spread ratio exceeds R_TAIL.
    """
    flags: List[ValidityFlag] = []
    n = 0
    triggered_segments: List[str] = []
    for seg in scenario.stakeholders:
        if seg.distribution_assumptions:
            for v in seg.distribution_assumptions.values():
                if _heavy_tail_in_distribution(v):
                    triggered_segments.append(seg.segment_id)
                    break
    # Cohort imbalance proxy: highest / lowest influence_level ratio.
    if len(scenario.stakeholders) >= 3:
        levels = [s.influence_level for s in scenario.stakeholders]
        lo = max(1, min(levels))
        hi = max(levels)
        if hi / lo >= R_TAIL:
            # Pseudo-segment marker; the source_fields entry below
            # surfaces the influence_level path so a reader sees why.
            triggered_segments.append("__influence_spread__")

    if not triggered_segments:
        return flags

    flags.append(
        ValidityFlag(
            flag_id=_flag_id("power_law_tail", n),
            kind="power_law_tail",
            severity="info",
            message=_CANNED_MESSAGES["power_law_tail"],
            claim_refs=[],
            segment_refs=[
                s for s in triggered_segments if s != "__influence_spread__"
            ],
            source_fields=[
                "stakeholders[*].distribution_assumptions",
                "stakeholders[*].influence_level",
            ],
            method="heuristic_v1",
            confidence="low",
            field_validation_prompt=None,
        )
    )
    return flags


def _emit_monte_carlo_coverage(
    report: Report, scenario: Scenario,
) -> List[ValidityFlag]:
    """Ensemble-coverage flag (kind=``monte_carlo_coverage``).

    Trigger: an aggregate-class claim exists AND
    ``scenario.run.ensemble_n < T_MC``. Filed once at report level;
    ``claim_refs`` enumerates the aggregate-class claim_ids.
    """
    if scenario.run.ensemble_n >= T_MC:
        return []
    aggregate_claims = _aggregate_class_claims(report)
    if not aggregate_claims:
        return []
    message = _CANNED_MESSAGES["monte_carlo_coverage"].format(
        N=scenario.run.ensemble_n, T_MC=T_MC,
    )
    return [
        ValidityFlag(
            flag_id=_flag_id("monte_carlo_coverage", 0),
            kind="monte_carlo_coverage",
            severity="info",
            message=message,
            claim_refs=[c.claim_id for c in aggregate_claims],
            segment_refs=[],
            source_fields=["run.ensemble_n"],
            method="heuristic_v1",
            confidence="medium",
            field_validation_prompt=None,
        )
    ]


def _emit_survivorship_or_cohort_gap(
    scenario: Scenario,
) -> List[ValidityFlag]:
    """Selection / cohort-gap flag (kind=``survivorship_or_cohort_gap``).

    Trigger: any of
      * scenario.selection_criteria is not None
      * scenario.excluded_cohorts non-empty
      * len(scenario.stakeholders) < T_COHORT
      * any segment.selection_frame is not None
      * the persona's canonical silent cohort is absent from the
        stakeholder set
    """
    reasons_source_fields: List[str] = []
    if scenario.selection_criteria is not None:
        reasons_source_fields.append("selection_criteria")
    if scenario.excluded_cohorts:
        reasons_source_fields.append("excluded_cohorts")
    if len(scenario.stakeholders) < T_COHORT:
        reasons_source_fields.append("stakeholders[*].type")
    if any(s.selection_frame is not None for s in scenario.stakeholders):
        reasons_source_fields.append("stakeholders[*].selection_frame")

    # Silent-cohort absence check (persona-keyed).
    persona_key = scenario.persona.value
    scope_key = scenario.scope.value if scenario.scope is not None else None
    expected_silent = _SILENT_COHORTS_BY_PERSONA.get((persona_key, scope_key))
    seg_types = {s.type for s in scenario.stakeholders}
    if expected_silent is not None and expected_silent not in seg_types:
        if "stakeholders[*].type" not in reasons_source_fields:
            reasons_source_fields.append("stakeholders[*].type")

    if not reasons_source_fields:
        return []

    return [
        ValidityFlag(
            flag_id=_flag_id("survivorship_or_cohort_gap", 0),
            kind="survivorship_or_cohort_gap",
            severity="notice",
            message=_CANNED_MESSAGES["survivorship_or_cohort_gap"],
            claim_refs=[],
            segment_refs=[],
            source_fields=reasons_source_fields,
            method="heuristic_v1",
            confidence="medium",
            field_validation_prompt=None,
        )
    ]


def _emit_observer_effect(scenario: Scenario) -> List[ValidityFlag]:
    """Observer-effect flag (kind=``observer_effect``).

    Always emitted at report level. Severity bumps to ``warning`` if
    any stakeholder carries social_desirability_bias > T_OBS,
    otherwise ``info``.
    """
    tripped = [
        s for s in scenario.stakeholders
        if s.social_desirability_bias is not None
        and s.social_desirability_bias > T_OBS
    ]
    if tripped:
        return [
            ValidityFlag(
                flag_id=_flag_id("observer_effect", 0),
                kind="observer_effect",
                severity="warning",
                message=_CANNED_MESSAGES["observer_effect_warning"],
                claim_refs=[],
                segment_refs=[s.segment_id for s in tripped],
                source_fields=["stakeholders[*].social_desirability_bias"],
                method="heuristic_v1",
                confidence="high",
                field_validation_prompt=None,
            )
        ]
    return [
        ValidityFlag(
            flag_id=_flag_id("observer_effect", 0),
            kind="observer_effect",
            severity="info",
            message=_CANNED_MESSAGES["observer_effect_info"],
            claim_refs=[],
            segment_refs=[],
            source_fields=["stakeholders[*].social_desirability_bias"],
            method="heuristic_v1",
            confidence="medium",
            field_validation_prompt=None,
        )
    ]


def _emit_simpson_aggregate_disagreement(
    report: Report, scenario: Scenario,
) -> List[ValidityFlag]:
    """Aggregate-coverage flag (kind=``simpson_aggregate_disagreement``), scoped to aggregate-class claims only.

    A claim is "aggregate-class" if its provenance tag is
    ``HypothesisTag.INFERENCE`` -- inferences synthesise across
    segments, so a missing or single-segment ``segment_refs`` on an
    inference is the genuine Simpson-paradox surface. Per-segment
    ``simulated_observation``, source-backed ``source_grounded``,
    held-belief ``assumption``, and gap ``requires_validation`` claims
    are NOT aggregate-class: a single ``segment_refs`` entry on those
    is the correct shape and must not trip the flag.

    Trigger: for each INFERENCE claim in exec_summary or
    sections[*].claims with empty or single-entry segment_refs, when
    the scenario carries >=2 stakeholders with >=2 distinct types,
    emit one flag attached to that claim_id. Severity ``notice`` for
    exec-summary claims, ``info`` for section claims.
    """
    flags: List[ValidityFlag] = []
    if len(scenario.stakeholders) < 2:
        return flags
    distinct_types = {s.type for s in scenario.stakeholders}
    if len(distinct_types) < 2:
        return flags

    def _is_aggregate_class(claim: Claim) -> bool:
        return claim.tag == HypothesisTag.INFERENCE

    n = 0
    for claim in report.executive_summary:
        if not _is_aggregate_class(claim):
            continue
        if len(claim.segment_refs) <= 1:
            flags.append(
                ValidityFlag(
                    flag_id=_flag_id("simpson_aggregate_disagreement", n),
                    kind="simpson_aggregate_disagreement",
                    severity="notice",
                    message=_CANNED_MESSAGES[
                        "simpson_aggregate_disagreement"
                    ],
                    claim_refs=[claim.claim_id],
                    segment_refs=[],
                    source_fields=[
                        "executive_summary",
                        "stakeholders",
                    ],
                    method="heuristic_v1",
                    confidence="low",
                    field_validation_prompt=None,
                )
            )
            n += 1
    for sec in report.sections:
        for claim in sec.claims:
            if not _is_aggregate_class(claim):
                continue
            if len(claim.segment_refs) <= 1:
                flags.append(
                    ValidityFlag(
                        flag_id=_flag_id(
                            "simpson_aggregate_disagreement", n,
                        ),
                        kind="simpson_aggregate_disagreement",
                        severity="info",
                        message=_CANNED_MESSAGES[
                            "simpson_aggregate_disagreement"
                        ],
                        claim_refs=[claim.claim_id],
                        segment_refs=[],
                        source_fields=[
                            "sections[*].claims",
                            "stakeholders",
                        ],
                        method="heuristic_v1",
                        confidence="low",
                        field_validation_prompt=None,
                    )
                )
                n += 1
    return flags


def _emit_mean_reversion(scenario: Scenario) -> List[ValidityFlag]:
    """Drift / trust-extremity flag (kind=``mean_reversion``).

    Trigger: trust_baseline outside [TRUST_LOW, TRUST_HIGH] (when
    present) OR any segment.drift_params.delta > T_DRIFT.
    """
    trust = scenario.trust_baseline
    trust_extreme = (
        trust is not None and (trust > TRUST_HIGH or trust < TRUST_LOW)
    )
    drift_segments = [
        s for s in scenario.stakeholders
        if s.drift_params is not None and s.drift_params.delta > T_DRIFT
    ]
    if not trust_extreme and not drift_segments:
        return []
    source_fields: List[str] = []
    if trust_extreme:
        source_fields.append("trust_baseline")
    if drift_segments:
        source_fields.append("stakeholders[*].drift_params.delta")
    return [
        ValidityFlag(
            flag_id=_flag_id("mean_reversion", 0),
            kind="mean_reversion",
            severity="notice",
            message=_CANNED_MESSAGES["mean_reversion"],
            claim_refs=[],
            segment_refs=[s.segment_id for s in drift_segments],
            source_fields=source_fields,
            method="heuristic_v1",
            confidence="medium",
            field_validation_prompt=None,
        )
    ]


def _emit_utility_curve(scenario: Scenario) -> List[ValidityFlag]:
    """Utility-curve flag (kind=``utility_curve``).

    Trigger: any segment.utility_params is not None.
    """
    triggered = [
        s for s in scenario.stakeholders if s.utility_params is not None
    ]
    if not triggered:
        return []
    return [
        ValidityFlag(
            flag_id=_flag_id("utility_curve", 0),
            kind="utility_curve",
            severity="info",
            message=_CANNED_MESSAGES["utility_curve"],
            claim_refs=[],
            segment_refs=[s.segment_id for s in triggered],
            source_fields=["stakeholders[*].utility_params"],
            method="heuristic_v1",
            confidence="medium",
            field_validation_prompt=None,
        )
    ]


def _emit_confirmation_bias(
    report: Report, scenario: Scenario,
) -> List[ValidityFlag]:
    """Confirmation-bias flag (kind=``confirmation_bias``; folds the falsifier-audit panel into the same flag's field_validation_prompt).

    Trigger: ``run.assumption_confirmation_ratio`` >= T_CONF, OR
    the count of claims referencing scenario.assumptions strictly
    exceeds the count referencing scenario.failure_modes_to_watch
    AND no claim carries an ``H-OVERCONFIDENCE`` expected_risk_tag.

    The optional ``field_validation_prompt`` lists scenario.assumptions
    / failure_modes_to_watch / stakeholders[*].what_changes_their_mind
    entries that are NOT referenced by any field_validation_questions
    substring -- this satisfies the falsifier-audit content contract
    without introducing a 10th kind.
    """
    ratio = scenario.run.assumption_confirmation_ratio
    ratio_triggered = ratio is not None and ratio >= T_CONF

    # Co-occurrence proxy: count claims whose text references any
    # assumption / failure-mode substring. This is a cheap heuristic
    # (substring containment), not semantic NLI.
    def _claim_refs_substr(claim: Claim, hay: List[str]) -> bool:
        lowered = claim.text.lower()
        return any(h.lower() in lowered for h in hay if h)

    all_claims = _all_claims(report)
    confirm_count = sum(
        1 for c in all_claims if _claim_refs_substr(c, scenario.assumptions)
    )
    falsify_count = sum(
        1 for c in all_claims
        if _claim_refs_substr(c, scenario.failure_modes_to_watch)
    )
    overconf_tagged = any(
        "H-OVERCONFIDENCE" in c.expected_risk_tags for c in all_claims
    )
    proxy_triggered = (
        confirm_count > falsify_count and not overconf_tagged
    )

    if not (ratio_triggered or proxy_triggered):
        return []

    # Falsifier-audit panel: which assumptions / failure_modes /
    # what_changes_their_mind entries are NOT referenced by any
    # field_validation_question substring?
    fvq_lower = [q.lower() for q in report.field_validation_questions]

    def _unreferenced(entries: List[str]) -> List[str]:
        out = []
        for e in entries:
            el = (e or "").lower().strip()
            if not el:
                continue
            if not any(el in q for q in fvq_lower):
                out.append(e)
        return out

    unref_assumptions = _unreferenced(list(scenario.assumptions))
    unref_failures = _unreferenced(list(scenario.failure_modes_to_watch))
    unref_changes: List[str] = []
    for seg in scenario.stakeholders:
        unref_changes.extend(_unreferenced(list(seg.what_changes_their_mind)))

    field_validation_prompt: Optional[str] = None
    if unref_assumptions or unref_failures or unref_changes:
        # Structural sanitisation, NOT hedge-wrapping. The previous
        # version concatenated raw user/scenario text into a single
        # sentence carrying a hedge prefix. Because the language guard
        # is sentence-local and pardons any sentence that carries a
        # hedge, raw user text containing a banned-token stem would
        # render through the guard unchallenged on the strength of the
        # prefix hedge alone. Fix: replace banned-token matches in
        # user text with ``[scrubbed]`` BEFORE the prompt is built, so
        # the rendered string is contract-safe regardless of hedge
        # placement (see ``_scrub_banned_tokens`` above).
        bits: List[str] = []
        if unref_assumptions:
            bits.append(
                "assumptions: "
                + "; ".join(_scrub_banned_tokens(s) for s in unref_assumptions[:3])
            )
        if unref_failures:
            bits.append(
                "failure modes: "
                + "; ".join(_scrub_banned_tokens(s) for s in unref_failures[:3])
            )
        if unref_changes:
            bits.append(
                "what could change their mind: "
                + "; ".join(_scrub_banned_tokens(s) for s in unref_changes[:3])
            )
        joined = " | ".join(bits)
        prompt = "Un-referenced falsifiers may need field check -- " + joined
        if len(prompt) > 240:
            prompt = prompt[:237] + "..."
        field_validation_prompt = prompt

    return [
        ValidityFlag(
            flag_id=_flag_id("confirmation_bias", 0),
            kind="confirmation_bias",
            severity="notice",
            message=_CANNED_MESSAGES["confirmation_bias"],
            claim_refs=[],
            segment_refs=[],
            source_fields=[
                "run.assumption_confirmation_ratio",
                "assumptions",
                "failure_modes_to_watch",
                "stakeholders[*].what_changes_their_mind",
            ],
            method="heuristic_v1",
            confidence="low",
            field_validation_prompt=field_validation_prompt,
        )
    ]


def _emit_selection_axis(
    report: Report, scenario: Scenario,
) -> List[ValidityFlag]:
    """Selection-axis (Berkson) flag (kind=``selection_axis``).

    Trigger: ``scenario.selection_criteria['axes']`` is a non-empty
    list. Severity ``notice`` when ``len(axes) >= 2``, else ``info``.
    The single emitted flag carries every exec-summary claim_id in
    ``claim_refs`` (one flag, list of refs).
    """
    if scenario.selection_criteria is None:
        return []
    axes_val = scenario.selection_criteria.get("axes")
    if not isinstance(axes_val, list) or len(axes_val) < 1:
        return []
    severity = "notice" if len(axes_val) >= 2 else "info"
    return [
        ValidityFlag(
            flag_id=_flag_id("selection_axis", 0),
            kind="selection_axis",
            severity=severity,
            message=_CANNED_MESSAGES["selection_axis"],
            claim_refs=[c.claim_id for c in report.executive_summary],
            segment_refs=[],
            source_fields=["selection_criteria.axes"],
            method="heuristic_v1",
            confidence="medium",
            field_validation_prompt=None,
        )
    ]


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def derive_validity_flags(
    report: Report, scenario: Scenario,
) -> List[ValidityFlag]:
    """Pure heuristic scan; deterministic ordering.

    Iterates every per-kind emitter in canonical order
    (:data:`_KIND_ORDER`), concatenates the emitted flags, and returns
    the final list sorted by ``(kind_index, flag_id)`` so even an
    out-of-order append path collapses to the canonical order.

    The two arguments are inputs only -- the caller's :class:`Report`
    and :class:`Scenario` are NOT mutated.
    """
    raw: List[ValidityFlag] = []
    raw.extend(_emit_power_law_tail(scenario))
    raw.extend(_emit_monte_carlo_coverage(report, scenario))
    raw.extend(_emit_survivorship_or_cohort_gap(scenario))
    raw.extend(_emit_observer_effect(scenario))
    raw.extend(_emit_simpson_aggregate_disagreement(report, scenario))
    raw.extend(_emit_mean_reversion(scenario))
    raw.extend(_emit_utility_curve(scenario))
    raw.extend(_emit_confirmation_bias(report, scenario))
    raw.extend(_emit_selection_axis(report, scenario))
    # Final defensive sort: (kind_index, flag_id) makes the order
    # invariant to any future re-ordering of the emitter calls above.
    kind_index: Dict[str, int] = {k: i for i, k in enumerate(_KIND_ORDER)}
    raw.sort(key=lambda f: (kind_index[f.kind], f.flag_id))
    return raw


def apply_transparency_flags(report: Report, scenario: Scenario) -> Report:
    """Return a NEW :class:`Report` with ``validity_flags`` populated AND
    re-validated.

    Re-validation is required even though the caller already validated
    the input Report: the validity-flag hygiene validator on
    :class:`Report` (unique flag_id, canonical kind ordering,
    non-empty + non-dangling refs) only fires when the flags field is
    materialised, which is right here. A naive ``model_copy(update=...)``
    skips post-validators by design and would let a future drift in
    ``derive_validity_flags`` produce a generate-time 200 artifact
    that only fails later on the read path.

    Implementation: dump the original report to dict (already a
    validated round-trippable shape), overwrite ``validity_flags``
    with the freshly-derived list dumped to dict form, then
    :func:`Report.model_validate` the merged payload. The cost is one
    Pydantic round-trip on the schema graph -- negligible relative to
    the generator's LLM call -- and the gain is that hygiene drift
    fails LOUDLY at generate-time, not silently at read-time.

    The input ``report`` and ``scenario`` are NOT mutated.
    """
    flags = derive_validity_flags(report, scenario)
    payload = report.model_dump(mode="python")
    payload["validity_flags"] = [f.model_dump(mode="python") for f in flags]
    return Report.model_validate(payload)


__all__ = [
    "derive_validity_flags",
    "apply_transparency_flags",
    "T_CONF",
    "T_OBS",
    "T_DRIFT",
    "T_COHORT",
    "T_MC",
    "R_TAIL",
    "TRUST_LOW",
    "TRUST_HIGH",
]
