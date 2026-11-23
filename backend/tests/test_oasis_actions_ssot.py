"""OASIS action lists SSoT.

Pins that ``scripts/run_parallel_simulation.py`` and
``app.config.Config`` share one canonical Twitter/Reddit action list:
the script's resolved ``ActionType`` members must equal
``Config.OASIS_*_ACTIONS`` verbatim. Skips when ``oasis`` is not
importable; the Config half still runs because Config has no oasis
dependency.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.config import Config


_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "run_parallel_simulation.py"
)


def _oasis_importable() -> bool:
    return importlib.util.find_spec("oasis") is not None


@pytest.mark.skipif(
    not _oasis_importable(),
    reason=(
        "oasis package not installed; run_parallel_simulation cannot import "
        "(optional import check; the RawFish config half still runs without OASIS)"
    ),
)
def test_twitter_actions_match_config_ssot():
    """The script's TWITTER_ACTIONS must resolve to Config.OASIS_TWITTER_ACTIONS."""
    # Import lazily so the skipif above gates oasis-dependent code.
    spec = importlib.util.spec_from_file_location(
        "run_parallel_simulation_under_test", _SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    resolved_names = [action.name for action in module.TWITTER_ACTIONS]
    assert resolved_names == Config.OASIS_TWITTER_ACTIONS


@pytest.mark.skipif(
    not _oasis_importable(),
    reason=(
        "oasis package not installed; run_parallel_simulation cannot import "
        "(optional import check; the RawFish config half still runs without OASIS)"
    ),
)
def test_reddit_actions_match_config_ssot():
    """The script's REDDIT_ACTIONS must resolve to Config.OASIS_REDDIT_ACTIONS."""
    spec = importlib.util.spec_from_file_location(
        "run_parallel_simulation_under_test", _SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    resolved_names = [action.name for action in module.REDDIT_ACTIONS]
    assert resolved_names == Config.OASIS_REDDIT_ACTIONS


def test_config_action_names_are_uppercase_strings():
    """Config-level invariant that holds with or without oasis installed."""
    for name in Config.OASIS_TWITTER_ACTIONS + Config.OASIS_REDDIT_ACTIONS:
        assert isinstance(name, str)
        assert name == name.upper(), f"OASIS action name must be UPPER: {name!r}"
        assert name.isidentifier(), f"OASIS action name must be a valid identifier: {name!r}"
