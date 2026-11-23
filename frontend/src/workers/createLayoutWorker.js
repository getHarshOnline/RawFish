/**
 * Factory for the graph-layout Web Worker.
 *
 * Wrapping ``new Worker(...)`` in a tiny factory has two payoffs:
 *
 *   1. Tests can stub the factory to return a fake worker
 *      ({postMessage, onmessage, terminate}) without touching
 *      ``import.meta.url`` or the bundler's Worker resolution.
 *   2. The call-site in ``GraphPanel.vue`` does not have to repeat
 *      the ``new URL(..., import.meta.url)`` + ``{type: 'module'}``
 *      incantation that Vite requires for ESM workers.
 *
 * The factory returns ``null`` in environments where ``Worker`` is
 * unavailable (server-side rendering, older test runners). The
 * caller MUST check for null and fall back to inline computation.
 *
 * @returns {Worker|null}
 */
export function createLayoutWorker () {
  if (typeof Worker === 'undefined') {
    return null
  }
  return new Worker(
    new URL('./graphLayout.worker.js', import.meta.url),
    { type: 'module' },
  )
}

// Threshold above which GraphPanel.vue offloads layout to the
// worker. Below this, the worker's spin-up + message round-trip
// overhead would exceed the inline simulation cost, so we keep the
// existing animated path. The number is empirical (force-layout
// becomes visibly janky around 200 nodes in the main graph view)
// and can be raised if the worker hand-off itself shows up in a
// profiler.
export const LAYOUT_WORKER_THRESHOLD = 200
