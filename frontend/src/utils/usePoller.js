/**
 * Shared recursive-timeout poller composable.
 *
 * Use this instead of ``setInterval(fn, ms)`` for any cockpit or panel
 * that polls backend state. Differences vs the raw setInterval pattern:
 *
 *   * **Recursive setTimeout**: each next tick is scheduled AFTER the
 *     current ``fn`` resolves. With ``setInterval`` a slow ``fn`` (e.g.
 *     a 5s network round-trip on a 2s interval) creates overlapping
 *     in-flight requests that mutate the same component state in
 *     undefined order.
 *   * **AbortController per tick**: ``fn`` receives an
 *     :class:`AbortSignal` so a stop() mid-flight aborts the in-flight
 *     fetch instead of letting it land into the component AFTER
 *     teardown.
 *   * **Stale-token guard**: every tick mints a monotonically
 *     increasing token. ``fn`` can check ``token === currentToken()``
 *     before committing a mutation; a stop() / restart() bumps the
 *     token so the in-flight result becomes a no-op.
 *   * **Auto-stop on error budget**: consecutive errors beyond
 *     ``maxConsecutiveErrors`` halt the poller and surface the last
 *     error to the caller via ``onError``.
 *
 * The composable is framework-agnostic (no Vue imports) so unit tests
 * can drive it with a fake clock without spinning up a component
 * tree.
 *
 * @param {object} opts
 * @param {(ctx: {signal: AbortSignal, token: number}) => Promise<void>} opts.fn
 *   The work to perform on every tick. The ctx carries an AbortSignal
 *   (linked to the per-tick AbortController) + the tick's stale token.
 * @param {number} opts.intervalMs - Delay between ``fn`` resolutions.
 * @param {number} [opts.maxConsecutiveErrors=5] - Stop after this many
 *   consecutive throws.
 * @param {(err: Error) => void} [opts.onError] - Optional sink for
 *   per-tick errors (and the final error when the budget runs out).
 * @returns {{start: () => void, stop: () => void, isRunning: () => boolean, currentToken: () => number}}
 */
export function createPoller (opts) {
  const {
    fn,
    intervalMs,
    maxConsecutiveErrors = 5,
    onError = null,
  } = opts || {}
  if (typeof fn !== 'function') {
    throw new TypeError('createPoller: opts.fn must be a function')
  }
  if (typeof intervalMs !== 'number' || intervalMs < 0) {
    throw new RangeError(
      'createPoller: opts.intervalMs must be a non-negative number',
    )
  }

  let timerId = null
  let abort = null
  let running = false
  let token = 0
  let consecutiveErrors = 0

  async function _tick () {
    if (!running) return
    const myToken = ++token
    abort = new AbortController()
    try {
      await fn({ signal: abort.signal, token: myToken })
      consecutiveErrors = 0
    } catch (err) {
      consecutiveErrors += 1
      if (onError) {
        try {
          onError(err)
        } catch {
          // Swallow onError callback failures: the poller MUST stay
          // alive on its own loop even if the consumer's handler
          // raises (e.g. a stale Vue ref no longer mounted).
        }
      }
      if (consecutiveErrors >= maxConsecutiveErrors) {
        running = false
        return
      }
    }
    if (!running) return
    timerId = setTimeout(_tick, intervalMs)
  }

  function start () {
    if (running) return
    running = true
    consecutiveErrors = 0
    timerId = setTimeout(_tick, 0)
  }

  function stop () {
    running = false
    if (timerId !== null) {
      clearTimeout(timerId)
      timerId = null
    }
    if (abort !== null) {
      try {
        abort.abort()
      } catch {
        // Abort can throw on unfinished controllers in some
        // engines; swallow so stop() is unconditionally safe to
        // call from a teardown hook.
      }
      abort = null
    }
    token += 1
  }

  return {
    start,
    stop,
    isRunning: () => running,
    currentToken: () => token,
  }
}
