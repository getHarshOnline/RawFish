"""Deterministic markdown renderer for the policy :class:`Report` model.

Pure function: ``render_report(report) -> str``. Same ``Report`` in, same
markdown bytes out. The renderer is deliberately stripped of every source
of nondeterminism:

* No clock reads (``datetime.now`` / ``time.time``). The two timestamps
  in the output (``report.generated_at`` and the embedded
  ``run_metadata.prepared_at`` / ``.created_at``) are FIELDS on the input
  ``Report`` -- they were captured at report-construction time and are
  part of the persisted artifact, so rendering the same ``Report`` twice
  reproduces the same string.
* No environment reads (``os.environ``).
* No filesystem reads or writes.
* No random sources.
* No network calls.
* No LLM client construction or invocation.
* No Flask or web framework imports.
* No dependency on the report-agent module. The
  policy lane renders from its persisted report model only.

Imports are restricted to :mod:`app.policy` models and standard-library
typing. A test pins the import-graph invariant so a future refactor
cannot quietly add an LLM or Flask dependency.

The output format mirrors the canonical report-and-hypothesis-tags
template. Field references resolve against the live Pydantic models;
a missing field is a renderer bug, not a template-only TODO.
"""
from __future__ import annotations

from typing import List

from ...policy.schema import Claim, EvidenceRef, Report, ValidityFlag


# ----------------------------------------------------------------------
# Caveat block
# ----------------------------------------------------------------------
#
# The caveat block must satisfy two contracts at once:
#   1. Surface the non-predictive framing prominently at the top of every
#      rendered report.
#   2. Not trip the eventual banned-token regex gate (the planted-
#      regression test). The gate forbids tokens like ``forecast`` /
#      ``predict`` unless the same sentence contains a hedge token (one
#      of ``hypothesis`` / ``simulation suggests`` / ``may`` / ``might``
#      / ``could`` / ``risk cluster`` / ``under these assumptions``).
#
# The wording below is constructed so every banned token sits in a
# sentence that also carries a hedge: "hypotheses" + "forecast" co-occur
# in sentence 1; "hypotheses" alone in sentences 2 and 3.
_CAVEAT_BLOCK = (
    "> **Caveat:** This is a simulated pre-mortem of audience reactions -- "
    "these are hypotheses, not a forecast. Every claim is tagged "
    "`simulated_observation` / `inference` / `source_grounded` / "
    "`assumption` / `requires_validation`. Treat the report as structured "
    "hypotheses for human review."
)


# ----------------------------------------------------------------------
# Empty-claim-list placeholder
# ----------------------------------------------------------------------
#
# Sections (and the executive-summary / evidence-gaps blocks) with zero
# claims render the placeholder below rather than disappearing. The
# placeholder is a stable string the renderer always emits in the empty
# case, so two renders of the same empty section are byte-identical.
_EMPTY_CLAIMS_PLACEHOLDER = "_(no claims emitted)_"


# ----------------------------------------------------------------------
# Confidence band bucket (categorical, not numeric percent)
# ----------------------------------------------------------------------
#
# The schema permits ``Claim.confidence`` as Optional[float] in [0, 1].
# The rendered surface buckets it into three categorical labels so the
# reader cannot mistake the value for calibrated probability:
#   confidence <  0.34          -> "low"
#   0.34 <= c   <  0.67         -> "advisory"
#   0.67 <= c                   -> "source-grounded"
# Mirrors the categorical band used by the cockpit claim renderer.

_CONFIDENCE_BAND_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.34, "low"),
    (0.67, "advisory"),
)


def _confidence_band(value: float) -> str:
    """Return the categorical confidence label for ``value`` in [0, 1].

    Out-of-range values clamp: a stray ``confidence: 5`` reads as
    ``source-grounded`` rather than raising. The Pydantic ``Claim``
    schema already constrains the field to [0, 1]; this is
    belt-and-suspenders against hand-edited / older state.json blobs.
    """
    if value is None:
        return "low"
    for threshold, label in _CONFIDENCE_BAND_THRESHOLDS:
        if value < threshold:
            return label
    return "source-grounded"


def render_report(report: Report) -> str:
    """Render the canonical :class:`Report` to deterministic markdown.

    The returned string ends in exactly one trailing newline. All field
    values come from the input ``report`` model (and its embedded
    ``run_metadata`` snapshot) so the same ``Report`` produces the same
    bytes on every call.
    """
    lines: List[str] = []

    # Title: persona always present; scope only when non-None (which the
    # Report-level discriminator validator ensures is exactly when
    # persona == 'policy'). Tag values are the string form
    # (``persona.value``), never the enum repr.
    persona_str = report.persona.value
    if report.scope is not None:
        title = f"# Audience Reaction Report -- {persona_str} / {report.scope.value}"
    else:
        title = f"# Audience Reaction Report -- {persona_str}"
    lines.append(title)
    lines.append("")

    # Non-predictive framing caveat -- must appear at the top, above any
    # metadata block, so a reader scanning the report does not encounter
    # claims before the framing.
    lines.append(_CAVEAT_BLOCK)
    lines.append("")

    # Metadata block. Field references resolve against the live Pydantic
    # models (Report header + embedded RunMetadata snapshot). The
    # ScaleCaps multiplicands render as a single line so a reader can
    # see the cap product at a glance.
    rm = report.run_metadata
    sc = rm.scale_caps
    lines.append(f"**Report:** {report.report_id}")
    lines.append(f"**Simulation:** {report.simulation_id}")
    lines.append(f"**Generated:** {report.generated_at.isoformat()}")
    lines.append(f"**Run prepared:** {rm.prepared_at.isoformat()}")
    lines.append(f"**Run started:** {rm.created_at.isoformat()}")
    lines.append(f"**Scenario hash:** {report.scenario_hash}")
    lines.append(f"**Seed hash:** {report.seed_hash}")
    lines.append(f"**Audience store:** {rm.zep_mode}")
    lines.append(
        f"**Scale:** ensemble_n={sc.ensemble_n} x stakeholders={sc.stakeholders} "
        f"x rounds={sc.rounds} (cap {sc.cap_value})"
    )
    lines.append("")

    # Executive summary.
    lines.append("## Executive summary")
    lines.append("")
    _extend_with_claims(lines, report.executive_summary)
    lines.append("")

    # Per-section bodies in render order.
    for section in report.sections:
        lines.append(f"## {section.title}")
        lines.append("")
        _extend_with_claims(lines, section.claims)
        lines.append("")

    # Evidence gaps.
    lines.append("## Evidence gaps")
    lines.append("")
    _extend_with_claims(lines, report.evidence_gaps)
    lines.append("")

    # Validity and transparency notes. Emitted ONLY when at least one
    # validity flag was populated by the transparency post-processor
    # so unaffected fixtures keep their deterministic-byte stability.
    if report.validity_flags:
        lines.append("## Validity and transparency notes")
        lines.append("")
        for flag in report.validity_flags:
            _extend_with_validity_flag(lines, flag)
        lines.append("")

    # Field validation questions.
    lines.append("## Field validation questions")
    lines.append("")
    if report.field_validation_questions:
        for question in report.field_validation_questions:
            lines.append(f"- {question}")
    else:
        lines.append("_(no questions)_")
    lines.append("")

    # Confidence and limitations. ``confidence_notes`` is rendered verbatim
    # (multi-line allowed); ``source_limitations`` follow as a bullet list.
    lines.append("## Confidence and limitations")
    lines.append("")
    if report.confidence_notes.strip():
        lines.append(report.confidence_notes)
    else:
        lines.append("_(no confidence notes)_")
    lines.append("")
    if report.source_limitations:
        for limitation in report.source_limitations:
            lines.append(f"- {limitation}")
        lines.append("")

    # Run metadata pointer. The full snapshot is already embedded in the
    # report's ``run_metadata`` field (validated against the header by
    # ``Report._header_matches_embedded_run_metadata``); the standalone
    # ``run_metadata.json`` file on disk is the human-facing artifact a
    # reviewer can open separately.
    lines.append("## Run metadata")
    lines.append("")
    lines.append(
        "(full snapshot embedded in this report's `run_metadata` field; "
        "also persisted standalone at `run_metadata.json` next to the "
        "report)"
    )

    # Exactly one trailing newline. Strip trailing whitespace from the
    # joined output then add a single ``\n`` so platform-specific text
    # editors do not surface a missing-final-newline complaint.
    return "\n".join(lines).rstrip() + "\n"


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _extend_with_claims(lines: List[str], claims: List[Claim]) -> None:
    """Append the markdown for a list of claims (or the empty-list
    placeholder) to ``lines``.

    Each claim renders as one bullet with the ``[tag]`` prefix + text,
    plus indented metadata lines for any non-empty optional fields.
    Tag values are the string form (``claim.tag.value``) -- never the
    enum's ``repr()`` which would leak the Python type name.
    """
    if not claims:
        lines.append(_EMPTY_CLAIMS_PLACEHOLDER)
        return
    for claim in claims:
        lines.append(f"- **[{claim.tag.value}]** {claim.text}")
        if claim.segment_refs:
            lines.append(
                f"  - Segments: {', '.join(claim.segment_refs)}"
            )
        if claim.confidence is not None:
            # Confidence renders as a CATEGORICAL band, never a raw
            # numeric. The schema-level field stays Optional[float] in
            # 0..1 so the LLM-owned payload can carry whatever Pydantic
            # accepts, but the rendered surface buckets it -- the
            # "Not a Predictor" contract pins confidence at the
            # directional level; emitting "0.82" in markdown would
            # read as calibrated probability the synthetic-agent
            # ensemble cannot deliver.
            band = _confidence_band(claim.confidence)
            lines.append(f"  - Confidence band: {band}")
        if claim.expected_risk_tags:
            # Visible label deliberately avoids the verb "expect" /
            # "expected" (banned by the rendered-output regression
            # test). The Python field name stays ``expected_risk_tags``
            # because it's the schema-level contract; only the visible
            # markdown label changes.
            lines.append(
                f"  - Risk tags: {', '.join(claim.expected_risk_tags)}"
            )
        if claim.evidence_refs:
            formatted = " | ".join(
                _format_evidence_ref(e) for e in claim.evidence_refs
            )
            lines.append(f"  - Evidence: {formatted}")
        if claim.driver_hints:
            for hint in claim.driver_hints:
                lines.append(
                    f"  - Driver: {hint.kind}/{hint.ref} "
                    f"(contribution={hint.contribution}, "
                    f"method={hint.method}, "
                    f"confidence={hint.attribution_confidence}) "
                    f"-- {hint.attribution_disclaimer}"
                )


def _extend_with_validity_flag(lines: List[str], flag: ValidityFlag) -> None:
    """Append the markdown for a single :class:`ValidityFlag` bullet.

    The bullet shape is:
      ``- **[<severity>]** _<kind>_ -- <message> (method=<method>,
        confidence=<confidence>)``
    plus optional indented sub-lines for any non-empty list field
    (claim_refs / segment_refs / source_fields) and the optional
    ``field_validation_prompt``. Pure: iteration order follows the
    flag fields as declared on the model; the caller is responsible
    for the outer flag-list ordering (which is already deterministic
    via :func:`derive_validity_flags`).
    """
    lines.append(
        f"- **[{flag.severity}]** _{flag.kind}_ -- {flag.message} "
        f"(method={flag.method}, confidence={flag.confidence})"
    )
    if flag.claim_refs:
        lines.append(f"  - Claims: {', '.join(flag.claim_refs)}")
    if flag.segment_refs:
        lines.append(f"  - Segments: {', '.join(flag.segment_refs)}")
    if flag.source_fields:
        lines.append(
            f"  - Source fields: {', '.join(flag.source_fields)}"
        )
    if flag.field_validation_prompt:
        lines.append(f"  - Field check: {flag.field_validation_prompt}")


def _format_evidence_ref(ref: EvidenceRef) -> str:
    """Render one :class:`EvidenceRef` to its canonical link form.

    The 4 ``ref_type`` values map to distinct link shapes so a reader
    can see at a glance which source kind backs the claim:

    * ``simulation_action`` -> ``actions.jsonl#<ref_id>``
    * ``seed_text_line`` -> ``<ref_id>`` verbatim (the catalog stores
      the full ``seed_text:L<n>`` key and the LLM is asked to echo
      that key back; prepending ``seed_text:L`` here would produce
      ``seed_text:Lseed_text:L<n>``).
    * ``agent_interview`` -> ``interviews/<ref_id>.json``
    * ``external_source`` -> ``<ref_id>`` (the ref_id verbatim inside a
      single pair of angle-brackets so adjacent rendered prose cannot
      silently extend the URL).
    """
    if ref.ref_type == "simulation_action":
        return f"actions.jsonl#{ref.ref_id}"
    if ref.ref_type == "seed_text_line":
        # The catalog publishes keys of the form ``seed_text:L<n>`` and
        # the binding check (:meth:`EvidenceCatalog.contains`) matches
        # against those keys verbatim, so a valid ``ref_id`` IS the
        # full key. Render it as-is. Pre-existing payloads that carry
        # just the bare line number (e.g. ``ref_id="14"``) still
        # produce a recognisable link by being prefixed -- this is the
        # backward-compat path; new code should always emit the full
        # key so the renderer takes the verbatim branch.
        if ref.ref_id.startswith("seed_text:L"):
            return ref.ref_id
        return f"seed_text:L{ref.ref_id}"
    if ref.ref_type == "agent_interview":
        return f"interviews/{ref.ref_id}.json"
    # external_source: render the ref_id verbatim inside angle brackets
    # so adjacent rendered prose cannot silently extend the URL.
    return f"<{ref.ref_id}>"


__all__ = ["render_report"]
