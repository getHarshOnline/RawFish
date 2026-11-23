"""Banned-token regression gate against rendered policy reports.

Mechanically proves the non-predictive-framing contract holds in actual
:func:`render_report` output (not just in source-code review). The gate
catches both the easy cases (a banned verb appearing alone) and the
nuanced ones (a hedge in a previous sentence that does NOT pardon a
banned token in the next sentence -- the sneaky cross-sentence-pardon
failure mode the planted-regression test specifically targets).

Coverage matrix:

* Renderer-output sanity: minimal Report + rich Report (with sections,
  evidence refs, driver hints, risk tags) both pass the gate.
* Caveat-block exemption: the caveat sentence's banned word ("forecast")
  is pardoned because the SAME sentence contains "hypotheses".
* Planted failures (one per banned phrase): "will happen", "will result",
  "predicts", "forecast", "definitely", "certainly", "anticipates",
  "expects", "likely to happen", "guarantees", "ensures".
* Hedged same-sentence cases: each hedge token correctly pardons a
  banned phrase in the same sentence.
* Cross-sentence pardon FAILS: a hedge in sentence N does not protect
  sentence N+1.
* Renderer-integrated failure: a Report constructed with a banned-token
  claim text fails the gate AFTER rendering -- proves the helper scans
  real output, not test-isolated strings.
* Failure message: contains the offending sentence so a CI diagnostic
  is reviewable.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.policy.hypothesis_tags import HypothesisTag
from app.policy.run_metadata import RunMetadata, ScaleCaps
from app.policy.schema import (
    Claim,
    DriverHint,
    EvidenceRef,
    Persona,
    Report,
    Section,
)
from app.services.policy.markdown_renderer import render_report

from tests.policy._banned_tokens import (
    assert_no_unhedged_banned_tokens,
    find_unhedged_banned_tokens,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _build_run_metadata() -> RunMetadata:
    return RunMetadata(
        simulation_id="sim_1",
        created_at=datetime(2026, 6, 26, 12, 0, 0, tzinfo=timezone.utc),
        persona=Persona.CREATOR,
        scenario_hash="a" * 64,
        seed_hash="b" * 64,
        prepared_at=datetime(2026, 6, 26, 11, 59, 0, tzinfo=timezone.utc),
        artifact_paths={"manifest.json": "manifest.json"},
        artifact_hashes={"manifest.json": "c" * 64},
        zep_mode="mock",
        scale_caps=ScaleCaps(
            ensemble_n=1, stakeholders=2, rounds=10, cap_value=500,
        ),
    )


def _build_minimal_report() -> Report:
    rm = _build_run_metadata()
    return Report(
        report_id="report_min_1",
        simulation_id=rm.simulation_id,
        persona=rm.persona,
        scenario_hash=rm.scenario_hash,
        seed_hash=rm.seed_hash,
        generated_at=datetime(2026, 6, 26, 12, 5, 0, tzinfo=timezone.utc),
        sections=[
            Section(
                section_id="s1",
                title="Engagement themes",
                claims=[
                    Claim(
                        claim_id="c1",
                        text="Fans cohort engages with retention prompts.",
                        tag=HypothesisTag.SIMULATED_OBSERVATION,
                    ),
                ],
            ),
        ],
        run_metadata=rm,
    )


def _build_rich_report() -> Report:
    """Report exercising every claim-metadata field path so the rendered
    output covers every line the gate must walk."""
    rm = _build_run_metadata()
    return Report(
        report_id="report_rich_1",
        simulation_id=rm.simulation_id,
        persona=rm.persona,
        scenario_hash=rm.scenario_hash,
        seed_hash=rm.seed_hash,
        generated_at=datetime(2026, 6, 26, 12, 5, 0, tzinfo=timezone.utc),
        executive_summary=[
            Claim(
                claim_id="e1",
                text="Engagement is concentrated in the opening minutes.",
                tag=HypothesisTag.INFERENCE,
                segment_refs=["s_fans"],
                confidence=0.7,
                expected_risk_tags=["H-RETENTION"],
                evidence_refs=[
                    EvidenceRef(ref_type="simulation_action", ref_id="a-uuid-1"),
                    EvidenceRef(ref_type="seed_text_line", ref_id="14"),
                ],
                driver_hints=[
                    DriverHint(
                        kind="segment",
                        ref="s_fans",
                        # Single-hint fixture -> contribution=1.0 to
                        # satisfy the per-claim sum invariant.
                        contribution=1.0,
                        method="cooccurrence_v1",
                    ),
                ],
            ),
        ],
        sections=[
            Section(
                section_id="s1",
                title="Comment cluster risks",
                claims=[
                    Claim(
                        claim_id="c1",
                        text="A bonded sub-cohort plausibly forms around the announcement.",
                        tag=HypothesisTag.INFERENCE,
                    ),
                ],
            ),
        ],
        evidence_gaps=[
            Claim(
                claim_id="g1",
                text="Real-world response of the trader cohort is unknown.",
                tag=HypothesisTag.REQUIRES_VALIDATION,
            ),
        ],
        field_validation_questions=[
            "Survey 30 traders before pricing rollout.",
            "Interview cohort leads about retention drivers.",
        ],
        confidence_notes=(
            "Confidence is directional, not numeric. Treat ensemble means "
            "as hypotheses for human review."
        ),
        source_limitations=[
            "seed_text is a draft",
            "no trader interviews conducted",
        ],
        run_metadata=rm,
    )


# ----------------------------------------------------------------------
# Renderer output passes the gate
# ----------------------------------------------------------------------


def test_minimal_report_render_passes_gate() -> None:
    assert_no_unhedged_banned_tokens(render_report(_build_minimal_report()))


def test_rich_report_render_passes_gate() -> None:
    """A Report exercising every optional claim-metadata field path
    still passes -- the rich rendering does not accidentally introduce
    a banned token via labels / evidence formatting / driver lines."""
    assert_no_unhedged_banned_tokens(render_report(_build_rich_report()))


def test_caveat_sentence_passes_via_hypotheses_hedge() -> None:
    """The caveat line says 'not a forecast' but is pardoned because the
    same sentence contains 'hypotheses' (a hedge token)."""
    out = render_report(_build_minimal_report())
    # The actual caveat text should be present in the output for this
    # test to be meaningful.
    assert "not a forecast" in out
    assert "hypotheses" in out
    assert_no_unhedged_banned_tokens(out)


# ----------------------------------------------------------------------
# Planted failures -- one per banned phrase
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "banned_sentence",
    [
        "This will happen in 6 weeks.",
        "Sales will result in a 30% drop.",
        "The model predicts a backlash.",
        "Our forecast shows churn at 12%.",
        "The cohort will definitely revolt.",
        "Adoption certainly slows in week 3.",
        "We anticipate widespread criticism.",
        "The model expects revenue to fall.",
        "A coordinated boycott is likely to happen.",
        "Compliance is guaranteed by the rollout date.",
        "The redesign ensures retention improves.",
    ],
)
def test_planted_failure_per_banned_phrase(banned_sentence: str) -> None:
    """Each banned phrase, in a sentence with NO hedge, must fail the
    gate. The parametrize ID surfaces the offending sentence in the
    pytest output if a regression silently widens the contract."""
    with pytest.raises(AssertionError):
        assert_no_unhedged_banned_tokens(banned_sentence)


# ----------------------------------------------------------------------
# Hedged same-sentence cases pass
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "hedged_sentence",
    [
        # may / might / could
        "The cohort may forecast a downturn.",
        "Sales might result in churn under stress.",
        "This could ensure a sharper transition.",
        # explicit hedge phrases
        "Simulation suggests this will happen under stress.",
        "These are hypotheses about what the cohort expects.",
        "The risk cluster guarantees nothing without validation.",
        "Under these assumptions sales will result in a small dip.",
        # softer hedges
        "Engagement appears to ensure cohesion.",
        "Engagement seems to definitely peak early.",
        "The cohort plausibly anticipates the move.",
        "Adoption is directionally likely to happen.",
    ],
)
def test_hedged_same_sentence_passes(hedged_sentence: str) -> None:
    """Each hedge token must pardon a banned token in the same sentence."""
    assert_no_unhedged_banned_tokens(hedged_sentence)


# ----------------------------------------------------------------------
# Cross-sentence hedge does NOT pardon
# ----------------------------------------------------------------------


def test_cross_sentence_hedge_does_not_pardon_next_sentence() -> None:
    """A hedge in sentence N must NOT pardon a banned token in sentence N+1.

    This is the most subtle failure mode: a writer who leans on a
    leading hedge ("These are hypotheses.") and then drops the hedge
    in the next sentence ("This will happen in 6 weeks.") would slip
    past a naive whole-document hedge check. The sentence-local logic
    here catches it.
    """
    cross_sentence = "These are hypotheses. This will happen in 6 weeks."
    findings = find_unhedged_banned_tokens(cross_sentence)
    assert len(findings) >= 1, (
        "cross-sentence pardon must NOT save sentence N+1; "
        f"got findings={findings!r}"
    )
    assert any(
        "will happen" in f.matched_text.lower() for f in findings
    ), f"expected 'will happen' finding; got {findings!r}"


def test_same_sentence_hedge_does_pardon_when_hedge_is_present() -> None:
    """Companion to the cross-sentence test: putting the hedge IN the
    same sentence does pardon. This pair pins the boundary."""
    same_sentence = "These are hypotheses; this will happen under stress."
    # The sentence splitter treats ``;`` as a normal character (not a
    # boundary), so "hypotheses" + "will happen" are in the same
    # sentence and the banned token is pardoned.
    assert_no_unhedged_banned_tokens(same_sentence)


# ----------------------------------------------------------------------
# Tag names are NOT hedges (structural marker, not semantic hedging)
# ----------------------------------------------------------------------


def test_tag_name_in_backticks_does_not_pardon_banned_token() -> None:
    """A tag name inside backticks (e.g. ``\\`simulated_observation\\```)
    is a structural marker, not a semantic hedge. Pairing it with an
    otherwise-unhedged banned phrase must fail the gate -- otherwise
    any sentence could trivially pass by name-dropping a tag."""
    bad = "This will happen `simulated_observation` tomorrow."
    findings = find_unhedged_banned_tokens(bad)
    assert len(findings) >= 1, (
        f"backticked tag name must NOT pardon a banned phrase; "
        f"got findings={findings!r}"
    )
    assert any(
        "will happen" in f.matched_text.lower() for f in findings
    ), f"expected 'will happen' finding; got {findings!r}"


def test_rendered_bullet_tag_marker_does_not_pardon_banned_token() -> None:
    """The actual rendered claim-bullet form ``- **[tag]** text...`` must
    not pardon a banned phrase in the same line either. This is the
    failure mode the previous tag-name hedge escape hatch enabled: a
    claim like ``- **[simulated_observation]** This will happen
    tomorrow.`` would have silently passed."""
    bad = "- **[simulated_observation]** This will happen tomorrow."
    findings = find_unhedged_banned_tokens(bad)
    assert len(findings) >= 1, (
        f"rendered claim bullet form must NOT pardon a banned phrase; "
        f"got findings={findings!r}"
    )
    assert any(
        "will happen" in f.matched_text.lower() for f in findings
    ), f"expected 'will happen' finding; got {findings!r}"


@pytest.mark.parametrize(
    "tag_marker",
    [
        "`simulated_observation`",
        "`inference`",
        "`source_grounded`",
        "`assumption`",
        "`requires_validation`",
        "**[simulated_observation]**",
        "**[inference]**",
        "**[source_grounded]**",
        "**[assumption]**",
        "**[requires_validation]**",
    ],
)
def test_no_canonical_tag_marker_is_a_hedge(tag_marker: str) -> None:
    """None of the 5 canonical tag names, in either the backtick or the
    rendered-bullet form, must pardon an otherwise-unhedged banned
    phrase. Parametrize covers all 10 combinations so a future change to
    HEDGE_PATTERNS that re-adds any tag name would surface here."""
    bad = f"This will happen {tag_marker} tomorrow."
    findings = find_unhedged_banned_tokens(bad)
    assert len(findings) >= 1, (
        f"tag marker {tag_marker!r} pardoned a banned phrase; "
        f"got findings={findings!r}"
    )


# ----------------------------------------------------------------------
# Renderer-integrated planted failure
# ----------------------------------------------------------------------


def test_render_with_banned_claim_text_fails_the_gate() -> None:
    """A Report whose claim text contains a banned phrase must fail the
    gate AFTER being rendered through ``render_report``. This proves the
    helper scans actual renderer output -- not a hand-crafted string the
    test author shaped to pass."""
    rm = _build_run_metadata()
    report = Report(
        report_id="report_planted_1",
        simulation_id=rm.simulation_id,
        persona=rm.persona,
        scenario_hash=rm.scenario_hash,
        seed_hash=rm.seed_hash,
        generated_at=datetime(2026, 6, 26, 12, 5, 0, tzinfo=timezone.utc),
        sections=[
            Section(
                section_id="s1",
                title="Engagement themes",
                claims=[
                    Claim(
                        claim_id="c1",
                        # Planted banned sentence with NO hedge. The
                        # renderer must surface this verbatim; the gate
                        # must then catch it.
                        text="This will happen in 6 weeks across the cohort.",
                        tag=HypothesisTag.INFERENCE,
                    ),
                ],
            ),
        ],
        run_metadata=rm,
    )
    rendered = render_report(report)
    # Sanity: the planted text actually landed in the rendered output.
    assert "This will happen in 6 weeks" in rendered
    with pytest.raises(AssertionError) as excinfo:
        assert_no_unhedged_banned_tokens(rendered)
    # Failure message must surface the offending sentence so a CI
    # diagnostic is actionable without grepping the rendered output.
    msg = str(excinfo.value)
    assert "will happen" in msg.lower()
    assert "This will happen in 6 weeks" in msg


# ----------------------------------------------------------------------
# Failure message shape
# ----------------------------------------------------------------------


def test_failure_message_names_pattern_and_sentence() -> None:
    """The AssertionError text must carry the matched token + the
    enclosing sentence so a reviewer can fix the source without
    re-running the gate locally. ``pattern`` appears too but is
    repr-formatted so its raw-regex form (``\\\\bwill happen\\\\b``)
    survives the str(exc) round-trip; the user-facing assertion
    targets the readable substring."""
    with pytest.raises(AssertionError) as excinfo:
        assert_no_unhedged_banned_tokens("This will happen tomorrow.")
    msg = str(excinfo.value)
    # The matched literal token (readable) appears.
    assert "will happen" in msg.lower()
    # The enclosing sentence appears (the splitter keeps the period off
    # for ``.`` terminators -- this is by design so the message is the
    # readable form).
    assert "This will happen tomorrow" in msg
    # The pattern is also surfaced (repr-escaped); a literal substring
    # of the escaped form proves the pattern field is populated.
    assert "will happen" in msg  # appears inside the pattern AND match
    assert "pattern=" in msg
    assert "matched=" in msg
    assert "sentence:" in msg
