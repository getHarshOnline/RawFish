import service from './index.js'

export const USE_CASES_BASE = '/api/sim/use-cases'

function unwrapEnvelope (response) {
  if (response && response.success === true && 'data' in response) {
    return response.data
  }
  if (response && response.data && response.success === undefined) {
    return response.data
  }
  return response
}

function idempotencyConfig (idempotencyKey) {
  if (!idempotencyKey) return {}
  return { headers: { 'Idempotency-Key': idempotencyKey } }
}

function laneBase (laneId) {
  return `${USE_CASES_BASE}/${encodeURIComponent(laneId)}`
}

function simulationBase (laneId, simulationId) {
  return `${laneBase(laneId)}/simulation/${encodeURIComponent(simulationId)}`
}

export async function getCatalog (laneId) {
  return unwrapEnvelope(await service.get(`${laneBase(laneId)}/catalog`))
}

export async function listUseCases () {
  return unwrapEnvelope(await service.get(USE_CASES_BASE))
}

export async function listPersonas (laneId) {
  return unwrapEnvelope(await service.get(`${laneBase(laneId)}/personas`))
}

export async function evaluatePreflight (laneId, payload) {
  return unwrapEnvelope(await service.post(`${laneBase(laneId)}/evals/preflight_meter`, payload))
}

export async function parseScenarioSeed (laneId, payload) {
  return unwrapEnvelope(await service.post(`${laneBase(laneId)}/scenario/parse`, payload))
}

export async function getStatisticalManifest (laneId) {
  return unwrapEnvelope(await service.get(`${laneBase(laneId)}/statistical-manifest`))
}

export async function createSimulation (laneId, payload, idempotencyKey) {
  return unwrapEnvelope(await service.post(
    `${laneBase(laneId)}/simulation/create`,
    payload,
    idempotencyConfig(idempotencyKey)
  ))
}

export async function prepareSimulation (laneId, simulationId, idempotencyKey) {
  return unwrapEnvelope(await service.post(
    `${simulationBase(laneId, simulationId)}/prepare`,
    {},
    idempotencyConfig(idempotencyKey)
  ))
}

export async function startSimulation (laneId, simulationId, idempotencyKey) {
  return unwrapEnvelope(await service.post(
    `${simulationBase(laneId, simulationId)}/start`,
    {},
    idempotencyConfig(idempotencyKey)
  ))
}

export async function getSimulationStatus (laneId, simulationId) {
  return unwrapEnvelope(await service.get(`${simulationBase(laneId, simulationId)}/status`))
}

export async function getSimulationGraph (laneId, simulationId) {
  return unwrapEnvelope(await service.get(`${simulationBase(laneId, simulationId)}/graph`))
}

export async function generateSimulationReport (laneId, simulationId, idempotencyKey) {
  return unwrapEnvelope(await service.post(
    `${simulationBase(laneId, simulationId)}/report/generate`,
    { async: true },
    idempotencyConfig(idempotencyKey)
  ))
}

export async function getReportGenerationJob (laneId, simulationId, jobId) {
  return unwrapEnvelope(await service.get(
    `${simulationBase(laneId, simulationId)}/report/jobs/${encodeURIComponent(jobId)}`
  ))
}

export async function getSimulationReport (laneId, simulationId) {
  return unwrapEnvelope(await service.get(`${simulationBase(laneId, simulationId)}/report`))
}

export async function chatWithSimulationReport (
  laneId,
  simulationId,
  message,
  chatHistory = [],
  idempotencyKey = null
) {
  return unwrapEnvelope(await service.post(
    `${simulationBase(laneId, simulationId)}/report/chat`,
    {
      message,
      chat_history: Array.isArray(chatHistory) ? chatHistory : []
    },
    idempotencyConfig(idempotencyKey)
  ))
}

export async function getStatisticalConfig (laneId, simulationId) {
  return unwrapEnvelope(await service.get(`${simulationBase(laneId, simulationId)}/statistical-config`))
}

export async function patchStatisticalConfig (laneId, simulationId, ops, expectedHash, idempotencyKey) {
  const normalizedOps = Array.isArray(ops) ? ops : []
  return unwrapEnvelope(await service.patch(
    `${simulationBase(laneId, simulationId)}/statistical-config`,
    {
      ops: normalizedOps,
      expected_hash: expectedHash || undefined
    },
    idempotencyConfig(idempotencyKey)
  ))
}

export async function reseedStatisticalConfig (laneId, simulationId, payloadOrSeed, reason, idempotencyKey) {
  const body = typeof payloadOrSeed === 'object' && payloadOrSeed !== null
    ? payloadOrSeed
    : { seed: payloadOrSeed, reason }
  return unwrapEnvelope(await service.post(
    `${simulationBase(laneId, simulationId)}/statistical-config/reseed`,
    body,
    idempotencyConfig(idempotencyKey)
  ))
}

export async function getStatisticalLineage (laneId, simulationId) {
  return unwrapEnvelope(await service.get(`${simulationBase(laneId, simulationId)}/statistical-lineage`))
}

export async function getStatisticalVariants (laneId, simulationId, axis = 'statistical') {
  try {
    const query = axis ? `?axis=${encodeURIComponent(axis)}` : ''
    return unwrapEnvelope(await service.get(`${simulationBase(laneId, simulationId)}/variants${query}`))
  } catch (_error) {
    const lineage = await getStatisticalLineage(laneId, simulationId)
    return {
      variants: Array.isArray(lineage?.lineage)
        ? lineage.lineage.map((event, index) => ({
            variant_id: event.variant_id || event.id || `reseeded-${index + 1}`,
            base_run_id: event.base_run_id || simulationId,
            diff_hash: event.diff_hash || event.settings_hash || null,
            statistical_settings_hash: event.statistical_settings_hash || event.settings_hash || null,
            created_at: event.created_at || null,
            label: event.reason || `Reseeded #${index + 1}`
          }))
        : []
    }
  }
}

export async function compareStatisticalVariants (laneId, payload, idempotencyKey) {
  return unwrapEnvelope(await service.post(
    `${laneBase(laneId)}/simulation/compare`,
    payload,
    idempotencyConfig(idempotencyKey)
  ))
}

export const compareSimulations = compareStatisticalVariants

/* Generic sensitivity surface (S6 · Findings · data). Lanes without a
   real-ensemble sensitivity runner answer with a typed 501
   CAPABILITY_UNSUPPORTED envelope — the cockpit surfaces the reason. */
export async function runSensitivity (laneId, simulationId, payload = {}, idempotencyKey = null) {
  return unwrapEnvelope(await service.post(
    `${simulationBase(laneId, simulationId)}/sensitivity`,
    payload,
    idempotencyConfig(idempotencyKey)
  ))
}

export async function findSimulationStatus (simulationId, laneIds, preferredLaneId = null) {
  const uniqueLaneIds = [
    preferredLaneId,
    ...laneIds
  ].filter((laneId, index, all) => laneId && all.indexOf(laneId) === index)

  const errors = []
  for (const laneId of uniqueLaneIds) {
    try {
      const status = await getSimulationStatus(laneId, simulationId)
      return { laneId, status }
    } catch (error) {
      errors.push({ laneId, error })
    }
  }
  return { laneId: null, status: null, errors }
}
