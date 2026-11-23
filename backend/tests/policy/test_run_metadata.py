"""Tests for the typed :class:`RunMetadata` model.

These tests pin the strict-extra contract, the ``zep_mode`` Literal
enforcement, the ``model_dump_json`` / ``model_validate_json``
round-trip identity, and the historical-dict constructor
:func:`RunMetadata.from_policy_dict`.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.policy.run_metadata import (
    CostSummary,
    LLMAuditEntry,
    PhaseCounters,
    RunMetadata,
    ScaleCaps,
    refresh_output_path,
    summarise_llm_audit,
)
from app.policy.schema import Persona


def _make_metadata(**overrides: object) -> RunMetadata:
    """Return a fully-populated :class:`RunMetadata` for round-trip tests.

    Accepts ``**overrides`` so per-test mutation (e.g. setting an empty
    ``scenario_hash`` to drive the rejection-validator) stays a one-line
    diff in the caller rather than a copy-paste of the full payload.
    """
    defaults: dict[str, object] = dict(
        simulation_id="sim_test_001",
        created_at=datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc),
        persona=Persona.POLICY,
        scenario_preset_id="preset_public_policy_demo",
        scenario_hash="0" * 64,
        seed_hash="1" * 64,
        prepared_at=datetime(2026, 6, 25, 11, 59, 0, tzinfo=timezone.utc),
        artifact_paths={"manifest.json": "manifest.json"},
        artifact_hashes={"policy_report.json": "abc123"},
        zep_mode="mock",
        scale_caps=ScaleCaps(
            ensemble_n=2, stakeholders=3, rounds=4, cap_value=500,
        ),
        output_paths={"policy_report.json": "policy_report.json"},
        runner_task_id="task_abc",
        llm_audit=[
            {
                "call_id": "c1",
                "model": "gpt-4o-mini",
                "mode": "loose",
                "tokens_in": 10,
                "tokens_out": 5,
                "latency_ms": 120,
                "phase": "report",
                "timestamp": datetime(2026, 6, 25, 12, 0, 30, tzinfo=timezone.utc),
            },
        ],
    )
    defaults.update(overrides)
    return RunMetadata(**defaults)  # type: ignore[arg-type]  # WHY: dict[str, object] spread fans out to typed kwargs; mypy/pyright cannot prove the per-key types here.


def test_extra_fields_rejected():
    """``extra='forbid'`` MUST trip on unknown keys."""
    payload = _make_metadata().model_dump(mode="json")
    payload["bogus_extra"] = "nope"
    with pytest.raises(ValidationError) as excinfo:
        RunMetadata.model_validate(payload)
    # Pydantic v2 surfaces ``extra_forbidden`` for the offending field.
    assert any(
        err["type"] == "extra_forbidden" and err["loc"] == ("bogus_extra",)
        for err in excinfo.value.errors()
    )


def test_zep_mode_enum_enforced():
    """``zep_mode`` is a ``Literal[mock|graphiti|experimental_cloud]``.

    Any other string MUST fail validation. The three valid values MUST
    all parse cleanly.
    """
    payload = _make_metadata().model_dump(mode="json")
    payload["zep_mode"] = "not_a_real_mode"
    with pytest.raises(ValidationError) as excinfo:
        RunMetadata.model_validate(payload)
    assert any(
        err["loc"] == ("zep_mode",) for err in excinfo.value.errors()
    )

    for valid_mode in ("mock", "graphiti", "experimental_cloud"):
        payload["zep_mode"] = valid_mode
        # Should not raise.
        RunMetadata.model_validate(payload)


def test_json_round_trip_is_identity():
    """``model_dump_json`` -> ``model_validate_json`` returns equal model."""
    original = _make_metadata()
    blob = original.model_dump_json()
    restored = RunMetadata.model_validate_json(blob)
    assert restored == original
    # Idempotent: re-dumping the restored model produces an identical blob.
    assert restored.model_dump_json() == blob


def test_from_policy_dict_accepts_historical_shape():
    """The transitional constructor consumes the historical inline dict.

    The historical call site built a loose dict with ``simulation_id``,
    ``created_at`` (ISO string), ``agents`` / ``rounds`` / ``ensemble_n``,
    ``zep_mode``, ``expensive_override``, and the ``_rough_cost_estimate``
    spread. ``from_policy_dict`` MUST accept that bag (ignoring the
    cost-keys + expensive-override + loose ``agents`` int that no longer
    live on the typed model) when given the new required fields as
    overrides -- which now includes the provenance set
    (``scenario_hash``, ``seed_hash``, ``prepared_at``,
    ``artifact_paths``, ``artifact_hashes``).
    """
    legacy = {
        "simulation_id": "sim_legacy_42",
        "created_at": "2026-06-25T12:00:00+00:00",
        "agents": 5,
        "rounds": 3,
        "ensemble_n": 2,
        "zep_mode": "mock",
        "expensive_override": False,
        "base_tokens_per_call": 100,
        "total_llm_calls": 60,
        "est_total_tokens": 6000,
        "method": "phase4_v2",
    }
    meta = RunMetadata.from_policy_dict(
        legacy,
        persona=Persona.CREATOR,
        scenario_hash="a" * 64,
        seed_hash="b" * 64,
        prepared_at=datetime(2026, 6, 25, 11, 59, 0, tzinfo=timezone.utc),
        artifact_paths={"manifest.json": "manifest.json"},
        artifact_hashes={"manifest.json": "c" * 64},
        scale_caps=ScaleCaps(
            ensemble_n=2, stakeholders=4, rounds=3, cap_value=500,
        ),
    )
    assert meta.simulation_id == "sim_legacy_42"
    assert meta.zep_mode == "mock"
    assert meta.persona == Persona.CREATOR
    assert meta.scale_caps.cap_value == 500
    # ISO string was coerced to a timezone-aware ``datetime``.
    assert meta.created_at == datetime(2026, 6, 25, 12, 0, 0, tzinfo=timezone.utc)
    # Provenance fields preserved verbatim.
    assert meta.scenario_hash == "a" * 64
    assert meta.seed_hash == "b" * 64
    assert meta.artifact_hashes == {"manifest.json": "c" * 64}
    assert meta.runner_task_id is None
    assert meta.llm_audit is None


@pytest.mark.parametrize("field", ["scenario_hash", "seed_hash"])
def test_empty_hash_string_rejected(field):
    """Empty / whitespace-only ``scenario_hash`` / ``seed_hash`` are rejected.

    Catches the regression where the inline call site forgets to wire
    the provenance fields and the typed model silently accepts ``""``.
    Both an empty string (caught by ``min_length=1``) and a
    whitespace-only string (caught by the model_validator) must trip
    ValidationError.
    """
    for bad in ("", "   "):
        with pytest.raises(ValidationError):
            _make_metadata(**{field: bad})


def test_empty_artifact_paths_rejected():
    """Empty ``artifact_paths`` mapping is rejected at validation time."""
    with pytest.raises(ValidationError):
        _make_metadata(artifact_paths={})


def test_empty_artifact_hashes_rejected():
    """Empty ``artifact_hashes`` mapping is rejected at validation time."""
    with pytest.raises(ValidationError):
        _make_metadata(artifact_hashes={})


def test_empty_string_inside_artifact_hashes_rejected():
    """Whitespace-only value inside ``artifact_hashes`` is rejected.

    Closes the validator-walk hole the top-level ``min_length=1`` does
    not catch: a non-empty dict whose value is an empty string.
    """
    with pytest.raises(ValidationError):
        _make_metadata(artifact_hashes={"manifest.json": ""})


def test_naive_prepared_at_rejected():
    """Naive (tz-less) ``prepared_at`` is rejected.

    Pairs with the ``created_at`` convention -- both timestamps on the
    operational metadata MUST be timezone-aware so on-disk ordering is
    unambiguous.
    """
    with pytest.raises(ValidationError):
        _make_metadata(prepared_at=datetime(2026, 6, 25, 11, 59, 0))


# ---------------------------------------------------------------------------
# Run-metadata enrichment tests: LLMAuditEntry, CostSummary,
# refresh_output_path, summarise_llm_audit.
# ---------------------------------------------------------------------------


def _entry(**overrides: object) -> LLMAuditEntry:
    """Build a valid :class:`LLMAuditEntry` for tests."""
    defaults: dict[str, object] = dict(
        call_id="c1",
        model="gpt-4o-mini",
        mode="loose",
        tokens_in=10,
        tokens_out=5,
        latency_ms=120,
        phase="report",
        timestamp=datetime(2026, 6, 25, 12, 0, 30, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return LLMAuditEntry(**defaults)  # type: ignore[arg-type]


def test_llm_audit_entry_extra_fields_rejected():
    """Strict ``extra='forbid'`` pins the typed contract."""
    with pytest.raises(ValidationError):
        LLMAuditEntry(
            call_id="c1",
            model="m",
            mode="loose",
            tokens_in=0,
            tokens_out=0,
            latency_ms=0,
            phase="other",
            timestamp=datetime(2026, 6, 25, tzinfo=timezone.utc),
            stray_key="nope",  # type: ignore[call-arg]
        )


def test_llm_audit_entry_naive_timestamp_rejected():
    """Timestamps MUST be timezone-aware."""
    with pytest.raises(ValidationError):
        _entry(timestamp=datetime(2026, 6, 25, 12, 0, 30))


def test_llm_audit_entry_negative_tokens_rejected():
    """Non-negative invariant on counter fields."""
    with pytest.raises(ValidationError):
        _entry(tokens_in=-1)


def test_llm_audit_entry_phase_literal_enforced():
    """Phase is constrained to the 7 canonical values."""
    with pytest.raises(ValidationError):
        _entry(phase="bogus_phase")  # type: ignore[arg-type]


def test_summarise_llm_audit_empty_list_returns_zero_summary():
    """A run with zero LLM calls yields an all-zero summary."""
    summary = summarise_llm_audit([])
    assert summary.total_calls == 0
    assert summary.total_tokens_in == 0
    assert summary.total_tokens_out == 0
    assert summary.total_latency_ms == 0
    assert summary.total_errors == 0
    assert summary.by_phase == {}


def test_summarise_llm_audit_sums_and_buckets_by_phase():
    """Counters sum across entries; by_phase rolls up per-phase."""
    entries = [
        _entry(call_id="c1", phase="ontology", tokens_in=10, tokens_out=5, latency_ms=100),
        _entry(call_id="c2", phase="ontology", tokens_in=20, tokens_out=8, latency_ms=200),
        _entry(call_id="c3", phase="report", tokens_in=50, tokens_out=30, latency_ms=300),
        _entry(call_id="c4", phase="report", tokens_in=0, tokens_out=0, latency_ms=50, error="rate_limit"),
    ]
    s = summarise_llm_audit(entries)
    assert s.total_calls == 4
    assert s.total_tokens_in == 80
    assert s.total_tokens_out == 43
    assert s.total_latency_ms == 650
    assert s.total_errors == 1
    assert s.by_phase["ontology"] == PhaseCounters(
        calls=2, tokens_in=30, tokens_out=13, errors=0,
    )
    assert s.by_phase["report"] == PhaseCounters(
        calls=2, tokens_in=50, tokens_out=30, errors=1,
    )


def test_cost_summary_extra_fields_rejected():
    with pytest.raises(ValidationError):
        CostSummary(
            total_calls=0, total_tokens_in=0, total_tokens_out=0,
            total_latency_ms=0, total_errors=0, by_phase={},
            stray="nope",  # type: ignore[call-arg]
        )


def test_refresh_output_path_skips_when_metadata_missing(tmp_path):
    """Helper returns False (no raise) when run_metadata.json is absent."""
    assert refresh_output_path(tmp_path, "sensitivity.json", "deadbeef") is False


def test_refresh_output_path_updates_in_place(tmp_path):
    """Existing metadata gets the new output_paths + artifact_hashes entry."""
    initial = _make_metadata()
    (tmp_path / "run_metadata.json").write_text(initial.model_dump_json())
    assert refresh_output_path(tmp_path, "sensitivity.json", "abc123def4567890") is True
    reloaded = RunMetadata.model_validate_json(
        (tmp_path / "run_metadata.json").read_text()
    )
    assert reloaded.output_paths["sensitivity.json"] == "sensitivity.json"
    assert reloaded.artifact_hashes["sensitivity.json"] == "abc123def4567890"
    # Earlier entries are preserved.
    assert reloaded.output_paths["policy_report.json"] == "policy_report.json"
    assert reloaded.artifact_hashes["policy_report.json"] == "abc123"


def test_refresh_output_path_honours_relative_path_override(tmp_path):
    """``relative_path`` overrides the default ``artifact_name`` placement."""
    initial = _make_metadata()
    (tmp_path / "run_metadata.json").write_text(initial.model_dump_json())
    refresh_output_path(
        tmp_path,
        artifact_name="v_alt_42__scenario.json",
        sha256_hex="cafef00d" * 4,
        relative_path="variants/v_alt_42__scenario.json",
    )
    reloaded = RunMetadata.model_validate_json(
        (tmp_path / "run_metadata.json").read_text()
    )
    assert reloaded.output_paths["v_alt_42__scenario.json"] == "variants/v_alt_42__scenario.json"


def test_refresh_output_path_raises_on_corrupt_existing(tmp_path):
    """Corrupt run_metadata.json MUST surface as a ValidationError, not a silent skip."""
    (tmp_path / "run_metadata.json").write_text('{"simulation_id": "sim_x"}')  # missing required fields
    with pytest.raises(ValidationError):
        refresh_output_path(tmp_path, "sensitivity.json", "deadbeef")


def test_cost_summary_optional_default_is_none():
    """RunMetadata.cost_summary defaults to None until run completion."""
    meta = _make_metadata()
    assert meta.cost_summary is None


def test_run_metadata_errors_warnings_default_empty():
    """RunMetadata.errors / warnings default to empty lists."""
    meta = _make_metadata()
    assert meta.errors == []
    assert meta.warnings == []
