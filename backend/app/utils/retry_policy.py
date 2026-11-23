"""Shared retry policy + sync/async runners.

This module owns the single retry-policy contract used by production
clients that need bounded, exponentially-backed-off retries. Two
callable surfaces are exposed:

* :func:`run_with_retry` — synchronous; calls ``time.sleep`` between
  attempts. Suitable inside request handlers that are themselves sync
  (Flask views, Zep tool helpers, paginated readers).
* :func:`run_with_retry_async` — asynchronous; calls
  ``asyncio.sleep``. Suitable inside ``async def`` code paths.

Both helpers share :class:`RetryPolicy` byte-for-byte. The intent is
that a sync site and an async site that disagree about retry count
or backoff envelope can be reconciled by inspecting the policy
object alone, not by reading the loop body.

The helpers do NOT log on their own — callers pass an ``on_retry``
callback that decides how to surface the warning. This keeps
i18n / structured-logging concerns at the call site where the
operation name and locale binding live, instead of baking English
log strings into a shared utility.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Tuple, Type, TypeVar

T = TypeVar("T")

RetryCallback = Callable[[BaseException, int, float], None]
"""Signature: ``(exception, attempt_number_starting_at_1, delay_seconds)``."""


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded exponential-backoff retry envelope.

    Attributes:
        max_attempts: Total number of attempts including the first
            call. ``max_attempts=3`` means: try once, then retry up to
            two more times. Must be ``>= 1``.
        base_delay_s: Delay before the first retry (after attempt 1
            fails). The delay doubles by ``backoff_multiplier`` after
            each subsequent failure, capped at ``max_delay_s``.
        backoff_multiplier: Multiplier applied to the delay after
            every failed attempt. ``2.0`` doubles the wait each round.
        max_delay_s: Upper bound on the per-retry delay. ``None`` means
            no cap (the legacy zep paging / zep tools loops were
            uncapped; preserving that behaviour requires ``None``).
        jitter: When ``True``, the actual sleep is ``delay * U[0.5, 1.5)``
            so concurrent callers don't sync up on backoff windows.
        retry_on: Exception types that trigger a retry. Anything not
            in this tuple propagates immediately.
    """

    max_attempts: int = 3
    base_delay_s: float = 2.0
    backoff_multiplier: float = 2.0
    max_delay_s: Optional[float] = None
    jitter: bool = False
    retry_on: Tuple[Type[BaseException], ...] = field(default_factory=lambda: (Exception,))

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("RetryPolicy.max_attempts must be >= 1")
        if self.base_delay_s < 0:
            raise ValueError("RetryPolicy.base_delay_s must be >= 0")
        if self.backoff_multiplier < 1.0:
            raise ValueError("RetryPolicy.backoff_multiplier must be >= 1.0")
        if self.max_delay_s is not None and self.max_delay_s < 0:
            raise ValueError("RetryPolicy.max_delay_s must be >= 0 when set")

    def delay_for_attempt(self, failed_attempt: int) -> float:
        """Return the un-jittered delay to sleep AFTER ``failed_attempt`` failed.

        ``failed_attempt`` is 1-indexed (1 = first call failed, sleep
        before attempt 2). The result respects ``max_delay_s`` but
        does NOT apply jitter — callers handle jitter so a test can
        seed RNG once.
        """
        if failed_attempt < 1:
            raise ValueError("failed_attempt must be >= 1")
        delay = self.base_delay_s * (self.backoff_multiplier ** (failed_attempt - 1))
        if self.max_delay_s is not None:
            delay = min(delay, self.max_delay_s)
        return delay


def _apply_jitter(delay: float, *, rng: Optional[random.Random] = None) -> float:
    """Multiplicative jitter in ``[0.5, 1.5)`` — the envelope
    ``current_delay * (0.5 + random())``.
    """
    r = rng.random() if rng is not None else random.random()
    return delay * (0.5 + r)


def run_with_retry(
    policy: RetryPolicy,
    func: Callable[[], T],
    *,
    on_retry: Optional[RetryCallback] = None,
    sleep: Optional[Callable[[float], None]] = None,
    rng: Optional[random.Random] = None,
) -> T:
    """Run ``func`` under ``policy``, sleeping synchronously between retries.

    Args:
        policy: The retry envelope.
        func: Zero-arg callable; bind arguments with ``functools.partial``
            or a ``lambda`` at the call site.
        on_retry: Optional callback invoked AFTER a retryable failure
            but BEFORE the sleep. Signature
            ``(exc, attempt_number, delay_s)``. Failures in the
            callback propagate (they are not retryable).
        sleep: Injected sleep function (default :func:`time.sleep`).
            Tests pass a recording mock to assert wait patterns.
        rng: Injected RNG for jitter; tests pass a seeded
            :class:`random.Random` for determinism.

    Returns:
        Whatever ``func`` returns on the first successful attempt.

    Raises:
        The final exception of the last failed attempt, if every
        attempt failed with a type in ``policy.retry_on``. Exceptions
        outside ``policy.retry_on`` propagate immediately.
    """
    # Resolve sleep at call time so tests that monkeypatch
    # ``time.sleep`` see the patched function. Capturing it as a
    # default argument would freeze the reference at import time.
    sleep_fn = sleep if sleep is not None else time.sleep
    last_exception: Optional[BaseException] = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return func()
        except policy.retry_on as exc:  # type: ignore[misc]
            last_exception = exc
            if attempt >= policy.max_attempts:
                break
            delay = policy.delay_for_attempt(attempt)
            if policy.jitter:
                delay = _apply_jitter(delay, rng=rng)
            if on_retry is not None:
                on_retry(exc, attempt, delay)
            sleep_fn(delay)
    assert last_exception is not None, (
        "run_with_retry: loop exhausted without capturing an exception — "
        "reachable only when max_attempts < 1, which __post_init__ rejects."
    )
    raise last_exception


async def run_with_retry_async(
    policy: RetryPolicy,
    func: Callable[[], Awaitable[T]],
    *,
    on_retry: Optional[RetryCallback] = None,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    rng: Optional[random.Random] = None,
) -> T:
    """Async sibling of :func:`run_with_retry` — same policy contract.

    ``func`` is an async zero-arg callable (typically a lambda that
    awaits a coroutine). ``sleep`` defaults to :func:`asyncio.sleep`.
    The retry budget and backoff envelope match the sync helper
    byte-for-byte; the only difference is the await on each attempt.
    """
    if sleep is None:
        sleep = asyncio.sleep
    last_exception: Optional[BaseException] = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await func()
        except policy.retry_on as exc:  # type: ignore[misc]
            last_exception = exc
            if attempt >= policy.max_attempts:
                break
            delay = policy.delay_for_attempt(attempt)
            if policy.jitter:
                delay = _apply_jitter(delay, rng=rng)
            if on_retry is not None:
                on_retry(exc, attempt, delay)
            await sleep(delay)
    assert last_exception is not None, (
        "run_with_retry_async: loop exhausted without capturing an exception — "
        "reachable only when max_attempts < 1, which __post_init__ rejects."
    )
    raise last_exception
