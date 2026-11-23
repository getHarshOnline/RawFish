/**
 * usePoller composable contract.
 *
 * Pins the four-piece behavior:
 *
 *   1. ``start()`` runs ``fn`` immediately, then schedules each next
 *      tick AFTER ``fn`` resolves (no setInterval overlap).
 *   2. ``stop()`` aborts the in-flight tick's AbortSignal AND bumps
 *      the stale token so an in-flight ``fn`` can short-circuit its
 *      result before mutating shared state.
 *   3. Consecutive errors past ``maxConsecutiveErrors`` halt the
 *      poller; ``onError`` is invoked for each one.
 *   4. The composable carries no Vue imports so the test runs
 *      without mounting a component tree.
 */
import { describe, it, expect, vi } from 'vitest'
import { createPoller } from '../usePoller.js'

function _flushTimers (ms) {
  // Helper: advance the fake clock then yield to microtasks so
  // ``await``ed work inside the tick resolves.
  vi.advanceTimersByTime(ms)
  return Promise.resolve()
}

describe('usePoller', () => {
  it('runs the fn immediately on start, then on each interval', async () => {
    vi.useFakeTimers()
    const calls = []
    const poller = createPoller({
      fn: async () => { calls.push(Date.now()) },
      intervalMs: 100,
    })
    poller.start()
    await _flushTimers(0)
    await _flushTimers(100)
    await _flushTimers(100)
    poller.stop()
    vi.useRealTimers()
    expect(calls.length).toBe(3)
  })

  it('schedules the next tick AFTER fn resolves (no overlap)', async () => {
    vi.useFakeTimers()
    let inFlight = 0
    let maxOverlap = 0
    const poller = createPoller({
      fn: async () => {
        inFlight += 1
        maxOverlap = Math.max(maxOverlap, inFlight)
        // Simulate a slow call that takes longer than the interval.
        await new Promise((resolve) => setTimeout(resolve, 500))
        inFlight -= 1
      },
      intervalMs: 100,
    })
    poller.start()
    await _flushTimers(0)      // schedule first tick
    await _flushTimers(500)    // first tick body
    await _flushTimers(100)    // next tick scheduled
    await _flushTimers(500)    // second tick body
    poller.stop()
    vi.useRealTimers()
    expect(maxOverlap).toBe(1)
  })

  it('stop() bumps the stale token so an in-flight tick can short-circuit', async () => {
    vi.useFakeTimers()
    let observedToken = null
    let externalToken = null
    const poller = createPoller({
      fn: async ({ token }) => {
        externalToken = token
        // Simulate a long-running fetch; capture the token-at-start.
        await new Promise((resolve) => setTimeout(resolve, 200))
        observedToken = token
      },
      intervalMs: 100,
    })
    poller.start()
    await _flushTimers(0)
    // Stop mid-tick. The current tick's token should be staler than
    // the post-stop currentToken().
    poller.stop()
    await _flushTimers(200)
    vi.useRealTimers()
    expect(externalToken).not.toBeNull()
    expect(observedToken).toBe(externalToken)
    expect(poller.currentToken()).toBeGreaterThan(observedToken)
  })

  it('passes an AbortSignal that flips on stop()', async () => {
    vi.useFakeTimers()
    let capturedSignal = null
    const poller = createPoller({
      fn: async ({ signal }) => {
        capturedSignal = signal
        await new Promise((resolve) => setTimeout(resolve, 500))
      },
      intervalMs: 100,
    })
    poller.start()
    await _flushTimers(0)
    expect(capturedSignal).not.toBeNull()
    expect(capturedSignal.aborted).toBe(false)
    poller.stop()
    expect(capturedSignal.aborted).toBe(true)
    vi.useRealTimers()
  })

  it('halts after maxConsecutiveErrors and surfaces each via onError', async () => {
    vi.useFakeTimers()
    const onError = vi.fn()
    const poller = createPoller({
      fn: async () => { throw new Error('boom') },
      intervalMs: 100,
      maxConsecutiveErrors: 3,
      onError,
    })
    poller.start()
    await _flushTimers(0)
    await _flushTimers(100)
    await _flushTimers(100)
    await _flushTimers(100)
    vi.useRealTimers()
    expect(onError).toHaveBeenCalledTimes(3)
    expect(poller.isRunning()).toBe(false)
  })

  it('resets the consecutive-error counter after a successful tick', async () => {
    vi.useFakeTimers()
    const onError = vi.fn()
    let calls = 0
    const poller = createPoller({
      fn: async () => {
        calls += 1
        // Throws every OTHER tick; never two consecutive errors.
        if (calls % 2 === 1 && calls > 1) throw new Error('boom')
      },
      intervalMs: 100,
      maxConsecutiveErrors: 2,
      onError,
    })
    poller.start()
    for (let i = 0; i < 8; i++) await _flushTimers(100)
    poller.stop()
    vi.useRealTimers()
    expect(poller.isRunning()).toBe(false)
    // We stopped manually so the budget never tripped.
    expect(onError.mock.calls.length).toBeLessThan(4)
  })

  it('start() is idempotent (no double-scheduling)', async () => {
    vi.useFakeTimers()
    const calls = []
    const poller = createPoller({
      fn: async () => { calls.push(1) },
      intervalMs: 100,
    })
    poller.start()
    poller.start()
    poller.start()
    await _flushTimers(0)
    await _flushTimers(100)
    poller.stop()
    vi.useRealTimers()
    expect(calls.length).toBe(2)
  })

  it('rejects invalid options at construction', () => {
    expect(() => createPoller({})).toThrow(TypeError)
    expect(() => createPoller({ fn: () => {} })).toThrow(RangeError)
    expect(() => createPoller({ fn: () => {}, intervalMs: -1 })).toThrow(
      RangeError,
    )
  })
})
