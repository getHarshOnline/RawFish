"""Advisory paraphrase-overclaim detector + labelled corpus.

The deterministic banned-token regex in :mod:`language_guard` is the
BLOCKING gate: any unhedged "predict", "forecast", "guarantee" in
the rendered markdown is a hard failure (the route refuses to
persist the report). That regex is exact-string and intentionally
narrow -- it catches the canonical overclaim vocabulary cheaply.

This module owns the COMPLEMENT: paraphrased overclaims that slip
the exact regex but still over-promise.

  * "engagement is set to climb 30% next quarter"
  * "this is bound to drive churn down"
  * "we will inevitably see the cohort split"
  * "by Q4 we see the pattern reverse"
  * "the market is poised to react"
  * "subscribers are destined to churn"

These are advisory because (a) every paraphrase carries some risk
of false positive (some legitimately-hedged variants share lexemes
with the overclaim cohort), and (b) the cost of a false demotion
is a lost real finding. The contract:

  * :func:`find_overclaim_paraphrases` returns a list of findings
    PER claim text. Each finding names the matched paraphrase and
    its hedge status.
  * Hedges (``may`` / ``might`` / ``could`` / ``under these
    assumptions`` / ``simulation suggests`` / ``directional``) in
    the SAME sentence pardon the paraphrase, matching the
    sentence-local hedge contract :mod:`language_guard` uses.
  * Negation windows pardon ("we do NOT expect to see X" is fine;
    a 3-token left window from the match is checked for
    ``not`` / ``no`` / ``never`` / ``unlikely``).
  * The caller (the report generator's post-validate seam) decides
    whether to demote the claim or persist a soft warning.

The module ALSO ships a labelled CORPUS (``OVERCLAIM_CORPUS``,
``HEDGED_CORPUS``) used by the eval harness
(:func:`evaluate_corpus`). A test fails CI if a known bad
paraphrase slips past the detector OR if a known hedged variant
is wrongly flagged -- pinning FN=0 on positives and FP=0 on
negatives keeps the precision / recall tradeoff visible and
under review.

Pure module: no clock, no env, no I/O, no LLM. Deterministic
for a given input string.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Pattern, Tuple


# ----------------------------------------------------------------------
# Paraphrase patterns
# ----------------------------------------------------------------------
#
# Each pattern is one paraphrase the canonical banned-token regex does
# NOT catch. Anchored on word boundaries (\b) so partial matches do
# not fire ("setting" must not match "is set to"). Inflection
# variations are listed exhaustively rather than glob-matched, mirror
# of the morphology coverage in BANNED_PATTERNS.
PARAPHRASE_PATTERNS: List[Tuple[Pattern[str], str]] = [
    # "is set to X": copular construction with "set to" predicate.
    (re.compile(r"\b(?:is|are|was|were)\s+set\s+to\b", re.IGNORECASE),
     "is_set_to"),
    # "is bound to X": modal-strength deterministic claim.
    (re.compile(r"\b(?:is|are|was|were)\s+bound\s+to\b", re.IGNORECASE),
     "is_bound_to"),
    # "poised to X" / "destined to X": deterministic-trajectory phrasing.
    (re.compile(r"\b(?:is|are|was|were)\s+poised\s+to\b", re.IGNORECASE),
     "poised_to"),
    (re.compile(r"\b(?:is|are|was|were)\s+destined\s+to\b", re.IGNORECASE),
     "destined_to"),
    # "inevitably X" / "inevitable that": certainty adverb / adjective.
    (re.compile(r"\binevitably\b", re.IGNORECASE),
     "inevitably"),
    (re.compile(r"\binevitable\b", re.IGNORECASE),
     "inevitable"),
    # "we see X next" / "we'll see X next" -- temporal future-tense
    # without hedge. The pattern requires a temporal cue ("next",
    # "by Q", "by month", "tomorrow", "soon") so generic "we see X"
    # (sim-observation report language) is not flagged.
    (re.compile(r"\bwe\s+(?:will\s+)?see\b.{0,60}\b(?:next|tomorrow|soon|by\s+(?:q\d|month|quarter|year|week))\b",
                re.IGNORECASE),
     "we_see_next"),
    # "by Q4 X will": calendar-anchored future commitment.
    (re.compile(r"\bby\s+(?:q\d|month|quarter|year|week|the\s+end\s+of)\b.{0,80}\bwill\b", re.IGNORECASE),
     "by_calendar_will"),
    # "must X" with a non-modal subject (i.e. NOT "we must verify",
    # "we must consult" -- those are framework requirements, not
    # overclaims). We narrow to copular subjects ("the cohort must",
    # "engagement must", "trust must") via a lookbehind alternative.
    (re.compile(r"\b(?:cohort|cohorts|engagement|trust|adoption|retention|churn|segment|segments|the\s+market|the\s+audience|subscribers|users)\s+must\b",
                re.IGNORECASE),
     "subject_must"),
]


# Hedge patterns: a hedge in the SAME sentence pardons a paraphrase
# match. Mirror of :data:`language_guard.HEDGE_PATTERNS` so the two
# contracts cannot diverge.
HEDGE_PATTERNS: List[Pattern[str]] = [
    re.compile(r"\bmay\b", re.IGNORECASE),
    re.compile(r"\bmight\b", re.IGNORECASE),
    re.compile(r"\bcould\b", re.IGNORECASE),
    re.compile(r"\bsimulation suggests\b", re.IGNORECASE),
    re.compile(r"\bhypothes[ei]s\b", re.IGNORECASE),
    re.compile(r"\bunder these assumptions\b", re.IGNORECASE),
    re.compile(r"\bappears\b", re.IGNORECASE),
    re.compile(r"\bseems\b", re.IGNORECASE),
    re.compile(r"\bplausibly\b", re.IGNORECASE),
    re.compile(r"\bdirectional(?:ly)?\b", re.IGNORECASE),
    re.compile(r"\bif\b.+\bthen\b", re.IGNORECASE),  # conditional
]


# Negation words. When ANY of these appears in the 4-token window
# immediately left of the match, the paraphrase is treated as
# negated and pardoned. The window is small so a downstream
# negation does not silently pardon ("we do not expect X but Y is
# bound to happen" -- here "not" pardons "expect" only, NOT
# "bound to").
_NEGATION_WINDOW = 4
_NEGATION_TOKENS = frozenset({
    "not", "no", "never", "unlikely", "doubtful", "won't", "wont",
    "isn't", "isnt", "aren't", "arent", "wasn't", "wasnt",
    "weren't", "werent",
})


# Sentence splitter: same shape as :mod:`language_guard` -- bullets
# and headings count as sentence boundaries so a hedge on bullet N
# cannot pardon a paraphrase on bullet N+1.
_SENTENCE_BOUNDARY = re.compile(r"(?:[.!?]+\s+)|\n+|(?=^[-*]\s)", re.MULTILINE)


# ----------------------------------------------------------------------
# Finding shape
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class OverclaimFinding:
    """One paraphrase detection inside a single piece of prose.

    Fields:
      * ``pattern_id`` -- the symbolic id from
        :data:`PARAPHRASE_PATTERNS` (e.g. ``"is_set_to"``).
      * ``matched_text`` -- the literal substring that matched.
      * ``sentence`` -- the sentence the match lives in (for the
        operator's triage view).
      * ``hedged`` -- ``True`` when a hedge pattern matched the same
        sentence (advisory finding still emitted; caller can choose
        to suppress hedged findings if it wants).
      * ``negated`` -- ``True`` when a negation token appeared in the
        4-token left window of the match.
    """

    pattern_id: str
    matched_text: str
    sentence: str
    hedged: bool
    negated: bool


# ----------------------------------------------------------------------
# Detection API
# ----------------------------------------------------------------------


def find_overclaim_paraphrases(text: str) -> List[OverclaimFinding]:
    """Return every paraphrase finding in ``text``.

    Operates sentence-locally: a hedge in sentence N pardons a
    paraphrase in sentence N only (consistent with the language
    guard's sentence-local hedge contract). Each finding records its
    hedge / negation status so the caller can choose its severity
    threshold (e.g. demote only on un-hedged un-negated findings).
    """
    if not text:
        return []
    findings: List[OverclaimFinding] = []
    sentences = _split_into_sentences(text)
    for sentence in sentences:
        if not sentence.strip():
            continue
        hedged = any(h.search(sentence) for h in HEDGE_PATTERNS)
        for pattern, pid in PARAPHRASE_PATTERNS:
            for m in pattern.finditer(sentence):
                negated = _is_negated(sentence, m.start())
                findings.append(OverclaimFinding(
                    pattern_id=pid,
                    matched_text=m.group(0),
                    sentence=sentence.strip(),
                    hedged=hedged,
                    negated=negated,
                ))
    return findings


def find_un_pardoned_paraphrases(text: str) -> List[OverclaimFinding]:
    """Subset of :func:`find_overclaim_paraphrases` that excludes
    hedged AND negated findings.

    This is the list a strict caller would use to decide demotion --
    a hedge or a negation in the same sentence is treated as
    legitimate epistemic guarding and the finding is suppressed.
    """
    return [
        f for f in find_overclaim_paraphrases(text)
        if not f.hedged and not f.negated
    ]


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _split_into_sentences(text: str) -> List[str]:
    """Split prose into sentences using the same rules as the
    language guard so paraphrase scope matches banned-token scope."""
    if not text:
        return []
    parts = _SENTENCE_BOUNDARY.split(text)
    return [p for p in parts if p and p.strip()]


def _is_negated(sentence: str, match_start: int) -> bool:
    """Return True iff a negation token appears in the
    :data:`_NEGATION_WINDOW`-token left window of ``match_start``."""
    left = sentence[:match_start]
    tokens = re.findall(r"\w+'?\w*", left.lower())
    window = tokens[-_NEGATION_WINDOW:] if tokens else []
    return any(tok in _NEGATION_TOKENS for tok in window)


# ----------------------------------------------------------------------
# Labelled corpus
# ----------------------------------------------------------------------
#
# Positives (overclaim): a strict caller WOULD demote each of these.
# Negatives (hedged): a strict caller MUST NOT demote any of these
# -- they are legitimately hedged variants that share lexemes with
# the overclaim set. The eval harness pins FN=0 on positives and
# FP=0 on negatives.

OVERCLAIM_CORPUS: List[str] = [
    "engagement is set to climb 30% next quarter",
    "this is bound to drive churn down",
    "we will inevitably see the cohort split",
    "subscribers are destined to churn",
    "the market is poised to react sharply",
    "by Q4 the retention curve will reverse",
    "the cohort must respond to the new pricing",
    "trust must drop after the announcement",
    "we see the split next quarter",
    "we will see the cohort drop off by month 3",
    "this is inevitable given the new policy",
    "engagement is set to triple by year end",
]


HEDGED_CORPUS: List[str] = [
    "engagement may climb if the rollout succeeds",
    "the cohort could split under these assumptions",
    "we hypothesise the retention curve might reverse",
    "simulation suggests subscribers could churn",
    "directional signal: the market appears poised to react",  # 'poised' inside a hedge sentence
    "if the policy lands then trust may drop",
    "the audience seems likely to disengage",
    "we do not expect the cohort to split",
    "no segment is bound to churn under the current draft",  # negation pardons
    "the market is not set to react in any direction",
    "we must verify the cohort response (this is a hypothesis)",  # framework "must"
    "this remains an open hypothesis pending evidence",
]


# ----------------------------------------------------------------------
# Eval harness
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class CorpusEvalResult:
    """Summary of the detector's performance on a labelled corpus.

    Fields:
      * ``positives_total`` -- size of the overclaim corpus.
      * ``positives_caught`` -- positives where
        :func:`find_un_pardoned_paraphrases` returned at least one
        finding (true positives).
      * ``positives_missed`` -- list of positive items the detector
        did NOT catch (false negatives). MUST be empty in a healthy
        run; the CI test fails when this list is non-empty.
      * ``negatives_total`` -- size of the hedged corpus.
      * ``negatives_passed`` -- hedged items where
        :func:`find_un_pardoned_paraphrases` returned zero findings
        (true negatives).
      * ``negatives_flagged`` -- list of hedged items wrongly flagged
        (false positives). MUST be empty in a healthy run.
    """

    positives_total: int
    positives_caught: int
    positives_missed: List[str]
    negatives_total: int
    negatives_passed: int
    negatives_flagged: List[str]


def evaluate_corpus(
    positives: Optional[Iterable[str]] = None,
    negatives: Optional[Iterable[str]] = None,
) -> CorpusEvalResult:
    """Run the detector against the labelled corpus and return a
    structured eval result.

    Defaults to the module-level corpus; callers can substitute their
    own corpora to evaluate the detector against a domain-specific
    set (e.g. a customer-specific paraphrase list).
    """
    pos = list(positives if positives is not None else OVERCLAIM_CORPUS)
    neg = list(negatives if negatives is not None else HEDGED_CORPUS)
    pos_missed = [
        item for item in pos if not find_un_pardoned_paraphrases(item)
    ]
    neg_flagged = [
        item for item in neg if find_un_pardoned_paraphrases(item)
    ]
    return CorpusEvalResult(
        positives_total=len(pos),
        positives_caught=len(pos) - len(pos_missed),
        positives_missed=pos_missed,
        negatives_total=len(neg),
        negatives_passed=len(neg) - len(neg_flagged),
        negatives_flagged=neg_flagged,
    )


__all__ = [
    "CorpusEvalResult",
    "HEDGED_CORPUS",
    "OVERCLAIM_CORPUS",
    "OverclaimFinding",
    "PARAPHRASE_PATTERNS",
    "evaluate_corpus",
    "find_overclaim_paraphrases",
    "find_un_pardoned_paraphrases",
]
