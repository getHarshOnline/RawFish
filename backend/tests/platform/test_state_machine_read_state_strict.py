"""``state_machine.read_state_strict`` distinguishes missing vs corrupt.

The legacy :func:`read_state` collapses both "file absent" and "file
exists but unreadable" into ``None``. That silent skip hides corrupt
on-disk state behind a missing-id error envelope at the route layer.

The distinguishing helper :func:`read_state_strict` returns ``None``
ONLY when the file is absent and raises
:class:`StateCorruptedError` when the file exists but cannot be
parsed -- with a ``reason`` discriminator the endpoint echoes into
its 500 ``STATE_CORRUPTED`` envelope.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.simulation_platform.state_machine import (
    StateCorruptedError,
    read_state,
    read_state_strict,
    state_path,
)


def _seed(monkeypatch, tmp_path: Path) -> None:
    # Config.OASIS_SIMULATION_DATA_DIR is a class attribute set at
    # class-definition time (not env-driven); monkeypatch it directly
    # so :func:`state_path` resolves under the per-test tmp dir.
    from app.config import Config

    monkeypatch.setattr(Config, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))
    import app.simulation_platform.state_machine as sm

    monkeypatch.setattr(sm, "Config", Config)


def test_read_state_strict_returns_none_when_file_absent(monkeypatch, tmp_path: Path):
    _seed(monkeypatch, tmp_path)
    sim_id = "sim_test_absent"
    # Pre-create the run_dir so the path resolves but state.json is
    # absent (this models a freshly-prepared run that has not yet had
    # state.json staged by the lane).
    os.makedirs(os.path.join(str(tmp_path), sim_id), exist_ok=True)
    assert read_state_strict(sim_id) is None


def test_read_state_strict_raises_on_invalid_json(monkeypatch, tmp_path: Path):
    _seed(monkeypatch, tmp_path)
    sim_id = "sim_test_corrupt"
    os.makedirs(os.path.join(str(tmp_path), sim_id), exist_ok=True)
    Path(state_path(sim_id)).write_text("{ not json", encoding="utf-8")

    with pytest.raises(StateCorruptedError) as exc_info:
        read_state_strict(sim_id)
    assert exc_info.value.reason == "state_json_parse_failed"
    assert exc_info.value.simulation_id == sim_id


def test_read_state_strict_raises_on_non_dict_top_level(
    monkeypatch, tmp_path: Path,
):
    _seed(monkeypatch, tmp_path)
    sim_id = "sim_test_wrong_top"
    os.makedirs(os.path.join(str(tmp_path), sim_id), exist_ok=True)
    Path(state_path(sim_id)).write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(StateCorruptedError) as exc_info:
        read_state_strict(sim_id)
    assert exc_info.value.reason == "state_wrong_top_level_type"


def test_permissive_read_state_still_silently_skips_corrupt(
    monkeypatch, tmp_path: Path,
):
    """The permissive helper still collapses missing and corrupt states.

    New endpoint code should use ``read_state_strict``; this helper remains
    for callers that intentionally expose one "not found" surface.
    """
    _seed(monkeypatch, tmp_path)
    sim_id = "sim_test_permissive_corrupt"
    os.makedirs(os.path.join(str(tmp_path), sim_id), exist_ok=True)
    Path(state_path(sim_id)).write_text("{ not json", encoding="utf-8")
    assert read_state(sim_id) is None


def test_read_state_strict_returns_dict_on_valid_state(
    monkeypatch, tmp_path: Path,
):
    _seed(monkeypatch, tmp_path)
    sim_id = "sim_test_ok"
    os.makedirs(os.path.join(str(tmp_path), sim_id), exist_ok=True)
    Path(state_path(sim_id)).write_text('{"status": "created"}', encoding="utf-8")
    state = read_state_strict(sim_id)
    assert state == {"status": "created"}
