"""Advisory semantic pass: planted overclaims and numeric contradictions
MUST be demoted before persist.

These tests run the full ``PolicyReportGenerator.generate_and_persist``
flow with stub LLM payloads that carry:

  * a predictive-overclaim paraphrase ("is set to climb 30%") that
    the deterministic banned-token regex does NOT catch but the
    paraphrase corpus DOES, and
  * a numeric claim ("30% lift") that disagrees with the run's
    ``sensitivity.json`` artifact when one is present.

The persisted ``policy_report.json`` MUST reflect the demotion
(claim tag flipped to ``requires_validation``) AND ``run_metadata.json``
MUST carry the operator-readable warning describing which verifier
fired and why.

The flow tests under ``tests/services/policy/test_policy_report_generator.py``
exercise the happy path with the default fixture; THIS file exercises
the failure modes the advisory pass exists to catch.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.policy.hypothesis_tags import HypothesisTag
from app.policy.schema import Persona, Report
from app.policy.sensitivity.oat_runner import (
    FactorSummary,
    OATRunEntry,
    SensitivityReport,
)
from app.services.policy.policy_report_generator import PolicyReportGenerator

from tests.services.policy.test_policy_report_generator import (
    _StubLLM,
    _StubScenario,
    _llm_payload_for_persona,
    _minimal_run_metadata,
)


def _payload_with_first_claim_text(persona: Persona, text: str) -> dict:
    """Build a per-persona payload and rewrite ``sections[0].claims[0].text``
    to ``text``. Tag stays SOURCE_GROUNDED + ref stays ``seed_text:L1``
    so the upstream evidence-binding step is unaffected; the only
    surface under test is the new advisory pass."""
    payload = _llm_payload_for_persona(persona, None)
    payload["sections"][0]["claims"][0]["text"] = text
    return payload


def _write_sensitivity_report(run_dir: Path, abs_swing: float) -> None:
    """Persist a minimal ``sensitivity.json`` with ONE factor whose
    swing magnitude is ``abs_swing``. The factor name does NOT
    appear in any planted claim text so the verifier's claim-link
    narrowing can be exercised separately."""
    from datetime import datetime, timezone

    runs = [
        OATRunEntry(
            factor_id="orthogonal_factor",
            perturbation=-0.15,
            baseline_metric=0.5,
            perturbed_metric=0.5 - abs_swing / 2,
            delta=-abs_swing / 2,
            rank=0,
        ),
        OATRunEntry(
            factor_id="orthogonal_factor",
            perturbation=0.15,
            baseline_metric=0.5,
            perturbed_metric=0.5 + abs_swing / 2,
            delta=abs_swing / 2,
            rank=1,
        ),
    ]
    factors = [
        FactorSummary(
            factor="orthogonal_factor",
            delta_low=-abs_swing / 2,
            delta_high=abs_swing / 2,
            swing_low=abs_swing / 2,
            swing_high=abs_swing / 2,
            abs_swing=abs_swing,
        ),
    ]
    sens = SensitivityReport(
        simulation_id="sim_advisory_test",
        baseline_run_id="baseline",
        n_perturbations=len(runs),
        capped=False,
        max_runs=16,
        runs=runs,
        factors=factors,
        ranking=["orthogonal_factor"],
        generated_at=datetime(2026, 6, 29, tzinfo=timezone.utc),
    )
    (run_dir / "sensitivity.json").write_text(
        sens.model_dump_json(indent=2), encoding="utf-8",
    )


# ----------------------------------------------------------------------
# Overclaim paraphrase demotion
# ----------------------------------------------------------------------


def test_planted_overclaim_paraphrase_is_demoted_before_persist(
    tmp_path: Path,
) -> None:
    """A claim whose prose carries an un-hedged paraphrased overclaim
    ("is set to climb") MUST be demoted to ``requires_validation``
    in the persisted ``policy_report.json`` AND a warning MUST land
    on the persisted ``run_metadata.json``.

    The deterministic banned-token regex does NOT catch "is set to";
    the paraphrase corpus is the layer that does. This test pins
    that the corpus is actually wired into the persist path (not
    merely shipped as a module).
    """
    persona = Persona.CREATOR
    payload = _payload_with_first_claim_text(
        persona,
        "engagement is set to climb next quarter for the cohort.",
    )
    scenario = _StubScenario(persona=persona, scope=None)
    rm = _minimal_run_metadata(persona=persona)
    # Pre-write run_metadata.json so refresh_warning has a target.
    (tmp_path / "run_metadata.json").write_text(
        rm.model_dump_json(indent=2), encoding="utf-8",
    )

    gen = PolicyReportGenerator(
        scenario=scenario,  # type: ignore[arg-type]
        llm=_StubLLM(payload),  # type: ignore[arg-type]
    )
    artifacts = gen.generate_and_persist(rm, tmp_path)

    # Persisted JSON: the flagged claim is demoted.
    persisted = Report.model_validate_json(
        artifacts.report_json_path.read_text(encoding="utf-8"),
    )
    demoted = persisted.sections[0].claims[0]
    assert demoted.tag == HypothesisTag.REQUIRES_VALIDATION, (
        f"planted overclaim must be demoted; got tag={demoted.tag!r}"
    )

    # Persisted run_metadata.json: warning describes the verifier.
    on_disk = json.loads(
        (tmp_path / "run_metadata.json").read_text(encoding="utf-8"),
    )
    overclaim_warnings = [
        w for w in on_disk.get("warnings", [])
        if w.startswith("overclaim_paraphrases:")
    ]
    assert overclaim_warnings, (
        f"expected overclaim_paraphrases warning in run_metadata.json; "
        f"got warnings={on_disk.get('warnings', [])!r}"
    )
    assert demoted.claim_id in overclaim_warnings[0]


def test_hedged_paraphrase_is_not_demoted(tmp_path: Path) -> None:
    """The companion safety pin: a HEDGED paraphrase ("under these
    assumptions, engagement is set to climb") MUST NOT be demoted.
    False positives at the advisory layer cost real findings -- the
    sentence-local hedge contract pardons the match."""
    persona = Persona.CREATOR
    payload = _payload_with_first_claim_text(
        persona,
        "under these assumptions, engagement is set to climb.",
    )
    scenario = _StubScenario(persona=persona, scope=None)
    rm = _minimal_run_metadata(persona=persona)
    (tmp_path / "run_metadata.json").write_text(
        rm.model_dump_json(indent=2), encoding="utf-8",
    )

    gen = PolicyReportGenerator(
        scenario=scenario,  # type: ignore[arg-type]
        llm=_StubLLM(payload),  # type: ignore[arg-type]
    )
    artifacts = gen.generate_and_persist(rm, tmp_path)
    persisted = Report.model_validate_json(
        artifacts.report_json_path.read_text(encoding="utf-8"),
    )
    # Tag preserved.
    assert persisted.sections[0].claims[0].tag == HypothesisTag.SOURCE_GROUNDED

    on_disk = json.loads(
        (tmp_path / "run_metadata.json").read_text(encoding="utf-8"),
    )
    overclaim_warnings = [
        w for w in on_disk.get("warnings", [])
        if w.startswith("overclaim_paraphrases:")
    ]
    assert not overclaim_warnings, (
        f"hedged paraphrase should NOT be flagged; got {overclaim_warnings!r}"
    )


# ----------------------------------------------------------------------
# Numeric contradiction demotion
# ----------------------------------------------------------------------


def test_planted_numeric_contradiction_is_demoted_when_sensitivity_present(
    tmp_path: Path,
) -> None:
    """A claim says "30% lift" but the persisted ``sensitivity.json``
    reports only a 5% swing on an unrelated factor. The numeric
    verifier returns CONTRADICTED (no claim-linked factor, and the
    artifact does not carry a 30% swing); the advisory pass demotes
    the claim and persists the warning.
    """
    persona = Persona.CREATOR
    payload = _payload_with_first_claim_text(
        persona,
        "we observed a 30% lift in engagement across the cohort.",
    )
    scenario = _StubScenario(persona=persona, scope=None)
    rm = _minimal_run_metadata(persona=persona)
    (tmp_path / "run_metadata.json").write_text(
        rm.model_dump_json(indent=2), encoding="utf-8",
    )
    _write_sensitivity_report(tmp_path, abs_swing=0.05)

    gen = PolicyReportGenerator(
        scenario=scenario,  # type: ignore[arg-type]
        llm=_StubLLM(payload),  # type: ignore[arg-type]
    )
    artifacts = gen.generate_and_persist(rm, tmp_path)
    persisted = Report.model_validate_json(
        artifacts.report_json_path.read_text(encoding="utf-8"),
    )
    demoted = persisted.sections[0].claims[0]
    assert demoted.tag == HypothesisTag.REQUIRES_VALIDATION

    on_disk = json.loads(
        (tmp_path / "run_metadata.json").read_text(encoding="utf-8"),
    )
    numeric_warnings = [
        w for w in on_disk.get("warnings", [])
        if w.startswith("numeric_claim_verifier:")
        and "CONTRADICTED" in w
    ]
    assert numeric_warnings, (
        f"expected numeric_claim_verifier CONTRADICTED warning; "
        f"got warnings={on_disk.get('warnings', [])!r}"
    )
    assert demoted.claim_id in numeric_warnings[0]


def test_numeric_claim_without_sensitivity_is_not_demoted(
    tmp_path: Path,
) -> None:
    """When no sensitivity artifact exists, a numeric claim becomes
    NOT_VERIFIABLE -- the verifier MUST NOT silently bless it OR
    silently demote it. NOT_VERIFIABLE carries no demotion implication
    today (advisory-only).
    """
    persona = Persona.CREATOR
    payload = _payload_with_first_claim_text(
        persona,
        "we observed a 30% lift in engagement.",
    )
    scenario = _StubScenario(persona=persona, scope=None)
    rm = _minimal_run_metadata(persona=persona)
    (tmp_path / "run_metadata.json").write_text(
        rm.model_dump_json(indent=2), encoding="utf-8",
    )
    # No sensitivity.json written.

    gen = PolicyReportGenerator(
        scenario=scenario,  # type: ignore[arg-type]
        llm=_StubLLM(payload),  # type: ignore[arg-type]
    )
    artifacts = gen.generate_and_persist(rm, tmp_path)
    persisted = Report.model_validate_json(
        artifacts.report_json_path.read_text(encoding="utf-8"),
    )
    # Tag preserved (no demotion on NOT_VERIFIABLE).
    assert persisted.sections[0].claims[0].tag == HypothesisTag.SOURCE_GROUNDED


def test_corrupt_sensitivity_artifact_surfaces_loud_warning(
    tmp_path: Path,
) -> None:
    """When ``sensitivity.json`` EXISTS but cannot be parsed /
    validated, the advisory pass MUST surface an explicit
    ``sensitivity_corrupted`` warning rather than silently
    treating it as "sensitivity not run". None-on-corrupt being
    indistinguishable from None-on-missing would silently disable
    the guardrail.
    """
    persona = Persona.CREATOR
    payload = _payload_with_first_claim_text(
        persona,
        "we observed a 30% lift in engagement.",
    )
    scenario = _StubScenario(persona=persona, scope=None)
    rm = _minimal_run_metadata(persona=persona)
    (tmp_path / "run_metadata.json").write_text(
        rm.model_dump_json(indent=2), encoding="utf-8",
    )
    # Plant a syntactically-broken sensitivity.json: the loader
    # MUST detect this as "exists but unreadable" and surface the
    # loud warning, NOT silently fall through to NOT_VERIFIABLE
    # without explanation.
    (tmp_path / "sensitivity.json").write_text(
        "this is not valid JSON",
        encoding="utf-8",
    )

    gen = PolicyReportGenerator(
        scenario=scenario,  # type: ignore[arg-type]
        llm=_StubLLM(payload),  # type: ignore[arg-type]
    )
    artifacts = gen.generate_and_persist(rm, tmp_path)

    # The report still persists (corrupt sensitivity is a
    # guardrail-disable, not a report-blocker), but the persisted
    # run_metadata.json carries the loud warning.
    persisted = Report.model_validate_json(
        artifacts.report_json_path.read_text(encoding="utf-8"),
    )
    assert persisted is not None

    on_disk = json.loads(
        (tmp_path / "run_metadata.json").read_text(encoding="utf-8"),
    )
    corrupt_warnings = [
        w for w in on_disk.get("warnings", [])
        if "sensitivity_corrupted" in w
    ]
    assert corrupt_warnings, (
        f"expected loud sensitivity_corrupted warning; got "
        f"warnings={on_disk.get('warnings', [])!r}"
    )


def test_unsupported_numeric_logs_advisory_warning_without_demoting(
    tmp_path: Path,
) -> None:
    """A claim that names "30%" with a sensitivity artifact that DOES
    carry a 30% swing but on an UNRELATED factor (no claim link)
    yields UNSUPPORTED. The advisory pass logs the warning but does
    NOT demote (false-positive cost too high at UNSUPPORTED severity).
    """
    persona = Persona.CREATOR
    payload = _payload_with_first_claim_text(
        persona,
        "we observed a 30% lift across the cohort.",
    )
    scenario = _StubScenario(persona=persona, scope=None)
    rm = _minimal_run_metadata(persona=persona)
    (tmp_path / "run_metadata.json").write_text(
        rm.model_dump_json(indent=2), encoding="utf-8",
    )
    # Artifact carries 30% swing on an UNRELATED factor; no claim link.
    _write_sensitivity_report(tmp_path, abs_swing=0.30)

    gen = PolicyReportGenerator(
        scenario=scenario,  # type: ignore[arg-type]
        llm=_StubLLM(payload),  # type: ignore[arg-type]
    )
    artifacts = gen.generate_and_persist(rm, tmp_path)
    persisted = Report.model_validate_json(
        artifacts.report_json_path.read_text(encoding="utf-8"),
    )
    # Tag preserved -- UNSUPPORTED is advisory-only.
    assert persisted.sections[0].claims[0].tag == HypothesisTag.SOURCE_GROUNDED

    on_disk = json.loads(
        (tmp_path / "run_metadata.json").read_text(encoding="utf-8"),
    )
    advisory_warnings = [
        w for w in on_disk.get("warnings", [])
        if w.startswith("numeric_claim_verifier:")
        and "UNSUPPORTED" in w
    ]
    assert advisory_warnings, (
        f"expected UNSUPPORTED advisory warning even without demotion; "
        f"got warnings={on_disk.get('warnings', [])!r}"
    )
