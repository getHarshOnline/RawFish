"""Non-predictive-framing language guard for rendered policy reports.

Scans a rendered markdown string for the canonical contract: a small set
of banned verbs (predict / forecast / will happen / ...) may appear only
inside a sentence that also carries a hedge (may / might / could /
hypotheses / appears / ...). A hedge in a PREVIOUS sentence does NOT
pardon a banned token in the next sentence -- sentence-local hedge
logic only. That cross-sentence-pardon failure mode is the regression
helper's primary target.

Lives in ``app/`` (not ``tests/``) so the report generator can call it
before persisting ``policy_report.json``. The runtime check rejects
unhedged banned output rather than writing a contract-violating report
to disk. Tests at ``backend/tests/policy/_banned_tokens.py`` re-export
the public symbols here so existing test files keep working without
duplicating the regex contract.

Allowlisted in ``process/check_no_predictor_language.py`` because this
module DEFINES the banned-token contract -- the same posture as the
spec / README / persona_defaults.py / markdown_renderer.py entries.

Public surface (consumed by tests + by the generator's pre-persist check):

* :data:`BANNED_PATTERNS` -- compiled regexes; one per banned phrase.
  Morphology covers every common inflection (e.g. predict /
  predictions / predictor / predictive; expect / expectation /
  expecting; anticipate / anticipating; guarantee / guaranteeing;
  ensure / ensuring) so a single missed -ing / -ation suffix cannot
  silently slip rendered output past the contract.
* :data:`HEDGE_PATTERNS` -- compiled regexes; one per hedge phrase.
  Tag names (``simulated_observation`` etc.) are deliberately NOT
  hedges -- a structural marker is not semantic hedging.
* :class:`BannedTokenFinding` -- one offending pattern + matched text +
  enclosing sentence so callers can surface a clear failure message.
* :func:`find_unhedged_banned_tokens` -- pure scan; returns ALL
  findings (one per match, not collapsed across repeated matches of
  the same pattern within a single sentence).
* :func:`assert_no_unhedged_banned_tokens` -- raises ``AssertionError``
  with the offending pattern + sentence on the first finding.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Pattern


# ----------------------------------------------------------------------
# Pattern lists (compiled once at import time)
# ----------------------------------------------------------------------
#
# Each pattern is compiled with IGNORECASE so capitalised /
# sentence-leading forms are caught uniformly. Word boundaries are
# anchored explicitly so substrings inside larger words ("Predictor"
# inside "Class Predictor") cannot slip past. The morphology MUST
# cover every common inflection (e.g. -ions plural vs -ion singular);
# missing one would silently let banned content through.
BANNED_PATTERNS: List[Pattern[str]] = [
    # predict family: predict / predicts / predicted / predicting /
    # prediction / predictions / predictor / predictors / predictive.
    re.compile(r"\bpredict(?:s|ed|ing|ion|ions|or|ors|ive)?\b", re.IGNORECASE),
    # forecast family: forecast / forecasts / forecasted / forecasting /
    # forecaster / forecasters.
    re.compile(r"\bforecast(?:s|ed|ing|er|ers)?\b", re.IGNORECASE),
    re.compile(r"\bwill happen\b", re.IGNORECASE),
    re.compile(r"\bwill result\b", re.IGNORECASE),
    re.compile(r"\bdefinitely\b", re.IGNORECASE),
    re.compile(r"\bcertainly\b", re.IGNORECASE),
    # anticipate family: anticipate / anticipates / anticipated /
    # anticipating.
    re.compile(r"\banticipat(?:e|es|ed|ing)\b", re.IGNORECASE),
    # expect family: expect / expects / expected / expecting /
    # expectation / expectations.
    re.compile(r"\bexpect(?:s|ed|ing|ation|ations)?\b", re.IGNORECASE),
    re.compile(r"\blikely to happen\b", re.IGNORECASE),
    # guarantee family: guarantee / guarantees / guaranteed /
    # guaranteeing.
    re.compile(r"\bguarantee(?:s|d|ing)?\b", re.IGNORECASE),
    # ensure family: ensure / ensures / ensured / ensuring.
    re.compile(r"\bensur(?:e|es|ed|ing)\b", re.IGNORECASE),
]


# Hedge patterns are the words / phrases whose presence in the SAME
# sentence as a banned token pardons the banned token. Sentence-local
# only: a hedge in sentence N must NOT pardon a banned token in
# sentence N+1.
#
# Tag NAMES (``simulated_observation`` etc.) are deliberately NOT in
# this list. A tag is a structural marker, not a semantic hedge -- a
# bullet like ``- **[simulated_observation]** This will happen tomorrow.``
# carries no genuine epistemic hedging just because the tag appears in
# the prefix.
HEDGE_PATTERNS: List[Pattern[str]] = [
    re.compile(r"\bmay\b", re.IGNORECASE),
    re.compile(r"\bmight\b", re.IGNORECASE),
    re.compile(r"\bcould\b", re.IGNORECASE),
    re.compile(r"\bsimulation suggests\b", re.IGNORECASE),
    re.compile(r"\bhypothes[ei]s\b", re.IGNORECASE),
    re.compile(r"\brisk cluster\b", re.IGNORECASE),
    re.compile(r"\bunder these assumptions\b", re.IGNORECASE),
    re.compile(r"\bappears\b", re.IGNORECASE),
    re.compile(r"\bseems\b", re.IGNORECASE),
    re.compile(r"\bplausibly\b", re.IGNORECASE),
    re.compile(r"\bdirectional(ly)?\b", re.IGNORECASE),
    re.compile(r"\b(this is a |these are )hypothes[ei]s\b", re.IGNORECASE),
]


# ----------------------------------------------------------------------
# Sentence splitter
# ----------------------------------------------------------------------
#
# Markdown is not prose, so a naive ".  " split misses bullet boundaries
# / heading boundaries. The splitter below treats each of these as a
# sentence terminator so a hedge on one bullet cannot pardon a banned
# token on the next bullet:
#
#   * sentence-ending punctuation: ``.``, ``!``, ``?``
#   * newline (always; bullets and headings are separate sentences)
#   * markdown list-item start (``- `` / ``* ``): the start of a new
#     bullet ends the previous "sentence" for hedge-scope purposes
#
# The regex returns the boundary positions; the caller slices the input
# accordingly. Empty / whitespace-only resulting fragments are dropped.
_SENTENCE_BOUNDARY = re.compile(r"(?:[.!?]+\s+)|\n+|(?=^[-*]\s)", re.MULTILINE)


def _split_into_sentences(markdown: str) -> List[str]:
    """Split rendered markdown into hedge-scope sentences.

    Each bullet / heading / period-terminated phrase is a separate
    "sentence" so a hedge cannot cross those boundaries. Empty fragments
    (from runs of whitespace) are dropped; punctuation is kept on the
    preceding fragment so error messages show the readable form.
    """
    pieces = _SENTENCE_BOUNDARY.split(markdown)
    return [p.strip() for p in pieces if p and p.strip()]


# ----------------------------------------------------------------------
# Finding shape + scan
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class BannedTokenFinding:
    """One offending pattern + the sentence containing it.

    ``pattern`` is the compiled regex object so callers can format its
    ``.pattern`` attribute for the error message; ``matched_text`` is
    the substring the regex actually matched; ``sentence`` is the
    enclosing sentence (rendered exactly as it appears in the input,
    minus surrounding whitespace) so the failure message can be
    pinpointed.
    """

    pattern: Pattern[str]
    matched_text: str
    sentence: str


def find_unhedged_banned_tokens(markdown: str) -> List[BannedTokenFinding]:
    """Return EVERY banned-token match that lacks a same-sentence hedge.

    Pure scan; no side effects. Uses ``finditer`` (not ``search``) so
    repeated matches of the same pattern inside one sentence each
    contribute a finding -- the "all findings" docstring claim is
    literally true, not collapsed-by-pattern. Use this when you want
    to report ALL findings (e.g. a developer-facing CI summary). Use
    :func:`assert_no_unhedged_banned_tokens` when you want a hard fail
    on the first finding.
    """
    findings: List[BannedTokenFinding] = []
    for sentence in _split_into_sentences(markdown):
        # A sentence is hedged if it contains at least one hedge match.
        # Cheap pre-check via any().
        sentence_is_hedged = any(
            hedge.search(sentence) for hedge in HEDGE_PATTERNS
        )
        if sentence_is_hedged:
            continue
        for banned in BANNED_PATTERNS:
            for match in banned.finditer(sentence):
                findings.append(
                    BannedTokenFinding(
                        pattern=banned,
                        matched_text=match.group(0),
                        sentence=sentence,
                    )
                )
    return findings


def assert_no_unhedged_banned_tokens(markdown: str) -> None:
    """Raise ``AssertionError`` on the first unhedged banned-token hit.

    The error message names the pattern and quotes the enclosing
    sentence so a failing CI run lands a reviewable diagnostic without
    a developer having to grep the rendered output themselves.
    """
    findings = find_unhedged_banned_tokens(markdown)
    if not findings:
        return
    first = findings[0]
    raise AssertionError(
        "rendered markdown contains an unhedged banned token "
        f"(pattern={first.pattern.pattern!r}, matched={first.matched_text!r}): "
        f"sentence: {first.sentence!r}"
        + (
            f"  [+{len(findings) - 1} more]" if len(findings) > 1 else ""
        )
    )


def assert_report_render_contract(report: "Report") -> str:
    """Render a Report and assert the language contract holds.

    Returns the rendered markdown on success so callers do not pay the
    render cost twice. Raises ``AssertionError`` (the same exception
    type as :func:`assert_no_unhedged_banned_tokens`) on a language
    contract violation -- the rendered output contains an unhedged
    banned token.

    This is the single shared code path the generator AND the GET
    routes use. Without it the generator's pre-persist gate and the
    routes' read-time gate would drift: a hand-edited / older
    ``policy_report.json`` whose schema parses but whose claim text
    contains "This will happen..." would render cleanly through
    ``render_report`` and reach the wire because the routes only ran
    ``Report.model_validate_json`` and skipped the language check.
    Funnelling both paths through this helper closes that gap.

    Imports are local (inside the function body, not at module top)
    to keep :mod:`app.services.policy.language_guard` importable from
    contexts that do not have :mod:`app.policy.schema` /
    :mod:`app.services.policy.markdown_renderer` available (e.g. the
    regex-only contract module is reusable in isolation for shell
    tools or future CI scripts).
    """
    from app.policy.schema import Report as _Report  # noqa: F401  (runtime check below)
    from app.services.policy.markdown_renderer import render_report

    if not isinstance(report, _Report):
        raise TypeError(
            "assert_report_render_contract expects an "
            "app.policy.schema.Report instance; "
            f"got {type(report).__name__}"
        )
    markdown = render_report(report)
    assert_no_unhedged_banned_tokens(markdown)
    return markdown


# Type-only import below so the ``Report`` annotation on
# :func:`assert_report_render_contract` resolves for tooling without
# pulling the schema module at runtime (the function's body lazy-imports
# it). Wrapped in ``TYPE_CHECKING`` so pyright sees the type but the
# runtime cost is zero.
from typing import TYPE_CHECKING  # noqa: E402  (intentional bottom-of-module placement)

if TYPE_CHECKING:
    from app.policy.schema import Report  # noqa: F401


__all__ = [
    "BANNED_PATTERNS",
    "HEDGE_PATTERNS",
    "BannedTokenFinding",
    "find_unhedged_banned_tokens",
    "assert_no_unhedged_banned_tokens",
    "assert_report_render_contract",
]
