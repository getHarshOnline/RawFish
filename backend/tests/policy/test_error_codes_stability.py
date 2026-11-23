"""Frozen snapshot of :class:`ErrorCode` (the /api/sim/* SSoT).

Enumerates the canonical codes emitted by ``policy_sim_bp``. Adding or
removing a code is a contract change; update the expected set in this
file in the same diff that edits the enum.
"""
from __future__ import annotations

from app.api.error_codes import ErrorCode


# 22 canonical codes emitted by policy_sim_bp. CAPABILITY_UNSUPPORTED
# is a PLATFORM concept (signals adapter capability gap, emitted by
# the generic dispatch blueprint) and lives in
# :class:`app.simulation_platform.error_codes.PlatformErrorCode`,
# not here. The disjoint-enums contract is pinned in
# ``tests/platform/test_platform_error_codes_stability.py``.
_CANONICAL_ERROR_CODES = frozenset({
    "UNAUTHORIZED",
    "MISSING_FIELD",
    "INVALID_REQUEST",
    "INVALID_PERSONA",
    "INVALID_SCOPE",
    "INVALID_SCENARIO",
    "INVALID_ID",
    "INVALID_ZEP_MODE",
    "SIMULATION_NOT_FOUND",
    "SIM_NOT_PREPARED",
    "REPORT_NOT_READY",
    "REPORT_CORRUPTED",
    "SCALE_GUARD_EXCEEDED",
    "ZEP_API_KEY_MISSING",
    "ZEP_GRAPHITI_UNAVAILABLE",
    "MOCK_ZEP_NOT_IMPLEMENTED",
    "PRESET_NOT_FOUND",
    "PREVIEW_FAILED",
    "PARSE_FAILED",
    "IDEMPOTENCY_KEY_MISMATCH",
    "RUNNER_START_FAILED",
    "INTERNAL_ERROR",
})


def test_error_code_set_matches_frozen_snapshot():
    """Every wire code must appear in the snapshot; no extras, no deletions."""
    live = {member.value for member in ErrorCode}
    assert live == _CANONICAL_ERROR_CODES, (
        f"ErrorCode drift: extras={live - _CANONICAL_ERROR_CODES} "
        f"missing={_CANONICAL_ERROR_CODES - live}"
    )


def test_error_code_member_count_pinned():
    """22 canonical codes; bump intentionally when extending the contract."""
    assert len(list(ErrorCode)) == 22


def test_error_code_values_are_strings():
    """Wire shape contract: ``ErrorCode.X.value`` is a plain ``str``."""
    for member in ErrorCode:
        assert isinstance(member.value, str)
        # name == value invariant -- the SCREAMING_SNAKE label IS the wire code.
        assert member.name == member.value, (
            f"ErrorCode.{member.name} value {member.value!r} drifted from its name"
        )


def test_error_code_is_str_enum_for_json_dump():
    """str+Enum mix lets jsonify treat the member as the wire string directly
    so callers passing ``ErrorCode.X`` to an envelope dict still serialise
    cleanly under both str-coerce and explicit ``.value`` paths."""
    assert issubclass(ErrorCode, str)
    sample = ErrorCode.UNAUTHORIZED
    assert sample == "UNAUTHORIZED"
    assert str(sample.value) == "UNAUTHORIZED"
