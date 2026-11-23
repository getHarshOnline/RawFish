"""Runtime environment variable parsing (single SSoT).

Centralised parsers for ``ZEP_MODE`` and ``POLICY_ALLOW_EXPENSIVE`` used
by ``app.config``, the scenario loader, and ``/api/sim/*``. One
normalization rule (``.strip().lower()``), one default (``mock``), one
``InvalidZepMode`` exception. Strict: unknown values raise rather than
silently falling through. Never inline ``os.environ.get('ZEP_MODE', ...)``
outside this module; import :func:`parse_zep_mode`.

The valid 3-mode ladder (local-first posture) is
``{mock, graphiti, experimental_cloud}``. The legacy value ``real`` is
accepted as a deprecated alias for ``experimental_cloud`` for one
release; passing it emits a :class:`DeprecationWarning` naming the
planned removal. Callers that already pass ``mock`` are unaffected.
"""
from __future__ import annotations

import os
import warnings
from typing import Literal

ZepModeLiteral = Literal["mock", "graphiti", "experimental_cloud"]
_VALID_ZEP_MODES = {"mock", "graphiti", "experimental_cloud"}
# ``real`` is the legacy spelling kept alive for one release so existing
# .env files / docker-compose overrides do not break the boot. Resolved
# to ``experimental_cloud`` at parse time with a DeprecationWarning.
_LEGACY_REAL_ALIAS = "real"


class InvalidZepMode(ValueError):
    """Raised when ``ZEP_MODE`` is set to a value outside the valid set.

    The valid set is ``{mock, graphiti, experimental_cloud}`` (with
    ``real`` accepted as a deprecated alias). ``raw_value`` exposes the
    offending env value so callers can echo it in their own error format
    without re-reading ``os.environ``.
    """

    def __init__(self, message: str, *, raw_value: object = None) -> None:
        super().__init__(message)
        self.raw_value = raw_value


def parse_zep_mode(
    raw: str | None = None,
    *,
    default: ZepModeLiteral = "mock",
) -> ZepModeLiteral:
    """Single SSoT for parsing ZEP_MODE env / value.

    Strips whitespace and lower-cases. When ``raw`` is None (the typical
    caller path), reads ``os.environ['ZEP_MODE']`` at call time so a test
    harness that monkeypatches the env between requests sees the live
    value. Raises :class:`InvalidZepMode` on anything outside
    ``{mock, graphiti, experimental_cloud}`` (plus the deprecated
    ``real`` alias). ``experimental_cloud`` remains accepted for old
    deployments but emits a deprecation warning; new RawFish runs should
    use ``mock`` or local ``graphiti``.

    Contract notes
    --------------
    * Env unset OR raw=None AND env unset -> ``default`` ("mock") wins.
    * Explicit ``raw=""`` (or env set to ``""``) -> REJECTED, NOT defaulted.
      Matches the legacy in-line behaviour (``(raw or "").strip().lower()``
      would produce ``""`` and fall through the allowlist). Distinguishing
      "unset" from "set-to-empty" is the whole reason the test harness
      uses ``monkeypatch.delenv`` vs ``monkeypatch.setenv("", "")``.
    * ``experimental_cloud`` emits a :class:`DeprecationWarning`; local
      Graphiti is the durable production path.
    * ``real`` -> normalized to ``experimental_cloud`` and emits the same
      warning plus the alias-specific warning.
    """
    if raw is None:
        # Sentinel: env-unset vs env-empty are indistinguishable in
        # os.environ.get — we use the default only when the key is absent.
        if "ZEP_MODE" not in os.environ:
            value = default
        else:
            value = os.environ["ZEP_MODE"]
    else:
        value = raw if raw else default  # explicit empty-string -> default
    normalized = (value or "").strip().lower()
    if normalized == _LEGACY_REAL_ALIAS:
        warnings.warn(
            (
                "ZEP_MODE='real' is deprecated; use "
                "ZEP_MODE='experimental_cloud' (or switch to the local "
                "default ZEP_MODE='graphiti'). The 'real' alias is removed "
                "in the release that ships GraphitiAudienceStore."
            ),
            DeprecationWarning,
            stacklevel=2,
        )
        normalized = "experimental_cloud"
    if normalized == "experimental_cloud":
        warnings.warn(
            (
                "ZEP_MODE=experimental_cloud is deprecated and scheduled "
                "to be REMOVED; use ZEP_MODE=graphiti for local graph "
                "memory. No further experimental_cloud implementation is "
                "planned."
            ),
            DeprecationWarning,
            stacklevel=2,
        )
    if normalized not in _VALID_ZEP_MODES:
        raise InvalidZepMode(
            (
                f"ZEP_MODE={value!r} invalid; "
                f"expected one of {sorted(_VALID_ZEP_MODES)}"
            ),
            raw_value=value,
        )
    return normalized  # type: ignore[return-value]


def get_allow_expensive() -> bool:
    """Return ``True`` iff ``POLICY_ALLOW_EXPENSIVE`` env is the literal
    ``'true'`` after strip+lowercase. Any other value (unset, ``'false'``,
    ``'0'``, ``'yes'``, etc.) returns ``False``. Mirrors the historical
    parser at ``api.policy._expensive_allowed`` and
    ``policy.scenario_loader._allow_expensive_default``.
    """
    return os.environ.get("POLICY_ALLOW_EXPENSIVE", "false").strip().lower() == "true"


__all__ = ["InvalidZepMode", "ZepModeLiteral", "parse_zep_mode", "get_allow_expensive"]
