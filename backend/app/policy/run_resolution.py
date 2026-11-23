"""Canonical rounds-resolution chain.

:func:`resolve_rounds` is the single source of truth for picking the
``rounds`` value used by start orchestration, state.json round-trip,
and any future caller (runner, CLI backfill, replay tooling).

Precedence (first non-None wins): explicit ``body.rounds`` >
``scenario.drift_horizon_rounds`` (root canonical) >
``scenario.run.drift_horizon_rounds`` (legacy; emits
``DeprecationWarning`` if it ever fires -- the schema's mode='before'
validator normally lifts it to root first) > persona default from
:func:`scale_defaults_for`.
"""
from __future__ import annotations

import warnings
from typing import Optional

from .persona_defaults import scale_defaults_for
from .schema import Scenario


def resolve_rounds(
    body_rounds: Optional[int],
    scenario: Scenario,
    zep_mode: str,
) -> int:
    """Resolve the per-simulation ``rounds`` count.

    See module docstring for the precedence chain. ``zep_mode`` selects
    the persona-default scale row (``'mock'`` vs ``'real'``).
    """
    if body_rounds is not None:
        return int(body_rounds)
    if scenario.drift_horizon_rounds is not None:
        return int(scenario.drift_horizon_rounds)
    legacy_run_rounds = (
        getattr(scenario.run, "drift_horizon_rounds", None)
        if scenario.run is not None
        else None
    )
    if legacy_run_rounds is not None:
        # The schema-level ``RunControls.drift_horizon_rounds`` field
        # was REMOVED; the canonical location is ``Scenario.drift_horizon_rounds``.
        # This branch only fires when a state.json blob from an older
        # run is rehydrated and carries the value under the removed
        # path. New scenarios fail Pydantic strict-extra validation
        # if they try to set the legacy path.
        warnings.warn(
            "scenario.run.drift_horizon_rounds is no longer a schema "
            "field; rehydrating from a legacy state.json blob -- write "
            "drift_horizon_rounds at the Scenario root in any new "
            "payload.",
            DeprecationWarning,
            stacklevel=2,
        )
        return int(legacy_run_rounds)
    return int(scale_defaults_for(scenario.persona, scenario.scope, zep_mode)["rounds"])
