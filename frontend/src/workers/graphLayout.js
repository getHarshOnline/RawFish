/**
 * Headless force-directed layout for the entity graph.
 *
 * This module owns the layout algorithm shared between the main
 * thread (small graphs) and the dedicated Web Worker (large graphs).
 * The function is intentionally framework-agnostic: it does not
 * import Vue or touch the DOM, so the same call can run inside a
 * Worker context where ``document`` and ``window`` do not exist.
 *
 * The algorithm mirrors the inline d3-force pipeline used by
 * ``GraphPanel.renderGraph``:
 *
 *   * ``forceLink`` with a per-edge distance: base 150, +50 per
 *     additional edge between the same node pair.
 *   * ``forceManyBody`` strength -400 (node repulsion).
 *   * ``forceCenter`` pulling the cluster toward (width/2, height/2).
 *   * ``forceCollide`` radius 50 to keep node icons from overlapping.
 *   * Soft ``forceX`` / ``forceY`` (strength 0.04) re-centering pull
 *     so isolated clusters do not drift off-canvas.
 *
 * The caller passes plain edge objects ({source, target, pairTotal}).
 * d3-force mutates the input ``nodes`` array in place to attach ``x``
 * / ``y`` coordinates -- so the worker can post the array straight
 * back over ``postMessage`` and the main thread re-uses the same
 * shape that the inline path produces.
 *
 * @param {object} args
 * @param {Array<{id: string}>} args.nodes - Mutated in place: each
 *   node gains ``x``, ``y``, ``vx``, ``vy`` after the simulation
 *   stops.
 * @param {Array<{source: string, target: string, pairTotal?: number}>} args.edges
 *   Edge list; d3 rewrites ``source`` / ``target`` to node refs
 *   during the run.
 * @param {number} args.width - Canvas width in CSS pixels.
 * @param {number} args.height - Canvas height in CSS pixels.
 * @param {number} [args.iterations=300] - Number of synchronous
 *   ticks to run before stopping the simulation.
 * @returns {{nodes: Array, edges: Array}} - The same arrays, with
 *   ``nodes`` carrying final positions.
 */
export async function computeForceLayout (args) {
  const {
    nodes,
    edges,
    width,
    height,
    iterations = 300,
  } = args || {}

  if (!Array.isArray(nodes) || !Array.isArray(edges)) {
    throw new TypeError(
      'computeForceLayout: nodes and edges must both be arrays',
    )
  }
  if (typeof width !== 'number' || typeof height !== 'number') {
    throw new TypeError(
      'computeForceLayout: width and height must both be numbers',
    )
  }

  // d3-force is ESM-only and pulls in d3-dispatch / d3-timer; we
  // load it dynamically so the worker entry stays small and Node
  // test environments can still import this module synchronously
  // (the dynamic import resolves to the same package the main
  // thread loads).
  const d3Force = await import('d3-force')

  const simulation = d3Force.forceSimulation(nodes)
    .force('link', d3Force.forceLink(edges).id((d) => d.id).distance((d) => {
      const baseDistance = 150
      const edgeCount = d.pairTotal || 1
      return baseDistance + (edgeCount - 1) * 50
    }))
    .force('charge', d3Force.forceManyBody().strength(-400))
    .force('center', d3Force.forceCenter(width / 2, height / 2))
    .force('collide', d3Force.forceCollide(50))
    .force('x', d3Force.forceX(width / 2).strength(0.04))
    .force('y', d3Force.forceY(height / 2).strength(0.04))

  // Stop the auto-running animation loop and drive the simulation
  // deterministically: ``stop()`` halts the internal timer so the
  // explicit ``tick(iterations)`` becomes the only forward motion.
  // Without the stop() the simulation would also fire its own
  // d3-timer ticks asynchronously, which is invisible in a
  // worker but wastes CPU after the result has been posted back.
  simulation.stop()
  simulation.tick(iterations)

  return { nodes, edges }
}
