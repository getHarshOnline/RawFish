"""Canonical error-code SSoT for the generic ``/api/sim/use-cases/*`` dispatch.

:class:`PlatformErrorCode` is the single set of stable string codes the
:mod:`app.api.use_cases` blueprint emits in addition to the policy-lane
codes already pinned in :class:`app.api.error_codes.ErrorCode`. Adding
or renaming a code is a wire-contract change -- a downstream client
switching on ``error.code`` will break -- so
``tests/platform/test_platform_error_codes_stability.py`` pins the
current set the same way the policy lane pins its own.

The two enums are intentionally disjoint:

  * Codes shared with the policy lane (``INVALID_ID``,
    ``SIMULATION_NOT_FOUND``, ``REPORT_NOT_READY``,
    ``REPORT_CORRUPTED``, ``RUNNER_START_FAILED``,
    ``IDEMPOTENCY_KEY_MISMATCH``, ``INVALID_REQUEST``) live in
    :class:`app.api.error_codes.ErrorCode` and are imported from
    there at the generic-route call sites so shared failures use the
    same wire codes.
  * Codes unique to the generic dispatch surface (USE_CASE_NOT_REGISTERED,
    CAPABILITY_UNSUPPORTED, ADAPTER_*_FAILED, etc.) live here.

This split keeps the policy lane's frozen enum stable while letting the
platform layer evolve its own vocabulary without mutating the policy
lane's frozen contract. :func:`app.simulation_platform.error_envelope.make_err`
already accepts either enum at the same call site.
"""
from __future__ import annotations

from enum import Enum


class PlatformErrorCode(str, Enum):
    """Stable error codes emitted by the generic use-case dispatch routes.

    The string value is the wire contract; renaming a member is a
    breaking change for clients that switch on ``error.code``.
    """

    USE_CASE_NOT_REGISTERED = "USE_CASE_NOT_REGISTERED"
    INVALID_INPUT = "INVALID_INPUT"
    SCENARIO_INVALID = "SCENARIO_INVALID"
    INVALID_STATE = "INVALID_STATE"
    CAPABILITY_UNSUPPORTED = "CAPABILITY_UNSUPPORTED"
    STATE_CORRUPTED = "STATE_CORRUPTED"
    INVALID_ADAPTER_ARTIFACT_NAME = "INVALID_ADAPTER_ARTIFACT_NAME"
    ADAPTER_CATALOG_FAILED = "ADAPTER_CATALOG_FAILED"
    ADAPTER_PREPARE_FAILED = "ADAPTER_PREPARE_FAILED"
    ADAPTER_REPORT_FAILED = "ADAPTER_REPORT_FAILED"
    ADAPTER_SENSITIVITY_FAILED = "ADAPTER_SENSITIVITY_FAILED"


__all__ = ["PlatformErrorCode"]
