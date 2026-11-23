"""Dual-port invocation seam for lane lifecycle methods.

The platform's :class:`SimulationUseCase` Protocol declares the sync
methods ``run_simulation`` + ``generate_report``. Adapters MAY
additionally ship ``async_run_simulation`` + ``async_generate_report``
under those literal names; the invocation helpers
``invoke_lane_run_simulation`` / ``invoke_lane_generate_report``
probe for the async variant and route through it when present.

These pins assert:

  1. An adapter that ships only the sync method is invoked
     synchronously (the helper does not blow up on missing async).
  2. An adapter that ships both gets the async variant called; the
     sync variant is NOT invoked in that case.
  3. Both helpers propagate ``NotImplementedError`` unchanged (the
     opt-out signal).
  4. Both helpers thread the documented kwargs correctly:
     :func:`invoke_lane_generate_report` passes ``llm_client``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.simulation_platform.tests_helpers import FakeUseCase
from app.simulation_platform.use_case import (
    invoke_lane_generate_report,
    invoke_lane_run_simulation,
)


def test_invoke_run_simulation_uses_sync_when_only_sync_present(tmp_path: Path):
    adapter = FakeUseCase(uid="sync_only")
    invoke_lane_run_simulation(adapter, scenario={"x": 1}, run_dir=tmp_path)
    # FakeUseCase.run_simulation records the run_dir under
    # ``run_simulation_called_with`` on the instance; the seam
    # delegated through the sync path because no async variant exists.
    assert adapter.run_simulation_called_with == [tmp_path]


def test_invoke_run_simulation_uses_async_when_both_present(tmp_path: Path):
    adapter = FakeUseCase(uid="async_path")
    sync_calls: list[tuple] = []
    async_calls: list[tuple] = []

    def _sync_run(scenario, run_dir):
        sync_calls.append((scenario, run_dir))

    async def _async_run(scenario, run_dir):
        async_calls.append((scenario, run_dir))

    adapter.run_simulation = _sync_run  # type: ignore[assignment]
    adapter.async_run_simulation = _async_run  # type: ignore[attr-defined]

    invoke_lane_run_simulation(adapter, scenario={"y": 2}, run_dir=tmp_path)
    assert async_calls == [({"y": 2}, tmp_path)]
    assert sync_calls == []


def test_invoke_run_simulation_propagates_notimplemented_from_sync(tmp_path: Path):
    adapter = FakeUseCase(uid="opt_out_sync")

    def _raise(scenario, run_dir):
        del scenario, run_dir
        raise NotImplementedError("lane opts out of generic run")

    adapter.run_simulation = _raise  # type: ignore[assignment]
    with pytest.raises(NotImplementedError):
        invoke_lane_run_simulation(adapter, scenario={}, run_dir=tmp_path)


def test_invoke_run_simulation_propagates_notimplemented_from_async(tmp_path: Path):
    adapter = FakeUseCase(uid="opt_out_async")

    async def _async_raise(scenario, run_dir):
        del scenario, run_dir
        raise NotImplementedError("async opt-out")

    adapter.async_run_simulation = _async_raise  # type: ignore[attr-defined]
    with pytest.raises(NotImplementedError):
        invoke_lane_run_simulation(adapter, scenario={}, run_dir=tmp_path)


def test_invoke_generate_report_uses_sync_when_only_sync_present():
    adapter = FakeUseCase(uid="report_sync")
    out = invoke_lane_generate_report(
        adapter,
        scenario={"s": "v"},
        run_metadata={"sim": 1},
        evidence_catalog=None,
        llm_client=None,
    )
    assert isinstance(out, dict) and "report" in out


def test_invoke_generate_report_uses_async_when_both_present():
    adapter = FakeUseCase(uid="report_async")
    sync_calls: list[Any] = []
    async_calls: list[Any] = []

    def _sync_report(scenario, run_metadata, evidence_catalog, llm_client=None):
        sync_calls.append((scenario, run_metadata, evidence_catalog, llm_client))
        return {"sync": True}

    async def _async_report(
        scenario, run_metadata, evidence_catalog, llm_client=None,
    ):
        async_calls.append((scenario, run_metadata, evidence_catalog, llm_client))
        return {"async": True}

    adapter.generate_report = _sync_report  # type: ignore[assignment]
    adapter.async_generate_report = _async_report  # type: ignore[attr-defined]

    out = invoke_lane_generate_report(
        adapter,
        scenario={"s": "v"},
        run_metadata={"sim": 2},
        evidence_catalog={"facts": []},
        llm_client="audited-client",
    )
    assert out == {"async": True}
    assert async_calls == [
        ({"s": "v"}, {"sim": 2}, {"facts": []}, "audited-client"),
    ]
    assert sync_calls == []


def test_invoke_generate_report_threads_llm_client_through_sync_path():
    adapter = FakeUseCase(uid="llm_thread_sync")
    captured: list[Any] = []

    def _sync_report(scenario, run_metadata, evidence_catalog, llm_client=None):
        captured.append(llm_client)
        return {}

    adapter.generate_report = _sync_report  # type: ignore[assignment]
    invoke_lane_generate_report(
        adapter,
        scenario={},
        run_metadata={},
        evidence_catalog=None,
        llm_client="sentinel-client",
    )
    assert captured == ["sentinel-client"]
