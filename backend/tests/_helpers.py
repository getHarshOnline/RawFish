"""Shared test helpers (regular importable module, not pytest's conftest).

conftest.py is auto-loaded by pytest but is not on sys.path for direct import.
This module provides the same helpers in an importable form.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

# Make backend/ importable when tests run from the package root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _set_dummy_env(monkeypatch, **overrides):
    """Default env that satisfies Config.validate() for boot-only tests.
    SECRET_KEY uses the test-fixture sentinel value (which Config.validate
    rejects in production-mode tests); FLASK_DEBUG defaults to true so the
    production-mode placeholder rejection only fires when the test
    explicitly sets FLASK_DEBUG=false."""
    defaults = {
        "LLM_API_KEY": "test-llm-key",
        "ZEP_API_KEY": "test-zep-key",
        "API_KEY": "test-api-key",
        "AUTH_ENABLED": "true",
        # Tests run in DEBUG mode by default so the production
        # SECRET_KEY rejection does not fire on every test.
        "FLASK_DEBUG": "true",
        "SECRET_KEY": "test-secret-32chars-test-secret-32",
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)


def make_app(monkeypatch, **env_overrides):
    """Reload Config + create_app under a clean env snapshot."""
    _set_dummy_env(monkeypatch, **env_overrides)
    if "app.config" in sys.modules:
        importlib.reload(sys.modules["app.config"])
    # Re-sync cached Config references inside already-imported submodules that
    # bound `Config` at their original import time.
    import app.config as _cfg_mod
    for _modname in (
        "app.simulation_platform.state_machine",
        "app.simulation_platform.idempotency",
    ):
        if _modname in sys.modules:
            # setattr (not direct attribute assignment) because Pyright
            # statically knows ModuleType doesn't declare ``Config``; the
            # rebind is dynamic-by-design and setattr is the canonical
            # Python idiom for this kind of cross-module patch.
            setattr(sys.modules[_modname], "Config", _cfg_mod.Config)
    if "app" in sys.modules:
        importlib.reload(sys.modules["app"])
    from app import create_app
    from app.config import Config
    return create_app(Config)


def catalog_payload(*, use_case_id: str, personas: list[Any], **kwargs: Any):
    """Build a strict CatalogPayload with the required RawFish lane manifest."""
    from app.simulation_platform.catalog import (
        CatalogPayload,
        build_minimal_lane_manifest,
    )

    lane_manifest = kwargs.pop(
        "lane_manifest",
        build_minimal_lane_manifest(
            lane_id=use_case_id,
            label=use_case_id.replace("_", " ").title(),
            summary=f"{use_case_id} test lane",
        ),
    )
    return CatalogPayload(
        use_case_id=use_case_id,
        lane_manifest=lane_manifest,
        personas=personas,
        **kwargs,
    )
