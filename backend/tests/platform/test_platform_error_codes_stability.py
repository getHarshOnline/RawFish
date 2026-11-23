"""Frozen snapshot of :class:`PlatformErrorCode` -- the generic
``/api/sim/use-cases/*`` dispatch SSoT.

Mirrors the policy-lane stability pin in
:mod:`tests.policy.test_error_codes_stability`. Adding or removing a
code is a wire-contract change; update the expected set in this file
in the same diff that edits the enum.
"""
from __future__ import annotations

from app.simulation_platform.error_codes import PlatformErrorCode


# Canonical codes emitted by app.api.use_cases (the generic dispatch
# blueprint) in addition to the policy-lane ErrorCode set. The split
# is documented on PlatformErrorCode itself: shared codes (INVALID_ID,
# SIMULATION_NOT_FOUND, REPORT_NOT_READY, REPORT_CORRUPTED,
# RUNNER_START_FAILED, IDEMPOTENCY_KEY_MISMATCH, INVALID_REQUEST)
# live in app.api.error_codes.ErrorCode and are imported from there.
_CANONICAL_PLATFORM_ERROR_CODES = frozenset({
    "USE_CASE_NOT_REGISTERED",
    "INVALID_INPUT",
    "SCENARIO_INVALID",
    "INVALID_STATE",
    "CAPABILITY_UNSUPPORTED",
    "STATE_CORRUPTED",
    "INVALID_ADAPTER_ARTIFACT_NAME",
    "ADAPTER_CATALOG_FAILED",
    "ADAPTER_PREPARE_FAILED",
    "ADAPTER_REPORT_FAILED",
    "ADAPTER_SENSITIVITY_FAILED",
})


def test_platform_error_code_set_matches_frozen_snapshot():
    """Every wire code must appear in the snapshot; no extras, no deletions."""
    live = {member.value for member in PlatformErrorCode}
    assert live == _CANONICAL_PLATFORM_ERROR_CODES, (
        f"PlatformErrorCode drift: "
        f"extras={live - _CANONICAL_PLATFORM_ERROR_CODES} "
        f"missing={_CANONICAL_PLATFORM_ERROR_CODES - live}"
    )


def test_platform_error_codes_disjoint_from_policy_error_codes():
    """The two enums MUST emit disjoint string values.

    Shared codes (e.g. INVALID_ID) live ONLY in
    :class:`app.api.error_codes.ErrorCode`; PlatformErrorCode lists
    only codes that the generic dispatch blueprint emits that the
    policy lane does NOT. Overlap would create two enum members for
    the same wire string and a future call-site refactor could
    accidentally pick the wrong one.
    """
    from app.api.error_codes import ErrorCode

    platform_values = {m.value for m in PlatformErrorCode}
    policy_values = {m.value for m in ErrorCode}
    overlap = platform_values & policy_values
    assert overlap == set(), (
        f"PlatformErrorCode and ErrorCode overlap on {overlap!r}; "
        "shared codes must live in ErrorCode only."
    )
