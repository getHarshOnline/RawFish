/**
 * RawFish cockpit — IA + Design Language v2 suite.
 *
 * Covers the spec's load-bearing behaviors:
 *   · fixed phase vocabulary (board 12)
 *   · step permission model + locked reasons (board 07)
 *   · URL scheme + no compatibility surface (board 07)
 *   · boot sequence create → prepare (idempotent, retry-safe)
 *   · console: 34px idle line, expands only while streaming
 *   · overlays: summoned, trailed, reversible (board 06)
 *   · S0 home: draft → /run handoff, disabled-with-reason law
 */
import { mount, config, enableAutoUnmount, flushPromises } from '@vue/test-utils'
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest'
import { reactive, nextTick } from 'vue'
import { createMemoryHistory } from 'vue-router'

const apiMock = vi.hoisted(() => ({
  getCatalog: vi.fn(),
  evaluatePreflight: vi.fn(),
  getStatisticalManifest: vi.fn(),
  getStatisticalConfig: vi.fn(),
  getStatisticalLineage: vi.fn(),
  getStatisticalVariants: vi.fn(),
  patchStatisticalConfig: vi.fn(),
  reseedStatisticalConfig: vi.fn(),
  compareSimulations: vi.fn(),
  compareStatisticalVariants: vi.fn(),
  findSimulationStatus: vi.fn(),
  getSimulationGraph: vi.fn(),
  getSimulationStatus: vi.fn(),
  getSimulationReport: vi.fn(),
  getReportGenerationJob: vi.fn(),
  chatWithSimulationReport: vi.fn(),
  generateSimulationReport: vi.fn(),
  createSimulation: vi.fn(),
  prepareSimulation: vi.fn(),
  parseScenarioSeed: vi.fn(),
  startSimulation: vi.fn(),
  runSensitivity: vi.fn(),
  listUseCases: vi.fn()
}))

vi.mock('../../api/useCases.js', () => apiMock)
vi.mock('../../components/GraphPanel.vue', () => ({
  default: {
    name: 'GraphPanel',
    props: ['graphData', 'loading', 'currentPhase', 'isSimulating', 'chromeless'],
    emits: ['refresh'],
    template: '<section data-testid="graph-panel"><span data-testid="graph-node-count">{{ graphData?.nodes?.length || 0 }}</span></section>'
  }
}))

import CockpitShell from '../CockpitShell.vue'
import HomeView from '../HomeView.vue'
import { createRunStore, displayPhase, SPEC_STEPS } from '../runStore.js'
import { readDraft, setDraft } from '../draftStore.js'
import { createRawFishRouter } from '../../router/index.js'
import { laneManifests } from '../manifestRegistry.js'

config.global.mocks = { $t: key => key }
enableAutoUnmount(afterEach)

function resetApiMocks () {
  for (const fn of Object.values(apiMock)) {
    if (typeof fn?.mockReset === 'function') fn.mockReset()
  }
  apiMock.listUseCases.mockRejectedValue(new Error('offline'))
  apiMock.getCatalog.mockResolvedValue({ lane_manifest: null, personas: [] })
  apiMock.getStatisticalManifest.mockResolvedValue({
    manifest: { fields: [] },
    default_config: { settings: {}, statistical_settings_hash: 'sha256:test' }
  })
  apiMock.getSimulationGraph.mockResolvedValue({})
  apiMock.evaluatePreflight.mockResolvedValue({
    scale_ok: true,
    scale_product: 240,
    scale_cap: 500,
    token_estimate: 36000,
    provider_readiness: { ready: true, status: 'ready' }
  })
}

function storeHarness ({ props = {}, routeState = {} } = {}) {
  const router = {
    push: vi.fn(async () => {}),
    replace: vi.fn(async () => {}),
    back: vi.fn()
  }
  const route = reactive({ query: {}, hash: '', ...routeState })
  const fullProps = {
    runId: null,
    step: null,
    mode: null,
    lane: 'creator',
    ...props
  }
  const store = createRunStore(fullProps, router, route)
  return { store, router, route, props: fullProps }
}

function appRouter () {
  return createRawFishRouter(createMemoryHistory())
}

/* Node ≥22 ships an experimental WebStorage that, without
   --localstorage-file, leaves window.localStorage undefined under the
   test DOM. Provision an in-memory Storage so the suite is
   environment-agnostic (same API the app exercises via try/catch). */
function memoryStorage () {
  const map = new Map()
  return {
    getItem: key => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => { map.set(String(key), String(value)) },
    removeItem: key => { map.delete(key) },
    clear: () => { map.clear() },
    key: index => Array.from(map.keys())[index] ?? null,
    get length () { return map.size }
  }
}

function ensureWebStorage () {
  for (const name of ['localStorage', 'sessionStorage']) {
    let usable = false
    try {
      usable = Boolean(window[name]) && typeof window[name].clear === 'function'
    } catch { usable = false }
    if (!usable) {
      Object.defineProperty(window, name, { value: memoryStorage(), configurable: true })
    }
  }
}

beforeEach(() => {
  resetApiMocks()
  setDraft(null)
  ensureWebStorage()
  window.localStorage.clear()
  window.sessionStorage.clear()
  window.history.replaceState({}, '', '/')
})

/* ---------------------------------------------------------------- boards 02/12 */
describe('phase vocabulary (board 12)', () => {
  it('maps backend phases onto the six fixed words', () => {
    expect(displayPhase(null, false)).toBe('draft')
    expect(displayPhase('created', true)).toBe('draft')
    expect(displayPhase('prepared', true)).toBe('prepared')
    expect(displayPhase('starting', true)).toBe('running')
    expect(displayPhase('running', true)).toBe('running')
    expect(displayPhase('failed', true)).toBe('halted')
    expect(displayPhase('completed', true)).toBe('complete')
    expect(displayPhase('reported', true)).toBe('reported')
  })
})

/* -------------------------------------------------------------------- board 07 */
describe('step permission model (board 07)', () => {
  it('locks everything past build until the question parses', () => {
    const { store } = storeHarness()
    store.scenarioText.value = ''
    const states = Object.fromEntries(store.spineItems.value.map(item => [item.id, item]))
    expect(states.scenario.state).toBe('active')
    expect(states.build.state).toBe('locked')
    expect(states.build.reason).toBe('opens once the question parses')
    expect(states.cohorts.state).toBe('locked')
    expect(states.simulate.reason).toBe('launch from preflight')
    expect(states.findings.reason).toBe('no artifacts yet')
  })

  it('opens build once a question exists, keeps cohorts locked until a world is built', () => {
    const { store } = storeHarness()
    store.scenarioText.value = 'Will long-time fans read the rebrand as abandonment?'
    const states = Object.fromEntries(store.spineItems.value.map(item => [item.id, item]))
    expect(states.build.state).not.toBe('locked')
    expect(states.cohorts.state).toBe('locked')
    expect(states.cohorts.reason).toBe('needs a built world')
    expect(states.preflight.reason).toBe('needs cohorts')
  })

  it('refuses navigation to a locked step, allows it to a reachable one', () => {
    const { store, router } = storeHarness()
    store.scenarioText.value = 'A seed'
    store.goStep('findings')
    expect(router.push).not.toHaveBeenCalled()
    store.goStep('build')
    expect(router.push).toHaveBeenCalledWith(expect.objectContaining({
      path: '/run',
      query: expect.objectContaining({ step: 'build' })
    }))
  })

  it('strikethrough never renders — locked is a dimmed variant with a title reason', () => {
    const { store } = storeHarness()
    for (const item of store.spineItems.value) {
      expect(item.state === 'locked' ? item.reason : '').not.toContain('DISABLED')
    }
    expect(SPEC_STEPS).toHaveLength(6)
  })
})

/* -------------------------------------------------------------- boot sequence */
describe('build → boot sequence (boards 02/05)', () => {
  it('create then prepare, idempotency-keyed, then routes into the run', async () => {
    apiMock.createSimulation.mockResolvedValue({ simulation_id: 'sim_v2', phase: 'created' })
    apiMock.prepareSimulation.mockResolvedValue({ phase: 'prepared' })
    const { store, router } = storeHarness()
    await store.boot()
    store.scenarioText.value = 'Will the pricing change alienate loyal fans?'
    await store.buildWorld()

    expect(apiMock.createSimulation).toHaveBeenCalledTimes(1)
    const [laneId, payload, idempotencyKey] = apiMock.createSimulation.mock.calls[0]
    expect(laneId).toBe('creator')
    expect(payload.scenario.stress_question).toContain('pricing change')
    expect(idempotencyKey).toMatch(/^rawfish-create-creator-/)

    expect(apiMock.prepareSimulation).toHaveBeenCalledTimes(1)
    expect(router.push).toHaveBeenCalledWith(expect.objectContaining({ path: '/run/sim_v2' }))

    const index = JSON.parse(window.localStorage.getItem('rawfish.runs'))
    expect(index[0]).toMatchObject({ id: 'sim_v2', lane: 'creator' })
  })

  it('launch gate carries its reason beside the action, never inside it', () => {
    const { store } = storeHarness()
    store.scenarioText.value = ''
    expect(store.launchGateReason.value).toBe('paste a draft first')
  })
})

/* --------------------------------------------------------------- workbench */
describe('Cohorts workbench ownership', () => {
  it('keeps Preflight-owned scale fields out of Cohorts run settings', async () => {
    apiMock.getStatisticalManifest.mockResolvedValue({
      manifest: {
        fields: [
          { id: 'run.ensemble_n', label: 'Ensemble N', default: 5 },
          { id: 'run.drift_horizon_rounds', label: 'Rounds', default: 16 },
          { id: 'run.run_seed', label: 'Run seed', default: 4096 },
          { id: 'cohort.active_hours', label: 'Active hours', default: [9, 10] }
        ]
      },
      default_config: {
        settings: {
          'run.ensemble_n': 5,
          'run.drift_horizon_rounds': 16,
          'run.run_seed': 4096,
          'cohort.active_hours': [9, 10]
        },
        statistical_settings_hash: 'sha256:test'
      }
    })
    const { store } = storeHarness()
    await store.boot()

    expect(store.runStatisticalFields.value.map(field => field.id)).toEqual(['run.run_seed'])
    expect(store.cohortStatisticalFields.value.map(field => field.id)).toEqual(['cohort.active_hours'])
  })
})

/* ------------------------------------------------------------------- console */
describe('Z4 console (board 03)', () => {
  it('idle with no run attached, collapsed', () => {
    const { store } = storeHarness()
    expect(store.consoleLine.value).toBe('idle · no run attached')
    expect(store.consoleOpen.value).toBe(false)
  })

  it('expands only while a run streams; halted goes danger', () => {
    const { store } = storeHarness({ props: { runId: 'sim_live' } })
    store.statusPayload.value = { phase: 'running' }
    expect(store.consoleOpen.value).toBe(true)
    expect(store.consoleDot.value).toBe('live')
    store.statusPayload.value = { phase: 'failed', errors: ['zep unavailable · neo4j_uri_missing'] }
    expect(store.consoleDot.value).toBe('danger')
    expect(store.consoleLine.value).toContain('halted')
    expect(store.consoleLine.value).toContain('neo4j_uri_missing')
    store.statusPayload.value = { phase: 'prepared' }
    expect(store.consoleOpen.value).toBe(false)
  })
})

/* ------------------------------------------------------------------ overlays */
describe('overlays are summoned, trailed, reversible (board 06)', () => {
  it('summon pushes a url hash; drill deepens the trail; esc dismisses all', () => {
    const { store, router, route } = storeHarness()
    store.summon('claim:c-03/acts')
    expect(router.push).toHaveBeenCalledWith(expect.objectContaining({ hash: '#claim:c-03/acts' }))
    route.hash = '#claim:c-03/acts'
    expect(store.overlayTrail.value).toHaveLength(1)
    expect(store.overlay.value).toMatchObject({ kind: 'claim', id: 'c-03', facet: 'acts' })

    store.summon('act:a-2214')
    route.hash = '#act:a-2214'
    expect(store.overlayTrail.value).toHaveLength(2)
    expect(store.overlay.value).toMatchObject({ kind: 'act', id: 'a-2214' })

    store.popOverlay()
    expect(store.overlayTrail.value).toHaveLength(1)
    expect(router.replace).toHaveBeenCalledWith(expect.objectContaining({ hash: '#claim:c-03/acts' }))

    store.dismissOverlays()
    expect(store.overlayTrail.value).toHaveLength(0)
    expect(router.replace).toHaveBeenCalledWith(expect.objectContaining({ hash: '' }))
  })

  it('a deep link rehydrates the same sheet', async () => {
    const { store, route } = storeHarness()
    route.hash = '#node:n-18'
    await nextTick()
    expect(store.overlay.value).toMatchObject({ kind: 'node', id: 'n-18' })
  })
})

/* -------------------------------------------------------------------- routes */
describe('URL scheme (board 07) — no compatibility surface', () => {
  it('declares exactly the two canonical routes; legacy URLs do not resolve', async () => {
    const routerModule = await import('../../router/index.js')
    const router = routerModule.default

    const paths = router.getRoutes().map(record => record.path).sort()
    expect(paths).toEqual(['/', '/run/:runId?'])

    /* legacy URLs 404 — that is the entire migration posture. */
    expect(router.resolve('/cockpit/sim_9').matched).toHaveLength(0)
    expect(router.resolve('/runs/sim_9/report').matched).toHaveLength(0)
    expect(router.resolve('/runs/sim_9/analysis').matched).toHaveLength(0)
  })

  it('refuses legacy draft payloads, missing local drafts and saved-run selectors', async () => {
    const router = appRouter()
    await router.push('/run?step=scenario')
    expect(router.currentRoute.value.fullPath).toBe('/')
    setDraft({ lane: 'creator', source: 'paste', text: 'local draft', preview: null })
    for (const url of ['/run?draft=b2xk', '/run?lane=internal_policy', '/run/sim_9?source=paste', '/run/sim_9?lane=creator']) {
      await router.push(url)
      expect(router.currentRoute.value.fullPath).toBe('/')
      expect(readDraft().text).toBe('local draft')
    }
    await router.push('/run/sim_9?step=findings&mode=read')
    expect(router.currentRoute.value.params.runId).toBe('sim_9')
  })

  it('duplicates into local draft state without serializing the seed', () => {
    const { store, router } = storeHarness({ props: { runId: 'sim_9' } })
    store.selectedLaneId.value = 'creator'
    store.scenarioText.value = 'Draft to duplicate'
    store.duplicateAsDraft()
    expect(router.push).toHaveBeenCalledWith({ path: '/run', query: { step: 'scenario', lane: 'creator' } })
    expect(readDraft()).toEqual({ lane: 'creator', text: 'Draft to duplicate', source: 'paste', preview: null })
  })
})

/* ---------------------------------------------------------------------- S0 */
describe('S0 · Home', () => {
  it('renders lanes, gates the primary action with a visible reason', async () => {
    const router = appRouter()
    await router.push('/')
    const wrapper = mount(HomeView, { global: { plugins: [router] } })
    await flushPromises()

    const laneCards = wrapper.findAll('.lane-rail .rf-card')
    expect(laneCards.length).toBeGreaterThanOrEqual(laneManifests.length)

    const primary = wrapper.find('.rf-btn--primary')
    expect(primary.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('paste a draft first')

    await wrapper.find('textarea').setValue('Stress-test a public apology before posting.')
    await nextTick()
    expect(wrapper.find('.rf-btn--primary').attributes('disabled')).toBeUndefined()
  })

  it('hands the draft to /run without putting its contents in the URL or browser storage', async () => {
    const router = appRouter()
    await router.push('/')
    const push = vi.spyOn(router, 'push')
    const wrapper = mount(HomeView, { global: { plugins: [router] } })
    await flushPromises()
    const seed = 'A risky launch. · #42'
    await wrapper.find('textarea').setValue(seed)
    await wrapper.find('.rf-btn--primary').trigger('click')
    const call = push.mock.calls.find(([target]) => target?.path === '/run')
    expect(call).toBeTruthy()
    expect(call[0].query).toEqual({ lane: 'creator', step: 'scenario' })
    expect(readDraft()).toMatchObject({ text: seed, lane: 'creator' })
    const { store } = storeHarness()
    expect(store.scenarioText.value).toBe(seed)
    expect(window.sessionStorage.length).toBe(0)
    expect(window.localStorage.length).toBe(0)
  })
})

/* -------------------------------------------------------------------- shell */
describe('cockpit shell zones (board 03)', () => {
  it('scenario: dock solo + centered, no stage, console idle', async () => {
    const router = appRouter()
    setDraft({ lane: 'creator', source: 'paste', text: '', preview: null })
    await router.push('/run?lane=creator&step=scenario')
    const wrapper = mount(CockpitShell, {
      props: { runId: null, step: 'scenario', mode: null, lane: 'creator' },
      global: { plugins: [router] }
    })
    await flushPromises()

    expect(wrapper.find('.rf-z3').classes()).toContain('rf-z3--solo')
    expect(wrapper.find('.rf-z2').exists()).toBe(false)
    expect(wrapper.findAll('.rf-spine__step')).toHaveLength(6)
    expect(wrapper.find('.rf-console__text').text()).toBe('idle · no run attached')
    expect(wrapper.find('.rf-z1__ctx').text()).toContain('draft')
  })

  it('locked spine steps are dimmed buttons with title reasons — no strikethrough anywhere', async () => {
    const router = appRouter()
    setDraft({ lane: 'creator', source: 'paste', text: '', preview: null })
    await router.push('/run?lane=creator&step=scenario')
    const wrapper = mount(CockpitShell, {
      props: { runId: null, step: 'scenario', mode: null, lane: 'creator' },
      global: { plugins: [router] }
    })
    await flushPromises()

    const locked = wrapper.findAll('.rf-spine__step--locked')
    expect(locked.length).toBeGreaterThan(0)
    for (const step of locked) {
      expect(step.attributes('disabled')).toBeDefined()
      expect((step.attributes('title') || '').length).toBeGreaterThan(0)
    }
    expect(wrapper.html()).not.toContain('line-' + 'through')
  })
})
