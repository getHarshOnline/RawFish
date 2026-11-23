"""Idempotency-Key cache for non-idempotent simulation-platform POSTs.

Non-idempotent generic POSTs (create, prepare, start,
and the priors / counterfactual / sensitivity
companions) accept an ``Idempotency-Key`` header. The cache is keyed on
(key, METHOD, ROUTE_PATTERN, view_args, body-fingerprint):

  * cache-file PATH includes a sanitised method+route discriminator so a
    client reusing the same key across prepare and
    start (legitimate -- both naturally take
    ``{"simulation_id": ...}``) lands in DIFFERENT cache entries and
    each endpoint replays its OWN response. Without the discriminator a
    prepare-then-start with a reused key would either alias responses
    or surface as a spurious IDEMPOTENCY_KEY_MISMATCH.
  * cache-file CONTENTS include ``method`` + ``route`` fields alongside
    the fingerprint for operator debug visibility (cat-ing a stray
    idempotency file tells you exactly which endpoint produced it).
  * the fingerprint itself ALSO folds method + route + view_args into
    the hashed input -- belt-and-suspenders so a future path-flattening
    refactor cannot silently re-introduce the collision, and so the same
    Idempotency-Key against two different ``sim_id``s does not collide.

Storage layout::

    {OASIS_SIMULATION_DATA_DIR}/.idempotency/{sanitised_endpoint}/{key}.json

Only 2xx responses are cached -- a transient 500 must not become a
permanent cache hit. Error responses on the first call do NOT touch the
cache; the client can retry with the same key and try again.

The public ``lookup`` helper accepts the caller's ``err_factory``
callable rather than reaching across the package boundary. The factory
signature mirrors the generic API error helper::

    err_factory(code, message, status, details=None) -> (Response, int)

``release_claim_if_any`` is public because product API error helpers
invoke it on every error path so a key whose first attempt erred is not
blocked behind a stale per-key sentinel.

Production-readiness boundary
-----------------------------

The file-sentinel storage layout is a local-filesystem implementation
with an operator-tunable claim TTL. A stale ``.claim`` left behind by a
hard process crash (SIGKILL, OOM, abrupt container restart) is removed
after ``IDEMPOTENCY_CLAIM_TTL_SECONDS`` (default 900 seconds). The
request-side wait window is still bounded by ``IDEMPOTENCY_WAIT_SECONDS``.

Multi-process correctness is filesystem-dependent: ``os.open`` with
    ``O_CREAT | O_EXCL`` is atomic on local POSIX filesystems but
    NOT on NFS shares, network volumes, or filesystems mounted
    without ``-o noac``. Multi-host deployments MUST keep the
    idempotency root on a single-host volume, OR migrate the
    backing store to SQLite (or Redis) before scaling out.

The SQLite migration trigger is: the first deployment that
horizontally scales the simulation-platform layer across more than
one host, OR the first deployment that needs operator-readable
TTL semantics. The migration is mechanical (the public surface
here -- ``lookup`` / ``store`` / ``release_claim_if_any`` -- keeps
its signature; the backend swap-out is the only change).
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Callable, Optional, Tuple

from flask import jsonify, request

from ..config import Config
from ..utils.atomic_io import atomic_write_json
from ..utils.id_validator import safe_join, validate_safe_id
from ..utils.logger import get_logger

logger = get_logger("rawfish.api.policy_sim")

_IDEMPOTENCY_HEADER = "Idempotency-Key"
_IDEMPOTENCY_WAIT_DEFAULT_SECONDS: float = 5.0
_IDEMPOTENCY_CLAIM_TTL_DEFAULT_SECONDS: float = 900.0


def _resolve_wait_seconds() -> float:
    """Read the env-configurable in-flight wait window.

    Slow endpoints (notably report generation, which can exceed
    the historical 5-second default on a real LLM call) need a
    longer wait window so a legitimate same-key retry does NOT
    short-circuit into a 409 in-progress while the first call is
    still legitimately working. ``IDEMPOTENCY_WAIT_SECONDS`` is
    the operator-tunable knob; non-numeric / non-positive values
    fall back to the safe default.

    The safety contract (never delete the sentinel and proceed as
    a second writer) is UNAFFECTED by this knob -- only the wait
    window before the 409 fires is configurable.
    """
    raw = os.environ.get("IDEMPOTENCY_WAIT_SECONDS")
    if raw is None:
        return _IDEMPOTENCY_WAIT_DEFAULT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _IDEMPOTENCY_WAIT_DEFAULT_SECONDS
    if value <= 0:
        return _IDEMPOTENCY_WAIT_DEFAULT_SECONDS
    return value


def _resolve_claim_ttl_seconds() -> float:
    """Read the env-configurable stale-claim cleanup window."""
    raw = os.environ.get("IDEMPOTENCY_CLAIM_TTL_SECONDS")
    if raw is None:
        return _IDEMPOTENCY_CLAIM_TTL_DEFAULT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _IDEMPOTENCY_CLAIM_TTL_DEFAULT_SECONDS
    if value <= 0:
        return _IDEMPOTENCY_CLAIM_TTL_DEFAULT_SECONDS
    return value


def _remove_stale_claim_if_any(claim_path: str) -> bool:
    """Remove an abandoned first-writer claim after the configured TTL."""
    try:
        stat = os.stat(claim_path)
    except OSError:
        return False
    try:
        import time as _time
        age_seconds = _time.time() - stat.st_mtime
    except Exception:
        return False
    ttl_seconds = _resolve_claim_ttl_seconds()
    if age_seconds < ttl_seconds:
        return False
    try:
        os.unlink(claim_path)
        logger.warning(
            "Removed stale idempotency claim sentinel %s after %.1fs "
            "(ttl %.1fs)",
            claim_path, age_seconds, ttl_seconds,
        )
        return True
    except OSError:
        return False


def _request_route_pattern() -> str:
    """Return the Flask route rule pattern for the active request.

    Uses ``request.url_rule.rule`` (the registered route pattern) rather
    than ``request.path`` so the
    fingerprint is stable across deployments that mount the same blueprint
    under a different prefix. Falls back to ``request.path`` when
    ``url_rule`` is unset (defensive -- inside a registered view function
    ``url_rule`` is always populated, but the fallback keeps the helper
    callable in unit tests that construct a bare RequestContext).
    """
    rule = request.url_rule
    if rule is None:
        return request.path
    return rule.rule


def _endpoint_slug(method: str, route: str) -> str:
    """Sanitise ``METHOD:ROUTE`` into a filesystem-safe directory component.

    Replaces every character that is not ``[A-Za-z0-9_-]`` with ``_``. The
    output is used as a single path component beneath ``.idempotency/`` so it
    must not contain ``/``, ``.``, or other separator-sensitive bytes. The
    transform is stable + idempotent (re-running it on its own output is a
    no-op) so cache-file paths for the same endpoint are reproducible across
    processes.
    """
    raw = f"{method}:{route}"
    return "".join(ch if (ch.isalnum() or ch in {"_", "-"}) else "_" for ch in raw)


def _idempotency_fingerprint(body: Any) -> str:
    """Canonical sha256 of ``METHOD:ROUTE_PATTERN:view_args:canonical_body_json``.

    Folding method + route + the resolved path parameters (``view_args``)
    into the hashed input prevents cross-endpoint AND cross-resource
    fingerprint collisions:

      * prepare and start requests with
        the same Idempotency-Key + the same ``{"simulation_id": ...}``
        body produce DIFFERENT digests (method+route discriminator).
      * sensitivity requests for two different run ids with the same key + same
        empty body produce DIFFERENT digests (view_args discriminator).
        Without view_args in the fingerprint the same key/body against
        two different ``sim_id``s would collide and replay sim_A's
        response when the second call hits sim_B.

    The body component subsumes the ``scenario_hash + seed_hash +
    config_hash`` triplet: any byte difference in the canonical JSON form
    yields a different digest. ``ensure_ascii=False`` matches the on-disk
    JSON convention used by ``atomic_write_json``.
    """
    canonical_body = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    # request.view_args is None outside a routed view function; default
    # to an empty dict so the helper is callable from defensive contexts.
    view_args = request.view_args or {}
    canonical_view_args = json.dumps(
        view_args, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
    payload = (
        f"{request.method}:{_request_route_pattern()}:"
        f"{canonical_view_args}:{canonical_body}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _idempotency_cache_path(key: str) -> str:
    """Resolve the per-(endpoint, key) cache file path.

    Layout: ``{OASIS_SIMULATION_DATA_DIR}/.idempotency/{endpoint_slug}/{key}.json``.
    The endpoint slug is derived from the active request's method + route
    pattern via :func:`_endpoint_slug` so that reusing the same client
    Idempotency-Key across two distinct endpoints lands in DIFFERENT files
    (independent replay slots) instead of a spurious mismatch.

    ``safe_join`` already rejects traversal on the base dir; the endpoint
    slug is sanitised in :func:`_endpoint_slug`; the caller has already run
    ``validate_safe_id`` on ``key`` so the filename is provenance-clean.
    """
    slug = _endpoint_slug(request.method, _request_route_pattern())
    base = safe_join(Config.OASIS_SIMULATION_DATA_DIR, ".idempotency")
    endpoint_dir = safe_join(base, slug)
    os.makedirs(endpoint_dir, exist_ok=True)
    return os.path.join(endpoint_dir, f"{key}.json")


# Request-environ attribute name the lookup helper writes when it WINS the
# O_EXCL claim race. Without this marker, an error helper that
# blindly unlinks ``<key>.claim`` would delete the in-flight first
# writer's sentinel from the SECOND (losing) caller's request
# context -- exactly the duplicate-execution failure mode the
# Idempotency-Key contract exists to prevent. The marker lives on the
# WSGI environ rather than Flask ``g`` because ``g`` is app-context
# scoped; a leaked or manually managed app context can outlive a
# request. The environ is specific to the active request. The marker
# stores the absolute claim path the request owns; ``release_claim_if_any``
# only deletes when the current request OWNS that exact path.
_OWNED_CLAIM_ATTR = "_rawfish_idempotency_claim_path"


def _mark_claim_owned(claim_path: str) -> None:
    """Tag the current request as the owner of ``claim_path``.

    Stored on ``request.environ`` so successive helper calls within
    the same request can release; concurrent requests each have
    their own environ and cannot read across.
    """
    try:
        request.environ[_OWNED_CLAIM_ATTR] = claim_path
    except Exception:
        # ``request`` is unavailable in non-Flask test harnesses; the
        # request-scoped ownership concept does not apply, and
        # ``release_claim_if_any`` becomes a no-op below.
        return


def _claim_owned_by_request() -> Optional[str]:
    """Return the claim path the current request owns, or ``None``."""
    try:
        return request.environ.get(_OWNED_CLAIM_ATTR)
    except Exception:
        return None


def release_claim_if_any() -> None:
    """Delete the per-Idempotency-Key claim sentinel **iff this
    request owns it**.

    Ownership is established only when :func:`lookup` successfully
    wins the ``O_EXCL`` create race for the sentinel; loser paths
    (in-flight 409, mismatch 422, replay) MUST NOT delete the
    winner's claim. The error-response factories upstream
    (``_err`` in the policy blueprint, equivalents in any other
    blueprint) call this on every error path; before the ownership
    check existed, a loser whose response funnelled through
    ``_err`` would delete the in-flight first writer's sentinel,
    creating exactly the duplicate-execution race the
    Idempotency-Key contract exists to prevent.

    No-ops when:
      * the header is absent OR the key is malformed,
      * this request never created the sentinel (lookup hit the
        cache OR encountered another caller's in-flight claim),
      * the sentinel was already released by an earlier call
        within this request,
      * Flask request-context is unavailable (test harness
        constructing the helper outside a request scope).
    """
    owned_path = _claim_owned_by_request()
    if not owned_path:
        return
    # Belt-and-braces: re-check the header so a future caller that
    # constructs ownership manually cannot delete an arbitrary file.
    key = request.headers.get(_IDEMPOTENCY_HEADER) if request else None
    if not key:
        return
    try:
        validate_safe_id(key, "idempotency_key")
    except ValueError:
        return
    try:
        cache_path = _idempotency_cache_path(key)
    except Exception:
        return
    expected_claim_path = cache_path + ".claim"
    if owned_path != expected_claim_path:
        # The request claims to own a path that doesn't match its
        # header's derived path -- treat as "no ownership" (defensive
        # against a future bug that lets ownership leak across
        # requests via a shared mutable). Caller's eventual response
        # path is unaffected; only the unlink is suppressed.
        return
    try:
        os.unlink(owned_path)
    except OSError:
        pass
    # Clear the marker so a second call within the same request is
    # a no-op (the release_claim_if_any helper is called from both
    # ``_err`` and ``store`` so a request that completes via store
    # then erroneously calls _err must not race against itself).
    try:
        request.environ.pop(_OWNED_CLAIM_ATTR, None)
    except Exception:
        pass


def lookup(
    body: Any,
    err_factory: Callable[..., Tuple[Any, int]],
) -> Optional[Tuple[Any, int]]:
    """Read ``Idempotency-Key`` header and either replay, mismatch, or claim.

    Args:
        body: The parsed request body whose canonical JSON is folded into
            the per-key fingerprint.
        err_factory: The caller's structured-error factory. Invoked with
            ``(ErrorCode, message, status, details)`` exactly as the
            policy layer's ``_err`` is; the returned ``(Response, int)``
            tuple is propagated verbatim. Injected (rather than imported)
            to keep this module free of any HTTP-envelope coupling and to
            avoid a circular import through the API package.

    Returns:
        ``None`` when no header was sent OR this is the first call with the
            key AND we successfully claimed the per-key cache slot (caller
            should proceed and then call ``store``).
        ``(response, status)`` Flask tuple when:
            * the key already has a cached entry with a matching fingerprint
              (replay path -- the cached body is returned verbatim including
              status code), OR
            * the key already has a cached entry with a DIFFERENT fingerprint
              (mismatch path -- 422 IDEMPOTENCY_KEY_MISMATCH with both
              fingerprints in the error envelope), OR
            * the key contains path-traversal characters (400 INVALID_ID), OR
            * a parallel first-writer is in-flight against the same key
              and its cache commit became visible while we waited
              (replay or mismatch as above; never a duplicate side effect).

    First-writer arbitration: when no cache file exists, this helper
    attempts to create a per-key ``<cache_path>.claim`` sentinel via
    ``os.open(O_CREAT | O_EXCL)``. Exactly one concurrent caller wins
    the create; losers poll briefly for the winner's cache commit and
    then replay it (or surface the mismatch if their fingerprint
    differs). The winner cleans the sentinel in ``store``.
    """
    # Local import keeps this module's import graph free of the api
    # package; ErrorCode is only referenced when we need to construct
    # an error response through ``err_factory``.
    from ..api.error_codes import ErrorCode

    key = request.headers.get(_IDEMPOTENCY_HEADER)
    if not key:
        return None
    try:
        validate_safe_id(key, "idempotency_key")
    except ValueError as ve:
        return err_factory(
            ErrorCode.INVALID_ID, str(ve), 400, {"field": "Idempotency-Key"},
        )

    fingerprint = _idempotency_fingerprint(body)
    cache_path = _idempotency_cache_path(key)
    claim_path = cache_path + ".claim"

    def _read_cache_or_mismatch() -> Optional[Tuple[Any, int]]:
        try:
            with open(cache_path, "r", encoding="utf-8") as fh:
                cached = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Idempotency cache unreadable for key=%s: %s", key, exc,
            )
            return None
        cached_fp = cached.get("fingerprint")
        if cached_fp != fingerprint:
            return err_factory(
                ErrorCode.IDEMPOTENCY_KEY_MISMATCH,
                (
                    f"Idempotency-Key={key!r} was previously used with a "
                    "different request body; replays must send byte-identical "
                    "bodies"
                ),
                422,
                {
                    "idempotency_key": key,
                    "cached_fingerprint": cached_fp,
                    "request_fingerprint": fingerprint,
                },
            )
        return jsonify(cached["body"]), int(cached["status"])

    if os.path.exists(cache_path):
        return _read_cache_or_mismatch()

    # No cache yet. Clear an abandoned sentinel before trying to
    # claim the slot; active same-key callers are still protected by
    # the O_EXCL create below.
    _remove_stale_claim_if_any(claim_path)
    try:
        fd = os.open(
            claim_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(fd, fingerprint.encode("utf-8"))
        finally:
            os.close(fd)
        # We won the claim; the caller proceeds and commits via
        # ``store`` (which deletes the claim sentinel
        # whether the response was cacheable or not).
        # Record ownership on the request scope so an error path
        # that eventually flows through ``release_claim_if_any``
        # deletes the sentinel WE created -- but only ours. A loser
        # path that did NOT win this O_EXCL race never marks
        # ownership and therefore cannot accidentally delete the
        # winner's sentinel.
        _mark_claim_owned(claim_path)
        return None
    except FileExistsError:
        if _remove_stale_claim_if_any(claim_path):
            try:
                fd = os.open(
                    claim_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                try:
                    os.write(fd, fingerprint.encode("utf-8"))
                finally:
                    os.close(fd)
                _mark_claim_owned(claim_path)
                return None
            except FileExistsError:
                pass
        # Another caller is in-flight. Before polling for their cache
        # commit, FIRST compare OUR request fingerprint against the
        # in-flight peer's sentinel fingerprint: if they mismatch, the
        # current request is a different body using the same key and
        # the contract demands an IDEMPOTENCY_KEY_MISMATCH 422 even
        # though no cache file exists yet. This closes the
        # "slow first writer + different body" race where the loser
        # would otherwise wait 5s then double-execute.
        try:
            with open(claim_path, "r", encoding="utf-8") as fh:
                in_flight_fp = fh.read().strip()
        except OSError:
            in_flight_fp = ""
        if in_flight_fp and in_flight_fp != fingerprint:
            return err_factory(
                ErrorCode.IDEMPOTENCY_KEY_MISMATCH,
                (
                    f"Idempotency-Key={key!r} is in-flight on a different "
                    "body fingerprint; the concurrent first writer has not "
                    "yet committed but its body differs. Replays must send "
                    "byte-identical bodies."
                ),
                422,
                {
                    "idempotency_key": key,
                    "in_flight_fingerprint": in_flight_fp,
                    "request_fingerprint": fingerprint,
                },
            )
        # Same fingerprint -> a legitimate concurrent retry. Poll
        # briefly for the in-flight peer's cache commit. The wait
        # window is env-configurable via ``IDEMPOTENCY_WAIT_SECONDS``
        # (default 5) so slow endpoints (notably /report/generate,
        # which can exceed 5s on a real LLM call) can be tuned up at
        # deploy time WITHOUT changing the safety contract below.
        import time as _time
        wait_seconds = _resolve_wait_seconds()
        poll_interval = 0.1
        total_polls = max(1, int(wait_seconds / poll_interval))
        for _ in range(total_polls):
            _time.sleep(poll_interval)
            if os.path.exists(cache_path):
                return _read_cache_or_mismatch()
        # **Safety contract**: when the wait window elapses without
        # the peer committing, we MUST NOT delete the sentinel and
        # proceed as a second writer. A slow same-key request (real
        # LLM call >5s, slow Graphiti ingest, etc.) would otherwise
        # trigger a duplicate side-effect on retry -- exactly the
        # failure mode Idempotency-Key exists to prevent. Instead,
        # surface a structured 409 telling the caller "the original
        # request is still in flight; do not retry yet". The
        # operator-readable reason ``idempotency_in_progress`` is
        # routable in client retry policies without needing to grep
        # error text.
        logger.warning(
            "Idempotency claim sentinel for key=%s did not converge "
            "after %.1fs; refusing to double-execute -- returning "
            "in-progress to caller",
            key, wait_seconds,
        )
        return err_factory(
            ErrorCode.INVALID_REQUEST,
            (
                f"Idempotency-Key={key!r} is still in-flight after "
                f"{wait_seconds:.1f}s; retry after the original "
                "request completes. The framework refuses to "
                "double-execute a non-idempotent handler."
            ),
            409,
            {
                "idempotency_key": key,
                "reason": "idempotency_in_progress",
                "wait_seconds": wait_seconds,
            },
        )


def store(body: Any, response_body: Any, status: int) -> None:
    """Persist (status, response_body) under the request's Idempotency-Key.

    No-op when the header is absent OR the status is not 2xx (we never
    cache error responses -- a transient 500 must not become permanent).
    Writes go through ``atomic_write_json`` so a crash mid-write leaves
    the previous cache (or no cache) -- never a half-written file.

    Always attempts to release the per-key ``.claim`` sentinel that
    ``lookup`` may have written, even when the response is non-cacheable
    (4xx/5xx). Otherwise a key whose first call erred would stay claimed
    and block legitimate retries.
    """
    key = request.headers.get(_IDEMPOTENCY_HEADER)
    if not key:
        return
    try:
        validate_safe_id(key, "idempotency_key")
    except ValueError:
        # Lookup already returned 400 for this case; defensive belt-and-suspenders.
        return
    cache_path = _idempotency_cache_path(key)
    claim_path = cache_path + ".claim"
    # store() is only invoked on the WINNER's path by today's route
    # contract -- losers (cache hits / mismatches / in-flight 409s)
    # never reach this code. But the ownership check below is the
    # belt-and-braces against a future refactor that wires store
    # into a loser path: we only unlink claims THIS request owns.
    owned_path = _claim_owned_by_request()
    we_own_claim = owned_path == claim_path
    if not (200 <= status < 300):
        # Non-cacheable: release the claim so the next caller is not
        # blocked. The response itself is returned to THIS caller in
        # the normal route flow; only the cache write is skipped.
        # Ownership-gated: never delete another request's claim.
        if we_own_claim:
            try:
                os.unlink(claim_path)
            except OSError:
                pass
            try:
                request.environ.pop(_OWNED_CLAIM_ATTR, None)
            except Exception:
                pass
        return
    fingerprint = _idempotency_fingerprint(body)
    atomic_write_json(
        cache_path,
        {
            "fingerprint": fingerprint,
            "status": status,
            "body": response_body,
            # method + route recorded for operator debug visibility -- a stray
            # idempotency file can be cat'd to see which endpoint produced it.
            # The cache PATH already discriminates by endpoint, and the
            # fingerprint already folds method+route into its hashed input,
            # so these two fields are documentation rather than enforcement.
            "method": request.method,
            "route": _request_route_pattern(),
        },
    )
    # Release the per-key first-writer sentinel now that the cache
    # entry has landed. The docstring above promises this, and the
    # earlier implementation only handled the non-cacheable path -- a
    # successful 2xx call would leave the .claim file dangling on disk
    # and a future caller whose cache file vanished (operator delete,
    # filesystem corruption) would block on the stale sentinel for the
    # full wait window. Ownership-gated against the same future-refactor
    # risk as the non-cacheable branch above.
    if we_own_claim:
        try:
            os.unlink(claim_path)
        except OSError:
            pass
        try:
            request.environ.pop(_OWNED_CLAIM_ATTR, None)
        except Exception:
            pass


__all__ = [
    "lookup",
    "store",
    "release_claim_if_any",
]
