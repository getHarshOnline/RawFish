/**
 * Worker-side layout computation: numeric output contract.
 *
 * The computeForceLayout function is pure (no DOM, no Vue) so we
 * import and call it directly here -- the Worker shim that wraps
 * it lives in graphLayout.worker.js and is exercised by the
 * dispatch test in graphLayoutWorkerDispatch.test.js.
 */
import { describe, it, expect } from 'vitest'
import { computeForceLayout } from '../graphLayout.js'

describe('computeForceLayout', () => {
  it('assigns numeric x/y to every node after running', async () => {
    const nodes = [
      { id: 'a' },
      { id: 'b' },
      { id: 'c' },
    ]
    const edges = [
      { source: 'a', target: 'b' },
      { source: 'b', target: 'c' },
    ]
    const result = await computeForceLayout({
      nodes,
      edges,
      width: 800,
      height: 600,
      iterations: 50,
    })
    expect(result.nodes).toHaveLength(3)
    for (const n of result.nodes) {
      expect(typeof n.x).toBe('number')
      expect(typeof n.y).toBe('number')
      expect(Number.isFinite(n.x)).toBe(true)
      expect(Number.isFinite(n.y)).toBe(true)
    }
  })

  it('keeps the connected nodes within a bounded box near center', async () => {
    // Sanity: forceCenter pulls the cluster toward (w/2, h/2) so
    // positions should not blow up beyond +/-2x the canvas after
    // a reasonable iteration count. Pins the algorithm against
    // a future regression where someone drops forceCenter and
    // nodes drift to infinity.
    const nodes = Array.from({ length: 10 }, (_, i) => ({ id: `n${i}` }))
    const edges = nodes.slice(1).map((n, i) => ({
      source: nodes[i].id,
      target: n.id,
    }))
    await computeForceLayout({
      nodes,
      edges,
      width: 1000,
      height: 800,
      iterations: 200,
    })
    for (const n of nodes) {
      expect(Math.abs(n.x)).toBeLessThan(3000)
      expect(Math.abs(n.y)).toBeLessThan(3000)
    }
  })

  it('rejects invalid arguments at the type boundary', async () => {
    await expect(
      computeForceLayout({ nodes: null, edges: [], width: 10, height: 10 }),
    ).rejects.toThrow(TypeError)
    await expect(
      computeForceLayout({ nodes: [], edges: [], width: 'big', height: 10 }),
    ).rejects.toThrow(TypeError)
  })
})
