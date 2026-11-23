"""Read-modify-write lock on ``run_metadata.json``.

The ``refresh_*`` helpers in :mod:`app.policy.run_metadata` are
naturally read-modify-write: load record, mutate field, write back.
``atomic_write_text`` makes the *write* itself atomic at the
filesystem layer, but it does NOT serialise the *RMW window*. Without
a lock, two background threads each appending an
:class:`LLMAuditEntry` can both read the same baseline (``len=N``),
both mutate to ``len=N+1``, and both write back -- the last writer
wins and one entry is lost.

This pin asserts that under concurrent ``refresh_audit_entry`` calls
EVERY entry lands on disk. With the per-run-dir RMW lock in place the
final ``llm_audit`` length equals the number of appenders.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

from app.policy.run_metadata import (
    LLMAuditEntry,
    RunMetadata,
    ScaleCaps,
    refresh_audit_entry,
)
from app.policy.schema import Persona


def _seed_metadata(tmp_path: Path) -> None:
    meta = RunMetadata(
        simulation_id="sim-rmw",
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
    (tmp_path / "run_metadata.json").write_text(meta.model_dump_json())


def _entry(call_id: str) -> LLMAuditEntry:
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


def test_concurrent_appenders_do_not_lose_entries(tmp_path: Path) -> None:
    """Eight concurrent appenders -> eight entries on disk.

    Without the RMW lock, the read-then-write race typically loses
    one or more entries; the final length is a Bernoulli-like
    random variable below 8. The lock makes the contract
    deterministic: every appender's mutation is observed by the next.
    """
    _seed_metadata(tmp_path)

    appenders = 8
    barrier = threading.Barrier(appenders)
    errors: list[BaseException] = []

    def _worker(call_id: str) -> None:
        try:
            barrier.wait(timeout=5.0)
            refresh_audit_entry(tmp_path, _entry(call_id))
        except BaseException as exc:  # pragma: no cover - surfaces in assertions
            errors.append(exc)

    threads = [
        threading.Thread(target=_worker, args=(f"c{i}",), daemon=True)
        for i in range(appenders)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)
        assert not t.is_alive(), "appender thread hung"

    assert not errors, f"worker raised: {errors!r}"

    reloaded = RunMetadata.model_validate_json(
        (tmp_path / "run_metadata.json").read_text()
    )
    assert reloaded.llm_audit is not None
    assert len(reloaded.llm_audit) == appenders
    call_ids = {e.call_id for e in reloaded.llm_audit}
    assert call_ids == {f"c{i}" for i in range(appenders)}
