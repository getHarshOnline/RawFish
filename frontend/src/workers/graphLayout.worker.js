/**
 * Web Worker entry point for the force-directed graph layout.
 *
 * The main thread posts a single message of the form::
 *
 *   { type: 'layout',
 *     payload: { nodes, edges, options: { width, height, iterations? } } }
 *
 * The worker runs the shared :func:`computeForceLayout` from
 * ``./graphLayout.js`` and posts back::
 *
 *   { type: 'result',
 *     payload: { nodes_with_positions, edges } }
 *
 * On failure (e.g. malformed payload, missing d3-force) the worker
 * posts back::
 *
 *   { type: 'error', payload: { message } }
 *
 * The compute function lives in a separate module so unit tests can
 * import it directly (a Worker context is hard to construct in
 * happy-dom). The worker entry stays a thin shim: parse the
 * message, delegate, post the result.
 */
import { computeForceLayout } from './graphLayout.js'

self.onmessage = async (event) => {
  const data = event && event.data
  if (!data || data.type !== 'layout' || !data.payload) {
    self.postMessage({
      type: 'error',
      payload: { message: 'graphLayout.worker: expected {type:"layout", payload}' },
    })
    return
  }

  const { nodes, edges, options } = data.payload
  const { width, height, iterations } = options || {}

  try {
    const result = await computeForceLayout({
      nodes,
      edges,
      width,
      height,
      iterations,
    })
    self.postMessage({
      type: 'result',
      payload: {
        nodes_with_positions: result.nodes,
        edges: result.edges,
      },
    })
  } catch (err) {
    self.postMessage({
      type: 'error',
      payload: { message: err && err.message ? err.message : String(err) },
    })
  }
}
