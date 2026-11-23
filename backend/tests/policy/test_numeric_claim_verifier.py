"""Numeric / comparative claim verifier contract pins.

The verifier is a deterministic post-validate seam: LLM-emitted
report claims that put numbers or comparatives on the prose are
re-checked against the deterministic ``sensitivity.json`` artifact
and the per-claim ``driver_hints``. The contract under test:

  * Supported (number agrees with artifact within tolerance) -> SUPPORTED.
  * Unbacked numbers (no matching factor swing) -> CONTRADICTED.
  * "Top driver" superlatives -> verified against ``ranking[0]``.
  * Missing artifact -> NOT_VERIFIABLE (never silent confirmation).
  * No numeric / comparative pattern -> NOT_APPLICABLE (no opinion).
  * Recall-vs-precision: false positives are worse than misses, so
    paraphrases the parser does not understand land as
    NOT_APPLICABLE -- the predictive-overclaim corpus owns that
    surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.services.policy.numeric_claim_verifier import (
    NumericClaimVerdict,
    Verdict,
    verify_claims,
)


# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class _Hint:
    """Minimal duck-typed driver_hint shape (just ``ref``)."""
    ref: str


@dataclass(frozen=True)
class _EvRef:
    """Minimal duck-typed evidence_ref shape (just ``ref_id``)."""
    ref_id: str


@dataclass(frozen=True)
class _Claim:
    """Minimal duck-typed claim shape the verifier consumes.

    ``driver_hints`` / ``evidence_refs`` default to empty so tests
    that don't care about narrowing can ignore them; tests that
    exercise the link narrowing supply them explicitly.
    """
    claim_id: str
    text: str
    driver_hints: tuple = ()
    evidence_refs: tuple = ()


@dataclass(frozen=True)
class _Factor:
    factor: str
    abs_swing: float
    # Optional inverse-link fields the verifier reads as priority 1.
    affected_claims: tuple = ()
    affected_claims_method: Optional[str] = None


@dataclass
class _SensitivityReport:
    factors: List[_Factor]
    ranking: List[str]


def _report(
    factors: Optional[List[tuple]] = None,
    ranking: Optional[List[str]] = None,
) -> _SensitivityReport:
    return _SensitivityReport(
        factors=[_Factor(f, s) for f, s in (factors or [])],
        ranking=list(ranking or []),
    )


def _report_with_affected(
    factor: str,
    abs_swing: float,
    affected: List[str],
    method: str = "structured_v1",
    ranking: Optional[List[str]] = None,
) -> _SensitivityReport:
    """Build a report where ``factor`` lists ``affected`` claim ids
    via the given attribution method (default ``structured_v1`` so
    the verifier's priority-1 path is exercised)."""
    return _SensitivityReport(
        factors=[
            _Factor(
                factor=factor,
                abs_swing=abs_swing,
                affected_claims=tuple(affected),
                affected_claims_method=method,
            ),
        ],
        ranking=list(ranking or []),
    )


# ----------------------------------------------------------------------
# Verdict shape
# ----------------------------------------------------------------------


def test_verifier_returns_one_verdict_per_claim_in_order() -> None:
    claims = [
        _Claim("c1", "no numbers here"),
        _Claim("c2", "30% lift"),
        _Claim("c3", "top driver is segment_pricing"),
    ]
    verdicts = verify_claims(claims, _report(
        factors=[("segment_pricing", 0.30)],
        ranking=["segment_pricing"],
    ))
    assert len(verdicts) == 3
    assert [v.claim_id for v in verdicts] == ["c1", "c2", "c3"]


# ----------------------------------------------------------------------
# NOT_APPLICABLE: parser has no opinion on paraphrases / free text
# ----------------------------------------------------------------------


def test_claim_with_no_numeric_or_comparative_pattern_is_not_applicable() -> None:
    """Recall-vs-precision: a claim the parser does NOT understand
    must land as NOT_APPLICABLE so the caller does not falsely demote
    a real finding. Paraphrases ("a meaningful change", "we expect
    movement") are owned by the predictive-overclaim corpus, not
    this verifier."""
    [v] = verify_claims(
        [_Claim("c1", "this is a meaningful change in the cohort response")],
        _report(),
    )
    assert v.verdict == Verdict.NOT_APPLICABLE
    assert v.parsed == ""


# ----------------------------------------------------------------------
# Percent claims
# ----------------------------------------------------------------------


def test_percent_within_tolerance_is_supported_when_factor_is_claim_linked() -> None:
    """A 30% claim is SUPPORTED when a factor's abs_swing is 0.30
    AND the claim names that factor in its driver_hints. Without the
    structured link, a numeric match would only be UNSUPPORTED."""
    [v] = verify_claims(
        [_Claim(
            "c1", "we observed a 30% lift",
            driver_hints=(_Hint("pricing"),),
        )],
        _report(factors=[("pricing", 0.30)]),
    )
    assert v.verdict == Verdict.SUPPORTED
    assert v.parsed == "percent=30.0"
    assert "linked factor 'pricing'" in v.reason


def test_percent_close_to_linked_factor_is_supported_within_5pt_tolerance() -> None:
    """The verifier tolerates +/- 5 percentage points of rounding,
    but only on linked factors."""
    [v] = verify_claims(
        [_Claim(
            "c1", "this is a 30% effect",
            driver_hints=(_Hint("a"),),
        )],
        _report(factors=[("a", 0.27)]),
    )
    assert v.verdict == Verdict.SUPPORTED


def test_percent_far_from_any_factor_is_contradicted_with_no_links() -> None:
    """A 30% claim with no structured links is CONTRADICTED when no
    factor's swing is anywhere near 30%. The verdict carries the
    closest swings in the reason."""
    [v] = verify_claims(
        [_Claim("c1", "this drives a 30% drop")],
        _report(factors=[("a", 0.05), ("b", 0.10), ("c", 0.12)]),
    )
    assert v.verdict == Verdict.CONTRADICTED
    assert "30.0" in v.reason
    assert "0.05" in v.reason or "0.1" in v.reason or "0.12" in v.reason


# ----------------------------------------------------------------------
# Narrowing contract: claim-linked factors only for SUPPORTED
# ----------------------------------------------------------------------


def test_percent_global_match_without_link_is_unsupported_not_supported() -> None:
    """The critical narrowing pin: a 30% claim against an artifact
    that happens to carry a 30% swing on an UNRELATED factor is
    UNSUPPORTED -- the LLM does not own statistics about a factor
    its claim never linked to. Persisting as SUPPORTED would let the
    verifier silently bless arbitrary numeric prose."""
    [v] = verify_claims(
        # No driver_hints, no evidence_refs naming a factor.
        [_Claim("c1", "we observed a 30% lift")],
        _report(factors=[("unrelated_factor", 0.30)]),
    )
    assert v.verdict == Verdict.UNSUPPORTED
    assert "GLOBAL" in v.reason
    assert "no structured link" in v.reason.lower() or "structured link" in v.reason


def test_percent_contradicting_linked_factor_ignores_global_match() -> None:
    """A claim linked to factor A (swing 0.10) that says "30%" is
    CONTRADICTED, even if some unrelated factor B happens to carry
    a 30% swing. The linked-factor mismatch wins -- the global
    coincidence is not a free pass."""
    [v] = verify_claims(
        [_Claim(
            "c1", "this drives a 30% lift",
            driver_hints=(_Hint("a"),),
        )],
        _report(factors=[("a", 0.10), ("b", 0.30)]),
    )
    assert v.verdict == Verdict.CONTRADICTED
    assert "linked factor" in v.reason.lower() or "claim's linked" in v.reason


def test_percent_supported_via_evidence_ref_link() -> None:
    """Evidence refs that name a factor id are also a valid link
    source (mirror of driver_hints)."""
    [v] = verify_claims(
        [_Claim(
            "c1", "we observed a 30% lift",
            evidence_refs=(_EvRef("pricing"),),
        )],
        _report(factors=[("pricing", 0.30)]),
    )
    assert v.verdict == Verdict.SUPPORTED


# ----------------------------------------------------------------------
# Priority 1: factor.affected_claims (structured attribution post-processor)
# ----------------------------------------------------------------------


def test_percent_supported_via_factor_affected_claims_structured() -> None:
    """**The wiring pin.** A claim with NO ``driver_hints`` and NO
    ``evidence_refs`` is still SUPPORTED when the factor's
    ``affected_claims`` lists this claim_id AND the factor's
    ``affected_claims_method == "structured_v1"``. This is the
    priority-1 link source the verifier docstring advertises but
    the implementation historically did not read."""
    [v] = verify_claims(
        # No driver_hints, no evidence_refs.
        [_Claim("c1", "we observed a 30% lift across the cohort")],
        _report_with_affected(
            factor="pricing",
            abs_swing=0.30,
            affected=["c1"],
            method="structured_v1",
        ),
    )
    assert v.verdict == Verdict.SUPPORTED
    # The reason names the linked factor so an operator sees WHY.
    assert "pricing" in v.reason


def test_substring_fallback_attribution_does_NOT_qualify_for_supported() -> None:
    """``substring_fallback_v1`` is explicitly LOW confidence. Even if
    a factor's ``affected_claims`` lists this claim_id, the verifier
    MUST NOT bless the numeric claim as SUPPORTED purely on a
    substring-fallback link -- token-collision attribution is too
    weak to ground a number. The claim falls through to global-scan
    UNSUPPORTED, NOT silently SUPPORTED."""
    [v] = verify_claims(
        [_Claim("c1", "we observed a 30% lift")],
        _report_with_affected(
            factor="pricing",
            abs_swing=0.30,
            affected=["c1"],
            method="substring_fallback_v1",
        ),
    )
    assert v.verdict == Verdict.UNSUPPORTED


def test_affected_claims_combines_with_driver_hints_no_double_count() -> None:
    """A claim linked BOTH via factor.affected_claims AND via its own
    driver_hints to the SAME factor is checked once, not twice. The
    reason cites a single linked factor."""
    [v] = verify_claims(
        [_Claim(
            "c1", "we observed a 30% lift",
            driver_hints=(_Hint("pricing"),),
        )],
        _report_with_affected(
            factor="pricing",
            abs_swing=0.30,
            affected=["c1"],
            method="structured_v1",
        ),
    )
    assert v.verdict == Verdict.SUPPORTED
    # Reason mentions 'pricing' exactly once (no duplicate in the
    # operator-readable string).
    assert v.reason.lower().count("pricing") == 1


def test_affected_claims_combines_with_driver_hints_different_factors() -> None:
    """A claim linked via affected_claims to factor A and via its own
    driver_hints to factor B is checked against BOTH factors. The
    verifier picks the matching one."""
    rep = _SensitivityReport(
        factors=[
            _Factor(
                factor="pricing",
                abs_swing=0.30,
                affected_claims=("c1",),
                affected_claims_method="structured_v1",
            ),
            _Factor(factor="trust", abs_swing=0.10),
        ],
        ranking=[],
    )
    [v] = verify_claims(
        [_Claim(
            "c1", "we observed a 30% lift",
            driver_hints=(_Hint("trust"),),
        )],
        rep,
    )
    # 30% matches the ``pricing`` swing (priority 1 link); the
    # ``trust`` driver-hint pair is also considered but its swing
    # disagrees, so the priority-1 match wins.
    assert v.verdict == Verdict.SUPPORTED


def test_percent_with_no_sensitivity_report_is_not_verifiable() -> None:
    """No artifact = no certainty either way. The verifier must NOT
    upgrade a missing artifact into a passing verdict."""
    [v] = verify_claims(
        [_Claim("c1", "a 30% lift was observed")],
        sensitivity_report=None,
    )
    assert v.verdict == Verdict.NOT_VERIFIABLE
    assert "missing" in v.reason.lower()


def test_percent_with_empty_factors_list_is_not_verifiable() -> None:
    """The report exists but reports no factors -- treated the same as
    a missing report (NOT_VERIFIABLE) rather than SUPPORTED."""
    [v] = verify_claims(
        [_Claim("c1", "we report a 5% swing")],
        _report(factors=[]),
    )
    assert v.verdict == Verdict.NOT_VERIFIABLE


# ----------------------------------------------------------------------
# Comparative / superlative claims
# ----------------------------------------------------------------------


def test_top_driver_matches_ranking_head_when_claim_is_linked() -> None:
    """A "top driver" claim that names ranking[0] AND is structurally
    linked to it is SUPPORTED. The narrowing contract matches the
    percent path: a comparative claim that merely MENTIONS the top
    factor without declaring it as a driver is UNSUPPORTED (see
    test_top_driver_without_link_is_unsupported below)."""
    [v] = verify_claims(
        [_Claim(
            "c1", "segment_pricing is the top driver of the swing",
            driver_hints=(_Hint("segment_pricing"),),
        )],
        _report(
            factors=[("segment_pricing", 0.4), ("trust_baseline", 0.1)],
            ranking=["segment_pricing", "trust_baseline"],
        ),
    )
    assert v.verdict == Verdict.SUPPORTED
    assert v.parsed == "superlative=top_driver"


def test_top_driver_without_link_is_unsupported() -> None:
    """**The narrowing pin for the comparative path.** A claim that
    NAMES the top factor by token but does NOT declare it as a
    driver (no affected_claims / driver_hints / evidence_refs link)
    is UNSUPPORTED, not SUPPORTED. Symmetry with the percent path's
    'global match without structured link' rule."""
    [v] = verify_claims(
        # No driver_hints, no evidence_refs naming the factor.
        [_Claim("c1", "segment_pricing is the top driver of the swing")],
        _report(
            factors=[("segment_pricing", 0.4)],
            ranking=["segment_pricing"],
        ),
    )
    assert v.verdict == Verdict.UNSUPPORTED
    assert "no structured link" in v.reason.lower() or "structured link" in v.reason


def test_top_driver_supported_via_factor_affected_claims_structured() -> None:
    """Comparative claim is SUPPORTED when the link comes through
    ``factor.affected_claims`` with ``structured_v1`` method (the
    priority-1 link source the percent path already consults)."""
    [v] = verify_claims(
        [_Claim("c1", "segment_pricing is the top driver of the swing")],
        _report_with_affected(
            factor="segment_pricing",
            abs_swing=0.4,
            affected=["c1"],
            method="structured_v1",
            ranking=["segment_pricing"],
        ),
    )
    assert v.verdict == Verdict.SUPPORTED


def test_top_driver_naming_wrong_factor_is_contradicted() -> None:
    """A "top driver" claim that names a non-top factor is CONTRADICTED."""
    [v] = verify_claims(
        [_Claim("c1", "trust_baseline is the top driver")],
        _report(ranking=["segment_pricing", "trust_baseline"]),
    )
    assert v.verdict == Verdict.CONTRADICTED
    # The reason names the actual top factor so the operator can fix
    # the prose without consulting the artifact.
    assert "segment_pricing" in v.reason


def test_top_driver_tokenized_check_rejects_substring_collision() -> None:
    """A "top driver" claim that mentions ``rto`` inside a longer
    word like ``ratio`` must NOT count as naming the factor ``rto``.
    Tokenized check uses word-boundary matching so substring
    collisions don't silently bless the wrong prose."""
    [v] = verify_claims(
        [_Claim("c1", "the ratio of fans to critics is the top driver")],
        _report(ranking=["rto"]),
    )
    assert v.verdict == Verdict.CONTRADICTED


def test_top_driver_split_token_matches_partial_factor_id() -> None:
    """A claim that names just ``trust`` (not the full
    ``trust_baseline``) is grounded against the factor's split
    tokens. Operator-friendly: not every prose mention will spell
    out the full underscore-joined id. SUPPORTED requires the
    same structured link as the canonical SUPPORTED path."""
    [v] = verify_claims(
        [_Claim(
            "c1", "trust is the top driver of the swing",
            driver_hints=(_Hint("trust_baseline"),),
        )],
        _report(
            factors=[("trust_baseline", 0.4)],
            ranking=["trust_baseline"],
        ),
    )
    assert v.verdict == Verdict.SUPPORTED


def test_top_driver_with_empty_ranking_is_not_verifiable() -> None:
    [v] = verify_claims(
        [_Claim("c1", "X is the top driver")],
        _report(ranking=[]),
    )
    assert v.verdict == Verdict.NOT_VERIFIABLE


def test_most_sensitive_phrase_is_treated_as_top_driver() -> None:
    """Synonymous superlatives ("most sensitive", "largest swing",
    "leading factor") all route to the top-driver check so the
    verifier is robust to LLM phrasing variation in this narrow band."""
    [v] = verify_claims(
        [_Claim(
            "c1", "segment_pricing is the most sensitive factor",
            driver_hints=(_Hint("segment_pricing"),),
        )],
        _report(
            factors=[("segment_pricing", 0.4)],
            ranking=["segment_pricing"],
        ),
    )
    assert v.verdict == Verdict.SUPPORTED


def test_largest_swing_phrase_is_treated_as_top_driver() -> None:
    [v] = verify_claims(
        [_Claim(
            "c1", "segment_pricing carries the largest swing",
            driver_hints=(_Hint("segment_pricing"),),
        )],
        _report(
            factors=[("segment_pricing", 0.4)],
            ranking=["segment_pricing"],
        ),
    )
    assert v.verdict == Verdict.SUPPORTED


# ----------------------------------------------------------------------
# Ratio claims (conservative: not yet verified)
# ----------------------------------------------------------------------


def test_ratio_claim_is_not_verifiable_today() -> None:
    """Ratio claims ("3x increase") are named by the parser but the
    verifier does not yet ground them against a swing-ratio
    interpretation. NOT_VERIFIABLE is the right answer -- the
    verifier neither blesses nor demotes the claim until the
    ratio-vs-artifact contract is added."""
    [v] = verify_claims(
        [_Claim("c1", "a 3x increase in engagement")],
        _report(factors=[("a", 0.1)]),
    )
    assert v.verdict == Verdict.NOT_VERIFIABLE
    assert "ratio" in v.parsed


# ----------------------------------------------------------------------
# Reason field is always populated (operator-readable)
# ----------------------------------------------------------------------


def test_every_verdict_carries_a_reason() -> None:
    """An operator reading the demotion warning needs to know WHY
    without re-running the verifier. The reason field is therefore
    always populated, including for NOT_APPLICABLE verdicts."""
    verdicts = verify_claims(
        [
            _Claim("a", "no numbers"),
            _Claim("b", "30% lift"),
            _Claim("c", "top driver is x"),
            _Claim("d", "5x boost"),
        ],
        _report(factors=[("x", 0.3)], ranking=["x"]),
    )
    for v in verdicts:
        assert isinstance(v, NumericClaimVerdict)
        assert v.reason, f"reason must be set for every verdict (claim={v.claim_id})"


# ----------------------------------------------------------------------
# Determinism: same input -> same output (no clock, no random)
# ----------------------------------------------------------------------


def test_verifier_is_pure_and_deterministic() -> None:
    claims = [_Claim("c1", "30% lift via segment_pricing")]
    rep = _report(factors=[("segment_pricing", 0.30)], ranking=["segment_pricing"])
    a = verify_claims(claims, rep)
    b = verify_claims(claims, rep)
    assert a == b
