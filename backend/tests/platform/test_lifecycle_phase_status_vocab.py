"""Lifecycle vocabulary unification across the two on-disk readers.

The legacy ``StateMachine.transition`` writer in
:mod:`app.simulation_platform.state_machine` writes ``state["status"]``;
the auto-report watcher (and other legacy callers) reads ``status``.
The generic dispatch's lifecycle routes write ``state["phase"]``.
Without unification, generic-dispatch sims leave ``state["status"]``
unset and any legacy reader sees ``None``.

This pin asserts the helper in
:mod:`app.api.use_cases._set_lifecycle_phase` writes BOTH keys with
the same value at every transition site, so both vocabularies stay
in lockstep across writers and readers.
"""
from __future__ import annotations

from app.api.use_cases import _set_lifecycle_phase


def test_set_lifecycle_phase_writes_both_keys_with_same_value() -> None:
    state = {"simulation_id": "sim_unify"}
    _set_lifecycle_phase(state, "prepared")
    assert state["phase"] == "prepared"
    assert state["status"] == "prepared"


def test_set_lifecycle_phase_is_idempotent_overwrite() -> None:
    """A subsequent transition overwrites both keys consistently --
    no partial update where ``phase`` advances but ``status`` lingers
    on the previous value (which would re-create the split-brain
    problem at runtime)."""
    state: dict = {"phase": "draft", "status": "draft"}
    _set_lifecycle_phase(state, "starting")
    assert state["phase"] == "starting"
    assert state["status"] == "starting"
    _set_lifecycle_phase(state, "completed")
    assert state["phase"] == "completed"
    assert state["status"] == "completed"


def test_set_lifecycle_phase_does_not_touch_unrelated_fields() -> None:
    """The helper must be a surgical mutation: only the two
    lifecycle keys are touched. Any auxiliary fields the route
    already staged (timestamps, error envelopes) must round-trip
    untouched."""
    state = {
        "simulation_id": "sim_aux",
        "started_at": "2026-06-29T00:00:00Z",
        "error": {"code": "RUNNER_START_FAILED"},
    }
    _set_lifecycle_phase(state, "failed")
    assert state["phase"] == "failed"
    assert state["status"] == "failed"
    assert state["simulation_id"] == "sim_aux"
    assert state["started_at"] == "2026-06-29T00:00:00Z"
    assert state["error"] == {"code": "RUNNER_START_FAILED"}
