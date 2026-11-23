"""Runner adapter -- boundary that wraps the protected ``simulation_runner``.

The platform side of generic start does two
things at the seam between the HTTP handler and the subprocess runner:

1. It hands off to a :class:`SimulationRunnerFactory` (the injection point
   tests rebind so a deterministic stub can stand in for the real
   subprocess-backed factory).
2. It reconciles the runner-owned ``run_state.json`` against the
  API-owned ``state.json``: the runner subprocess writes its own
   terminal status (``completed`` / ``failed``) into ``run_state.json``
   but never mirrors it back into ``state.json``, so any read-side
   handler that surfaces lifecycle status to clients has to fold the
   two files together.

This module owns the call mechanics for both pieces while leaving the
subprocess runner focused on execution.

Dependency injection
--------------------

The adapter accepts a :class:`SimulationRunnerFactory` via its
constructor (``RunnerAdapter(factory=...)``) so the platform layer
does not import any lane's concrete factory at module load. The
policy lane resolves its production factory through the platform runner
factory module.

The static :meth:`RunnerAdapter.start` method accepts ``factory=`` per call
so a caller can defer the injection to request time.

Surface:

* :meth:`RunnerAdapter.start` -- hand off ``(simulation_id, run_dir,
  config)`` to a :class:`SimulationRunnerFactory` and return the
  :class:`SimulationRunnerHandle` it produced. Available both as an
  instance method (uses the constructor-bound factory) and a
  staticmethod (caller supplies ``factory=`` per call).
* :meth:`RunnerAdapter.read_run_state` -- read and JSON-decode
  ``run_state.json`` from a run directory. Returns ``None`` when the
  file is absent. Raises :class:`OSError` or
  :class:`json.JSONDecodeError` on a read / parse failure so call sites
  can log site-specific warnings without the adapter dictating the
  message.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from .runner_types import SimulationRunnerFactory, SimulationRunnerHandle


class RunnerAdapter:
    """Wrapper around the runner-factory call + run_state.json read.

    Two construction modes are supported:

    * Instance with a constructor-bound factory:
      ``RunnerAdapter(factory=my_factory).start_run(...)``.
    * Static call passing ``factory=`` per request:
      ``RunnerAdapter.start(factory=my_factory, ...)``.

    The platform layer no longer imports any lane's concrete factory;
    callers pass the factory supplied by their own service seam.
    """

    def __init__(self, factory: Optional[SimulationRunnerFactory] = None) -> None:
        """Store the bound factory (optional).

        Callers that prefer per-request injection leave ``factory``
        unset and use the static :meth:`start` form; callers that
        prefer construction-time injection pass the factory here and
        use :meth:`start_run`.
        """
        self._factory = factory

    def start_run(
        self,
        *,
        simulation_id: str,
        run_dir: str,
        config: Dict[str, Any],
    ) -> SimulationRunnerHandle:
        """Dispatch the start call to the constructor-bound factory.

        Raises :class:`RuntimeError` if the adapter was constructed
        without a factory; callers in that mode MUST use the static
        :meth:`start` form with an explicit ``factory=`` kwarg.
        """
        if self._factory is None:
            raise RuntimeError(
                "RunnerAdapter.start_run requires a constructor-bound "
                "factory; use RunnerAdapter.start(factory=...) for the "
                "per-call injection path."
            )
        return self._factory.start(
            simulation_id=simulation_id,
            run_dir=run_dir,
            config=config,
        )

    @staticmethod
    def start(
        *,
        factory: SimulationRunnerFactory,
        simulation_id: str,
        run_dir: str,
        config: Dict[str, Any],
    ) -> SimulationRunnerHandle:
        """Dispatch the start call to ``factory.start`` and return the handle.

        Args (all kw-only to match the underlying Protocol):
            factory:
                The currently-bound :class:`SimulationRunnerFactory` supplied
                by the calling lane's service seam.
            simulation_id:
                The opaque sim id; validated upstream via
                ``validate_safe_id``.
            run_dir:
                Absolute path to the simulation's run directory.
            config:
                Parsed contents of ``simulation_config.json``.

        Returns:
            The :class:`SimulationRunnerHandle` produced by the factory.

        Raises:
            Any exception the factory raises. The handler wraps this in
            its own ``try/except`` so it can flip ``state.json`` to
            ``failed`` and persist a failure-context ``run_metadata.json``
            before returning ``500 RUNNER_START_FAILED``.
        """
        return factory.start(
            simulation_id=simulation_id,
            run_dir=run_dir,
            config=config,
        )

    @staticmethod
    def read_run_state(run_dir: str) -> Optional[Dict[str, Any]]:
        """Read ``run_state.json`` from ``run_dir`` and return the parsed dict.

        Returns ``None`` when ``run_state.json`` does not exist (the
        runner subprocess has not written it yet -- a brand-new
        ``starting`` run, or a synchronous test-stub run that never
        produced one). Raises :class:`OSError` on filesystem errors and
        :class:`json.JSONDecodeError` on a malformed file so call sites
        can attach their own context-specific warning text before
        falling back to the API-owned ``state.json`` status.
        """
        path = os.path.join(run_dir, "run_state.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)


__all__ = ["RunnerAdapter"]
