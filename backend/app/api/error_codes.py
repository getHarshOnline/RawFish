"""Canonical error-code SSoT for /api/sim/*.

:class:`ErrorCode` is the shared set of stable string codes emitted by
lane-specific adapters through the generic use-case API. The UI switches
on ``error.code``. Adding or renaming a code is a contract change --
``tests/policy/test_error_codes_stability.py`` pins the current set.
Never inline a new code as a string literal at a call site; add it here
first.
"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """Stable error codes emitted by /api/sim/*.

    The string value is the wire contract; renaming a member is a
    breaking change for clients switching on ``error.code``.
    """

    UNAUTHORIZED = "UNAUTHORIZED"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_PERSONA = "INVALID_PERSONA"
    INVALID_SCOPE = "INVALID_SCOPE"
    INVALID_SCENARIO = "INVALID_SCENARIO"
    INVALID_ID = "INVALID_ID"
    INVALID_ZEP_MODE = "INVALID_ZEP_MODE"
    SIMULATION_NOT_FOUND = "SIMULATION_NOT_FOUND"
    SIM_NOT_PREPARED = "SIM_NOT_PREPARED"
    REPORT_NOT_READY = "REPORT_NOT_READY"
    REPORT_CORRUPTED = "REPORT_CORRUPTED"
    SCALE_GUARD_EXCEEDED = "SCALE_GUARD_EXCEEDED"
    ZEP_API_KEY_MISSING = "ZEP_API_KEY_MISSING"
    ZEP_GRAPHITI_UNAVAILABLE = "ZEP_GRAPHITI_UNAVAILABLE"
    MOCK_ZEP_NOT_IMPLEMENTED = "MOCK_ZEP_NOT_IMPLEMENTED"
    PRESET_NOT_FOUND = "PRESET_NOT_FOUND"
    PREVIEW_FAILED = "PREVIEW_FAILED"
    PARSE_FAILED = "PARSE_FAILED"
    IDEMPOTENCY_KEY_MISMATCH = "IDEMPOTENCY_KEY_MISMATCH"
    RUNNER_START_FAILED = "RUNNER_START_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


__all__ = ["ErrorCode"]
