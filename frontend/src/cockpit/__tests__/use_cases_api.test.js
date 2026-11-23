import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api/index.js', () => ({
  default: {
    get: vi.fn(() => Promise.resolve({ success: true, data: { ok: true } })),
    post: vi.fn(() => Promise.resolve({ success: true, data: { ok: true } })),
    patch: vi.fn(() => Promise.resolve({ success: true, data: { ok: true } }))
  }
}))

import service from '../../api/index.js'
import {
  USE_CASES_BASE,
  createSimulation,
  compareSimulations,
  evaluatePreflight,
  getCatalog,
  generateSimulationReport,
  getReportGenerationJob,
  listUseCases,
  parseScenarioSeed,
  getSimulationGraph,
  getSimulationStatus,
  getStatisticalConfig,
  getStatisticalLineage,
  getStatisticalManifest,
  getStatisticalVariants,
  patchStatisticalConfig,
  reseedStatisticalConfig,
  prepareSimulation,
  startSimulation
} from '../../api/useCases.js'

function lastGet () { return service.get.mock.calls.at(-1) }
function lastPost () { return service.post.mock.calls.at(-1) }
function lastPatch () { return service.patch.mock.calls.at(-1) }

function calledUrls () {
  return [
    ...service.get.mock.calls,
    ...service.post.mock.calls,
    ...service.patch.mock.calls
  ].map(([url]) => url)
}

function expectOnlyGenericUseCaseEndpoints () {
  expect(calledUrls()).not.toHaveLength(0)
  const removedSimulationApi = '/api/' + 'simulation'
  const removedReportApi = '/api/' + 'report'
  for (const url of calledUrls()) {
    expect(url.startsWith(USE_CASES_BASE)).toBe(true)
    expect(url).not.toContain(removedSimulationApi)
    expect(url).not.toContain(removedReportApi)
  }
}

describe('RawFish generic use-case API client', () => {
  beforeEach(() => {
    service.get.mockClear()
    service.post.mockClear()
    service.patch.mockClear()
  })

  it('uses generic catalog and preflight endpoints', async () => {
    const index = await listUseCases()
    expect(index).toEqual({ ok: true })
    expect(lastGet()[0]).toBe('/api/sim/use-cases')

    await getCatalog('public_policy')
    expect(lastGet()[0]).toBe('/api/sim/use-cases/public_policy/catalog')

    const data = await evaluatePreflight('public_policy', { provider: { mode: 'local_ollama' } })
    expect(data).toEqual({ ok: true })
    expect(lastPost()[0]).toBe('/api/sim/use-cases/public_policy/evals/preflight_meter')
    expect(lastPost()[1]).toEqual({ provider: { mode: 'local_ollama' } })

    await parseScenarioSeed('public_policy', { seed_text: 'seed' })
    expect(lastPost()[0]).toBe('/api/sim/use-cases/public_policy/scenario/parse')
    expect(lastPost()[1]).toEqual({ seed_text: 'seed' })

    expectOnlyGenericUseCaseEndpoints()
  })

  it('uses generic create prepare start and status endpoints', async () => {
    await createSimulation('creator', { scenario: { seed_text: 'x' } }, 'idem-create')
    expect(lastPost()[0]).toBe('/api/sim/use-cases/creator/simulation/create')
    expect(lastPost()[2].headers['Idempotency-Key']).toBe('idem-create')

    await prepareSimulation('creator', 'sim/odd', 'idem-prepare')
    expect(lastPost()[0]).toBe('/api/sim/use-cases/creator/simulation/sim%2Fodd/prepare')
    expect(lastPost()[2].headers['Idempotency-Key']).toBe('idem-prepare')

    await startSimulation('creator', 'sim/odd', 'idem-start')
    expect(lastPost()[0]).toBe('/api/sim/use-cases/creator/simulation/sim%2Fodd/start')
    expect(lastPost()[2].headers['Idempotency-Key']).toBe('idem-start')

    await getSimulationStatus('creator', 'sim/odd')
    expect(lastGet()[0]).toBe('/api/sim/use-cases/creator/simulation/sim%2Fodd/status')

    await getSimulationGraph('creator', 'sim/odd')
    expect(lastGet()[0]).toBe('/api/sim/use-cases/creator/simulation/sim%2Fodd/graph')

    expectOnlyGenericUseCaseEndpoints()
  })

  it('uses generic statistical manifest config re-seed and lineage endpoints', async () => {
    await getStatisticalManifest('internal_policy')
    expect(lastGet()[0]).toBe('/api/sim/use-cases/internal_policy/statistical-manifest')

    await getStatisticalConfig('internal_policy', 'internal_policy_sim_1')
    expect(lastGet()[0]).toBe('/api/sim/use-cases/internal_policy/simulation/internal_policy_sim_1/statistical-config')

    await reseedStatisticalConfig(
      'internal_policy',
      'internal_policy_sim_1',
      456,
      'operator',
      'idem-reseed'
    )
    expect(lastPost()[0]).toBe('/api/sim/use-cases/internal_policy/simulation/internal_policy_sim_1/statistical-config/reseed')
    expect(lastPost()[1]).toEqual({ seed: 456, reason: 'operator' })
    expect(lastPost()[2].headers['Idempotency-Key']).toBe('idem-reseed')

    await getStatisticalLineage('internal_policy', 'internal_policy_sim_1')
    expect(lastGet()[0]).toBe('/api/sim/use-cases/internal_policy/simulation/internal_policy_sim_1/statistical-lineage')

    expectOnlyGenericUseCaseEndpoints()
  })

  it('patches statistical config through the canonical Workbench endpoint', async () => {
    const ops = [{ op: 'replace', path: '/run/run_seed', value: 123 }]

    await patchStatisticalConfig(
      'internal_policy',
      'sim/odd',
      ops,
      'sha256:old',
      'idem-stat'
    )
    expect(service.patch).toHaveBeenCalledTimes(1)
    expect(lastPatch()[0]).toBe('/api/sim/use-cases/internal_policy/simulation/sim%2Fodd/statistical-config')
    expect(lastPatch()[1]).toEqual({
      ops,
      expected_hash: 'sha256:old'
    })
    expect(lastPatch()[2].headers['Idempotency-Key']).toBe('idem-stat')

    expectOnlyGenericUseCaseEndpoints()
  })

  it('lists statistical variants through the canonical Workbench endpoint', async () => {
    await getStatisticalVariants('internal_policy', 'sim/odd')
    expect(service.get).toHaveBeenCalledTimes(1)
    expect(lastGet()).toEqual([
      '/api/sim/use-cases/internal_policy/simulation/sim%2Fodd/variants?axis=statistical'
    ])

    await getStatisticalVariants('internal_policy', 'sim/odd', 'findings delta')
    expect(service.get).toHaveBeenCalledTimes(2)
    expect(lastGet()).toEqual([
      '/api/sim/use-cases/internal_policy/simulation/sim%2Fodd/variants?axis=findings%20delta'
    ])

    expectOnlyGenericUseCaseEndpoints()
  })

  it('compares simulations through the canonical Workbench endpoint', async () => {
    const payload = {
      base_run_id: 'internal_policy_sim_1',
      variant_run_id: 'variant_a',
      axes: ['statistical', 'findings']
    }

    await compareSimulations(
      'internal_policy',
      payload,
      'idem-compare'
    )
    expect(service.post).toHaveBeenCalledTimes(1)
    expect(lastPost()[0]).toBe('/api/sim/use-cases/internal_policy/simulation/compare')
    expect(lastPost()[1]).toEqual(payload)
    expect(lastPost()[2].headers['Idempotency-Key']).toBe('idem-compare')

    expectOnlyGenericUseCaseEndpoints()
  })

  it('uses async report generation and job status endpoints', async () => {
    await generateSimulationReport('public_policy', 'run_1', 'idem-report')
    expect(lastPost()[0]).toBe('/api/sim/use-cases/public_policy/simulation/run_1/report/generate')
    expect(lastPost()[1]).toEqual({ async: true })
    expect(lastPost()[2].headers['Idempotency-Key']).toBe('idem-report')

    await getReportGenerationJob('public_policy', 'run_1', 'job/odd')
    expect(lastGet()[0]).toBe(
      '/api/sim/use-cases/public_policy/simulation/run_1/report/jobs/job%2Fodd'
    )

    expectOnlyGenericUseCaseEndpoints()
  })
})
