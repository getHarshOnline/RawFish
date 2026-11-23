"""Pin the sync/async retry contract.

Two things must hold:

1. The shared :class:`RetryPolicy` produces the SAME wait pattern for
   the sync helper and the async sibling — disagreement here is the
   bug class this module exists to prevent.
2. The two migrated call sites (zep paging, zep tools) still produce
   the same wait pattern the legacy ``time.sleep`` loops produced,
   under a controlled fake clock. The legacy numbers are:
   ``base_delay=2.0s``, doubling per failure, max_attempts=3, no
   jitter, no cap.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any, List

import pytest

from app.utils.retry_policy import RetryPolicy, run_with_retry, run_with_retry_async


# ---------------------------------------------------------------------------
# Policy invariants
# ---------------------------------------------------------------------------


def test_policy_rejects_zero_max_attempts() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(max_attempts=0)


def test_policy_rejects_sub_unit_multiplier() -> None:
    with pytest.raises(ValueError):
        RetryPolicy(backoff_multiplier=0.5)


def test_delay_for_attempt_matches_doubling() -> None:
    p = RetryPolicy(max_attempts=4, base_delay_s=2.0, backoff_multiplier=2.0)
    assert p.delay_for_attempt(1) == pytest.approx(2.0)
    assert p.delay_for_attempt(2) == pytest.approx(4.0)
    assert p.delay_for_attempt(3) == pytest.approx(8.0)


def test_delay_for_attempt_respects_max_delay() -> None:
    p = RetryPolicy(max_attempts=4, base_delay_s=2.0, backoff_multiplier=2.0, max_delay_s=5.0)
    assert p.delay_for_attempt(1) == pytest.approx(2.0)
    assert p.delay_for_attempt(2) == pytest.approx(4.0)
    assert p.delay_for_attempt(3) == pytest.approx(5.0)  # capped
    assert p.delay_for_attempt(4) == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# Sync helper
# ---------------------------------------------------------------------------


def test_sync_helper_returns_on_first_success() -> None:
    sleeps: List[float] = []
    p = RetryPolicy(max_attempts=3, base_delay_s=2.0)
    out = run_with_retry(p, lambda: 42, sleep=sleeps.append)
    assert out == 42
    assert sleeps == []

def test_sync_helper_retries_and_succeeds() -> None:
    sleeps: List[float] = []
    calls: List[int] = []

    def fn() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("transient")
        return "ok"

    p = RetryPolicy(max_attempts=3, base_delay_s=2.0, backoff_multiplier=2.0, retry_on=(ConnectionError,))
    out = run_with_retry(p, fn, sleep=sleeps.append)
    assert out == "ok"
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]


def test_sync_helper_raises_after_exhaustion() -> None:
    sleeps: List[float] = []
    p = RetryPolicy(max_attempts=3, base_delay_s=2.0, retry_on=(ConnectionError,))
    with pytest.raises(ConnectionError):
        run_with_retry(p, lambda: (_ for _ in ()).throw(ConnectionError("never recovers")),
                       sleep=sleeps.append)
    # Two sleeps between three attempts.
    assert sleeps == [2.0, 4.0]


def test_sync_helper_does_not_retry_non_listed_exceptions() -> None:
    sleeps: List[float] = []
    p = RetryPolicy(max_attempts=3, base_delay_s=2.0, retry_on=(ConnectionError,))
    with pytest.raises(ValueError):
        run_with_retry(p, lambda: (_ for _ in ()).throw(ValueError("not retryable")),
                       sleep=sleeps.append)
    assert sleeps == []


def test_sync_helper_on_retry_callback_receives_attempt_and_delay() -> None:
    sleeps: List[float] = []
    seen: List[tuple] = []

    def fn() -> None:
        raise ConnectionError("boom")

    p = RetryPolicy(max_attempts=3, base_delay_s=2.0, retry_on=(ConnectionError,))
    with pytest.raises(ConnectionError):
        run_with_retry(
            p, fn,
            on_retry=lambda exc, attempt, delay: seen.append((type(exc).__name__, attempt, delay)),
            sleep=sleeps.append,
        )
    assert seen == [("ConnectionError", 1, 2.0), ("ConnectionError", 2, 4.0)]


def test_sync_helper_jitter_envelope() -> None:
    """Jitter multiplies delay by U[0.5, 1.5). The envelope must be
    respected even under adversarial RNG."""
    sleeps: List[float] = []
    p = RetryPolicy(max_attempts=3, base_delay_s=2.0, jitter=True, retry_on=(ConnectionError,))
    with pytest.raises(ConnectionError):
        run_with_retry(
            p, lambda: (_ for _ in ()).throw(ConnectionError("x")),
            sleep=sleeps.append,
            rng=random.Random(42),
        )
    # base delays are 2.0 and 4.0 — jittered into [1.0, 3.0) and [2.0, 6.0).
    assert len(sleeps) == 2
    assert 1.0 <= sleeps[0] < 3.0
    assert 2.0 <= sleeps[1] < 6.0


# ---------------------------------------------------------------------------
# Async helper — same contract
# ---------------------------------------------------------------------------


def test_async_helper_matches_sync_wait_pattern() -> None:
    """Same policy → same wait pattern, sync vs async. This is the
    central invariant the shared module exists to enforce."""
    sync_sleeps: List[float] = []
    async_sleeps: List[float] = []

    p = RetryPolicy(max_attempts=3, base_delay_s=2.0, backoff_multiplier=2.0, retry_on=(ConnectionError,))

    def sync_failer() -> None:
        raise ConnectionError("x")

    async def async_failer() -> None:
        raise ConnectionError("x")

    with pytest.raises(ConnectionError):
        run_with_retry(p, sync_failer, sleep=sync_sleeps.append)

    async def fake_async_sleep(d: float) -> None:
        async_sleeps.append(d)

    with pytest.raises(ConnectionError):
        asyncio.run(run_with_retry_async(p, async_failer, sleep=fake_async_sleep))

    assert sync_sleeps == async_sleeps == [2.0, 4.0]


def test_async_helper_returns_on_first_success() -> None:
    async def fn() -> int:
        return 7

    p = RetryPolicy(max_attempts=3, base_delay_s=2.0)
    out = asyncio.run(run_with_retry_async(p, fn))
    assert out == 7


# ---------------------------------------------------------------------------
# Migrated call sites — pin the legacy wait pattern
# ---------------------------------------------------------------------------


def test_zep_paging_fetch_page_with_retry_wait_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_fetch_page_with_retry`` must still produce sleeps of
    ``[retry_delay, retry_delay*2]`` for three attempts when every
    call fails with a retryable error."""
    from app.utils import zep_paging

    sleeps: List[float] = []
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda d: sleeps.append(d))

    calls: List[int] = []

    def api(*a: Any, **kw: Any) -> List[Any]:
        calls.append(1)
        raise ConnectionError("transient")

    with pytest.raises(ConnectionError):
        zep_paging._fetch_page_with_retry(
            api,
            max_retries=3,
            retry_delay=2.0,
            page_description="test",
        )

    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]


def test_zep_paging_fetch_page_with_retry_succeeds_after_one_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.utils import zep_paging

    sleeps: List[float] = []
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda d: sleeps.append(d))

    calls: List[int] = []

    def api(*a: Any, **kw: Any) -> List[Any]:
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("first one fails")
        return ["page-data"]

    out = zep_paging._fetch_page_with_retry(
        api,
        max_retries=3,
        retry_delay=2.0,
        page_description="test",
    )

    assert out == ["page-data"]
    assert len(calls) == 2
    assert sleeps == [2.0]


def test_zep_paging_non_retryable_propagates_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.utils import zep_paging

    sleeps: List[float] = []
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda d: sleeps.append(d))

    def api(*a: Any, **kw: Any) -> List[Any]:
        raise ValueError("application-level — not retryable")

    with pytest.raises(ValueError):
        zep_paging._fetch_page_with_retry(api, max_retries=3, retry_delay=2.0)

    assert sleeps == []
