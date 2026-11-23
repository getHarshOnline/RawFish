"""Conformance tests for the guarded auto-report watcher.

The watcher's behaviour contract is:

1. When ``AUTO_REPORT_GENERATE`` is set and a run dir holds
   ``state.json[status]=="completed"`` AND no adapter-declared report
   artifact, a single ``run_once`` cycle invokes the injected
   generator factory and the artifact ends up on disk.
2. The per-simulation lock file makes the trigger single-fire: two
   concurrent ``run_once`` cycles racing the same sim dir cannot both
   invoke the factory. The lock is released after generation so a
   subsequent cycle (after the artifact lands) cleanly observes
   "report already exists" and skips.
3. When the env var is unset the watcher is inert -- ``run_once``
   returns immediately and the factory is never invoked, even on an
   otherwise eligible run dir.
4. Legacy policy-named env aliases do not activate the watcher; the
   only product opt-in is ``AUTO_REPORT_GENERATE``.

The factory in every test is a deterministic stub that writes a
minimal report payload + increments a call counter. The production
LLM stack is never instantiated -- the test isolates the watcher's
eligibility + locking logic from the report-generator internals
(which are exercised by other test files).

A fake use-case adapter is registered for each test so the watcher
can resolve the report filename via the platform registry. The
adapter declares its report filename as ``policy_report.json`` to
keep the historical eligibility expectations intact.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import List

import pytest

from app.simulation_platform import registry as platform_registry
from app.simulation_platform.auto_report_watcher import (
    AutoReportWatcher,
    GenerationOutcome,
)
from app.simulation_platform.tests_helpers import FakeUseCase


# Test-local id used for the registered fake adapter. Avoids
# colliding with the real ``policy`` registration that the app
# factory installs in fuller-stack tests.
_TEST_USE_CASE_ID = "auto_report_test_lane"
_TEST_REPORT_FILENAME = "policy_report.json"


def _registry_snapshot() -> dict:
    """Capture the registry so each test can restore it afterwards.

    Mirrors the snapshot/restore pattern in
    ``tests/platform/test_use_cases_blueprint.py``.
    """
    return dict(platform_registry._REGISTRY)  # type: ignore[attr-defined]


def _registry_restore(snap: dict) -> None:
    platform_registry._REGISTRY.clear()  # type: ignore[attr-defined]
    platform_registry._REGISTRY.update(snap)  # type: ignore[attr-defined]


@pytest.fixture
def registered_fake_adapter():
    """Register a fake adapter whose ``artifact_names()["report_json"]``
    is ``policy_report.json`` so the watcher's eligibility filter
    sees the same filename the tests pre-create. The fixture
    yields the test use-case id and tears down the registry to its
    pre-test snapshot.
    """
    snap = _registry_snapshot()

    class _FakeReportAdapter(FakeUseCase):
        def artifact_names(self):  # type: ignore[override]
            return {"report_json": _TEST_REPORT_FILENAME}

    platform_registry.register(_FakeReportAdapter(uid=_TEST_USE_CASE_ID))
    try:
        yield _TEST_USE_CASE_ID
    finally:
        _registry_restore(snap)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _seed_completed_run(
    data_dir: Path,
    sim_id: str,
    *,
    write_report: bool = False,
) -> Path:
    """Build a run dir whose ``state.json[status]=="completed"``.

    ``write_report`` -- when True, pre-creates ``policy_report.json``
    so the watcher's "already exists" branch fires. The default leaves
    the artifact absent so the watcher is eligible to trigger.
    """
    run_dir = data_dir / sim_id
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id,
            "status": "completed",
            "use_case_id": _TEST_USE_CASE_ID,
        }),
        encoding="utf-8",
    )
    if write_report:
        (run_dir / _TEST_REPORT_FILENAME).write_text(
            "{\"already\": \"there\"}", encoding="utf-8",
        )
    return run_dir


class _CountingFactory:
    """Generator factory stub that records every invocation.

    Writes a minimal report payload on each call so the watcher's
    post-generation "report exists" check fires on the next poll
    cycle (modelling the production behaviour where the adapter's
    generated artifact lands on disk).
    """

    def __init__(self) -> None:
        self.calls: List[str] = []
        self._lock = threading.Lock()

    def __call__(
        self, *, simulation_id: str, run_dir: Path, use_case_id: str,
    ) -> GenerationOutcome:
        with self._lock:
            self.calls.append(simulation_id)
        (run_dir / _TEST_REPORT_FILENAME).write_text(
            json.dumps({"simulation_id": simulation_id, "stub": True}),
            encoding="utf-8",
        )
        return GenerationOutcome(
            simulation_id=simulation_id, report_written=True,
        )


class _FailureOutcomeFactory:
    """Generator factory stub that reports a retryable failure."""

    def __init__(self, reason: str = "llm_provider_not_ready") -> None:
        self.reason = reason
        self.calls: List[str] = []

    def __call__(
        self, *, simulation_id: str, run_dir: Path, use_case_id: str,
    ) -> GenerationOutcome:
        del run_dir, use_case_id
        self.calls.append(simulation_id)
        return GenerationOutcome(
            simulation_id=simulation_id,
            report_written=False,
            reason=self.reason,
        )


def _make_watcher(tmp_path, factory) -> AutoReportWatcher:
    return AutoReportWatcher(
        use_case_id=_TEST_USE_CASE_ID,
        data_dir=str(tmp_path),
        generator_factory=factory,
    )


# ---------------------------------------------------------------------------
# 1. completed -> report_written end-to-end
# ---------------------------------------------------------------------------


def test_run_once_fires_generation_for_completed_run_without_report(
    monkeypatch, tmp_path, registered_fake_adapter,
) -> None:
    """state.status=completed + no report -> factory invoked, file appears."""
    monkeypatch.setenv("AUTO_REPORT_GENERATE", "true")
    sim_id = "sim_auto_watch_001"
    _seed_completed_run(tmp_path, sim_id, write_report=False)

    factory = _CountingFactory()
    watcher = _make_watcher(tmp_path, factory)

    outcomes = watcher.run_once()

    assert factory.calls == [sim_id], (
        f"expected exactly one call to the generator factory for {sim_id!r}; "
        f"got {factory.calls!r}"
    )
    assert (tmp_path / sim_id / _TEST_REPORT_FILENAME).exists(), (
        "factory stub should have written the report artifact"
    )
    assert len(outcomes) == 1, f"expected one outcome; got {outcomes!r}"
    assert outcomes[0].report_written is True
    # Lock file must be released after generation completes so a
    # subsequent poll cycle does not see a stale sentinel.
    assert not (tmp_path / sim_id / ".auto_report.lock").exists(), (
        "lock file must be removed after a successful generation"
    )


# ---------------------------------------------------------------------------
# 2. lock-file: two concurrent triggers -> only one factory call
# ---------------------------------------------------------------------------


def test_lock_file_prevents_double_generation(
    monkeypatch, tmp_path, registered_fake_adapter,
) -> None:
    """Two threads racing run_once on the same sim must single-fire.

    The factory stub blocks on a shared barrier so both poll cycles
    pass the "report missing" check before either acquires the lock.
    The exclusive-create lock guarantees only one thread proceeds to
    invoke the factory; the other observes EEXIST and skips.
    """
    monkeypatch.setenv("AUTO_REPORT_GENERATE", "true")
    sim_id = "sim_auto_watch_concurrent"
    _seed_completed_run(tmp_path, sim_id, write_report=False)

    proceed = threading.Event()
    call_count = {"n": 0}
    call_lock = threading.Lock()

    def _blocking_factory(
        *, simulation_id: str, run_dir: Path, use_case_id: str,
    ) -> GenerationOutcome:
        with call_lock:
            call_count["n"] += 1
        # Hold the factory call long enough for the second thread to
        # race the lock acquisition.
        proceed.wait(timeout=2.0)
        (run_dir / _TEST_REPORT_FILENAME).write_text(
            json.dumps({"simulation_id": simulation_id}),
            encoding="utf-8",
        )
        return GenerationOutcome(
            simulation_id=simulation_id, report_written=True,
        )

    watcher = _make_watcher(tmp_path, _blocking_factory)

    results: List[List[GenerationOutcome]] = [[], []]

    def _worker(idx: int) -> None:
        results[idx] = watcher.run_once()

    t1 = threading.Thread(target=_worker, args=(0,))
    t2 = threading.Thread(target=_worker, args=(1,))
    t1.start()
    t2.start()
    # Let both threads enter run_once and contend on the lock before
    # releasing the factory.
    proceed.set()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)
    assert not t1.is_alive() and not t2.is_alive(), (
        "both poll threads must have completed"
    )

    assert call_count["n"] == 1, (
        f"expected exactly one factory invocation under the lock; "
        f"got {call_count['n']}"
    )
    fired = [o for batch in results for o in batch if o.report_written]
    assert len(fired) == 1, (
        f"expected exactly one report_written outcome; got {fired!r}"
    )
    assert not (tmp_path / sim_id / ".auto_report.lock").exists(), (
        "lock file must be cleaned up after the factory call"
    )


# ---------------------------------------------------------------------------
# 3. opt-out: env unset -> watcher is inert
# ---------------------------------------------------------------------------


def test_watcher_is_inert_when_env_unset(
    monkeypatch, tmp_path, registered_fake_adapter,
) -> None:
    """Without AUTO_REPORT_GENERATE, run_once never calls the factory."""
    monkeypatch.delenv("AUTO_REPORT_GENERATE", raising=False)
    sim_id = "sim_auto_watch_optout"
    _seed_completed_run(tmp_path, sim_id, write_report=False)

    factory = _CountingFactory()
    watcher = _make_watcher(tmp_path, factory)

    outcomes = watcher.run_once()

    assert outcomes == [], (
        f"watcher must return no outcomes when env is unset; got {outcomes!r}"
    )
    assert factory.calls == [], (
        "factory must not be invoked when the opt-in env is unset"
    )
    assert not (tmp_path / sim_id / ".auto_report.lock").exists()
    assert not (tmp_path / sim_id / _TEST_REPORT_FILENAME).exists()


# ---------------------------------------------------------------------------
# 4. report already exists -> skip without calling factory
# ---------------------------------------------------------------------------


def test_run_once_skips_when_report_already_present(
    monkeypatch, tmp_path, registered_fake_adapter,
) -> None:
    """An existing report artifact short-circuits before the factory call.

    Idempotency invariant under restart: the watcher rebuilds its view
    from disk on every cycle. A run dir whose report landed in a prior
    cycle (or via a manual POST /report/generate before the watcher
    booted) must not retrigger.
    """
    monkeypatch.setenv("AUTO_REPORT_GENERATE", "true")
    sim_id = "sim_auto_watch_existing_report"
    _seed_completed_run(tmp_path, sim_id, write_report=True)

    factory = _CountingFactory()
    watcher = _make_watcher(tmp_path, factory)

    outcomes = watcher.run_once()

    assert outcomes == [], (
        f"watcher must skip runs that already have a report; got {outcomes!r}"
    )
    assert factory.calls == [], (
        "factory must not be invoked when the report artifact already exists"
    )


# ---------------------------------------------------------------------------
# 5. effective_status: run_state.json overrides state.json
# ---------------------------------------------------------------------------


def test_run_state_completed_overrides_state_running(
    monkeypatch, tmp_path, registered_fake_adapter,
) -> None:
    """Runner-owned run_state.json[runner_status]=completed wins.

    Mirrors the reconciliation logic the /report/generate route uses:
    the blueprint-owned state.json may be stale at 'running' for a
    finished run; the runner subprocess writes the terminal marker
    into run_state.json. The watcher must honour the same precedence
    so an in-flight runner that finished without a state.json flush
    still triggers auto-generation.
    """
    monkeypatch.setenv("AUTO_REPORT_GENERATE", "true")
    sim_id = "sim_auto_watch_run_state"
    run_dir = tmp_path / sim_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id, "status": "running",
            "use_case_id": _TEST_USE_CASE_ID,
        }),
        encoding="utf-8",
    )
    (run_dir / "run_state.json").write_text(
        json.dumps({"runner_status": "completed"}), encoding="utf-8",
    )

    factory = _CountingFactory()
    watcher = _make_watcher(tmp_path, factory)

    outcomes = watcher.run_once()

    assert factory.calls == [sim_id], (
        f"runner_status=completed must trigger generation; got {factory.calls!r}"
    )
    assert outcomes and outcomes[0].report_written is True


# ---------------------------------------------------------------------------
# 6. failed runs are not triggered
# ---------------------------------------------------------------------------


def test_failed_runs_are_not_triggered(
    monkeypatch, tmp_path, registered_fake_adapter,
) -> None:
    """A run that terminated in failed state must not auto-generate."""
    monkeypatch.setenv("AUTO_REPORT_GENERATE", "true")
    sim_id = "sim_auto_watch_failed"
    run_dir = tmp_path / sim_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id, "status": "running",
            "use_case_id": _TEST_USE_CASE_ID,
        }),
        encoding="utf-8",
    )
    (run_dir / "run_state.json").write_text(
        json.dumps({"runner_status": "failed"}), encoding="utf-8",
    )

    factory = _CountingFactory()
    watcher = _make_watcher(tmp_path, factory)

    outcomes = watcher.run_once()

    assert outcomes == []
    assert factory.calls == []
    assert not (tmp_path / sim_id / _TEST_REPORT_FILENAME).exists()


# ---------------------------------------------------------------------------
# 7. factory raises -> lock released so the run_dir is not wedged
# ---------------------------------------------------------------------------


def test_lock_released_when_factory_raises(
    monkeypatch, tmp_path, registered_fake_adapter,
) -> None:
    """A factory exception must not leave the lock file behind.

    The watcher's poll loop absorbs per-sim failures, but the lock
    cleanup happens via try/finally inside the per-sim helper so the
    sentinel does not survive a generator crash. A wedged lock would
    block every future auto-generate cycle for that sim_id.
    """
    monkeypatch.setenv("AUTO_REPORT_GENERATE", "true")
    sim_id = "sim_auto_watch_raises"
    _seed_completed_run(tmp_path, sim_id, write_report=False)

    def _raising_factory(
        *, simulation_id: str, run_dir: Path, use_case_id: str,
    ) -> GenerationOutcome:
        raise RuntimeError("simulated generator failure")

    watcher = _make_watcher(tmp_path, _raising_factory)

    with pytest.raises(RuntimeError):
        watcher.run_once()

    assert not (tmp_path / sim_id / ".auto_report.lock").exists(), (
        "lock file must be cleaned up even when the factory raises"
    )


def test_retryable_failure_writes_backoff_marker(
    monkeypatch, tmp_path, registered_fake_adapter,
) -> None:
    """A retryable failure is not retried again on the next poll cycle."""
    monkeypatch.setenv("AUTO_REPORT_GENERATE", "true")
    sim_id = "sim_auto_watch_backoff"
    run_dir = _seed_completed_run(tmp_path, sim_id, write_report=False)

    factory = _FailureOutcomeFactory()
    watcher = _make_watcher(tmp_path, factory)

    first = watcher.run_once()
    second = watcher.run_once()

    assert factory.calls == [sim_id]
    assert len(first) == 1
    assert first[0].reason == "llm_provider_not_ready"
    assert len(second) == 1
    assert second[0].reason == "auto_report_backoff:llm_provider_not_ready"

    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    marker = state["auto_report_failure"]
    assert marker["attempts"] == 1
    assert marker["last_reason"] == "llm_provider_not_ready"
    assert marker["next_retry_at"]
    assert marker["terminal"] is False


# ---------------------------------------------------------------------------
# 8. legacy env alias is not a product opt-in
# ---------------------------------------------------------------------------


def test_policy_named_env_alias_does_not_activate(
    monkeypatch, tmp_path, registered_fake_adapter,
) -> None:
    """Only ``AUTO_REPORT_GENERATE`` is honored by RawFish."""
    monkeypatch.delenv("AUTO_REPORT_GENERATE", raising=False)
    legacy_env = "POLICY_" + "AUTO_REPORT_GENERATE"
    monkeypatch.setenv(legacy_env, "true")
    sim_id = "sim_auto_watch_legacy_env_ignored"
    _seed_completed_run(tmp_path, sim_id, write_report=False)

    factory = _CountingFactory()
    watcher = _make_watcher(tmp_path, factory)

    outcomes = watcher.run_once()

    assert outcomes == []
    assert factory.calls == []


# ---------------------------------------------------------------------------
# 9. production_generator_factory end-to-end through a fake adapter
# ---------------------------------------------------------------------------


def test_production_generator_factory_writes_report_via_adapter(
    monkeypatch, tmp_path,
) -> None:
    """End-to-end pin: the production factory drives the full chain
    (registry lookup -> rehydrate -> generate_report -> JSON write).

    Uses a registered fake adapter whose ``generate_report`` returns
    a small Pydantic model and asserts the file lands on disk with
    the expected payload.
    """
    from datetime import datetime, timezone
    from pydantic import BaseModel

    from app.simulation_platform.auto_report_watcher import (
        production_generator_factory,
    )

    # A tiny Pydantic report model so we exercise the
    # ``hasattr(report, "model_dump_json")`` serialisation branch.
    class _MiniReport(BaseModel):
        simulation_id: str
        summary: str

    sim_id = "sim_factory_e2e"
    use_case_id = "factory_e2e_lane"
    report_filename = "factory_e2e_report.json"

    class _AdapterUnderTest(FakeUseCase):
        def artifact_names(self):  # type: ignore[override]
            return {"report_json": report_filename}

        def generate_report(  # type: ignore[override]
            self, scenario, run_metadata, evidence_catalog, llm_client=None,
        ):
            del llm_client
            return _MiniReport(
                simulation_id=str(run_metadata.simulation_id),
                summary="ok",
            )

    snap = _registry_snapshot()
    try:
        platform_registry.register(_AdapterUnderTest(uid=use_case_id))

        # Seed run dir with state.json + a minimal run_metadata.json
        # the production factory's RunMetadata.model_validate_json
        # call can parse.
        run_dir = tmp_path / sim_id
        run_dir.mkdir()
        (run_dir / "state.json").write_text(
            json.dumps({
                "simulation_id": sim_id,
                "status": "completed",
                "use_case_id": use_case_id,
                "scenario": {"persona": "video", "stress_question": "q?"},
            }),
            encoding="utf-8",
        )
        # Build a valid RunMetadata payload. The exact field set comes
        # from app.simulation_platform.run_metadata.RunMetadata; we use the model
        # itself to construct a round-trippable JSON so the factory's
        # validator does not reject the fixture.
        from app.simulation_platform.run_metadata import RunMetadata, ScaleCaps
        from app.policy.schema import Persona

        meta = RunMetadata(
            simulation_id=sim_id,
            created_at=datetime.now(timezone.utc),
            prepared_at=datetime.now(timezone.utc),
            persona=Persona.VIDEO,
            scenario_hash="a" * 64,
            seed_hash="b" * 64,
            artifact_paths={"manifest.json": "manifest.json"},
            artifact_hashes={"manifest.json": "c" * 64},
            zep_mode="mock",
            scale_caps=ScaleCaps(
                ensemble_n=1, stakeholders=1, rounds=1, cap_value=100,
            ),
        )
        (run_dir / "run_metadata.json").write_text(
            meta.model_dump_json(), encoding="utf-8",
        )

        # The factory's LLM-key precheck must succeed -- set a sentinel
        # value the seam recognises as "usable" for tests. The watcher
        # is wrapped around an injected fake adapter so no real LLM
        # call is issued; ``_llm_key_is_usable`` is the only gate.
        monkeypatch.setenv("LLM_API_KEY", "sk-test-fake-key-for-watcher-e2e")
        # Make sure no scale-cap branches gate the rehydration.
        monkeypatch.setenv("POLICY_ALLOW_EXPENSIVE", "false")

        outcome = production_generator_factory(
            simulation_id=sim_id,
            run_dir=run_dir,
            use_case_id=use_case_id,
        )

        # The fake adapter's ``generate_report`` returns a real
        # Pydantic model with no LLM dependency -- the factory should
        # succeed end-to-end.
        assert outcome.report_written is True, (
            f"factory must succeed end-to-end for fake adapter; "
            f"got reason={outcome.reason!r}"
        )
        artifact_path = run_dir / report_filename
        assert artifact_path.exists(), (
            f"factory must write the adapter's report artifact; "
            f"got contents: {list(run_dir.iterdir())!r}"
        )
        # Round-trip the persisted payload to confirm the
        # serialisation branch landed valid JSON.
        persisted = json.loads(artifact_path.read_text(encoding="utf-8"))
        assert persisted == {
            "simulation_id": sim_id, "summary": "ok",
        }, f"unexpected persisted payload: {persisted!r}"
    finally:
        _registry_restore(snap)


def test_production_generator_factory_records_unsupported_when_adapter_raises(
    monkeypatch, tmp_path,
) -> None:
    """Adapter ``generate_report`` raising ``NotImplementedError`` is the
    canonical opt-out -- the factory must record
    ``reason="generate_report_unsupported"`` and not crash the poll.
    """
    from datetime import datetime, timezone

    from app.simulation_platform.auto_report_watcher import (
        production_generator_factory,
    )

    sim_id = "sim_factory_unsupported"
    use_case_id = "factory_unsupported_lane"
    report_filename = "x_report.json"

    class _OptOutAdapter(FakeUseCase):
        def artifact_names(self):  # type: ignore[override]
            return {"report_json": report_filename}

        def generate_report(  # type: ignore[override]
            self, scenario, run_metadata, evidence_catalog, llm_client=None,
        ):
            del llm_client
            raise NotImplementedError("opt out")

    snap = _registry_snapshot()
    try:
        platform_registry.register(_OptOutAdapter(uid=use_case_id))

        run_dir = tmp_path / sim_id
        run_dir.mkdir()
        (run_dir / "state.json").write_text(
            json.dumps({
                "simulation_id": sim_id,
                "status": "completed",
                "use_case_id": use_case_id,
                "scenario": {},
            }),
            encoding="utf-8",
        )
        from app.simulation_platform.run_metadata import RunMetadata, ScaleCaps
        from app.policy.schema import Persona

        meta = RunMetadata(
            simulation_id=sim_id,
            created_at=datetime.now(timezone.utc),
            prepared_at=datetime.now(timezone.utc),
            persona=Persona.VIDEO,
            scenario_hash="a" * 64,
            seed_hash="b" * 64,
            artifact_paths={"manifest.json": "manifest.json"},
            artifact_hashes={"manifest.json": "c" * 64},
            zep_mode="mock",
            scale_caps=ScaleCaps(
                ensemble_n=1, stakeholders=1, rounds=1, cap_value=100,
            ),
        )
        (run_dir / "run_metadata.json").write_text(
            meta.model_dump_json(), encoding="utf-8",
        )

        monkeypatch.setenv("LLM_API_KEY", "sk-test-fake-key-for-watcher-e2e")

        outcome = production_generator_factory(
            simulation_id=sim_id,
            run_dir=run_dir,
            use_case_id=use_case_id,
        )

        assert outcome.report_written is False
        assert outcome.reason == "generate_report_unsupported", (
            f"unexpected reason: {outcome.reason!r}"
        )
        assert not (run_dir / report_filename).exists(), (
            "no artifact must be written when the adapter opts out"
        )
    finally:
        _registry_restore(snap)


# ---------------------------------------------------------------------------
# Multi-level run-dir scan: watcher discovers BOTH namespaced and flat layouts
# ---------------------------------------------------------------------------


def test_run_once_scans_namespaced_run_dir(
    monkeypatch, tmp_path, registered_fake_adapter,
) -> None:
    """The watcher discovers runs at
    ``<data>/<use_case_id>/<sim_id>/`` -- the canonical layout the
    generic ``/api/sim/use-cases/<id>/...`` blueprint writes.
    """
    monkeypatch.setenv("AUTO_REPORT_GENERATE", "true")
    ns_root = tmp_path / _TEST_USE_CASE_ID
    ns_root.mkdir()
    sim_id = "sim_auto_watch_ns_001"
    run_dir = ns_root / sim_id
    run_dir.mkdir()
    (run_dir / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id,
            "status": "completed",
            "use_case_id": _TEST_USE_CASE_ID,
        }),
        encoding="utf-8",
    )

    factory = _CountingFactory()
    watcher = _make_watcher(tmp_path, factory)
    outcomes = watcher.run_once()

    assert factory.calls == [sim_id], factory.calls
    assert (run_dir / _TEST_REPORT_FILENAME).is_file()
    assert any(o.report_written for o in outcomes)


def test_run_once_skips_flat_legacy_run_for_other_lane(
    monkeypatch, tmp_path, registered_fake_adapter,
) -> None:
    """A flat-layout legacy entry whose ``state.json[use_case_id]``
    names a DIFFERENT lane is ignored by this lane's watcher. The
    sibling lane's own watcher picks it up.
    """
    monkeypatch.setenv("AUTO_REPORT_GENERATE", "true")
    sim_id = "sim_other_lane_001"
    _seed_completed_run(tmp_path, sim_id)
    (tmp_path / sim_id / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id,
            "status": "completed",
            "use_case_id": "some_other_lane",
        }),
        encoding="utf-8",
    )

    factory = _CountingFactory()
    watcher = _make_watcher(tmp_path, factory)
    outcomes = watcher.run_once()

    assert factory.calls == [], factory.calls
    assert outcomes == []


def test_run_once_dedups_when_sim_id_in_both_layouts(
    monkeypatch, tmp_path, registered_fake_adapter,
) -> None:
    """When the same ``sim_id`` exists under BOTH the namespaced and
    flat layouts, the namespaced run_dir wins and the flat copy is
    treated as a legacy mirror (single fire, single artifact).
    """
    monkeypatch.setenv("AUTO_REPORT_GENERATE", "true")
    sim_id = "sim_in_both_layouts_001"

    flat_run = tmp_path / sim_id
    flat_run.mkdir()
    (flat_run / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id,
            "status": "completed",
            "use_case_id": _TEST_USE_CASE_ID,
        }),
        encoding="utf-8",
    )
    ns_run = tmp_path / _TEST_USE_CASE_ID / sim_id
    ns_run.mkdir(parents=True)
    (ns_run / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id,
            "status": "completed",
            "use_case_id": _TEST_USE_CASE_ID,
        }),
        encoding="utf-8",
    )

    factory = _CountingFactory()
    watcher = _make_watcher(tmp_path, factory)
    watcher.run_once()

    assert factory.calls == [sim_id], factory.calls
    # The artifact lands on the namespaced run_dir (the winner).
    assert (ns_run / _TEST_REPORT_FILENAME).is_file()
    # The flat copy is left alone.
    assert not (flat_run / _TEST_REPORT_FILENAME).exists()


# ---------------------------------------------------------------------------
# Lane-aware run_metadata: production_generator_factory builds duck-typed
# metadata for adapters that do not run guardrails internally
# ---------------------------------------------------------------------------


def test_production_factory_uses_duck_typed_run_metadata_for_non_guardrail_adapter(
    monkeypatch, tmp_path,
) -> None:
    """When ``adapter_runs_guardrails_internally`` is False (creator-
    style lane) the factory MUST NOT require ``run_metadata.json`` on
    disk -- it derives the four duck-typed attributes
    (``simulation_id`` / ``scenario_hash`` / ``seed_hash`` /
    ``persona``) from ``state.json`` and calls the adapter with
    that ``SimpleNamespace``.

    The legacy behaviour skipped these adapters with
    ``run_metadata_missing`` so the lane could never emit via the
    watcher; this pin asserts the factory now reaches
    ``generate_report``.
    """
    from app.simulation_platform.auto_report_watcher import (
        production_generator_factory,
    )

    snap = _registry_snapshot()
    captured: dict = {}

    class _DuckMetaAdapter(FakeUseCase):
        def artifact_names(self):  # type: ignore[override]
            return {"report_json": _TEST_REPORT_FILENAME}

        def rehydrate_scenario(self, state_dict):  # type: ignore[override]
            return state_dict.get("scenario", {})

        def generate_report(  # type: ignore[override]
            self, scenario, run_metadata, evidence_catalog, llm_client=None,
        ):
            del scenario, evidence_catalog, llm_client
            captured["run_metadata_type"] = type(run_metadata).__name__
            captured["persona"] = getattr(run_metadata, "persona", "<missing>")
            captured["simulation_id"] = getattr(
                run_metadata, "simulation_id", "<missing>",
            )
            captured["has_scenario_hash"] = hasattr(
                run_metadata, "scenario_hash",
            )
            return {"report_id": "rid-1", "simulation_id": captured["simulation_id"]}

    use_case_id = "duck_metadata_lane"
    platform_registry.register(_DuckMetaAdapter(uid=use_case_id))

    sim_id = "duck_meta_sim_001"
    run_dir = tmp_path / use_case_id / sim_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id,
            "use_case_id": use_case_id,
            "status": "completed",
            "scenario": {"persona": "creator_video"},
        }),
        encoding="utf-8",
    )
    # NOTE: deliberately do NOT write run_metadata.json. The factory
    # must succeed via the duck-typed fallback.

    try:
        outcome = production_generator_factory(
            simulation_id=sim_id, run_dir=run_dir, use_case_id=use_case_id,
        )
    finally:
        _registry_restore(snap)

    assert outcome.report_written is True, (
        f"factory should have written: outcome.reason={outcome.reason!r}"
    )
    assert captured["run_metadata_type"] == "SimpleNamespace"
    assert captured["simulation_id"] == sim_id
    assert captured["persona"] == "creator_video"
    assert captured["has_scenario_hash"] is True


# ---------------------------------------------------------------------------
# Path-traversal guard: production_generator_factory refuses unsafe
# artifact_names entries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../../escape.json",      # parent-traversal segments
        "subdir/inside.json",     # nested basename
        "/etc/passwd",            # absolute path
        ".",                      # current-dir token
        "..",                     # parent-dir token
    ],
)
def test_production_factory_refuses_unsafe_report_json_basename(
    monkeypatch, tmp_path, unsafe_name,
) -> None:
    """An adapter that declares an unsafe ``artifact_names['report_json']``
    must be refused: the watcher returns ``artifact_name_unsafe`` and
    no file lands under (or escapes from) ``run_dir``.
    """
    from app.simulation_platform.auto_report_watcher import (
        production_generator_factory,
    )

    snap = _registry_snapshot()

    class _UnsafeAdapter(FakeUseCase):
        def artifact_names(self):  # type: ignore[override]
            return {"report_json": unsafe_name}

        def rehydrate_scenario(self, state_dict):  # type: ignore[override]
            return state_dict.get("scenario", {})

        def generate_report(  # type: ignore[override]
            self, scenario, run_metadata, evidence_catalog, llm_client=None,
        ):
            del scenario, run_metadata, evidence_catalog, llm_client
            raise AssertionError(
                "generate_report must not be reached when the artifact "
                "name is unsafe",
            )

    use_case_id = "unsafe_artifact_lane"
    platform_registry.register(_UnsafeAdapter(uid=use_case_id))

    sim_id = "unsafe_artifact_sim_001"
    run_dir = tmp_path / use_case_id / sim_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id,
            "use_case_id": use_case_id,
            "status": "completed",
            "scenario": {"persona": "p"},
        }),
        encoding="utf-8",
    )

    try:
        outcome = production_generator_factory(
            simulation_id=sim_id, run_dir=run_dir, use_case_id=use_case_id,
        )
    finally:
        _registry_restore(snap)

    assert outcome.report_written is False
    assert outcome.reason == "artifact_name_unsafe", outcome.reason
    assert list(run_dir.iterdir()) == [run_dir / "state.json"], (
        "no artifact must be written when the basename is unsafe"
    )


# ---------------------------------------------------------------------------
# Watcher reconciles the flat runner directory's ``run_state.json``
# ---------------------------------------------------------------------------


def test_effective_status_reconciles_flat_run_state(
    monkeypatch, tmp_path,
) -> None:
    """The watcher's reconciler reads
    ``SimulationRunner.RUN_STATE_DIR/<sim>/run_state.json`` so a
    real subprocess run that wrote ``runner_status="completed"``
    only into the flat path is admissible for auto-report. Without
    this probe, completed generic policy subprocess runs would
    never auto-emit.
    """
    from app.simulation_platform.auto_report_watcher import AutoReportWatcher
    from app.services.simulation_runner import SimulationRunner

    flat_root = tmp_path / "flat"
    flat_root.mkdir()
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(flat_root))

    sim_id = "sim_flat_reconcile_001"
    run_dir = tmp_path / sim_id
    run_dir.mkdir()
    (run_dir / "state.json").write_text(
        json.dumps({"status": "running", "simulation_id": sim_id}),
        encoding="utf-8",
    )
    flat_run = flat_root / sim_id
    flat_run.mkdir()
    (flat_run / "run_state.json").write_text(
        json.dumps({"runner_status": "completed"}), encoding="utf-8",
    )

    status = AutoReportWatcher._effective_status(run_dir, sim_id)
    assert status == "completed", (
        "watcher must reconcile flat-path run_state.json so a "
        "subprocess-completed run is auto-reportable"
    )


# ---------------------------------------------------------------------------
# Watcher write-back: state.json advances to ``reported`` after emit
# ---------------------------------------------------------------------------


def test_production_factory_writes_state_back_to_reported(
    monkeypatch, tmp_path,
) -> None:
    """After the watcher's factory writes the report artifact it MUST
    advance ``state.json`` to ``phase/status="reported"`` and stamp
    ``report_path`` / ``report_sha256``. Without this, an
    auto-generated run keeps ``state["phase"] == "completed"`` on
    disk and the manual ``/report/generate`` route would re-admit
    the run for a duplicate generation against artifacts the
    watcher already produced.
    """
    from app.simulation_platform.auto_report_watcher import (
        production_generator_factory,
    )

    snap = _registry_snapshot()

    class _WriteBackAdapter(FakeUseCase):
        def artifact_names(self):  # type: ignore[override]
            return {"report_json": _TEST_REPORT_FILENAME}

        def rehydrate_scenario(self, state_dict):  # type: ignore[override]
            return state_dict.get("scenario", {})

        def generate_report(  # type: ignore[override]
            self, scenario, run_metadata, evidence_catalog, llm_client=None,
        ):
            del scenario, run_metadata, evidence_catalog, llm_client
            return {"report_id": "rid-writeback", "simulation_id": "sim-x"}

    use_case_id = "writeback_lane"
    platform_registry.register(_WriteBackAdapter(uid=use_case_id))

    sim_id = "writeback_sim_001"
    run_dir = tmp_path / use_case_id / sim_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id,
            "use_case_id": use_case_id,
            "status": "completed",
            "phase": "completed",
            "scenario": {"persona": "p"},
        }),
        encoding="utf-8",
    )

    try:
        outcome = production_generator_factory(
            simulation_id=sim_id, run_dir=run_dir, use_case_id=use_case_id,
        )
    finally:
        _registry_restore(snap)

    assert outcome.report_written is True, outcome.reason
    state_after = json.loads(
        (run_dir / "state.json").read_text(encoding="utf-8"),
    )
    assert state_after["phase"] == "reported"
    assert state_after["status"] == "reported"
    assert state_after["report_path"] == _TEST_REPORT_FILENAME
    assert state_after["report_sha256"].startswith("sha256:")
