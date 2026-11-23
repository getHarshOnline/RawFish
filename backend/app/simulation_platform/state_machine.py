"""Lifecycle state persistence + transition helpers for simulations.

The simulation lifecycle is:

    draft -> created -> prepared -> starting -> running -> completed | failed

with a separate ``stopping`` side-step initiated by the stop endpoint.
The on-disk artifact for this lifecycle is ``state.json`` inside each
simulation's run directory (``OASIS_SIMULATION_DATA_DIR/<simulation_id>``).
This module owns:

* path resolution for the run directory (validates simulation_id),
* read of ``state.json`` (None on absence / unreadable),
* rehydration of the persisted scenario dict back into ``Scenario``,
* the :class:`StateMachine` writer that mutates ``status`` + a small
  set of timestamp / error fields and persists atomically.

The transition writer accepts an optional ``from_phase`` argument that
asserts the current persisted status matches an expected value (or set
of values). Callers may also pass ``from_phase=None`` to skip the
assertion entirely -- a handful of endpoints (notably ``/stop``) accept
the transition regardless of the current status, and the
``/start`` failure branch needs to record ``failed`` from any prior
status. The endpoint-specific structured error envelopes
(``INVALID_REQUEST`` 400 with discriminator fields) stay in the
endpoint layer; this module raises a plain :class:`StateTransitionError`
when ``from_phase`` is supplied and does not match.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, Optional, Union

from ..config import Config
from ..utils.atomic_io import atomic_write_json
from ..utils.id_validator import safe_join, validate_safe_id
from ..utils.logger import get_logger

# Avoid the eager top-level ``Scenario`` import: this module is part
# of the platform purity layer and must not pull in policy-specific
# schemas at import time. The single function that needs the symbol
# (``load_scenario_from_state``) imports it lazily so the rest of the
# module is consumable by lane-agnostic callers without dragging
# ``app.policy.schema`` into their import graph.

logger = get_logger("rawfish.simulation_platform.state_machine")


# ---------------------------------------------------------------------------
# Path + read helpers
# ---------------------------------------------------------------------------


def run_dir(simulation_id: str) -> str:
    """Resolve a validated run_dir path under ``OASIS_SIMULATION_DATA_DIR``.

    Validates ``simulation_id`` via :func:`validate_safe_id` so client-
    supplied ids cannot escape the data root via ``..`` segments. Never
    use ``os.path.join`` directly with a client-supplied id.
    """
    validate_safe_id(simulation_id, "simulation_id")
    return safe_join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)


def state_path(simulation_id: str) -> str:
    """Absolute path to ``state.json`` for ``simulation_id``."""
    return os.path.join(run_dir(simulation_id), "state.json")


class StateCorruptedError(RuntimeError):
    """Raised by :func:`read_state_strict` when ``state.json`` exists but
    cannot be parsed.

    This is the platform-level analogue of the generic dispatch's
    ``STATE_CORRUPTED`` 500 envelope: distinguishing "no simulation
    yet" (file absent -> ``None`` return) from "the persisted record
    is unreadable" (file present, parse fails). The endpoint layer
    catches this and maps it to its own structured error envelope --
    this module does NOT construct HTTP responses.

    The ``reason`` attribute carries a short discriminator string
    (``state_unreadable`` / ``state_json_parse_failed`` /
    ``state_wrong_top_level_type``) so the endpoint can echo it into
    ``details.reason`` for an operator-readable signal.
    """

    def __init__(
        self,
        simulation_id: str,
        reason: str,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(
            f"state.json for simulation_id={simulation_id!r} is corrupt: "
            f"{reason}"
        )
        self.simulation_id = simulation_id
        self.reason = reason
        self.__cause__ = cause


def read_state(simulation_id: str) -> Optional[Dict[str, Any]]:
    """Load ``state.json`` for ``simulation_id`` (silent-skip variant).

    Returns ``None`` when the file is absent OR unreadable (the
    unreadable case is logged at warning so an operator notices).
    Callers map ``None`` to a structured ``SIMULATION_NOT_FOUND`` 404
    at the endpoint boundary.

    This silent-skip semantic remains for callers that intentionally
    collapse missing-and-corrupt to a single error surface. **New callers
    MUST use :func:`read_state_strict`** so a
    corrupt persisted record is distinguished from a missing one --
    silently treating corrupt-as-missing lets a stale-but-broken
    state.json claim the route's missing-id error path and hide the
    real problem.
    """
    path = state_path(simulation_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read state.json for %s: %s", simulation_id, exc)
        return None


def read_state_strict(simulation_id: str) -> Optional[Dict[str, Any]]:
    """Load ``state.json`` for ``simulation_id`` (distinguishing variant).

    Returns ``None`` when the file is absent. **Raises**
    :class:`StateCorruptedError` when the file exists but cannot be
    parsed -- callers map the raise to a structured ``STATE_CORRUPTED``
    500 envelope with ``details.reason`` set to the exception's
    ``reason`` field.

    Distinguishing semantic surface:

      * file absent                          -> ``None``
      * read raises ``OSError``              -> ``state_unreadable``
      * parse raises ``json.JSONDecodeError`` -> ``state_json_parse_failed``
      * parsed top-level is not a dict       -> ``state_wrong_top_level_type``

    This is the platform-layer analogue of the generic dispatch's
    ``_read_state_or_err`` helper; it is exposed at the platform layer
    so any future call site that needs the distinguishing read does
    not have to depend on a route-private helper to do the right
    thing.
    """
    path = state_path(simulation_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise StateCorruptedError(simulation_id, "state_unreadable", exc) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StateCorruptedError(
            simulation_id, "state_json_parse_failed", exc,
        ) from exc
    if not isinstance(parsed, dict):
        raise StateCorruptedError(
            simulation_id, "state_wrong_top_level_type",
        )
    return parsed


def load_scenario_from_state(
    state: Dict[str, Any], *, allow_expensive: bool,
) -> Any:
    """Rehydrate the persisted scenario dict back into a scenario object.

    Dispatch strategy:

      * When ``state["use_case_id"]`` is present, the registered adapter's
        :meth:`SimulationUseCase.rehydrate_scenario` owns the
        rehydration. Each lane returns the scenario shape it owns;
        the platform layer never has to know which Pydantic model
        sits behind the persisted dict.
      * When the field is absent (a legacy ``state.json`` blob
        created before the use-case-id stamp landed), the function
        falls back to the policy schema path so existing on-disk
        artifacts keep loading.

    ``allow_expensive`` is preserved as a kwarg ONLY for the legacy
    policy fallback (the ``no use_case_id`` branch below). The
    dispatched path (``state["use_case_id"]`` present) ignores the
    kwarg -- each adapter's :meth:`SimulationUseCase.rehydrate_scenario`
    reads any per-call context (env-driven scale-cap flag, etc.) on
    its own. Callers may pass the kwarg unconditionally; it becomes
    a no-op once the state-dict starts carrying the id, which every
    state.json created by the generic create route
    does.
    """
    use_case_id = state.get("use_case_id")
    if isinstance(use_case_id, str) and use_case_id:
        # Lazy import: keeps the module-level import graph free of the
        # registry (which itself transitively imports every registered
        # adapter at first reference time).
        from . import registry as _registry

        try:
            adapter = _registry.get(use_case_id)
        except KeyError:
            adapter = None
        if adapter is not None and hasattr(adapter, "rehydrate_scenario"):
            return adapter.rehydrate_scenario(state)

    # Legacy fallback: state.json blobs created before the
    # ``use_case_id`` stamp landed are necessarily policy-lane (no
    # other lane existed at that time), so the policy schema path
    # is the correct rehydration. Lazy import: keeps the module-
    # level graph lane-agnostic when this branch is not taken.
    from ..policy.schema import Scenario

    return Scenario.model_validate(
        state["scenario"], context={"allow_expensive": allow_expensive},
    )


# ---------------------------------------------------------------------------
# Transition writer
# ---------------------------------------------------------------------------


class StateTransitionError(RuntimeError):
    """Raised when a :meth:`StateMachine.transition` ``from_phase``
    assertion does not match the persisted status.

    The endpoint layer catches this and maps it to its own structured
    error envelope (typically ``INVALID_REQUEST`` 400 with discriminator
    fields like ``current_status`` + ``reason``). This module does NOT
    construct HTTP error responses.
    """

    def __init__(
        self,
        simulation_id: str,
        current_status: Optional[str],
        expected: Union[str, Iterable[Optional[str]]],
    ) -> None:
        if isinstance(expected, str):
            expected_repr = repr(expected)
        else:
            expected_repr = repr(tuple(expected))
        super().__init__(
            f"state.json status={current_status!r} for "
            f"simulation_id={simulation_id!r} does not match "
            f"expected from_phase={expected_repr}"
        )
        self.simulation_id = simulation_id
        self.current_status = current_status
        self.expected = expected


class StateMachine:
    """Lifecycle writer for ``state.json``.

    Wraps the inline ``state["status"] = ...; atomic_write_json(...)``
    sequence so endpoints get a single call site with an optional
    ``from_phase`` guard. Callers stage mutations via ``extra_fields``
    (``started_at``, ``prepared_at``, ``stopped_at``, ``completed_at``,
    ``error_code``, ``error_message``, ``error``); the writer merges
    them onto the in-memory ``state`` dict before persisting.

    The instance carries no per-request state -- a single module-level
    :data:`STATE_MACHINE` singleton is shared by every endpoint.
    """

    @staticmethod
    def write(
        simulation_id: str,
        state: Dict[str, Any],
        *,
        run_dir: Optional[str] = None,
    ) -> None:
        """Persist ``state`` to ``state.json`` atomically.

        Lower-level escape hatch used by the few callers that mutate
        ``state`` in-place across several lines (notably ``/start``) and
        then need to flush at a specific moment. Prefer :meth:`transition`
        for the common case.

        ``run_dir`` (optional) overrides the default flat-path resolution
        (``OASIS_SIMULATION_DATA_DIR/<simulation_id>``) so callers that
        own a namespaced run directory (the generic dispatch's
        ``<data>/<use_case_id>/<simulation_id>/``) can write state to
        the directory the route reads back. When omitted the writer
        falls back to the legacy flat path so the existing legacy
        callers keep working unchanged.
        """
        if run_dir is None:
            target = state_path(simulation_id)
        else:
            target = os.path.join(run_dir, "state.json")
        atomic_write_json(target, state)

    @staticmethod
    def transition(
        simulation_id: str,
        from_phase: Union[None, str, Iterable[Optional[str]]],
        to_phase: str,
        *,
        state: Optional[Dict[str, Any]] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
        run_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Mutate ``state["status"]`` from ``from_phase`` to ``to_phase``.

        Parameters
        ----------
        simulation_id:
            The run id whose ``state.json`` is being mutated. Validated
            via :func:`validate_safe_id` indirectly through :func:`run_dir`.
        from_phase:
            Expected current status. May be a single string, an iterable
            of strings (e.g. ``(None, "created", "prepared")`` for the
            prepare regression guard), or ``None`` to skip the assertion
            entirely (used by ``/stop`` and the ``/start`` failure branch).
            When the assertion is supplied and does not match the
            persisted status, raises :class:`StateTransitionError`.
        to_phase:
            New status value to persist. The lifecycle vocabulary is
            ``draft | created | prepared | starting | running | stopping |
            completed | failed``.
        state:
            Optional in-memory state dict to mutate + persist. When
            ``None`` the writer reads ``state.json`` from disk first
            (and raises :class:`StateTransitionError` when the file is
            missing -- the endpoint should have caught that earlier as
            ``SIMULATION_NOT_FOUND``).
        extra_fields:
            Endpoint-specific status side-data merged onto the state dict
            before persisting (``started_at``, ``prepared_at``,
            ``stopped_at``, ``completed_at``, ``error_code``,
            ``error_message``, ``error``).
        run_dir:
            Optional override for the run directory ``state.json`` lives
            in. Defaults to the flat
            ``OASIS_SIMULATION_DATA_DIR/<simulation_id>`` resolution so
            the legacy callers keep working unchanged. The generic
            dispatch surface owns a namespaced run directory
            (``<data>/<use_case_id>/<simulation_id>/``) distinct from
            the flat layout; passing it explicitly here aligns the
            on-disk write with where the dispatch's ``/status`` route
            reads. When ``state`` is also ``None`` the read-back uses
            the SAME directory.

        Returns
        -------
        Dict[str, Any]
            The mutated state dict (same object the caller passed in via
            ``state=`` when supplied) so the endpoint can continue
            consuming fields after the write.
        """
        if run_dir is None:
            target = state_path(simulation_id)
        else:
            target = os.path.join(run_dir, "state.json")

        if state is None:
            if run_dir is None:
                loaded = read_state(simulation_id)
            else:
                # Mirror :func:`read_state`'s silent-skip semantics so
                # the writer's behaviour is identical to the flat-path
                # call when no in-memory state was supplied.
                if not os.path.exists(target):
                    loaded = None
                else:
                    try:
                        with open(target, "r", encoding="utf-8") as fh:
                            loaded = json.load(fh)
                    except (OSError, json.JSONDecodeError) as exc:
                        logger.warning(
                            "Failed to read state.json at %s: %s", target, exc,
                        )
                        loaded = None
            if loaded is None:
                raise StateTransitionError(
                    simulation_id, None, from_phase if from_phase is not None else "*"
                )
            state = loaded

        # ``state`` is narrowed to ``Dict[str, Any]`` here -- either the
        # caller passed it via ``state=`` (non-None contract on that
        # arg) or the read-back above raised on missing/corrupt input
        # and we successfully assigned ``state = loaded``. The assert
        # quiets the analyzer; runtime cost is negligible.
        assert state is not None
        current = state.get("status")

        if from_phase is not None:
            if isinstance(from_phase, str):
                ok = current == from_phase
            else:
                ok = current in tuple(from_phase)
            if not ok:
                raise StateTransitionError(simulation_id, current, from_phase)

        state["status"] = to_phase
        if extra_fields:
            state.update(extra_fields)
        atomic_write_json(target, state)
        return state


# Module-level singleton -- endpoints import this name. The class is
# stateless so a singleton is purely ergonomic; tests can still
# instantiate fresh ``StateMachine()`` objects when they need to.
STATE_MACHINE = StateMachine()
