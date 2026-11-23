/**
 * Layout-worker supersede + factory-throw safety contract.
 *
 * GraphPanel's large-graph branch delegates the converging force
 * layout to a Web Worker; the worker handle is stashed on the
 * module-scoped ``activeLayoutWorker`` slot and ``terminate()`` is
 * called when a rapid sequence of graph updates supersedes the
 * in-flight layout. Two pre-fix pathologies the tests below pin:
 *
 *   1. ``Worker.terminate()`` does NOT trigger ``onmessage`` or
 *      ``onerror`` -- so the awaiting ``new Promise(resolve => ...)``
 *      in the prior renderGraph() hung forever, retaining closure
 *      references to the node/edge arrays. The fix pairs each worker
 *      with a ``reject`` callback that the supersede path invokes
 *      alongside ``terminate()``.
 *
 *   2. ``createLayoutWorker()`` can throw synchronously (CSP blocks
 *      module workers, Worker constructor unavailable in the
 *      surrounding sandbox). The fix wraps the factory call in a
 *      try/catch and falls back to the inline simulation -- slow
 *      layout is strictly better than a crashed render.
 *
 * The tests stub the factory module so they can assert behaviour
 * without bringing up an actual Worker in jsdom/happy-dom.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { nextTick } from 'vue'

const { workerMock } = vi.hoisted(() => ({
  workerMock: {
    factory: vi.fn(),
  },
}))

vi.mock('../../workers/createLayoutWorker.js', () => ({
  // The component checks ``nodes.length > LAYOUT_WORKER_THRESHOLD``
  // to decide whether to use the worker; expose a small threshold
  // so the fixture below trips it with a manageable node count.
  LAYOUT_WORKER_THRESHOLD: 5,
  createLayoutWorker: (...args) => workerMock.factory(...args),
}))

// d3 is heavy and dynamically imported by GraphPanel; stubbing it
// here keeps the worker-hand-off logic the test cares about reachable
// in deterministic synchronous time. The stub exposes only the
// surface ``renderGraph`` exercises before the worker branch and the
// minimal d3.select/forceSimulation surface so the rest of the
// render does not throw. We don't assert on render output -- only
// the worker-supersede contract.
vi.mock('d3', () => {
  const chain = new Proxy(function () {}, {
    get: () => chain,
    apply: () => chain,
  })
  const selectStub = () => chain
  const forceLink = () => chain
  const forceManyBody = () => chain
  const forceCenter = () => chain
  const forceCollide = () => chain
  const forceX = () => chain
  const forceY = () => chain
  const forceSimulation = () => ({
    force: () => forceSimulation(),
    on: () => forceSimulation(),
    stop: () => {},
    alpha: () => forceSimulation(),
    alphaTarget: () => forceSimulation(),
    restart: () => forceSimulation(),
  })
  const zoom = () => ({
    extent: () => zoom(),
    scaleExtent: () => zoom(),
    on: () => zoom(),
  })
  const drag = () => ({ on: () => drag() })
  return {
    default: { select: selectStub },
    select: selectStub,
    forceLink,
    forceManyBody,
    forceCenter,
    forceCollide,
    forceX,
    forceY,
    forceSimulation,
    zoom,
    drag,
  }
})

import GraphPanel from '../GraphPanel.vue'

function makeI18n () {
  return createI18n({
    legacy: false,
    locale: 'en',
    fallbackLocale: 'en',
    missingWarn: false,
    fallbackWarn: false,
    silentTranslationWarn: true,
    silentFallbackWarn: true,
    messages: { en: {} },
  })
}

/**
 * Minimal Worker stub. Records the consumer-attached
 * ``onmessage``/``onerror`` so the test can verify they were never
 * invoked (supersede MUST reject the promise via the paired reject
 * hook, not via a synthesized onmessage/onerror).
 */
function makeFakeWorker () {
  return {
    posted: [],
    terminated: false,
    onmessage: null,
    onerror: null,
    postMessage (msg) { this.posted.push(msg) },
    terminate () { this.terminated = true },
  }
}

/**
 * Build a node-count fixture large enough to trip the worker
 * threshold. Each node carries the minimum keys GraphPanel reads.
 */
function makeGraphData (n) {
  const nodes = []
  for (let i = 0; i < n; i++) {
    nodes.push({
      uuid: `node_${i}`,
      name: `Node ${i}`,
      labels: ['Entity', 'Person'],
    })
  }
  return { nodes, edges: [] }
}

describe('GraphPanel layout-worker supersede', () => {
  beforeEach(() => {
    workerMock.factory.mockReset()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('rejects the pending layout promise when a newer renderGraph supersedes the in-flight worker', async () => {
    // Two distinct fake workers; the first call gets w1, the second
    // call gets w2. The first worker is NEVER asked to send a result;
    // the supersede path must reject the awaiting promise via the
    // paired reject hook so the first renderGraph() returns cleanly
    // rather than hanging on a never-resolving promise.
    const w1 = makeFakeWorker()
    const w2 = makeFakeWorker()
    workerMock.factory.mockReturnValueOnce(w1).mockReturnValueOnce(w2)

    const i18n = makeI18n()
    // Mount with an initial small (below-threshold) graph so the
    // ``<svg ref="graphSvg">`` slot renders (the template gates that
    // surface on graphData being truthy) and graphSvg.value is wired
    // up. Then bump to a >threshold graph to trip the worker path.
    const wrapper = mount(GraphPanel, {
      props: { graphData: makeGraphData(1) },
      global: { plugins: [i18n] },
    })
    // Wait for initial mount; the deep watcher does not fire on the
    // initial value, so renderGraph has NOT been called yet.
    await flushPromises()
    await nextTick()
    // First renderGraph: graphData transitions to a >threshold value.
    // The watcher fires and renderGraph awaits its worker promise.
    await wrapper.setProps({ graphData: makeGraphData(10) })
    for (let i = 0; i < 5; i++) {
      await flushPromises()
      await nextTick()
    }
    expect(workerMock.factory).toHaveBeenCalledTimes(1)
    expect(w1.posted.length).toBeGreaterThan(0)
    expect(typeof w1.onmessage).toBe('function')

    // Capture whether onmessage / onerror are EVER invoked by the
    // supersede path. They MUST NOT be -- the reject hook is the
    // only legitimate wake-up signal for terminate().
    let onmessageCalled = false
    let onerrorCalled = false
    const origOnmessage = w1.onmessage
    const origOnerror = w1.onerror
    w1.onmessage = (...args) => { onmessageCalled = true; return origOnmessage?.(...args) }
    w1.onerror = (...args) => { onerrorCalled = true; return origOnerror?.(...args) }

    // Trigger a second renderGraph() by updating graphData. The
    // component's deep watcher schedules renderGraph on the next
    // tick; before that fires, the prior renderGraph() is still
    // awaiting its w1 promise. The supersede block at the top of
    // renderGraph terminates w1 AND rejects its paired promise so
    // the prior call exits cleanly.
    await wrapper.setProps({ graphData: makeGraphData(11) })
    for (let i = 0; i < 5; i++) {
      await flushPromises()
      await nextTick()
    }

    // w1 MUST have been terminated by the supersede path, w2 MUST
    // have been constructed and posted to (the new render proceeded
    // past the previously-blocking await).
    expect(w1.terminated).toBe(true)
    expect(w2.posted.length).toBeGreaterThan(0)
    // The reject hook -- NOT a synthesized onmessage/onerror -- woke
    // the first renderGraph() up.
    expect(onmessageCalled).toBe(false)
    expect(onerrorCalled).toBe(false)
    wrapper.unmount()
  })

  it('falls back to inline layout when createLayoutWorker throws synchronously', async () => {
    // CSP-style failure: the factory throws (Worker constructor
    // disallowed by the host page). The component MUST swallow the
    // throw and proceed with the inline simulation path so the user
    // still sees a graph -- slow layout is better than a crashed
    // render with no recourse.
    workerMock.factory.mockImplementation(() => {
      throw new Error('Worker construction blocked by CSP')
    })

    const i18n = makeI18n()
    let renderError = null
    let wrapper
    try {
      // Same mount pattern as the supersede test: small graph first
      // so the svg ref binds, then bump to >threshold to trip the
      // worker path. The factory throws here; the component must
      // catch and degrade to inline simulation.
      wrapper = mount(GraphPanel, {
        props: { graphData: makeGraphData(1) },
        global: { plugins: [i18n] },
      })
      await flushPromises()
      await nextTick()
      await wrapper.setProps({ graphData: makeGraphData(10) })
      await flushPromises()
      await nextTick()
      // d3 is dynamically imported inside renderGraph; allow enough
      // microtask cycles for the import to settle before asserting.
      for (let i = 0; i < 10; i++) {
        await flushPromises()
        await nextTick()
      }
    } catch (err) {
      renderError = err
    }

    expect(renderError).toBeNull()
    expect(workerMock.factory).toHaveBeenCalled()
    // The svg slot is still present (component did not unmount or
    // throw an unhandled exception during render).
    expect(wrapper.find('svg.graph-svg').exists()).toBe(true)
    wrapper.unmount()
  })
})
