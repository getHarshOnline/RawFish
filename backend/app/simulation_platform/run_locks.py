"""Per-simulation exclusive locks for start orchestration.

Start handlers perform a read-check-write on ``state.json`` /
``run_state.json`` before invoking the runner factory. Two concurrent
start requests for the same ``simulation_id`` can otherwise both
pass the phase check, both flip the state, and both invoke the runner
factory against the same ``run_dir``. The runner's own internal checks
are also read-check-write operations, so the API boundary owns the
external mutex.

This module provides a filesystem-backed exclusive lock keyed on
``simulation_id``. The lock file lives in ``run_dir`` (``.start.lock``)
so the lock is naturally co-located with the artifacts it serialises
and is wiped together with the run directory on cleanup.

Two acquisition strategies, in order of preference:

1. POSIX ``fcntl.LOCK_EX | LOCK_NB`` -- works on the same physical
   inode across processes and threads, returns ``BlockingIOError`` /
   ``OSError`` when contended. This is the production path on Linux
   and macOS where Flask is hosted.
2. ``O_EXCL`` claim-file fallback -- atomic create of the lock file
   itself. Used when ``fcntl`` is unavailable (Windows; future Nix
   sandboxes that strip it). The claim file is removed on release.

The public surface is a single context manager,
:func:`start_lock`, that yields if the lock was acquired and raises
:class:`StartLockBusy` if another start is in flight. Callers are
expected to translate that exception into the lane's error envelope
(409 + ``details.reason="start_in_progress"`` is the documented shape).
"""
from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from typing import Iterator, Optional

try:
    import fcntl  # type: ignore[import]
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover - exercised on Windows / sandboxed envs
    fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False


__all__ = ["StartLockBusy", "start_lock", "LOCK_FILENAME"]


# Lock filename, intentionally dotfile so the wiped-cleanly directory
# scans in `state_machine.py` and the run-dir listing helpers do not
# trip over it as if it were an artifact. The leading dot also matches
# the existing ``.idempotency`` convention for platform-owned files.
LOCK_FILENAME = ".start.lock"


class StartLockBusy(RuntimeError):
    """Raised when another /start is mid-flight for the same simulation_id.

    The lane handler catches this and returns 409 INVALID_REQUEST with
    ``details.reason="start_in_progress"`` (and ``details.simulation_id``)
    so the operator gets a meaningful retry signal instead of a generic
    runner / state-machine error from the duplicated request.
    """

    def __init__(self, simulation_id: str, lock_path: str) -> None:
        super().__init__(
            f"start already in progress for simulation {simulation_id!r}",
        )
        self.simulation_id = simulation_id
        self.lock_path = lock_path


@contextmanager
def start_lock(run_dir: str, simulation_id: str) -> Iterator[None]:
    """Acquire an exclusive per-simulation lock for start.

    ``run_dir`` must already exist (it is created by the caller's prior
    prepare step). The lock file is created lazily on
    first acquisition; concurrent callers that find the lock held raise
    :class:`StartLockBusy` immediately (non-blocking).

    Usage::

        try:
            with start_lock(run_dir, simulation_id):
                # read state, transition to 'starting', invoke runner, write state
                ...
        except StartLockBusy:
            return _err(..., details={"reason": "start_in_progress", ...})
    """
    # ``run_dir`` is the caller's canonicalised path. ``simulation_id`` has
    # already been validated by the caller (the API layer's
    # ``validate_safe_id`` / ``validate_id``) so we do not re-validate
    # here -- the lock file ends up inside the already-safe ``run_dir``.
    os.makedirs(run_dir, exist_ok=True)
    lock_path = os.path.join(run_dir, LOCK_FILENAME)

    if _HAS_FCNTL:
        yield from _fcntl_acquire(lock_path, simulation_id)
    else:
        yield from _o_excl_acquire(lock_path, simulation_id)


def _fcntl_acquire(lock_path: str, simulation_id: str) -> Iterator[None]:
    """fcntl-based acquisition.

    Opens the lock file (O_CREAT so first caller creates it) and takes
    an exclusive non-blocking lock. The file descriptor is kept alive
    for the duration of the context so the kernel-side lock survives
    until ``release``.
    """
    # ``os.open`` with O_CREAT is idempotent -- the lock file persists
    # across runs intentionally; what serialises callers is the kernel
    # advisory lock, not the inode's existence. We do NOT unlink the
    # lock file on release (that would race against another caller's
    # open()).
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            assert fcntl is not None  # _HAS_FCNTL guarded the entry
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            # EWOULDBLOCK / EAGAIN -> another caller holds the lock.
            # We do NOT close-then-raise inside the try/finally to keep
            # the failure path linear and avoid hiding the original
            # exception behind a fcntl unlock error.
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                os.close(fd)
                raise StartLockBusy(simulation_id, lock_path) from None
            os.close(fd)
            raise
        try:
            yield
        finally:
            # Release the advisory lock before closing -- closing alone
            # is enough on Linux/macOS but explicit release matches the
            # acquire and survives any future code that holds the fd
            # open past the context's exit.
            try:
                assert fcntl is not None
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _o_excl_acquire(lock_path: str, simulation_id: str) -> Iterator[None]:
    """O_EXCL claim-file fallback.

    Used when ``fcntl`` is unavailable (Windows; sandboxed envs). The
    create-with-O_EXCL is atomic across processes on POSIX-compatible
    filesystems; on Windows ``os.open`` with ``O_CREAT | O_EXCL`` is
    also atomic at the NTFS layer.

    Unlike the fcntl path, the claim file IS unlinked on release so a
    subsequent caller can recreate it.
    """
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise StartLockBusy(simulation_id, lock_path) from None
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise StartLockBusy(simulation_id, lock_path) from None
        raise
    try:
        try:
            yield
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
    finally:
        try:
            os.unlink(lock_path)
        except OSError:
            pass


def is_locked(run_dir: str) -> bool:
    """Best-effort probe of whether ``run_dir`` currently holds a start lock.

    Intended for tests and operator introspection only -- the result is
    NOT authoritative for control flow because the lock state can flip
    between this call and any subsequent ``start_lock`` attempt. Use
    :func:`start_lock` itself for authoritative acquisition.
    """
    lock_path = os.path.join(run_dir, LOCK_FILENAME)
    if not _HAS_FCNTL:
        return os.path.exists(lock_path)
    # On POSIX the lock file may exist (we never unlink it) even when
    # no caller holds the advisory lock. The only true probe is a
    # non-blocking acquire-then-release.
    if not os.path.exists(lock_path):
        return False
    try:
        fd = os.open(lock_path, os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            assert fcntl is not None
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            return True
        try:
            assert fcntl is not None
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


# Module-level sentinel kept for symmetry with other simulation_platform
# helpers that expose an "unavailable" probe; consumers can branch on
# this to log a one-time warning when the fcntl fast path is missing.
HAS_FCNTL: Optional[bool] = _HAS_FCNTL
