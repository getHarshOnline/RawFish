"""Tests for app.utils.runtime_env.

Pins behaviour of :func:`app.utils.runtime_env.parse_zep_mode` and
:func:`get_allow_expensive` so any future change to the env-parsing
contract surfaces here loudly rather than silently changing the boot
matrix or the /personas degrade-to-mock fallback.

The valid ZEP_MODE set is
``{mock, graphiti, experimental_cloud}``; ``experimental_cloud`` is
deprecated and warns, and the legacy ``real`` value is accepted as an
alias that normalizes to it.
"""
from __future__ import annotations

import warnings

import pytest

from app.utils.runtime_env import (
    InvalidZepMode,
    get_allow_expensive,
    parse_zep_mode,
)


# ---------------------------------------------------------------------------
# parse_zep_mode — explicit value paths
# ---------------------------------------------------------------------------


def test_parse_zep_mode_accepts_mock():
    assert parse_zep_mode("mock") == "mock"


def test_parse_zep_mode_accepts_graphiti():
    """Graphiti is the local-first production default."""
    assert parse_zep_mode("graphiti") == "graphiti"


def test_parse_zep_mode_accepts_experimental_cloud():
    """``experimental_cloud`` is the deprecated-but-supported cloud lane."""
    with pytest.warns(DeprecationWarning, match="experimental_cloud is deprecated"):
        assert parse_zep_mode("experimental_cloud") == "experimental_cloud"


def test_parse_zep_mode_real_is_deprecated_alias_for_experimental_cloud():
    """Legacy ``real`` aliases to ``experimental_cloud`` with a DeprecationWarning.

    Removed in the release that ships GraphitiAudienceStore (deprecation sunset).
    """
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        result = parse_zep_mode("real")
    assert result == "experimental_cloud"
    messages = [str(w.message) for w in captured]
    assert any("ZEP_MODE='real' is deprecated" in message for message in messages)
    assert any("experimental_cloud is deprecated" in message for message in messages)


def test_parse_zep_mode_strips_whitespace():
    """``ZEP_MODE='GRAPHITI '`` must NOT silently fall through to mock; the
    centralised parser strips whitespace and accepts the trimmed value."""
    assert parse_zep_mode("  graphiti  ") == "graphiti"
    assert parse_zep_mode("\tmock\n") == "mock"
    # The deprecated ``real`` alias respects the same normalize step.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert parse_zep_mode("  REAL  ") == "experimental_cloud"


def test_parse_zep_mode_is_case_insensitive():
    assert parse_zep_mode("GRAPHITI") == "graphiti"
    assert parse_zep_mode("Mock") == "mock"
    assert parse_zep_mode("MoCk") == "mock"
    with pytest.warns(DeprecationWarning, match="experimental_cloud is deprecated"):
        assert parse_zep_mode("Experimental_Cloud") == "experimental_cloud"


def test_parse_zep_mode_default_applies_when_none_and_env_unset(monkeypatch):
    """When raw is None AND env is unset, ``default`` wins."""
    monkeypatch.delenv("ZEP_MODE", raising=False)
    assert parse_zep_mode() == "mock"
    assert parse_zep_mode(default="graphiti") == "graphiti"


def test_parse_zep_mode_default_applies_when_explicit_empty_string():
    """Explicit empty-string falls back to ``default`` (the ``value or default``
    short-circuit handles falsy). Whitespace-only, however, was historically
    rejected (pre-SSoT did ``(raw or 'mock').strip().lower()`` which would
    yield ``""`` and fall through the mode set) -- we preserve that strict
    behaviour so a misconfigured ``ZEP_MODE='   '`` surfaces rather than
    silently degrading to mock."""
    assert parse_zep_mode("") == "mock"
    with pytest.raises(InvalidZepMode):
        parse_zep_mode("   ")


def test_parse_zep_mode_raises_on_invalid_value():
    with pytest.raises(InvalidZepMode) as exc_info:
        parse_zep_mode("production")
    # Offending value is echoed in the message for the boot-matrix contract.
    assert "production" in str(exc_info.value)


def test_parse_zep_mode_raises_on_invalid_env(monkeypatch):
    """When raw=None, env value is consulted; bogus env raises."""
    monkeypatch.setenv("ZEP_MODE", "bogus-mode")
    with pytest.raises(InvalidZepMode):
        parse_zep_mode()


def test_parse_zep_mode_env_override_wins_over_default(monkeypatch):
    """``default`` kicks in only when env is unset, not when env is set."""
    monkeypatch.setenv("ZEP_MODE", "graphiti")
    assert parse_zep_mode(default="mock") == "graphiti"


# ---------------------------------------------------------------------------
# get_allow_expensive
# ---------------------------------------------------------------------------


def test_get_allow_expensive_default_false(monkeypatch):
    monkeypatch.delenv("POLICY_ALLOW_EXPENSIVE", raising=False)
    assert get_allow_expensive() is False


def test_get_allow_expensive_true(monkeypatch):
    monkeypatch.setenv("POLICY_ALLOW_EXPENSIVE", "true")
    assert get_allow_expensive() is True


def test_get_allow_expensive_case_insensitive(monkeypatch):
    monkeypatch.setenv("POLICY_ALLOW_EXPENSIVE", "TRUE")
    assert get_allow_expensive() is True
    monkeypatch.setenv("POLICY_ALLOW_EXPENSIVE", "True")
    assert get_allow_expensive() is True


def test_get_allow_expensive_strips_whitespace(monkeypatch):
    monkeypatch.setenv("POLICY_ALLOW_EXPENSIVE", "  true  ")
    assert get_allow_expensive() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "", "yes", "1"])
def test_get_allow_expensive_strict_true_match(monkeypatch, value):
    """Only the literal ``'true'`` (after strip+lower) is truthy. Mirrors the
    historical parser; ``'1'`` / ``'yes'`` are NOT accepted by design so the
    expensive flag cannot be flipped by an accidental docker-compose default."""
    monkeypatch.setenv("POLICY_ALLOW_EXPENSIVE", value)
    assert get_allow_expensive() is False
