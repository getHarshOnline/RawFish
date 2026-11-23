"""Simulation-platform package -- the use-case-agnostic boundary.

The platform owns three things and nothing else:

1. The :class:`SimulationUseCase` structural Protocol (see
   :mod:`.use_case`) -- the contract every use case must satisfy.
2. The catalog data shapes :class:`PersonaSpec` and
   :class:`CatalogPayload` (see :mod:`.catalog`) -- the persona /
   scope / segment-type envelope the frontend wizard consumes.
3. A process-wide registry (see :mod:`.registry`) -- the single
   lookup the HTTP layer and frontend share.

Re-exports are kept narrow on purpose: any consumer should import
from the package root rather than reaching into the submodule layout,
so the package can be reorganised without rippling through call
sites.
"""
from __future__ import annotations

from .catalog import CatalogPayload, PersonaSpec
from .registry import get, list_use_cases, register
from .state_machine import (
    STATE_MACHINE,
    StateMachine,
    StateTransitionError,
    load_scenario_from_state,
    read_state,
    run_dir,
    state_path,
)
from .use_case import SimulationUseCase

__all__ = [
    "CatalogPayload",
    "PersonaSpec",
    "STATE_MACHINE",
    "SimulationUseCase",
    "StateMachine",
    "StateTransitionError",
    "get",
    "list_use_cases",
    "load_scenario_from_state",
    "read_state",
    "register",
    "run_dir",
    "state_path",
]
