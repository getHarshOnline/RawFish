"""Subprocess-backed runner factory for the generic simulation platform.

The generic start flow hands prepared run artifacts to the existing
``SimulationRunner`` class. That runner still expects its inputs in a flat
``RUN_STATE_DIR/<simulation_id>/`` directory, while RawFish lanes prepare
artifacts inside lane-scoped run directories. This factory owns that mirror
step and gives tests a small injection point for deterministic runner stubs.
"""
from __future__ import annotations

from typing import Any, Dict

from .runner_types import (
    SimulationRunnerFactory,
    SimulationRunnerHandle,
    TerminalStatus,
)


class _DefaultSubprocessFactory:
    """Production factory that bridges lane-scoped runs to ``SimulationRunner``."""

    def start(
        self,
        *,
        simulation_id: str,
        run_dir: str,
        config: Dict[str, Any],
    ) -> SimulationRunnerHandle:
        import os
        import shutil

        from app.services.simulation_runner import SimulationRunner

        del config  # the runner re-reads simulation_config.json from disk

        flat_dir = os.path.join(SimulationRunner.RUN_STATE_DIR, simulation_id)
        os.makedirs(flat_dir, exist_ok=True)
        for artifact_name in (
            "simulation_config.json",
            "reddit_profiles.json",
            "twitter_profiles.csv",
        ):
            src = os.path.join(run_dir, artifact_name)
            dst = os.path.join(flat_dir, artifact_name)
            if os.path.abspath(src) == os.path.abspath(dst):
                continue
            if os.path.exists(src):
                shutil.copyfile(src, dst)

        SimulationRunner.start_simulation(simulation_id=simulation_id)
        return SimulationRunnerHandle(
            task_id=simulation_id,
            terminal_status=None,
            error=None,
        )


DEFAULT_FACTORY: SimulationRunnerFactory = _DefaultSubprocessFactory()
_runner_factory: SimulationRunnerFactory = DEFAULT_FACTORY


def get_runner_factory() -> SimulationRunnerFactory:
    """Return the runner factory currently bound for start orchestration."""

    return _runner_factory


def set_runner_factory(factory: SimulationRunnerFactory) -> None:
    """Bind a runner factory for tests or alternate runtime wiring."""

    global _runner_factory
    _runner_factory = factory


def reset_runner_factory() -> None:
    """Restore the production subprocess-backed runner factory."""

    set_runner_factory(DEFAULT_FACTORY)


__all__ = [
    "DEFAULT_FACTORY",
    "get_runner_factory",
    "reset_runner_factory",
    "set_runner_factory",
    "SimulationRunnerFactory",
    "SimulationRunnerHandle",
    "TerminalStatus",
]
