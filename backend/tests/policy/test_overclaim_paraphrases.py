"""Paraphrase detector + corpus eval contract pins.

CI MUST fail if:
  * A known overclaim paraphrase slips through the detector (FN > 0).
  * A known hedged variant is wrongly flagged (FP > 0).

These two assertions keep the precision / recall tradeoff visible in
the test suite -- a widening of PARAPHRASE_PATTERNS that catches more
positives at the cost of new false positives breaks the second test
loudly, forcing the author to either narrow the new pattern or add
the legitimate variant to the hedged corpus with a justification.
"""
from __future__ import annotations

from app.services.policy.overclaim_paraphrases import (
    HEDGED_CORPUS,
    OVERCLAIM_CORPUS,
    OverclaimFinding,
    evaluate_corpus,
    find_overclaim_paraphrases,
    find_un_pardoned_paraphrases,
)


# ----------------------------------------------------------------------
# Eval gates: FN=0 on positives, FP=0 on negatives
# ----------------------------------------------------------------------


def test_detector_catches_every_known_overclaim_paraphrase() -> None:
    """Recall=1.0 on the labelled overclaim corpus. A FN here means
    the detector silently lets an over-promise through to persist;
    that is the failure mode this guardrail exists to prevent."""
    result = evaluate_corpus()
    assert not result.positives_missed, (
        "predictive-overclaim paraphrases not caught by the detector: "
        f"{result.positives_missed!r}. Either widen PARAPHRASE_PATTERNS "
        "or remove the item from OVERCLAIM_CORPUS with an explicit reason."
    )


def test_detector_does_not_flag_hedged_variants() -> None:
    """Precision=1.0 on the labelled hedged corpus. A FP here means
    the detector falsely demotes a legitimate finding; that is more
    expensive than a miss because lost findings are silent while
    survived overclaims are loud."""
    result = evaluate_corpus()
    assert not result.negatives_flagged, (
        "hedged variants wrongly flagged as overclaims: "
        f"{result.negatives_flagged!r}. Narrow the offending pattern "
        "or add an explicit hedge / negation cue."
    )


def test_eval_result_shape_is_consistent() -> None:
    """Sanity: the count fields agree with the lists."""
    result = evaluate_corpus()
    assert result.positives_total == len(OVERCLAIM_CORPUS)
    assert result.negatives_total == len(HEDGED_CORPUS)
    assert result.positives_caught + len(result.positives_missed) == result.positives_total
    assert result.negatives_passed + len(result.negatives_flagged) == result.negatives_total


# ----------------------------------------------------------------------
# Hedge + negation pardons (sentence-local)
# ----------------------------------------------------------------------


def test_same_sentence_hedge_pardons_paraphrase() -> None:
    """A hedge in the same sentence flips the finding to hedged=True;
    the strict ``find_un_pardoned_paraphrases`` filter excludes it."""
    # "under these assumptions" is a registered hedge; "is set to"
    # matches the paraphrase pattern. Same sentence -> hedged finding.
    text = "under these assumptions, engagement is set to climb"
    findings = find_overclaim_paraphrases(text)
    assert any(f.hedged for f in findings)
    assert not find_un_pardoned_paraphrases(text)


def test_negation_left_window_pardons_paraphrase() -> None:
    """A negation token in the 4-token left window of the match
    flips the finding to negated=True; the strict filter excludes
    it. Window is small so a downstream negation cannot pardon."""
    # "no segment" puts ``no`` in the immediate left window of
    # ``is set to`` (tokens: no, segment, is, set, to -- window of 4
    # left of ``is`` is [no, segment]).
    text = "no segment is set to churn"
    findings = find_overclaim_paraphrases(text)
    assert any(f.negated for f in findings)
    assert not find_un_pardoned_paraphrases(text)


def test_hedge_in_other_sentence_does_not_pardon() -> None:
    """A hedge on sentence N must NOT pardon an overclaim on
    sentence N+1 -- sentence-local scope, mirror of the
    language-guard rule."""
    text = (
        "Engagement may shift under these assumptions. "
        "Engagement is set to climb 30% next quarter."
    )
    un_pardoned = find_un_pardoned_paraphrases(text)
    assert len(un_pardoned) >= 1
    assert any(f.pattern_id == "is_set_to" for f in un_pardoned)


def test_negation_far_to_the_left_does_not_pardon() -> None:
    """Negation more than 4 tokens left of the match must NOT pardon."""
    text = (
        "we do not have any reason at this point to think otherwise "
        "but the cohort is set to churn next month"
    )
    un_pardoned = find_un_pardoned_paraphrases(text)
    assert any(f.pattern_id == "is_set_to" for f in un_pardoned)


# ----------------------------------------------------------------------
# Specific pattern coverage (each pattern catches the canonical phrase)
# ----------------------------------------------------------------------


def test_inevitably_pattern_catches_adverb_and_adjective() -> None:
    """The 'inevitably' / 'inevitable' family covers both forms."""
    assert find_un_pardoned_paraphrases("we will inevitably see the split")
    assert find_un_pardoned_paraphrases("this is inevitable given the data")


def test_subject_must_pattern_only_fires_for_overclaim_subjects() -> None:
    """The 'must' pattern is narrowed to domain subjects to avoid
    flagging framework requirements ('we must verify')."""
    assert find_un_pardoned_paraphrases("the cohort must respond to the change")
    # Framework usage MUST NOT trigger: "we must" is a researcher
    # commitment, not an overclaim about the population.
    assert not find_un_pardoned_paraphrases("we must verify this against priors")


def test_we_see_next_requires_temporal_cue() -> None:
    """Plain 'we see X' is a sim-observation phrasing and MUST NOT
    fire. The pattern only catches the variant with an explicit
    temporal cue ('next', 'tomorrow', 'soon', 'by Q...')."""
    assert not find_un_pardoned_paraphrases(
        "we see the cohort cluster around segment_pricing"
    )
    assert find_un_pardoned_paraphrases(
        "we see the cohort drop off next quarter"
    )


def test_by_calendar_will_pattern_catches_calendar_commitment() -> None:
    """'by Q4 X will...' is a calendar-anchored future commitment."""
    assert find_un_pardoned_paraphrases(
        "by Q4 the retention curve will reverse"
    )


# ----------------------------------------------------------------------
# Finding-shape contract
# ----------------------------------------------------------------------


def test_finding_carries_pattern_id_matched_text_sentence() -> None:
    """An operator triaging a finding needs the pattern id + matched
    text + the sentence -- not just a boolean. The finding dataclass
    pins this."""
    findings = find_overclaim_paraphrases(
        "engagement is set to climb 30% next quarter"
    )
    assert findings
    f = findings[0]
    assert isinstance(f, OverclaimFinding)
    assert f.pattern_id == "is_set_to"
    assert "set to" in f.matched_text.lower()
    assert "climb" in f.sentence.lower()


# ----------------------------------------------------------------------
# Determinism (pure module)
# ----------------------------------------------------------------------


def test_detector_is_pure_and_deterministic() -> None:
    text = "subscribers are destined to churn by Q4"
    a = find_overclaim_paraphrases(text)
    b = find_overclaim_paraphrases(text)
    assert a == b
