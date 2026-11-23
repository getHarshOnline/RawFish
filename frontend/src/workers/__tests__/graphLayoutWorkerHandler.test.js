/**
 * Worker entry-point onmessage handler.
 *
 * graphLayout.worker.js binds ``self.onmessage`` to a single
 * dispatch function. happy-dom exposes ``self`` (it aliases
 * ``window``) so importing the worker module installs the
 * handler against the test runtime's ``self``. We then drive
 * it directly:
 *
 *   1. Override ``self.postMessage`` to capture replies.
 *   2. Invoke the handler with synthetic ``MessageEvent``-shaped
 *      objects.
 *   3. Assert the result / error message shape that
 *      GraphPanel.vue depends on.
 */
import { describe, it, expect, beforeEach, afterEach, beforeAll } from 'vitest'

describe('graphLayout.worker.js onmessage handler', () => {
  let originalPostMessage
  let posted
  let handler

  beforeAll(async () => {
    // Import once -- the worker module side-effects ``self.onmessage``
    // at import time. ES modules are cached, so capturing the handler
    // here keeps it available across the individual test cases even
    // when afterEach restores ``self.onmessage`` to null.
    await import('../graphLayout.worker.js')
    handler = self.onmessage
  })

  beforeEach(() => {
    posted = []
    originalPostMessage = self.postMessage
    self.postMessage = (msg) => { posted.push(msg) }
    // Re-attach the captured handler so a test that nulls
    // ``self.onmessage`` in teardown does not strand the next.
    self.onmessage = handler
  })

  afterEach(() => {
    self.postMessage = originalPostMessage
  })

  it('posts a result for a well-formed layout request', async () => {
    expect(typeof handler).toBe('function')
    await handler({
      data: {
        type: 'layout',
        payload: {
          nodes: [{ id: 'a' }, { id: 'b' }],
          edges: [{ source: 'a', target: 'b' }],
          options: { width: 800, height: 600, iterations: 30 },
        },
      },
    })
    expect(posted).toHaveLength(1)
    const reply = posted[0]
    expect(reply.type).toBe('result')
    expect(reply.payload.nodes_with_positions).toHaveLength(2)
    expect(typeof reply.payload.nodes_with_positions[0].x).toBe('number')
  })

  it('posts an error for a malformed message', async () => {
    // Using the captured handler from beforeAll.
    await handler({ data: { type: 'not-layout' } })
    expect(posted).toHaveLength(1)
    expect(posted[0].type).toBe('error')
    expect(typeof posted[0].payload.message).toBe('string')
  })

  it('posts an error when the payload fails type validation', async () => {
    // Using the captured handler from beforeAll.
    await handler({
      data: {
        type: 'layout',
        payload: {
          nodes: null,
          edges: [],
          options: { width: 100, height: 100 },
        },
      },
    })
    expect(posted).toHaveLength(1)
    expect(posted[0].type).toBe('error')
  })
})
