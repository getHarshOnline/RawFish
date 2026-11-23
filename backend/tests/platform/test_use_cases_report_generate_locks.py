"""Two-lock contract for ``POST /api/sim/use-cases/.../report/generate``.

The generic dispatch route guards the generate+persist sequence with
two locks:

* ``.start.lock`` (outer) -- per-simulation, blocks the request thread
  against multiple manual clicks on the same simulation. Acquired by
  the route entry, released on response.
* ``.auto_report.lock`` (inner) -- the SAME sentinel the auto-report
  watcher checks. Acquired inside the lock-guarded body so a watcher
  poll that fires mid-flight observes the lock and skips with
  ``reason="report_generate_in_progress"`` instead of racing the
  manual generator on the same artifacts.

Without the inner lock the watcher (which never goes through the
outer ``.start.lock`` because it has no request thread) would race
the manual route to last-writer-wins the canonical report JSON +
state.json transition.
"""
from __future__ import annotations

import pytest

from app.simulation_platform import registry as platform_registry
from app.simulation_platform.auto_report_watcher import (
    AUTO_REPORT_LOCK_FILENAME,
    AutoReportWatcher,
    GenerationOutcome,
)
from app.simulation_platform.tests_helpers import FakeUseCase
from tests._helpers import make_app


_TEST_USE_CASE_ID = "two_lock_test_lane"
_TEST_REPORT_FILENAME = "two_lock_report.json"


def _registry_snapshot() -> dict:
    return dict(platform_registry._REGISTRY)  # type: ignore[attr-defined]


def _registry_restore(snap: dict) -> None:
    platform_registry._REGISTRY.clear()  # type: ignore[attr-defined]
    platform_registry._REGISTRY.update(snap)  # type: ignore[attr-defined]


class _TwoLockAdapter(FakeUseCase):
    """Fake adapter whose generate_report writes a fixed payload.

    The watcher resolves the report filename via this adapter so the
    same value drives both the manual route's persist and the
    watcher's eligibility check.
    """

    report_schema = None

    def artifact_names(self):  # type: ignore[override]
        return {"report_json": _TEST_REPORT_FILENAME}

    def generate_report(self, scenario, run_metadata, evidence_catalog, llm_client=None):  # type: ignore[override]
        del scenario, run_metadata, evidence_catalog, llm_client
        return {"report_id": "two_lock_001", "use_case_id": self.use_case_id()}


def _redirect_data_dir(monkeypatch, tmp_path) -> None:
    """Point ``Config.OASIS_SIMULATION_DATA_DIR`` at ``tmp_path`` on EVERY
    Config object the route stack reads -- the post-reload class from
    ``app.config`` AND the original pre-reload binding still cached
    inside ``app.api.use_cases``. Without the second patch the
    use_cases route keeps reading the production data dir because
    ``app.api.use_cases.Config`` is bound at import time and the test
    harness's ``make_app`` does not rebind it.
    """
    import sys as _sys
    from app.config import Config
    monkeypatch.setattr(Config, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))
    use_cases_mod = _sys.modules.get("app.api.use_cases")
    if use_cases_mod is not None and use_cases_mod.Config is not Config:
        monkeypatch.setattr(
            use_cases_mod.Config,
            "OASIS_SIMULATION_DATA_DIR",
            str(tmp_path),
        )
    state_machine_mod = _sys.modules.get("app.simulation_platform.state_machine")
    if state_machine_mod is not None and state_machine_mod.Config is not Config:
        monkeypatch.setattr(
            state_machine_mod.Config,
            "OASIS_SIMULATION_DATA_DIR",
            str(tmp_path),
        )


@pytest.fixture
def app(monkeypatch):
    return make_app(monkeypatch, AUTH_ENABLED="false")


@pytest.fixture
def registered_two_lock_adapter():
    snap = _registry_snapshot()
    adapter = _TwoLockAdapter(uid=_TEST_USE_CASE_ID)
    platform_registry.register(adapter)  # type: ignore[arg-type]  # _TwoLockAdapter duck-types SimulationUseCase
    try:
        yield adapter
    finally:
        _registry_restore(snap)


def _create_and_complete_sim(client, use_case_id: str) -> str:
    """Drive the lifecycle to phase=completed so /report/generate is
    eligible."""
    created = client.post(
        f"/api/sim/use-cases/{use_case_id}/simulation/create",
        json={"scenario": {"persona": "p"}},
    )
    assert created.status_code == 201, created.get_data(as_text=True)
    sim_id = created.get_json()["data"]["simulation_id"]

    prepared = client.post(
        f"/api/sim/use-cases/{use_case_id}/simulation/{sim_id}/prepare",
    )
    assert prepared.status_code == 200, prepared.get_data(as_text=True)

    started = client.post(
        f"/api/sim/use-cases/{use_case_id}/simulation/{sim_id}/start",
    )
    assert started.status_code == 200, started.get_data(as_text=True)
    return sim_id


def test_report_generate_acquires_auto_report_lock_returns_409_when_held(
    app, registered_two_lock_adapter, tmp_path, monkeypatch,
):
    """A manual /report/generate must refuse to race the auto-report
    watcher's sentinel. Pre-create the sentinel and assert the route
    returns 409 with ``reason="auto_report_in_progress"``.
    """
    _redirect_data_dir(monkeypatch, tmp_path)

    client = app.test_client()
    sim_id = _create_and_complete_sim(client, _TEST_USE_CASE_ID)
    run_dir = tmp_path / _TEST_USE_CASE_ID / sim_id
    assert run_dir.is_dir(), f"expected run dir at {run_dir}"

    # Simulate the watcher mid-flight: pre-create the sentinel.
    lock_path = run_dir / AUTO_REPORT_LOCK_FILENAME
    lock_path.write_text("99999", encoding="utf-8")

    resp = client.post(
        f"/api/sim/use-cases/{_TEST_USE_CASE_ID}/simulation/{sim_id}/report/generate",
    )
    assert resp.status_code == 409, resp.get_data(as_text=True)
    body = resp.get_json()
    err = body["error"]
    assert err["code"] == "INVALID_REQUEST"
    assert err["details"]["reason"] == "auto_report_in_progress"
    assert err["details"]["simulation_id"] == sim_id
    # The pre-existing sentinel must survive the rejected request so
    # the in-flight peer (the watcher) can clean it up on its own exit.
    assert lock_path.exists(), (
        "the route must not unlink a sentinel it did not acquire"
    )
    # And the report artifact must NOT have been written.
    assert not (run_dir / _TEST_REPORT_FILENAME).exists(), (
        "manual route must not produce a report while the lock is held"
    )


def test_watcher_skips_with_reason_when_manual_generate_holds_lock(
    app, registered_two_lock_adapter, tmp_path, monkeypatch,
):
    """A watcher poll that runs while a manual /report/generate is mid-
    flight observes the ``.auto_report.lock`` sentinel and skips with
    ``reason="report_generate_in_progress"``. This is the watcher-side
    of the two-lock pattern: without the inner lock the manual route
    would race the watcher's factory on the same on-disk artifacts.
    """
    _redirect_data_dir(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTO_REPORT_GENERATE", "true")

    client = app.test_client()
    sim_id = _create_and_complete_sim(client, _TEST_USE_CASE_ID)
    run_dir = tmp_path / _TEST_USE_CASE_ID / sim_id

    # Simulate the manual route mid-flight by pre-creating the sentinel
    # in the same way the route does (O_CREAT|O_EXCL). The watcher's
    # ``_maybe_generate`` runs O_CREAT|O_EXCL on the same path and
    # MUST observe EEXIST.
    lock_path = run_dir / AUTO_REPORT_LOCK_FILENAME
    lock_path.write_text("manual_pid", encoding="utf-8")

    factory_calls: list[str] = []

    def _should_not_be_called(
        *, simulation_id, run_dir, use_case_id,
    ) -> GenerationOutcome:
        del run_dir, use_case_id
        factory_calls.append(simulation_id)
        return GenerationOutcome(
            simulation_id=simulation_id, report_written=True,
        )

    watcher = AutoReportWatcher(
        use_case_id=_TEST_USE_CASE_ID,
        data_dir=str(tmp_path / _TEST_USE_CASE_ID),
        generator_factory=_should_not_be_called,
    )

    outcomes = watcher.run_once()
    # Exactly one outcome: the watcher saw the lock and skipped.
    assert len(outcomes) == 1, f"expected one outcome; got {outcomes!r}"
    outcome = outcomes[0]
    assert outcome.simulation_id == sim_id
    assert outcome.report_written is False
    assert outcome.reason == "report_generate_in_progress", (
        f"watcher must skip with reason='report_generate_in_progress' "
        f"when the manual generate holds the lock; got {outcome!r}"
    )
    # The factory was NEVER invoked -- the lock-check happens BEFORE
    # the factory call.
    assert factory_calls == [], (
        "watcher must not call the factory while the lock is held; "
        f"got calls={factory_calls!r}"
    )
    # The lock the test pre-created must still exist -- the watcher
    # only releases sentinels it acquired itself.
    assert lock_path.exists(), (
        "watcher must not unlink a sentinel another writer created"
    )


def test_report_generate_releases_auto_report_lock_on_success(
    app, registered_two_lock_adapter, tmp_path, monkeypatch,
):
    """A successful /report/generate must release the auto-report
    sentinel so the next caller can proceed. A leaked lock would wedge
    every subsequent manual generate AND every watcher poll for this
    simulation.
    """
    _redirect_data_dir(monkeypatch, tmp_path)

    client = app.test_client()
    sim_id = _create_and_complete_sim(client, _TEST_USE_CASE_ID)
    run_dir = tmp_path / _TEST_USE_CASE_ID / sim_id

    resp = client.post(
        f"/api/sim/use-cases/{_TEST_USE_CASE_ID}/simulation/{sim_id}/report/generate",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    # Lock released after success.
    assert not (run_dir / AUTO_REPORT_LOCK_FILENAME).exists(), (
        "auto-report sentinel must be unlinked after a successful "
        "generation; otherwise the next caller is wedged"
    )
    # And the report artifact landed.
    assert (run_dir / _TEST_REPORT_FILENAME).exists()
