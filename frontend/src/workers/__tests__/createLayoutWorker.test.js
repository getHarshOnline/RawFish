/**
 * Worker-facade contract: message dispatch + result handling.
 *
 * The createLayoutWorker factory hides the bundler-specific
 * ``new Worker(new URL(...), {type: 'module'})`` form behind a
 * single call so tests can stub it out. Here we assert:
 *
 *   1. The factory returns ``null`` when ``Worker`` is absent
 *      from the runtime (SSR, legacy test environments). The
 *      caller MUST fall back to inline computation in that case.
 *   2. A stubbed Worker receives the correct message shape and
 *      its ``onmessage`` callback is wired through.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { createLayoutWorker, LAYOUT_WORKER_THRESHOLD } from '../createLayoutWorker.js'

describe('createLayoutWorker', () => {
  it('exports a sensible threshold', () => {
    expect(typeof LAYOUT_WORKER_THRESHOLD).toBe('number')
    expect(LAYOUT_WORKER_THRESHOLD).toBeGreaterThan(0)
  })

  it('returns null when Worker is undefined in the environment', () => {
    const originalWorker = globalThis.Worker
    // eslint-disable-next-line no-undef
    delete globalThis.Worker
    try {
      const w = createLayoutWorker()
      expect(w).toBeNull()
    } finally {
      if (originalWorker !== undefined) globalThis.Worker = originalWorker
    }
  })
})

describe('worker dispatch contract (fake Worker)', () => {
  let posted
  let fakeWorker
  let originalWorker

  beforeEach(() => {
    posted = []
    originalWorker = globalThis.Worker
    // Minimal Worker stub that records postMessage calls and
    // lets the test trigger onmessage / onerror manually.
    globalThis.Worker = class {
      constructor (url, opts) {
        this.url = url
        this.opts = opts
        this.onmessage = null
        this.onerror = null
        this.terminated = false
        fakeWorker = this
      }
      postMessage (msg) { posted.push(msg) }
      terminate () { this.terminated = true }
    }
  })

  afterEach(() => {
    if (originalWorker !== undefined) {
      globalThis.Worker = originalWorker
    } else {
      // eslint-disable-next-line no-undef
      delete globalThis.Worker
    }
  })

  it('constructs a module worker via the factory', () => {
    const w = createLayoutWorker()
    expect(w).not.toBeNull()
    expect(w.opts).toEqual({ type: 'module' })
    expect(w.terminated).toBe(false)
  })

  it('round-trips a layout request to the consumer onmessage handler', async () => {
    const w = createLayoutWorker()
    expect(w).not.toBeNull()
    const consumerSeen = []
    w.onmessage = (event) => { consumerSeen.push(event.data) }

    const payload = {
      type: 'layout',
      payload: {
        nodes: [{ id: 'a' }, { id: 'b' }],
        edges: [{ source: 'a', target: 'b' }],
        options: { width: 800, height: 600 },
      },
    }
    w.postMessage(payload)
    expect(posted).toHaveLength(1)
    expect(posted[0]).toEqual(payload)

    // Simulate the worker posting back a result.
    const result = {
      type: 'result',
      payload: {
        nodes_with_positions: [
          { id: 'a', x: 10, y: 20 },
          { id: 'b', x: 30, y: 40 },
        ],
        edges: [{ source: 'a', target: 'b' }],
      },
    }
    fakeWorker.onmessage({ data: result })
    expect(consumerSeen).toHaveLength(1)
    expect(consumerSeen[0].type).toBe('result')
    expect(consumerSeen[0].payload.nodes_with_positions[0].x).toBe(10)
  })

  it('terminates cleanly when the consumer is done', () => {
    const w = createLayoutWorker()
    expect(w).not.toBeNull()
    w.terminate()
    expect(w.terminated).toBe(true)
  })
})
