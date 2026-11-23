"""Creator lane package.

A registered :class:`app.simulation_platform.use_case.SimulationUseCase`
that models how a creator brief may land with subscriber cohorts.

The package is intentionally independent of :mod:`app.policy` so the
platform-abstraction claim is real: every cross-use-case primitive
(registry, catalog payload shape, artifact manifest helpers,
idempotency cache, run state machine) lives under
:mod:`app.simulation_platform` and is reused verbatim here. Persona /
segment-type / report shapes are owned by this package alone -- the
policy use case's enums and Pydantic models are NOT imported.

Public surface:

* :data:`adapter.CREATOR_USE_CASE` -- the module-level
  singleton the Flask app factory hands to
  :func:`app.simulation_platform.registry.register`.
* :class:`adapter.CreatorUseCase` -- the adapter class
  satisfying the platform Protocol structurally.
"""
from __future__ import annotations

from .adapter import (
    CREATOR_USE_CASE,
    CreatorUseCase,
)


__all__ = [
    "CREATOR_USE_CASE",
    "CreatorUseCase",
]
