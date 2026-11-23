"""Config.validate() contract pins.

Production config (FLASK_DEBUG=false + AUTH_ENABLED=true) must reject
the .env.example template placeholder API_KEY / VITE_API_KEY values.
The base unset-key check only rejects an UNSET key; an operator who
copied .env.example verbatim and never edited it would otherwise boot
with a key any reader of the repo could lift directly from version
control.

RawFish LLM validation is provider-mode aware: local modes can boot
without external credentials, while external provider mode must name
a provider, model, and runtime API key.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _reload_config(monkeypatch, **env):
    """Reload Config under the given env so class-level attributes
    (API_KEY, AUTH_ENABLED, DEBUG, ...) reflect the test's env exactly.

    Defaults satisfy validate() for everything OTHER than the variable
    under test (so the only error in the returned list is the one the
    test asserts on).
    """
    defaults = {
        "LLM_API_KEY": "test-llm-key",
        "ZEP_API_KEY": "test-zep-key",
        "API_KEY": "real-strong-api-key-not-placeholder",
        "AUTH_ENABLED": "true",
        "FLASK_DEBUG": "false",
        "SECRET_KEY": "real-prod-secret-32chars-or-more-real-prod-secret",
        "RAWFISH_TEST_SILENT_SECRET_WARN": "1",
        "ZEP_MODE": "mock",
    }
    defaults.update(env)
    # Clear VITE_API_KEY first so it doesn't leak across cases.
    monkeypatch.delenv("VITE_API_KEY", raising=False)
    for k, v in defaults.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    if "app.config" in sys.modules:
        importlib.reload(sys.modules["app.config"])
    else:
        importlib.import_module("app.config")
    from app.config import Config
    return Config


@pytest.mark.parametrize(
    "placeholder",
    [
        "change_me_to_a_strong_api_key",   # exact .env.example default
        "your_api_key_here",                # generic template prefix sample
        "test-api-key",                     # known test sentinel
        "CHANGE_ME_TO_A_STRONG_API_KEY",   # case-insensitive
        "Your_Api_Key_Here",
        "change_me_anything",               # prefix-match (change_me*)
        "your_strong_key",                  # prefix-match (your_*)
    ],
)
def test_config_rejects_placeholder_api_key(monkeypatch, placeholder):
    """Production + auth ON + placeholder API_KEY => validation error."""
    Config = _reload_config(monkeypatch, API_KEY=placeholder)
    errors = Config.validate()
    assert any(
        "API_KEY appears to be a template placeholder" in e for e in errors
    ), f"Expected placeholder rejection for API_KEY={placeholder!r}; got: {errors!r}"


def test_config_rejects_placeholder_vite_api_key(monkeypatch):
    """Placeholder VITE_API_KEY is also rejected in production."""
    Config = _reload_config(
        monkeypatch,
        VITE_API_KEY="change_me_to_a_strong_api_key",
    )
    errors = Config.validate()
    assert any(
        "VITE_API_KEY appears to be a template placeholder" in e
        for e in errors
    ), f"Expected VITE_API_KEY placeholder rejection; got: {errors!r}"


def test_config_rejects_empty_vite_api_key_in_production_with_auth(monkeypatch):
    """An UNSET VITE_API_KEY in production with auth on must hard-fail
    at boot. Without the check the bundled UI ships with an empty key
    and every /api/* request returns 401 with no boot-time signal."""
    # _reload_config deletes VITE_API_KEY before applying overrides, so
    # the default already exercises the "unset" branch.
    Config = _reload_config(monkeypatch)
    errors = Config.validate()
    assert any(
        "VITE_API_KEY to be set" in e for e in errors
    ), f"Expected VITE_API_KEY-required rejection; got: {errors!r}"


def test_config_accepts_set_vite_api_key_in_production_with_auth(monkeypatch):
    """A real, non-placeholder VITE_API_KEY clears the
    placeholder check (no error mentioning VITE_API_KEY)."""
    Config = _reload_config(
        monkeypatch,
        VITE_API_KEY="a-real-strong-random-key-aB3xZ9qY",
    )
    errors = Config.validate()
    assert not any(
        "VITE_API_KEY" in e for e in errors
    ), f"Real VITE_API_KEY was wrongly flagged; errors: {errors!r}"


def test_config_skips_vite_api_key_required_check_in_debug(monkeypatch):
    """The VITE_API_KEY-required check is production-only; FLASK_DEBUG=true
    skips it so local dev does not need to set the frontend key."""
    Config = _reload_config(monkeypatch, FLASK_DEBUG="true")
    errors = Config.validate()
    assert not any(
        "VITE_API_KEY" in e for e in errors
    ), f"VITE_API_KEY check fired in DEBUG mode; errors: {errors!r}"


def test_config_skips_vite_api_key_required_check_when_auth_disabled(monkeypatch):
    """AUTH_ENABLED=false makes VITE_API_KEY moot; the new check must
    not fire."""
    Config = _reload_config(monkeypatch, AUTH_ENABLED="false")
    errors = Config.validate()
    assert not any(
        "VITE_API_KEY" in e for e in errors
    ), f"VITE_API_KEY check fired with AUTH_ENABLED=false; errors: {errors!r}"


def test_config_accepts_real_api_key(monkeypatch):
    """A genuinely-set, non-placeholder API_KEY must NOT trip the placeholder check."""
    Config = _reload_config(
        monkeypatch,
        API_KEY="a-real-strong-random-key-aB3xZ9qY",
    )
    errors = Config.validate()
    assert not any(
        "API_KEY appears to be a template placeholder" in e for e in errors
    ), f"Real API_KEY was wrongly flagged as placeholder; errors: {errors!r}"


def test_config_skips_placeholder_check_in_debug(monkeypatch):
    """FLASK_DEBUG=true is a local-dev signal; placeholder check is skipped."""
    Config = _reload_config(
        monkeypatch,
        API_KEY="change_me_to_a_strong_api_key",
        FLASK_DEBUG="true",
    )
    errors = Config.validate()
    assert not any(
        "API_KEY appears to be a template placeholder" in e for e in errors
    ), f"Placeholder check fired in DEBUG mode; errors: {errors!r}"


def test_config_skips_placeholder_check_when_auth_disabled(monkeypatch):
    """AUTH_ENABLED=false makes API_KEY moot; skip the placeholder check."""
    Config = _reload_config(
        monkeypatch,
        API_KEY="change_me_to_a_strong_api_key",
        AUTH_ENABLED="false",
    )
    errors = Config.validate()
    assert not any(
        "API_KEY appears to be a template placeholder" in e for e in errors
    ), f"Placeholder check fired with AUTH_ENABLED=false; errors: {errors!r}"


def test_local_llm_mode_does_not_require_external_key(monkeypatch):
    """Local provider modes validate without external credentials."""
    Config = _reload_config(
        monkeypatch,
        RAWFISH_LLM_PROVIDER_MODE="local_ollama",
        RAWFISH_LLM_MODEL="qwen3-coder:30b",
        RAWFISH_LLM_BASE_URL="http://localhost:11434",
        RAWFISH_LLM_API_KEY=None,
        LLM_API_KEY=None,
        VITE_API_KEY="a-real-strong-random-key-aB3xZ9qY",
    )
    errors = Config.validate()
    assert not any("RAWFISH_LLM_API_KEY" in e for e in errors), errors


def test_external_llm_mode_requires_runtime_key(monkeypatch):
    """External provider mode rejects missing runtime credentials."""
    Config = _reload_config(
        monkeypatch,
        RAWFISH_LLM_PROVIDER_MODE="external",
        RAWFISH_LLM_PROVIDER="openai",
        RAWFISH_LLM_MODEL="gpt-4o-mini",
        RAWFISH_LLM_API_KEY=None,
        LLM_API_KEY=None,
        VITE_API_KEY="a-real-strong-random-key-aB3xZ9qY",
    )
    errors = Config.validate()
    assert any("RAWFISH_LLM_API_KEY" in e for e in errors), errors
