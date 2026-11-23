"""Aggregate-claim definition contract across the two consumer helpers.

Two helpers in the codebase decide whether a claim is "aggregate":

  * :func:`app.services.policy.policy_report_generator._is_aggregate_claim`
    -- structural rule: a claim is aggregate iff its ``claim_id`` lives
    in ``Report.executive_summary``. Used by the evidence-binding
    contract to decide whether an ``inference`` claim must be backed by
    a resolvable ``evidence_ref``.
  * :func:`app.services.policy.transparency_runtime._aggregate_class_claims`
    -- structural-plus-tag rule: returns every exec-summary claim PLUS
    every section claim tagged :data:`HypothesisTag.INFERENCE`. Used by
    the ``monte_carlo_coverage`` transparency flag to decide whether
    an ensemble-coverage advisory must fire.

The two helpers DO NOT return the same set today: the transparency-
runtime side is strictly broader (it folds in section-level inference
claims that the generator's binding rule does not treat as aggregate).
This file pins the invariant the two helpers DO agree on -- the
exec-summary set is the subset both helpers always include -- so a
divergence on that smaller, load-bearing subset surfaces here.

If the codebase grows a single shared aggregate-classification helper
that replaces both definitions, the subset assertion in the third
test below tightens to a full set-equality assertion against the
unified helper.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from app.policy.hypothesis_tags import HypothesisTag
from app.policy.run_metadata import RunMetadata, ScaleCaps
from app.policy.schema import (
    Claim,
    Persona,
    Report,
    Section,
)
from app.services.policy.policy_report_generator import (
    _is_aggregate_claim,
)
from app.services.policy.transparency_runtime import (
    _aggregate_class_claims,
)


def _run_metadata() -> RunMetadata:
    return RunMetadata(
        simulation_id="sim_agg_def",
        created_at=datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc),
        persona=Persona.CREATOR,
        scenario_hash="a" * 64,
        seed_hash="b" * 64,
        prepared_at=datetime(2026, 6, 29, 11, 59, 0, tzinfo=timezone.utc),
        artifact_paths={"manifest.json": "manifest.json"},
        artifact_hashes={"manifest.json": "c" * 64},
        zep_mode="mock",
        scale_caps=ScaleCaps(
            ensemble_n=1, stakeholders=1, rounds=10, cap_value=500,
        ),
    )


def _report(
    *,
    exec_claims: Optional[List[Claim]] = None,
    section_claims: Optional[List[Claim]] = None,
) -> Report:
    rm = _run_metadata()
    section = Section(
        section_id="sec_01",
        title="Engagement themes",
        claims=section_claims if section_claims is not None else [
            Claim(
                claim_id="c_sec_inf",
                text="A safe inference about section-level engagement.",
                tag=HypothesisTag.INFERENCE,
            ),
        ],
    )
    return Report(
        report_id="report_agg_def_v1",
        simulation_id=rm.simulation_id,
        persona=rm.persona,
        scope=None,
        scenario_hash=rm.scenario_hash,
        seed_hash=rm.seed_hash,
        generated_at=datetime(2026, 6, 29, 12, 5, 0, tzinfo=timezone.utc),
        executive_summary=exec_claims if exec_claims is not None else [
            Claim(
                claim_id="c_exec_01",
                text="A safe exec-summary observation about engagement.",
                tag=HypothesisTag.SIMULATED_OBSERVATION,
            ),
        ],
        sections=[section],
        run_metadata=rm,
    )


def test_exec_summary_claim_is_aggregate_under_both_helpers() -> None:
    """Every claim in :attr:`Report.executive_summary` is treated as
    aggregate by BOTH helpers.

    This is the load-bearing intersection of the two definitions: the
    evidence-binding contract requires aggregate inference claims to
    cite a resolvable ref, and the monte_carlo_coverage flag fires
    against the same aggregate set. If the two helpers disagreed on
    the exec-summary subset, an aggregate inference claim could pass
    binding without firing a coverage flag (or vice versa) -- which
    would silently weaken the report's transparency guarantees.
    """
    report = _report(
        exec_claims=[
            Claim(
                claim_id="c_exec_inf",
                text="An aggregate inference about cross-segment churn.",
                tag=HypothesisTag.INFERENCE,
            ),
            Claim(
                claim_id="c_exec_obs",
                text="An aggregate observation about cross-segment churn.",
                tag=HypothesisTag.SIMULATED_OBSERVATION,
            ),
        ],
        section_claims=[],  # No section claims; isolate exec-summary set.
    )

    exec_ids = {c.claim_id for c in report.executive_summary}
    # Generator side: every exec-summary claim returns True.
    for claim in report.executive_summary:
        assert _is_aggregate_claim(claim, exec_ids), (
            f"_is_aggregate_claim must treat exec-summary claim "
            f"{claim.claim_id!r} as aggregate"
        )
    # Transparency side: returned set INCLUDES every exec-summary claim.
    transparency_ids = {c.claim_id for c in _aggregate_class_claims(report)}
    assert exec_ids.issubset(transparency_ids), (
        f"_aggregate_class_claims must include the exec-summary "
        f"subset; missing: {exec_ids - transparency_ids!r}"
    )


def test_section_inference_claim_is_aggregate_under_transparency_only() -> None:
    """Pins the present divergence: a section-level ``inference`` claim
    is aggregate under the transparency-runtime helper (which folds in
    every section inference) but NOT under the generator's structural
    helper (which is exec-summary-only).

    This documents the divergence so a unification that collapses the
    two helpers behind a single shared function does not silently
    break the transparency-side coverage flag. A unified helper MUST
    keep section-level inference claims in the aggregate set; if it
    does, this test's expectations flip to assert BOTH helpers agree.
    """
    report = _report(
        exec_claims=[
            Claim(
                claim_id="c_exec_assumption",
                text="An exec-summary assumption (not aggregate-class).",
                tag=HypothesisTag.ASSUMPTION,
            ),
        ],
        section_claims=[
            Claim(
                claim_id="c_sec_inf",
                text="A section-level inference about engagement.",
                tag=HypothesisTag.INFERENCE,
            ),
        ],
    )

    exec_ids = {c.claim_id for c in report.executive_summary}
    section_inf_claim = report.sections[0].claims[0]

    # Generator side: section inference is NOT aggregate
    # (only exec-summary membership counts).
    assert not _is_aggregate_claim(section_inf_claim, exec_ids), (
        "_is_aggregate_claim is exec-summary-only; a section-level "
        "inference must NOT be flagged aggregate by this helper"
    )
    # Transparency side: section inference IS aggregate.
    transparency_ids = {c.claim_id for c in _aggregate_class_claims(report)}
    assert section_inf_claim.claim_id in transparency_ids, (
        "_aggregate_class_claims must fold section-level inference "
        "claims into the aggregate set (monte_carlo_coverage trigger)"
    )


def test_generator_aggregate_set_is_subset_of_transparency_set() -> None:
    """For any Report, the set of claims the generator helper flags
    aggregate MUST be a subset of the set the transparency helper
    returns.

    Equivalent to: ``{c | _is_aggregate_claim(c, exec_ids)} subseteq
    set(_aggregate_class_claims(report))``. This is the weakest
    invariant that keeps the two definitions compatible -- a claim
    that one helper treats as aggregate the OTHER must not treat as
    non-aggregate. The current implementations satisfy this because
    the transparency side is strictly broader.

    If the two helpers are unified behind a single shared function,
    this subset relation tightens to equality; this is the contract
    we can defend across the two-helper state.
    """
    report = _report(
        exec_claims=[
            Claim(
                claim_id="c_exec_inf",
                text="An aggregate inference about cross-segment churn.",
                tag=HypothesisTag.INFERENCE,
            ),
            Claim(
                claim_id="c_exec_obs",
                text="An aggregate observation about cross-segment churn.",
                tag=HypothesisTag.SIMULATED_OBSERVATION,
            ),
        ],
        section_claims=[
            Claim(
                claim_id="c_sec_inf",
                text="A section-level inference about engagement.",
                tag=HypothesisTag.INFERENCE,
            ),
            Claim(
                claim_id="c_sec_assumption",
                text="A section-level assumption (not aggregate).",
                tag=HypothesisTag.ASSUMPTION,
            ),
        ],
    )

    exec_ids = {c.claim_id for c in report.executive_summary}

    # Build the generator-side aggregate set across every claim location.
    generator_aggregate_ids: set[str] = set()
    for claim in report.executive_summary:
        if _is_aggregate_claim(claim, exec_ids):
            generator_aggregate_ids.add(claim.claim_id)
    for section in report.sections:
        for claim in section.claims:
            if _is_aggregate_claim(claim, exec_ids):
                generator_aggregate_ids.add(claim.claim_id)

    transparency_ids = {c.claim_id for c in _aggregate_class_claims(report)}

    missing = generator_aggregate_ids - transparency_ids
    assert not missing, (
        f"_is_aggregate_claim flagged claims that _aggregate_class_claims "
        f"does NOT include: {missing!r}; the two definitions must agree "
        "at least on the smaller (generator) set"
    )
