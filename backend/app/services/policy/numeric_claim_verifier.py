"""Numeric / comparative claim verifier.

Parses numeric and comparative claims out of report prose and
verifies them against the deterministic artifacts the runtime owns
(``SensitivityReport.factors`` + ``SensitivityReport.ranking`` +
per-claim ``driver_hints``). Contradictions and unsupported claims
are returned as a verdict list; a caller (the report generator's
post-validate step) can demote affected claims to
``requires_validation`` before persist.

**Design intent:** the LLM does not own statistics. Whenever its
prose puts a number on a claim ("a 12% lift", "a 30% drop",
"3x increase"), the deterministic sensitivity / driver artifacts
are the source of truth. Whenever its prose puts a comparative
("the top driver", "the largest factor", "more sensitive than X"),
the deterministic ``ranking`` is the source of truth.

**Confidence:** the parser is conservative. False positives matter
more than recall here: a claim that does NOT contain a recognisable
numeric / comparative pattern is reported as
:data:`Verdict.NOT_APPLICABLE` (the verifier has no opinion); a
demoted-by-mistake claim is more damaging than a missed claim
because the report would lose a real finding. The parser only
matches against:

  * percent expressions (e.g. ``12%``, ``12.5%``)
  * ratio expressions (e.g. ``3x``, ``2x``)
  * comparative superlatives mapped to a known dimension
    (``top driver`` / ``most sensitive`` / ``largest swing``)

It does NOT attempt to parse free-form "we see a meaningful change
in X" — that surface belongs to the predictive-overclaim corpus
(language guard).

**Pure module:** no clock, no env, no I/O, no LLM. Deterministic
for a given (claim list, sensitivity_report).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


# ----------------------------------------------------------------------
# Verdict shapes
# ----------------------------------------------------------------------


class Verdict(str, Enum):
    """Per-claim verifier outcome.

    Values:
      * ``SUPPORTED`` -- the numeric / comparative claim agrees with the
        deterministic artifact within tolerance.
      * ``UNSUPPORTED`` -- the claim makes a verifiable assertion but
        the deterministic artifact does NOT contain a matching value
        (no way to ground it). Caller's choice whether to demote.
      * ``CONTRADICTED`` -- the claim names a value that disagrees with
        the artifact beyond tolerance (e.g. claims "30%" but the
        artifact reports swing magnitude of 12%). Caller SHOULD
        demote.
      * ``NOT_APPLICABLE`` -- the claim text contains no recognisable
        numeric / comparative assertion the verifier knows how to
        check. The verifier has no opinion.
      * ``NOT_VERIFIABLE`` -- the verifier WOULD have checked but the
        deterministic artifact is missing or empty (e.g. sensitivity
        was never run). Caller MUST NOT treat absence as confirmation.
    """

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    NOT_APPLICABLE = "not_applicable"
    NOT_VERIFIABLE = "not_verifiable"


@dataclass(frozen=True)
class NumericClaimVerdict:
    """One claim's verdict + the structured evidence the verifier used.

    Fields:
      * ``claim_id`` -- the claim being verified.
      * ``verdict`` -- :class:`Verdict` member.
      * ``parsed`` -- short description of what the parser extracted
        (e.g. ``"percent=30.0"``, ``"superlative=top_driver"``).
        Empty when verdict is NOT_APPLICABLE.
      * ``reason`` -- one-line operator-readable explanation of the
        verdict. Always set; the caller threads it into the demotion
        warning text so an operator can triage without reverse-
        engineering the parser.
    """

    claim_id: str
    verdict: Verdict
    parsed: str
    reason: str


# ----------------------------------------------------------------------
# Pure parsing primitives
# ----------------------------------------------------------------------


# A percent or ratio expression. We keep the regex conservative on
# purpose:
#   * percent: 1-3 leading digits, optional decimal, '%' sign.
#   * ratio:   integer + 'x' (immediately following, no space) — covers
#     "3x", "10x"; NOT "x3" (which is rare in natural prose).
_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_RATIO_RE = re.compile(r"\b(\d{1,3})x\b", re.IGNORECASE)

# Superlative phrases mapped to a structured comparator the verifier
# understands. The right-hand value names the artifact field the
# verifier consults.
_SUPERLATIVE_PATTERNS = (
    (re.compile(r"\b(top|leading|primary)\s+driver\b", re.IGNORECASE),
     "top_driver"),
    (re.compile(r"\b(most|highest)\s+(sensitive|influential)\b", re.IGNORECASE),
     "top_driver"),
    (re.compile(r"\b(largest|biggest|greatest)\s+(swing|factor|impact)\b", re.IGNORECASE),
     "top_driver"),
)


# Tolerance band when matching a claim's percent against an artifact
# value. The artifact values are float swing magnitudes; LLM prose
# typically rounds to a whole percent, so a 0.05 absolute tolerance
# (i.e. 5 percentage points either way) catches "12% swing" claims
# against an artifact value of 0.10 (10%) without false alarms.
_PERCENT_ABS_TOLERANCE: float = 0.05


# ----------------------------------------------------------------------
# Verification API
# ----------------------------------------------------------------------


def verify_claims(
    claims: Iterable[Any],
    sensitivity_report: Optional[Any],
) -> List[NumericClaimVerdict]:
    """Verify each claim's numeric / comparative content.

    Args:
        claims: an iterable of :class:`Claim` instances (each
            exposing ``claim_id`` and ``text``; optionally a
            ``driver_hints`` list and / or an ``evidence_refs``
            list — used to NARROW the per-claim factor lookup).
        sensitivity_report: a :class:`SensitivityReport` (or any
            duck-typed object exposing ``factors`` + ``ranking``) OR
            ``None`` when the sensitivity sweep was not run for this
            simulation. Missing report does NOT short-circuit -- the
            verifier still walks every claim and emits
            ``NOT_VERIFIABLE`` for the ones that would need it.

    **Narrowing contract:** a percent or comparative claim is only
    SUPPORTED when it agrees with a factor that the claim itself is
    LINKED to. Links come from (in priority order):

      1. ``factor.affected_claims`` -- the structured attribution
         post-processor's per-factor list. The verifier reads this
         only for factors with
         ``affected_claims_method == "structured_v1"`` (high-
         confidence link via the claim's own ``driver_hints[*].ref``
         exact-match). Substring-fallback links are excluded by
         construction because token-collision attribution is too
         weak to bless a numeric claim as SUPPORTED.
      2. ``claim.driver_hints[*].ref`` -- structured refs the claim
         itself declares. If any factor id matches a driver-hint
         ref, the verifier checks ONLY those factors.
      3. ``claim.evidence_refs[*].ref_id`` -- evidence pointers that
         name a factor id directly.

    Link sources are combined (not exclusive): a claim linked to
    factor A via ``affected_claims`` AND factor B via
    ``driver_hints`` is checked against BOTH factors. Duplicates
    (the same factor in multiple sources) are de-duped.

    If none of the three link a factor to this claim, a percent /
    superlative match falls back to a GLOBAL scan but the result is
    UNSUPPORTED (advisory: "the value appears in the artifact, but
    not for THIS claim"), never SUPPORTED. The "LLM does not own
    statistics" promise requires the claim-link narrowing -- a
    global numeric match is too weak to bless a claim.
    """
    verdicts: List[NumericClaimVerdict] = []
    factor_lookup = _build_factor_lookup(sensitivity_report)
    factor_index = _build_factor_index(sensitivity_report)
    top_factor = _build_top_factor(sensitivity_report)
    # Pre-compute the inverse index ``{claim_id: [(factor_id, swing)]}``
    # from ``factor.affected_claims`` (priority 1 link source). Only
    # factors whose ``affected_claims_method == "structured_v1"``
    # qualify a claim for SUPPORTED -- substring_fallback_v1 stays
    # advisory regardless of how many claim_ids it surfaced, because
    # the substring path is explicitly low-confidence (a numeric
    # claim grounded only by a token-collision link would silently
    # bless arbitrary prose).
    affected_index = _build_structured_affected_index(sensitivity_report)
    for claim in claims:
        linked_swings = _linked_factor_swings(
            claim, factor_lookup, affected_index,
        )
        verdict = _verify_one(
            claim, factor_index, top_factor, linked_swings,
        )
        verdicts.append(verdict)
    return verdicts


def _verify_one(
    claim: Any,
    factor_index: Optional[List[float]],
    top_factor: Optional[str],
    linked_swings: Optional[List[Tuple[str, float]]],
) -> NumericClaimVerdict:
    text = (getattr(claim, "text", "") or "").lower()
    claim_id = getattr(claim, "claim_id", "<unknown>")

    # Order matters: superlatives first (they often precede a number
    # like "top driver, ~12% swing"), then percent, then ratio.
    superlative = _match_superlative(text)
    percent = _match_percent(text)
    ratio = _match_ratio(text)

    if superlative is None and percent is None and ratio is None:
        return NumericClaimVerdict(
            claim_id=claim_id,
            verdict=Verdict.NOT_APPLICABLE,
            parsed="",
            reason="claim contains no numeric / comparative pattern the verifier checks",
        )

    if superlative == "top_driver":
        if top_factor is None:
            return NumericClaimVerdict(
                claim_id=claim_id,
                verdict=Verdict.NOT_VERIFIABLE,
                parsed="superlative=top_driver",
                reason="sensitivity_report missing or empty; cannot ground 'top driver' assertion",
            )
        # Tokenized check: the top factor's id (case-folded) must
        # appear as a DISCRETE TOKEN in the claim text, not a
        # substring of an unrelated longer word ("rto" must not
        # match inside "ratio"). Word-boundary aware to avoid the
        # same collision class as the driver attribution fallback.
        text_tokens = _tokenize(text)
        top_lower = top_factor.lower()
        names_top_factor = (
            top_lower in text_tokens
            or any(part in text_tokens for part in _split_factor_id(top_lower))
        )
        if not names_top_factor:
            return NumericClaimVerdict(
                claim_id=claim_id,
                verdict=Verdict.CONTRADICTED,
                parsed="superlative=top_driver",
                reason=(
                    "top-driver assertion present but the claim does not name "
                    f"the actual ranking[0]={top_factor!r} as a discrete token"
                ),
            )
        # Narrowing contract (same as the percent path): a
        # comparative claim is only SUPPORTED when the claim is
        # ALSO structurally linked to ranking[0] -- via
        # ``factor.affected_claims``, ``claim.driver_hints[*].ref``,
        # or ``claim.evidence_refs[*].ref_id``. A claim that merely
        # MENTIONS the top factor without declaring it as a driver
        # is too weak to count as SUPPORTED ("the LLM does not own
        # statistics about a factor its claim never linked to" --
        # same contract as percent verification).
        if linked_swings is None or not any(
            name.lower() == top_lower or top_lower in {p for p in _split_factor_id(name.lower())}
            for name, _ in linked_swings
        ):
            return NumericClaimVerdict(
                claim_id=claim_id,
                verdict=Verdict.UNSUPPORTED,
                parsed="superlative=top_driver",
                reason=(
                    f"top-driver assertion names ranking[0]={top_factor!r} "
                    "but the claim has no structured link "
                    "(affected_claims / driver_hints / evidence_refs) to "
                    "that factor; cannot bless as SUPPORTED"
                ),
            )
        return NumericClaimVerdict(
            claim_id=claim_id,
            verdict=Verdict.SUPPORTED,
            parsed="superlative=top_driver",
            reason=(
                f"top-driver assertion matches ranking[0]={top_factor!r} "
                "and the claim is structurally linked to that factor"
            ),
        )

    if percent is not None:
        # Convert "30%" -> 0.30 to match the artifact's float scale.
        claimed_fraction = percent / 100.0
        return _verify_percent_against_factors(
            claim_id, claimed_fraction, factor_index, linked_swings,
        )

    # ratio: rare in this domain; we mark NOT_VERIFIABLE rather than
    # invent a ratio interpretation against the sensitivity report.
    # A future extension can add ratio→swing-ratio comparisons; for
    # now the contract is "the verifier names ratios but does not
    # claim to know what they should be".
    return NumericClaimVerdict(
        claim_id=claim_id,
        verdict=Verdict.NOT_VERIFIABLE,
        parsed=f"ratio={ratio}x",
        reason="ratio claims are not yet checked against any deterministic artifact",
    )


def _verify_percent_against_factors(
    claim_id: str,
    claimed_fraction: float,
    factor_index: Optional[List[float]],
    linked_swings: Optional[List[Tuple[str, float]]],
) -> NumericClaimVerdict:
    if factor_index is None:
        return NumericClaimVerdict(
            claim_id=claim_id,
            verdict=Verdict.NOT_VERIFIABLE,
            parsed=f"percent={claimed_fraction * 100.0:.1f}",
            reason="sensitivity_report missing; cannot ground percent claim",
        )
    if not factor_index:
        return NumericClaimVerdict(
            claim_id=claim_id,
            verdict=Verdict.NOT_VERIFIABLE,
            parsed=f"percent={claimed_fraction * 100.0:.1f}",
            reason="sensitivity_report has no factors; cannot ground percent claim",
        )
    # Primary path: claim-linked factors only. SUPPORTED requires the
    # numeric match to land on a factor the claim is structurally
    # linked to (via affected_claims, driver_hints, or evidence_refs
    # that name a factor id).
    if linked_swings:
        for factor_name, swing in linked_swings:
            if abs(swing - claimed_fraction) <= _PERCENT_ABS_TOLERANCE:
                return NumericClaimVerdict(
                    claim_id=claim_id,
                    verdict=Verdict.SUPPORTED,
                    parsed=f"percent={claimed_fraction * 100.0:.1f}",
                    reason=(
                        f"percent claim within +/- {_PERCENT_ABS_TOLERANCE * 100:.0f} "
                        f"points of linked factor {factor_name!r} "
                        f"(artifact={swing:.3f})"
                    ),
                )
        # Linked factors exist but none agreed -- the claim names a
        # number the claim's own driver / attribution factors do NOT
        # carry. That's a contradiction even if some unrelated factor
        # happens to match.
        return NumericClaimVerdict(
            claim_id=claim_id,
            verdict=Verdict.CONTRADICTED,
            parsed=f"percent={claimed_fraction * 100.0:.1f}",
            reason=(
                f"percent claim {claimed_fraction * 100.0:.1f}% does not match "
                f"any of the claim's linked factor swings "
                f"({[(n, round(s, 3)) for n, s in linked_swings]!r}); "
                "global factor swings ignored because LLM prose does not own "
                "statistics about factors it did not link"
            ),
        )
    # No claim-link available. Fall back to a GLOBAL scan, but
    # downgrade the best possible verdict to UNSUPPORTED: the value
    # appears in the artifact, but not for THIS claim. The advisory
    # caller can still flag it; persisting as SUPPORTED would let
    # the verifier silently bless arbitrary numeric claims.
    for swing in factor_index:
        if abs(swing - claimed_fraction) <= _PERCENT_ABS_TOLERANCE:
            return NumericClaimVerdict(
                claim_id=claim_id,
                verdict=Verdict.UNSUPPORTED,
                parsed=f"percent={claimed_fraction * 100.0:.1f}",
                reason=(
                    f"percent claim {claimed_fraction * 100.0:.1f}% matches a "
                    f"GLOBAL factor swing ({swing:.3f}) but the claim has no "
                    "structured link (affected_claims / driver_hints / "
                    "evidence_refs) to that factor; cannot bless as SUPPORTED"
                ),
            )
    # No factor matched at all. The artifact carries swing data but
    # disagrees with the claim. CONTRADICTED is right here.
    return NumericClaimVerdict(
        claim_id=claim_id,
        verdict=Verdict.CONTRADICTED,
        parsed=f"percent={claimed_fraction * 100.0:.1f}",
        reason=(
            f"percent claim {claimed_fraction * 100.0:.1f}% has no matching "
            f"factor swing within +/- {_PERCENT_ABS_TOLERANCE * 100:.0f} points "
            f"(closest swings: {sorted(factor_index)[:3]!r})"
        ),
    )


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


def _match_percent(text: str) -> Optional[float]:
    """Return the FIRST percent value mentioned in ``text``, or None."""
    m = _PERCENT_RE.search(text)
    if m is None:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _match_ratio(text: str) -> Optional[int]:
    """Return the FIRST ratio value mentioned in ``text``, or None."""
    m = _RATIO_RE.search(text)
    if m is None:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def _match_superlative(text: str) -> Optional[str]:
    """Return the named comparator key, or None."""
    for pattern, key in _SUPERLATIVE_PATTERNS:
        if pattern.search(text):
            return key
    return None


_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> Set[str]:
    """Return the discrete-token set of ``text`` for word-boundary
    matching (case-folded).
    """
    return set(_TOKEN_RE.findall(text))


def _split_factor_id(factor_id: str) -> List[str]:
    """Split a factor id on ``_`` / ``.`` and drop short noise tokens.

    Used so a claim that names just ``trust`` is considered a token
    match for a factor named ``trust_baseline``. Tokens shorter than
    3 chars are dropped to avoid false matches on common prefixes.
    """
    out: List[str] = []
    for part in factor_id.replace(".", "_").split("_"):
        if len(part) >= 3:
            out.append(part)
    return out


def _build_factor_lookup(
    sensitivity_report: Optional[Any],
) -> Dict[str, float]:
    """Return ``{factor_id_lower: abs_swing}`` for case-insensitive lookup."""
    if sensitivity_report is None:
        return {}
    factors = getattr(sensitivity_report, "factors", None)
    if not factors:
        return {}
    out: Dict[str, float] = {}
    for f in factors:
        name = getattr(f, "factor", None)
        swing = getattr(f, "abs_swing", None)
        if isinstance(name, str) and isinstance(swing, (int, float)):
            out[name.lower()] = float(swing)
    return out


def _build_structured_affected_index(
    sensitivity_report: Optional[Any],
) -> Dict[str, List[Tuple[str, float]]]:
    """Inverse-index ``factor.affected_claims`` for O(1) per-claim lookup.

    Returns ``{claim_id: [(factor_id, abs_swing), ...]}`` for every
    claim listed in ANY structured-method factor's affected_claims.
    Substring-fallback factors are SKIPPED: a claim linked to a
    factor only via substring token match is too weak a signal to
    bless a numeric claim as SUPPORTED. The verifier still has
    ``driver_hints`` / ``evidence_refs`` as priority-2/3 link
    sources for those claims.
    """
    out: Dict[str, List[Tuple[str, float]]] = {}
    if sensitivity_report is None:
        return out
    for f in getattr(sensitivity_report, "factors", None) or []:
        method = getattr(f, "affected_claims_method", None)
        # Priority 1 only fires on structured links; substring
        # fallback is intentionally excluded.
        if method != "structured_v1":
            continue
        affected = getattr(f, "affected_claims", None) or []
        swing = getattr(f, "abs_swing", None)
        factor_name = getattr(f, "factor", None)
        if not (
            isinstance(swing, (int, float))
            and isinstance(factor_name, str)
            and factor_name
        ):
            continue
        for cid in affected:
            if not isinstance(cid, str) or not cid:
                continue
            out.setdefault(cid, []).append((factor_name, float(swing)))
    return out


def _linked_factor_swings(
    claim: Any,
    factor_lookup: Dict[str, float],
    affected_index: Optional[Dict[str, List[Tuple[str, float]]]] = None,
) -> Optional[List[Tuple[str, float]]]:
    """Resolve the factor ids this claim is structurally linked to and
    return the matching ``(factor_id, abs_swing)`` pairs.

    Returns ``None`` (NOT an empty list) when the claim has no
    structured link surface at all -- that signals the verifier to
    fall through to the global-scan UNSUPPORTED path. An empty list
    return means the claim DOES carry link surfaces but none resolved
    to a registered factor -- treated identically to None for the
    purposes of "the verifier cannot ground this".

    Three link sources, in priority order. A match in any one is
    sufficient; sources are combined (a claim linked both via
    ``affected_claims`` and ``driver_hints`` to two different
    factors yields BOTH pairs so the verifier checks every linked
    factor's swing).

      1. ``factor.affected_claims`` (via :paramref:`affected_index`):
         the structured attribution post-processor's per-factor list,
         pre-built into a ``{claim_id: [(factor, swing), ...]}``
         inverse index by :func:`_build_structured_affected_index`.
         Only ``structured_v1`` factors contribute here; substring
         fallback is excluded by construction.
      2. ``claim.driver_hints[*].ref`` -- structured refs the claim
         itself declares.
      3. ``claim.evidence_refs[*].ref_id`` -- evidence pointers that
         name a factor id directly.
    """
    if not factor_lookup:
        return None
    pairs: List[Tuple[str, float]] = []
    # Priority 1: factor.affected_claims (structured method only).
    claim_id = getattr(claim, "claim_id", None)
    if (
        affected_index
        and isinstance(claim_id, str)
        and claim_id
        and claim_id in affected_index
    ):
        pairs.extend(affected_index[claim_id])

    # Priority 2/3: claim-declared structured refs.
    linked_ids: Set[str] = set()
    for hint in getattr(claim, "driver_hints", []) or []:
        ref = getattr(hint, "ref", None)
        if isinstance(ref, str) and ref:
            linked_ids.add(ref.lower())
    for ref in getattr(claim, "evidence_refs", []) or []:
        rid = getattr(ref, "ref_id", None)
        if isinstance(rid, str) and rid:
            linked_ids.add(rid.lower())
    for factor_id in linked_ids:
        if factor_id in factor_lookup:
            # Dedupe against priority-1 pairs (case-insensitive on
            # factor id) so we don't double-count the same factor's
            # swing when it appears in both link sources.
            already = {fname.lower() for fname, _ in pairs}
            if factor_id not in already:
                pairs.append((factor_id, factor_lookup[factor_id]))

    if not pairs and not linked_ids and not (
        affected_index and isinstance(claim_id, str) and claim_id in affected_index
    ):
        # No link surface at all -- signal global-scan fallback to
        # the caller via ``None``.
        return None
    return pairs


def _build_factor_index(
    sensitivity_report: Optional[Any],
) -> Optional[List[float]]:
    """Flatten the report's per-factor ``abs_swing`` values for O(N) percent matching.

    Returns ``None`` when the report is missing or has no factors;
    the verifier treats both cases as "cannot ground". An empty list
    is distinct from None: it means the report exists but reported
    zero factors (an edge case that the verifier also surfaces as
    NOT_VERIFIABLE).
    """
    if sensitivity_report is None:
        return None
    factors = getattr(sensitivity_report, "factors", None)
    if not factors:
        return []
    out: List[float] = []
    for f in factors:
        swing = getattr(f, "abs_swing", None)
        if isinstance(swing, (int, float)):
            out.append(float(swing))
    return out


def _build_top_factor(
    sensitivity_report: Optional[Any],
) -> Optional[str]:
    """Return the factor id at the top of the ranking, or None."""
    if sensitivity_report is None:
        return None
    ranking = getattr(sensitivity_report, "ranking", None)
    if not ranking:
        return None
    head = ranking[0]
    if isinstance(head, str) and head:
        return head
    return None


__all__ = [
    "NumericClaimVerdict",
    "Verdict",
    "verify_claims",
]
