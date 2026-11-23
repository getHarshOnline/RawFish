"""Idempotency safety contract: never double-execute a slow handler.

Pinned guarantees:

  * **Same key, same body, peer in flight, cache eventually appears**
    within the wait window -> replay the peer's cached response (no
    duplicate side effect).
  * **Same key, different body, peer in flight** -> immediate 422
    IDEMPOTENCY_KEY_MISMATCH (no waiting; the bodies disagree so a
    retry semantics has already failed).
  * **Same key, same body, peer in flight, wait window elapses** ->
    structured 409 with ``details.reason == "idempotency_in_progress"``.
    The framework MUST NOT delete the sentinel and proceed as a
    second writer; that would silently double-execute the original
    handler (a real LLM call >5s would trigger this on every retry).
  * **Wait window is env-configurable** via
    ``IDEMPOTENCY_WAIT_SECONDS`` so slow endpoints can tune the
    threshold WITHOUT changing the safety contract.

These tests exercise the ``lookup`` helper directly with hand-placed
claim sentinels so the race is deterministic; the route-level tests
under ``tests/policy/test_idempotency.py`` exercise the happy paths
through Flask.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

from tests._helpers import make_app


_IDEM_HEADER = "Idempotency-Key"


def _err_factory(code, message, status, details=None):
    """Mock the policy layer's _err signature: returns a
    ``({"error": ...}, status)`` tuple the same way."""
    code_str = getattr(code, "value", str(code))
    return (
        {"error": {"code": code_str, "message": message, "details": details or {}}},
        int(status),
    )


def _seed_claim(claim_path: str, fingerprint: str) -> None:
    """Hand-write a ``.claim`` sentinel for an "in-flight peer"."""
    os.makedirs(os.path.dirname(claim_path), exist_ok=True)
    with open(claim_path, "w", encoding="utf-8") as fh:
        fh.write(fingerprint)


def _idempotency_paths_for(key: str, body: Any, app):
    """Resolve (cache_path, claim_path, fingerprint) inside a request ctx."""
    from app.simulation_platform.idempotency import (
        _idempotency_cache_path,
        _idempotency_fingerprint,
    )
    with app.test_request_context(
        "/api/sim/use-cases/creator/simulation/create",
        method="POST",
        headers={_IDEM_HEADER: key, "Content-Type": "application/json"},
        data=json.dumps(body),
    ):
        cache_path = _idempotency_cache_path(key)
        fingerprint = _idempotency_fingerprint(body)
    return cache_path, cache_path + ".claim", fingerprint


# ---------------------------------------------------------------------------
# Safety: stale claim sentinel -> 409 in_progress (NOT silent duplicate)
# ---------------------------------------------------------------------------


def test_lookup_returns_409_in_progress_when_peer_never_commits(
    monkeypatch, tmp_path,
) -> None:
    """**The critical safety pin.** A same-key same-body request whose
    in-flight peer never writes a cache file (e.g. slow LLM call) MUST
    receive a structured 409 with ``reason=idempotency_in_progress``,
    NOT a None return that lets the second request execute the
    handler and trigger a duplicate side effect.
    """
    # Short wait window so the test runs fast.
    monkeypatch.setenv("IDEMPOTENCY_WAIT_SECONDS", "0.5")
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    from app.simulation_platform.idempotency import lookup

    key = "key-stale-claim-001"
    body = {"scenario": {"persona": "creator"}}
    _cache_path, claim_path, fingerprint = _idempotency_paths_for(key, body, app)
    _seed_claim(claim_path, fingerprint)

    try:
        with app.test_request_context(
            "/api/sim/use-cases/creator/simulation/create",
            method="POST",
            headers={_IDEM_HEADER: key, "Content-Type": "application/json"},
            data=json.dumps(body),
        ):
            result = lookup(body, _err_factory)
    finally:
        try:
            os.unlink(claim_path)
        except OSError:
            pass

    assert result is not None, "lookup MUST NOT return None (which would let the second request execute)"
    payload, status = result
    assert status == 409, f"expected 409 in_progress, got {status}: {payload}"
    err = payload.get("error", {})
    details = err.get("details") or {}
    assert details.get("reason") == "idempotency_in_progress"
    assert details.get("idempotency_key") == key
    # The wait_seconds knob's value is echoed back for operator triage.
    assert details.get("wait_seconds") == 0.5


def test_lookup_with_different_body_fails_immediately_no_wait(
    monkeypatch, tmp_path,
) -> None:
    """Same key, DIFFERENT body, peer in flight: the wait loop MUST
    be skipped entirely -- the bodies already disagree so a replay
    cannot recover. Surface the mismatch immediately (422)."""
    monkeypatch.setenv("IDEMPOTENCY_WAIT_SECONDS", "10")  # large; should not be hit
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    from app.simulation_platform.idempotency import lookup

    key = "key-mismatch-001"
    body_first = {"scenario": {"persona": "creator"}}
    body_retry = {"scenario": {"persona": "video"}}
    _cache_path, claim_path, fingerprint_first = _idempotency_paths_for(
        key, body_first, app,
    )
    _seed_claim(claim_path, fingerprint_first)

    import time
    started = time.monotonic()
    try:
        with app.test_request_context(
            "/api/sim/use-cases/creator/simulation/create",
            method="POST",
            headers={_IDEM_HEADER: key, "Content-Type": "application/json"},
            data=json.dumps(body_retry),
        ):
            result = lookup(body_retry, _err_factory)
    finally:
        try:
            os.unlink(claim_path)
        except OSError:
            pass
    elapsed = time.monotonic() - started

    assert result is not None
    payload, status = result
    assert status == 422
    err = payload.get("error", {})
    # The 10s wait window MUST NOT have engaged -- mismatch should
    # be detected and returned immediately (well under a second).
    assert elapsed < 2.0, (
        f"different-body mismatch should fail immediately; took {elapsed:.2f}s"
    )
    assert "IDEMPOTENCY_KEY_MISMATCH" in err.get("code", "")


def test_lookup_replays_cache_when_peer_commits_within_wait_window(
    monkeypatch, tmp_path,
) -> None:
    """Same key, same body, peer in flight, peer commits its cache
    file BEFORE the wait window elapses: the second caller replays
    the cached response. No 409, no duplicate execution."""
    monkeypatch.setenv("IDEMPOTENCY_WAIT_SECONDS", "2")
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    from app.simulation_platform.idempotency import lookup

    key = "key-commit-mid-wait-001"
    body = {"scenario": {"persona": "creator"}}
    cache_path, claim_path, fingerprint = _idempotency_paths_for(key, body, app)
    _seed_claim(claim_path, fingerprint)

    # Start a background thread that, after 200ms, writes the peer's
    # cache file (simulating a slow but successful first writer).
    def _peer_commit():
        import time
        time.sleep(0.2)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump({
                "fingerprint": fingerprint,
                "body": {"simulation_id": "sim_replayed_001"},
                "status": 201,
            }, fh)

    peer = threading.Thread(target=_peer_commit, daemon=True)
    peer.start()

    try:
        with app.test_request_context(
            "/api/sim/use-cases/creator/simulation/create",
            method="POST",
            headers={_IDEM_HEADER: key, "Content-Type": "application/json"},
            data=json.dumps(body),
        ):
            result = lookup(body, _err_factory)
    finally:
        peer.join(timeout=2.0)
        for path in (cache_path, claim_path):
            try:
                os.unlink(path)
            except OSError:
                pass

    assert result is not None, "lookup MUST replay the peer's cache, not return None"
    response, status = result
    assert status == 201
    # The replayed body is the peer's, not a freshly-executed handler's.
    response_json = response.get_json() if hasattr(response, "get_json") else response
    assert response_json["simulation_id"] == "sim_replayed_001"


# ---------------------------------------------------------------------------
# Route-level ownership: in-flight loser MUST NOT delete the winner's sentinel
# ---------------------------------------------------------------------------


def test_route_layer_err_factory_does_not_delete_in_flight_winners_sentinel(
    monkeypatch, tmp_path,
) -> None:
    """The generic error factory must not delete another request's claim.

    The platform-level test stub uses a no-op error factory, so this
    test exercises the product error path: ``lookup`` returns a 409 for
    an in-flight loser, the generic error helper calls
    ``release_claim_if_any()``, and the winner's claim must remain.
    """
    monkeypatch.setenv("IDEMPOTENCY_WAIT_SECONDS", "0.3")
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    from app.api.use_cases import _err as use_case_err
    from app.simulation_platform.idempotency import lookup

    key = "key-loser-must-not-delete-001"
    body = {"scenario": {"persona": "creator"}}
    _cache_path, claim_path, fingerprint = _idempotency_paths_for(key, body, app)
    _seed_claim(claim_path, fingerprint)

    try:
        # Simulate the loser's request context. The policy blueprint's
        # _err is the error factory the route layer uses; it calls
        # release_claim_if_any() internally. The loser MUST receive a
        # 409 in_progress AND leave the sentinel intact.
        with app.test_request_context(
            "/api/sim/use-cases/creator/simulation/create",
            method="POST",
            headers={_IDEM_HEADER: key, "Content-Type": "application/json"},
            data=json.dumps(body),
        ):
            result = lookup(body, use_case_err)
        # The loser response is structured 409 in_progress.
        assert result is not None
        _response, status = result
        assert status == 409
        # The winner's sentinel MUST still be on disk -- the loser
        # path went through the generic error helper which called
        # release_claim_if_any internally; the ownership check is
        # what protects the winner.
        assert os.path.exists(claim_path), (
            "the in-flight winner's .claim sentinel MUST survive a "
            "loser error path; the loser never owned the claim, so "
            "release_claim_if_any must be a no-op"
        )
    finally:
        try:
            os.unlink(claim_path)
        except OSError:
            pass


def test_route_layer_err_factory_does_not_delete_winner_on_mismatch_path(
    monkeypatch, tmp_path,
) -> None:
    """Companion safety pin for the same-key DIFFERENT-body case.
    The mismatch path returns 422 IDEMPOTENCY_KEY_MISMATCH through
    the generic error helper; the in-flight winner's sentinel MUST survive."""
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    from app.api.use_cases import _err as use_case_err
    from app.simulation_platform.idempotency import lookup

    key = "key-mismatch-via-err-001"
    body_first = {"scenario": {"persona": "creator"}}
    body_retry = {"scenario": {"persona": "video"}}
    _cache_path, claim_path, fingerprint_first = _idempotency_paths_for(
        key, body_first, app,
    )
    _seed_claim(claim_path, fingerprint_first)

    try:
        with app.test_request_context(
            "/api/sim/use-cases/creator/simulation/create",
            method="POST",
            headers={_IDEM_HEADER: key, "Content-Type": "application/json"},
            data=json.dumps(body_retry),
        ):
            result = lookup(body_retry, use_case_err)
        assert result is not None
        _response, status = result
        assert status == 422
        assert os.path.exists(claim_path), (
            "mismatch loser MUST NOT delete the winner's .claim sentinel"
        )
    finally:
        try:
            os.unlink(claim_path)
        except OSError:
            pass


def test_winner_path_owns_and_releases_its_own_claim(
    monkeypatch, tmp_path,
) -> None:
    """The companion positive pin: a WINNER that creates the sentinel
    via O_EXCL DOES own it; an error path through the generic helper DOES
    release it (so a key whose first attempt erred is not blocked
    behind a stale sentinel)."""
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    from app.api.use_cases import _err as use_case_err
    from app.simulation_platform.idempotency import lookup

    key = "key-winner-owns-release-001"
    body = {"scenario": {"persona": "creator"}}
    _cache_path, claim_path, _fingerprint = _idempotency_paths_for(key, body, app)
    # No pre-seeded claim -- this request becomes the winner.

    try:
        with app.test_request_context(
            "/api/sim/use-cases/creator/simulation/create",
            method="POST",
            headers={_IDEM_HEADER: key, "Content-Type": "application/json"},
            data=json.dumps(body),
        ):
            # Lookup wins the O_EXCL race; returns None so the route
            # would normally proceed to the handler.
            assert lookup(body, use_case_err) is None
            assert os.path.exists(claim_path), "winner must have created the sentinel"
            # Simulate the handler raising and the route returning
            # via the generic error helper -- the ownership marker is set, so the
            # winner's sentinel SHOULD be released by _err.
            use_case_err("INTERNAL_ERROR", "handler exploded", 500)
            assert not os.path.exists(claim_path), (
                "winner's sentinel MUST be released by _err on the "
                "winner's own error path"
            )
    finally:
        try:
            os.unlink(claim_path)
        except OSError:
            pass


def test_idempotency_wait_seconds_env_resolver_clamps_invalid_values(
    monkeypatch,
) -> None:
    """The env knob is operator-tunable but MUST NOT silently accept
    non-positive / non-numeric values that would either disable
    the wait (race window) or wrap to negative timeouts."""
    from app.simulation_platform.idempotency import _resolve_wait_seconds

    monkeypatch.delenv("IDEMPOTENCY_WAIT_SECONDS", raising=False)
    assert _resolve_wait_seconds() == 5.0

    monkeypatch.setenv("IDEMPOTENCY_WAIT_SECONDS", "12.5")
    assert _resolve_wait_seconds() == 12.5

    monkeypatch.setenv("IDEMPOTENCY_WAIT_SECONDS", "0")
    assert _resolve_wait_seconds() == 5.0  # falls back to default

    monkeypatch.setenv("IDEMPOTENCY_WAIT_SECONDS", "-3")
    assert _resolve_wait_seconds() == 5.0

    monkeypatch.setenv("IDEMPOTENCY_WAIT_SECONDS", "not-a-number")
    assert _resolve_wait_seconds() == 5.0
