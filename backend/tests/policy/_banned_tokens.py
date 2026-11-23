"""Backwards-compatible re-export shim.

The banned-token regression contract lives at
``app/services/policy/language_guard.py`` so the report generator can
call it before persisting ``policy_report.json`` (rejecting unhedged
output is a runtime contract, not a test-only one). This module exists
only so existing test files keep working with their
``from tests.policy._banned_tokens import ...`` imports; new test files
should import directly from
``app.services.policy.language_guard``.

Public surface is identical to the app module's; the implementation has
moved out, not changed shape.
"""
from __future__ import annotations

from app.services.policy.language_guard import (
    BANNED_PATTERNS,
    BannedTokenFinding,
    HEDGE_PATTERNS,
    assert_no_unhedged_banned_tokens,
    find_unhedged_banned_tokens,
)


__all__ = [
    "BANNED_PATTERNS",
    "HEDGE_PATTERNS",
    "BannedTokenFinding",
    "find_unhedged_banned_tokens",
    "assert_no_unhedged_banned_tokens",
]
