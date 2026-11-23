"""refresh_audit_entry: atomic, idempotent, schema-validated append.

Pins:

  * Missing ``run_metadata.json`` -> returns ``False`` (no raise).
  * Existing metadata with ``llm_audit=None`` -> first append produces
    a one-element list.
  * Subsequent append produces a two-element list (no overwrite).
  * Atomicity: no orphan ``.tmp`` file in ``run_dir`` after a
    successful append.
  * Schema validation: the persisted JSON round-trips through
    :class:`RunMetadata.model_validate_json`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.policy.run_metadata import (
    LLMAuditEntry,
    RunMetadata,
    ScaleCaps,
    refresh_audit_entry,
)
from app.policy.schema import Persona


def _meta(simulation_id: str = "sim-1") -> RunMetadata:
    return RunMetadata(
        simulation_id=simulation_id,
        created_at=datetime(2026, 6, 28, 12, 0, 0, tzinfo=timezone.utc),
        persona=Persona.POLICY,
        scenario_hash="a" * 64,
        seed_hash="b" * 64,
        prepared_at=datetime(2026, 6, 28, 11, 0, 0, tzinfo=timezone.utc),
        artifact_paths={"manifest.json": "manifest.json"},
        artifact_hashes={"manifest.json": "c" * 64},
        zep_mode="mock",
        scale_caps=ScaleCaps(
            ensemble_n=1, stakeholders=1, rounds=1, cap_value=10,
        ),
    )


def _entry(call_id: str = "c1") -> LLMAuditEntry:
    return LLMAuditEntry(
        call_id=call_id,
        model="m",
        mode="loose",
        tokens_in=1,
        tokens_out=2,
        latency_ms=3,
        phase="report",
        timestamp=datetime(2026, 6, 28, 12, 0, 1, tzinfo=timezone.utc),
    )


def test_refresh_audit_entry_returns_false_when_metadata_missing(tmp_path: Path) -> None:
    assert refresh_audit_entry(tmp_path, _entry()) is False


def test_refresh_audit_entry_appends_into_empty_list(tmp_path: Path) -> None:
    meta = _meta()
    (tmp_path / "run_metadata.json").write_text(meta.model_dump_json())

    assert refresh_audit_entry(tmp_path, _entry("c1")) is True

    reloaded = RunMetadata.model_validate_json(
        (tmp_path / "run_metadata.json").read_text()
    )
    assert reloaded.llm_audit is not None
    assert len(reloaded.llm_audit) == 1
    assert reloaded.llm_audit[0].call_id == "c1"


def test_refresh_audit_entry_appends_idempotently_across_calls(tmp_path: Path) -> None:
    meta = _meta()
    (tmp_path / "run_metadata.json").write_text(meta.model_dump_json())

    refresh_audit_entry(tmp_path, _entry("c1"))
    refresh_audit_entry(tmp_path, _entry("c2"))

    reloaded = RunMetadata.model_validate_json(
        (tmp_path / "run_metadata.json").read_text()
    )
    assert reloaded.llm_audit is not None
    assert [e.call_id for e in reloaded.llm_audit] == ["c1", "c2"]


def test_refresh_audit_entry_leaves_no_temp_orphan(tmp_path: Path) -> None:
    meta = _meta()
    (tmp_path / "run_metadata.json").write_text(meta.model_dump_json())
    refresh_audit_entry(tmp_path, _entry())
    tmp_files = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert tmp_files == []
