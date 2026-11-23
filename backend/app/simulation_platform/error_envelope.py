"""Structured response envelopes for the /api/sim/* HTTP surface.

Two pure builders, plus a re-export of :func:`safe_error`:

* :func:`make_ok` -- the uniform success envelope
  ``{"success": True, "data": <payload>}`` returned by product API
  endpoints.

* :func:`make_err` -- the uniform structured error envelope
  ``{"success": False, "error": {"code": "<STABLE_CODE>",
  "message": "...", "details": {...}}}`` returned by every non-2xx
  product API endpoint. The ``code`` field is the wire
  contract and the UI switches on it; see
  :class:`app.api.error_codes.ErrorCode` for the canonical set.

* :data:`safe_error` -- re-exported from :mod:`app.utils.security` so
  callers that already speak the envelope vocabulary have one import
  surface. The canonical definition stays in ``security.py`` because it
  is a general response-sanitising helper.

Both builders are deliberately side-effect-free. Concerns that need
to fire on the error path (e.g. releasing an Idempotency-Key claim
sentinel) live in the caller module, which composes the side effect
in front of :func:`make_err` -- keeping the envelope module a pure
platform boundary with no hidden state.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from flask import jsonify

from ..api.error_codes import ErrorCode
from ..utils.security import safe_error
from .error_codes import PlatformErrorCode

__all__ = ["make_ok", "make_err", "safe_error"]


def make_ok(data: Any, status: int = 200):
    """Return ``({"success": True, "data": data}, status)``.

    Every 2xx product API endpoint funnels through here
    so the success envelope shape is enforced in exactly one place.
    """
    return jsonify({"success": True, "data": data}), status


def make_err(
    code: ErrorCode | PlatformErrorCode | str,
    message: str,
    status: int,
    details: Optional[Dict[str, Any]] = None,
):
    """Return ``({"success": False, "error": {...}}, status)``.

    Accepts an :class:`ErrorCode` member (policy lane), a
    :class:`PlatformErrorCode` member (generic
    ``/api/sim/use-cases/*`` dispatch), or a raw string for callers that
    already resolved a stable code. The wire ``code`` field is
    always a plain string so downstream clients keep switching on
    string equality.

    The ``details`` block is included only when the caller passes a
    dict; an absent ``details`` omits the key entirely.
    """
    if isinstance(code, (ErrorCode, PlatformErrorCode)):
        # Both are ``(str, Enum)`` subclasses; ``.value`` yields the
        # canonical wire string. Pyright loses the Enum view through
        # the str-mixin so the cast quiets the analyzer without
        # changing runtime behaviour.
        wire_code = str(code.value)  # type: ignore[union-attr]
    else:
        wire_code = code
    payload: Dict[str, Any] = {
        "success": False,
        "error": {"code": wire_code, "message": message},
    }
    if details is not None:
        payload["error"]["details"] = details
    return jsonify(payload), status
