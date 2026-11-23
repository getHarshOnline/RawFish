"""Co-occurrence driver attribution (``cooccurrence_v1``).

Per-claim heuristic: given a :class:`SensitivityReport` and a
:class:`Claim`, attribute each contributing factor family to one of the
four existing :class:`app.policy.schema.DriverHint.kind` literals
(``segment | prior | drift | assumption``) with a contribution share
proportional to the factor's ``abs_swing`` in the report.

Heuristic, NOT causal. The ``attribution_confidence`` field is pinned
to ``"low"`` because the underlying OAT swings come from the
deterministic surrogate kernel (``static_kernel_v1`` in
:mod:`.oat_runner`); the epistemic floor of the upstream surrogate
flows through to the downstream attribution.

Per-claim contribution sum invariant: hints for a single claim sum to
~1.0 +/- 0.05. When the raw share sum drifts outside that band the
contributions are silently re-normalised and a ``logger.warning`` is
emitted; no :class:`ValidityFlag` is added (the 9-element
``ValidityFlag.kind`` Literal is closed by design).

Pure module: no clock, no env, no filesystem, no random, no network,
no LLM.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ...utils.logger import get_logger
from ..schema import Claim, DriverHint
from .oat_runner import SensitivityReport


logger = get_logger("rawfish.policy.driver_attribution")


# Tolerance band for the per-claim contribution sum invariant. The
# DriverHint module docstring pins ~1.0 +/- 0.05; this constant keeps
# the band in one place.
_CONTRIBUTION_TOLERANCE: float = 0.05


# Map each OAT factor family to one of the four existing
# :class:`DriverHint.kind` Literal values. The mapping is the wire
# contract for ``cooccurrence_v1`` -- adding a new factor family
# requires extending this table AND ensuring the chosen kind is one of
# the 4 closed values (extending the kind taxonomy is a schema-level
# breaking change).
_FACTOR_TO_KIND: Dict[str, str] = {
    "segment_weights": "segment",
    "persona_matrix.row_weights": "segment",
    "trust_priors": "prior",
    "sentiment_priors": "prior",
    "drift_rules.magnitude": "drift",
    "scenario_assumptions.platform": "assumption",
    "llm_temperature": "assumption",
}


def _swing_for(factor: str, factors: List[Any]) -> float:
    """Look up ``factor.abs_swing`` from a factor-summary list.

    Used by the intersection-weighted attributor's structural floor
    when the segment intersection is zero across the board; we fall
    back to the per-factor global swing so the renderer still sees
    non-empty weights. Lazy lookup keeps the call site readable.
    """
    for fs in factors:
        if fs.factor == factor:
            return fs.abs_swing
    return 0.0


def _resolve_ref(
    factor: str, kind: str, claim: Claim, scenario: Any,
) -> str:
    """Synthesise the ``DriverHint.ref`` value for a factor family.

    The schema requires ``ref`` to be non-empty. The reference shape
    depends on ``kind``:

    * ``segment`` -> the first segment_ref on the claim, falling back
      to ``"all"`` when the claim names no segments (a valid case for
      aggregate-only claims).
    * ``prior``   -> synthesised handle ``prior_<factor>``.
    * ``drift``   -> ``drift_params.magnitude`` (matches the existing
      drift-kind ref convention on the DriverHint module docstring).
    * ``assumption`` -> ``assumption_0`` if the scenario carries any
      assumptions; otherwise ``"assumption_default"`` (defensive --
      the schema requires non-empty ``assumptions`` so production
      Scenarios always have ``assumption_0``).
    """
    if kind == "segment":
        if claim.segment_refs:
            return claim.segment_refs[0]
        return "all"
    if kind == "prior":
        return f"prior_{factor}"
    if kind == "drift":
        return "drift_params.magnitude"
    if kind == "assumption":
        assumptions = getattr(scenario, "assumptions", None)
        if assumptions:
            return "assumption_0"
        return "assumption_default"
    # Defensive: any other kind is a programming error -- the
    # _FACTOR_TO_KIND table is the only producer.
    raise ValueError(f"unsupported driver-hint kind: {kind!r}")


def _normalise_contributions(
    raw: List[Tuple[str, str, float]], claim_id: str,
) -> List[Tuple[str, str, float]]:
    """Re-normalise contributions when the raw sum drifts out of band.

    Returns the (factor, ref, normalised_contribution) tuples. Silent
    no-op when the raw sum is already within ``_CONTRIBUTION_TOLERANCE``
    of 1.0. Emits a ``logger.warning`` when normalisation actually
    fires; does NOT emit a :class:`ValidityFlag` (the kind Literal is
    closed by design -- adding a 10th kind for normalisation noise
    would be a schema-level breaking change for a transient logging
    concern).
    """
    if not raw:
        return raw
    raw_sum = sum(item[2] for item in raw)
    if raw_sum <= 0.0:
        # All-zero swings -- distribute uniformly so the renderer sees
        # a usable contribution column. This keeps the schema happy
        # (each contribution >= 0) without inventing a non-zero
        # signal from a no-signal sensitivity report.
        uniform = 1.0 / len(raw)
        return [(f, r, uniform) for (f, r, _) in raw]
    if abs(raw_sum - 1.0) <= _CONTRIBUTION_TOLERANCE:
        return raw
    logger.warning(
        "driver_hints normalised for claim=%s: pre-sum=%.4f post-sum=1.0000",
        claim_id, raw_sum,
    )
    return [(f, r, c / raw_sum) for (f, r, c) in raw]


def attribute_cooccurrence_v1(
    claim: Claim,
    scenario: Any,
    sensitivity_report: SensitivityReport,
    report: Optional[Any] = None,
) -> List[DriverHint]:
    """Attribute a claim's drivers via co-occurrence with OAT swings.

    Method label: ``cooccurrence_v1``. Confidence: ``low`` (always, at
    POC, because the upstream OAT runner uses the static surrogate
    kernel; the epistemic floor of the kernel flows through to every
    hint produced here).

    Math (intersection-weighted):
      For each factor ``f`` listing this claim in ``affected_claims``:
          contribution(f) ∝ |claim.segment_refs ∩ factor_segments(f)|
                            × f.abs_swing
      where ``factor_segments(f)`` is the union of ``segment_refs``
      across every claim ``f`` affects (resolved from ``report`` when
      supplied). When the intersection is zero for every claim/factor
      pair (e.g. the claim has empty ``segment_refs``), the math
      collapses to global abs_swing weighting so the output stays
      non-empty -- the fallback is a structural floor, not a bug.
      Contributions are normalised so the per-claim sum is ~1.0.

    Filter:
      * ``sensitivity_report.affected_claims_populated == True``
        AND no factor lists this claim_id -> return ``[]`` (truthful
        zero-hint when the populator ran but matched nothing).
      * ``affected_claims_populated == False`` (legacy report,
        populator never ran) -> fall back to every factor so a
        pre-populator artifact still surfaces non-empty hints.

    Returns an empty list when the sensitivity report has no factors
    (defensive: a no-op upstream produces a no-op downstream).
    """
    if not sensitivity_report.factors:
        return []

    if sensitivity_report.affected_claims_populated:
        # Populator ran. ONLY factors that list this claim contribute;
        # zero matches => truthful empty return (no global fallback).
        scoped_factors = [
            fs for fs in sensitivity_report.factors
            if claim.claim_id in fs.affected_claims
        ]
        if not scoped_factors:
            return []
    else:
        # Legacy report -- populator never ran. Surface every factor
        # (the older "global ranking for every claim" behaviour) so a
        # pre-populator artifact still produces non-empty hints.
        scoped_factors = list(sensitivity_report.factors)

    # Intersection-weighted contribution math.
    # ``factor_segments`` is the union of segment_refs across every
    # claim a factor affects -- resolvable only when ``report`` is
    # supplied. When omitted (or when a factor's affected_claims is
    # empty) we fall back to weighting by ``f.abs_swing`` alone.
    factor_segments_map: dict[str, set[str]] = {}
    if report is not None and sensitivity_report.affected_claims_populated:
        # Build claim_id -> segment_refs index from the report.
        id_to_segments: dict[str, set[str]] = {}
        sources = [report.executive_summary]
        for section in getattr(report, "sections", []):
            sources.append(section.claims)
        sources.append(getattr(report, "evidence_gaps", []))
        for src in sources:
            for c in src:
                id_to_segments[c.claim_id] = set(c.segment_refs or [])
        for fs in scoped_factors:
            seg_union: set[str] = set()
            for cid in fs.affected_claims:
                seg_union |= id_to_segments.get(cid, set())
            factor_segments_map[fs.factor] = seg_union

    target_segments = set(claim.segment_refs or [])

    # Build the raw (factor, ref, contribution_weight) tuples.
    raw: List[Tuple[str, str, float]] = []
    any_intersection_positive = False
    for factor_summary in scoped_factors:
        factor = factor_summary.factor
        kind = _FACTOR_TO_KIND.get(factor)
        if kind is None:
            logger.warning(
                "attribute_cooccurrence_v1: factor=%s missing from "
                "_FACTOR_TO_KIND table; skipping", factor,
            )
            continue
        ref = _resolve_ref(factor, kind, claim, scenario)
        # Intersection-weighted contribution: proportional to the
        # intersection size between this claim's segment_refs and the
        # factor's affected-segment union, weighted by the factor's
        # global abs_swing. When the intersection is zero (or
        # unresolvable -- no report supplied), we record 0.0 here and
        # fall back to global-abs_swing weighting at the end.
        factor_segs = factor_segments_map.get(factor, set())
        intersection_size = len(target_segments & factor_segs)
        weight = intersection_size * factor_summary.abs_swing
        if intersection_size > 0:
            any_intersection_positive = True
        raw.append((factor, ref, weight))

    if not any_intersection_positive:
        # Structural floor: the intersection math produced no signal
        # (e.g. claim has no segment_refs, or no factor's claims share
        # segments with target). Fall back to global abs_swing weights
        # so the output is non-empty and the renderer still surfaces
        # a heuristic driver list -- the disclaimer makes the
        # low-confidence floor explicit.
        raw = [
            (factor, ref, _swing_for(factor, scoped_factors))
            for (factor, ref, _w) in raw
        ]

    if not raw:
        return []

    # Drop zero-swing entries before normalisation so the renderer does
    # not surface no-signal hints. If EVERY swing is zero we keep the
    # full list and let the normaliser distribute uniformly (preserves
    # the wire contract: a non-empty sensitivity_report yields a
    # non-empty hint list).
    non_zero = [item for item in raw if item[2] > 0.0]
    candidates = non_zero if non_zero else raw

    normalised = _normalise_contributions(candidates, claim.claim_id)

    hints: List[DriverHint] = []
    seen: set[Tuple[str, str]] = set()
    for factor, ref, contribution in normalised:
        kind = _FACTOR_TO_KIND[factor]
        # Deduplicate on (kind, ref) so two factors mapped to the same
        # segment ref do not surface twice. The contributions of the
        # collapsed entries are summed.
        key = (kind, ref)
        if key in seen:
            # Find the prior hint and accumulate.
            for existing in hints:
                if existing.kind == kind and existing.ref == ref:
                    accumulated = existing.contribution + contribution
                    # Clamp to [0, 1] -- a sum drifting above 1.0 means
                    # the upstream normalisation rounded; the schema
                    # bound is hard so we cap.
                    new_contribution = min(1.0, accumulated)
                    hints[hints.index(existing)] = DriverHint(
                        kind=existing.kind,  # type: ignore[arg-type]
                        ref=existing.ref,
                        contribution=new_contribution,
                        method=existing.method,  # type: ignore[arg-type]
                        attribution_confidence=existing.attribution_confidence,
                        attribution_disclaimer=existing.attribution_disclaimer,
                    )
                    break
            continue
        seen.add(key)
        hints.append(
            DriverHint(
                kind=kind,  # type: ignore[arg-type]
                ref=ref,
                # Clamp to schema's [0, 1] bound; defensive against
                # floating-point overshoot.
                contribution=min(1.0, max(0.0, contribution)),
                method="cooccurrence_v1",
                attribution_confidence="low",
            )
        )
    return hints


def _populate_affected_claims_structured(
    sensitivity_report: SensitivityReport,
    report: Any,
) -> Tuple[SensitivityReport, List[str]]:
    """Structured factor-to-claim attribution via ``Claim.driver_hints``.

    Each claim already carries a structured attribution list:
    :class:`DriverHint` instances with ``kind in {segment, prior,
    drift, assumption}`` and ``ref`` pointing at the structured id
    (segment_id / prior_id / drift_id / assumption_id) that drives
    the claim. The structured attribution walks those refs and links
    a claim to a factor when the factor's name appears in the
    claim's driver-hint refs. No substring matching, no token
    splitting -- the link is an EXACT id match between the
    sensitivity factor's id and a structured ref the claim
    already declared.

    Returns ``(updated_report, fallback_factor_ids)`` where
    ``fallback_factor_ids`` lists the factors that received NO
    structured links (so the caller's substring fallback runs only
    for those, not for every factor). A factor that gets at least
    one structured link is considered fully attributed at
    confidence ``structured_v1`` and the substring fallback skips
    it -- mixing structured and substring evidence for the same
    factor would silently bias the confidence judgement.
    """
    if not sensitivity_report.factors:
        return sensitivity_report, []

    # Walk every claim location and index (claim_id, set-of-driver-refs).
    # The ref set is built lowercase so factor-name comparison can be
    # case-insensitive without per-pair str.lower().
    claim_refs: List[Tuple[str, set]] = []
    sources = [report.executive_summary]
    for section in report.sections:
        sources.append(section.claims)
    sources.append(report.evidence_gaps)
    for src in sources:
        for c in src:
            refs = {
                (h.ref or "").lower()
                for h in getattr(c, "driver_hints", [])
                if getattr(h, "ref", None)
            }
            if refs:
                claim_refs.append((c.claim_id, refs))

    fallback: List[str] = []
    new_factors = []
    for fs in sensitivity_report.factors:
        factor_id = fs.factor.lower()
        affected = [
            cid for cid, refs in claim_refs if factor_id in refs
        ]
        if not affected:
            fallback.append(fs.factor)
            # Leave ``affected_claims_method`` unset on factors that
            # got NO structured link -- the substring fallback pass
            # below will set it explicitly (either ``substring_fallback_v1``
            # if it matches, or ``no_match_v1`` if it doesn't).
            new_factors.append(fs.model_copy(update={"affected_claims": affected}))
        else:
            new_factors.append(fs.model_copy(update={
                "affected_claims": affected,
                "affected_claims_method": "structured_v1",
            }))
    return (
        sensitivity_report.model_copy(update={"factors": new_factors}),
        fallback,
    )


def _populate_affected_claims_substring(
    sensitivity_report: SensitivityReport,
    report: Any,
    factor_ids: List[str],
) -> SensitivityReport:
    """Substring-match fallback. Runs ONLY on the named factor ids
    (the structured pass already attributed the others).

    Confidence: ``substring_fallback_v1`` -- explicitly low. Mixing
    factor names with claim text via lowercase substring overlap is
    deliberately weak: it's the last-resort heuristic for factors a
    claim's structured driver hints did NOT name. The structured
    pass is the primary; this exists so legacy reports (no driver
    hints attached) still get some attribution rather than zero.

    Token rules unchanged from the historical implementation
    (factor name split on ``_`` / ``.``, tokens >= 3 chars; risk
    tag tokens stripped of ``H-`` prefix). The collision tests
    pin: a 3-character factor name like ``rto`` correctly matches
    its tokens without false-matching ``ratio`` etc.
    """
    if not factor_ids:
        return sensitivity_report
    fallback_set = {fid for fid in factor_ids}

    claim_index: List[Tuple[str, str, List[str]]] = []
    sources = [report.executive_summary]
    for section in report.sections:
        sources.append(section.claims)
    sources.append(report.evidence_gaps)
    for src in sources:
        for c in src:
            risk_tokens = [
                t.lower().replace("h-", "").replace("-", "_")
                for t in c.expected_risk_tags
            ]
            claim_index.append((c.claim_id, c.text.lower(), risk_tokens))

    def _factor_token_candidates(factor: str) -> List[str]:
        """Candidate token strings the fallback considers for a factor.

        Includes both:
          * the full lowercased factor id (so ``s_critics`` matches a
            text token ``s_critics`` verbatim), AND
          * the split-on-``_/.`` tokens of length >= 3 (so a factor
            like ``segment.fans`` matches a text mention of just
            ``fans``).
        """
        lowered = factor.lower()
        candidates = [lowered]
        for tok in lowered.replace(".", "_").split("_"):
            if len(tok) >= 3 and tok != lowered:
                candidates.append(tok)
        return candidates

    new_factors = []
    for fs in sensitivity_report.factors:
        if fs.factor not in fallback_set:
            # Factor was already attributed structurally; keep its
            # structured affected_claims list + method unchanged.
            new_factors.append(fs)
            continue
        # Substring fallback for this factor only.
        tokens = _factor_token_candidates(fs.factor)
        affected: List[str] = []
        for claim_id, text_low, risk_tokens in claim_index:
            # Word-boundary aware: token must appear as a discrete
            # token in the lowercased text (not as a substring of
            # an unrelated longer word). The previous unconditional
            # ``t in text_low`` falsely matched "rto" inside "ratio"
            # and similar collisions.
            text_tokens = set(re.findall(r"[a-z0-9_]+", text_low))
            matched = any(t in text_tokens for t in tokens)
            if not matched:
                for risk in risk_tokens:
                    if any(t == risk for t in tokens):
                        matched = True
                        break
            if matched:
                affected.append(claim_id)
        # Method discriminator: substring fallback fired AND matched
        # at least one claim -> substring_fallback_v1; substring
        # fallback fired and matched ZERO claims -> no_match_v1
        # (the populator ran, but no claim was linkable by either
        # tier). Both cases are explicitly DIFFERENT from a legacy
        # report with ``affected_claims_method=None``.
        method = "substring_fallback_v1" if affected else "no_match_v1"
        new_factors.append(fs.model_copy(update={
            "affected_claims": affected,
            "affected_claims_method": method,
        }))
    return sensitivity_report.model_copy(update={"factors": new_factors})


def populate_affected_claims(
    sensitivity_report: SensitivityReport,
    report: Any,
) -> SensitivityReport:
    """Return a copy of ``sensitivity_report`` with per-factor
    ``affected_claims`` populated.

    **Primary path: structured attribution.** Each claim's
    ``driver_hints`` already names the structured ids (segment id,
    prior id, drift id, assumption id) that drive the claim. A
    factor is "affected by" a claim iff the factor's id appears as
    an exact (case-insensitive) match in the claim's driver-hint
    refs. No substring overlap, no token splitting -- the link is
    structural.

    **Fallback path: substring matching (``substring_fallback_v1``).**
    Runs ONLY on factors the structured pass did NOT attribute.
    Confidence is explicitly low; the fallback exists so legacy
    reports (no driver hints attached) still get some attribution
    rather than zero. The fallback uses word-boundary aware token
    matching (not raw substring) to avoid collisions like
    ``rto`` matching inside ``ratio``.

    Walks every claim location in the Report (``executive_summary``,
    ``sections[*].claims``, ``evidence_gaps``). Pure: no I/O, no
    clock, no env.
    """
    if not sensitivity_report.factors:
        return sensitivity_report

    # 1) Structured pass first.
    structured, fallback_ids = _populate_affected_claims_structured(
        sensitivity_report, report,
    )

    # 2) Substring fallback ONLY for factors the structured pass
    #    could not attribute. This keeps the structured confidence
    #    unmixed -- a factor is either structurally linked or
    #    substring-linked, never both.
    final = _populate_affected_claims_substring(
        structured, report, fallback_ids,
    )

    # 3) Mark the report so consumers can distinguish "populator
    #    ran" from "legacy report with empty per-factor lists".
    return final.model_copy(update={"affected_claims_populated": True})


__all__ = [
    "attribute_cooccurrence_v1",
    "populate_affected_claims",
]
