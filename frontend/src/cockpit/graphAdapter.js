function normalizeNode (node, index) {
  if (!node || typeof node !== 'object') return null
  const uuid = node.uuid || node.id || node.node_id || node.key || `node-${index}`
  const labels = Array.isArray(node.labels)
    ? node.labels
    : ['Entity', node.type || node.kind || 'RunNode'].filter(Boolean)
  return {
    ...node,
    uuid: String(uuid),
    name: node.name || node.label || node.title || String(uuid),
    labels,
    attributes: node.attributes || node.properties || node.meta || {}
  }
}

function normalizeEdge (edge, index) {
  if (!edge || typeof edge !== 'object') return null
  const source = edge.source_node_uuid || edge.source || edge.source_id || edge.from
  const target = edge.target_node_uuid || edge.target || edge.target_id || edge.to
  if (!source || !target) return null
  const name = edge.name || edge.label || edge.type || edge.fact_type || 'RELATED_TO'
  return {
    ...edge,
    uuid: String(edge.uuid || edge.id || `edge-${index}-${source}-${target}`),
    source_node_uuid: String(source),
    target_node_uuid: String(target),
    name,
    fact_type: edge.fact_type || edge.type || name,
    fact: edge.fact || edge.description || edge.summary || ''
  }
}

function normalizeGraphCandidate (candidate, source) {
  if (!candidate || typeof candidate !== 'object') return null
  const rawNodes = Array.isArray(candidate.nodes) ? candidate.nodes : []
  const rawEdges = Array.isArray(candidate.edges) ? candidate.edges : []
  if (!rawNodes.length) return null
  const nodes = rawNodes.map(normalizeNode).filter(Boolean)
  const nodeIds = new Set(nodes.map(node => node.uuid))
  const edges = rawEdges
    .map(normalizeEdge)
    .filter(edge => edge && nodeIds.has(edge.source_node_uuid) && nodeIds.has(edge.target_node_uuid))
  return {
    source,
    graphData: { nodes, edges }
  }
}

function graphCandidatesFromArtifacts (artifacts) {
  if (!artifacts) return []
  if (artifacts.graph || artifacts.graph_data || artifacts.artifact_graph) {
    return [
      ['artifacts.graph', artifacts.graph],
      ['artifacts.graph_data', artifacts.graph_data],
      ['artifacts.artifact_graph', artifacts.artifact_graph]
    ]
  }
  if (!Array.isArray(artifacts)) return []
  return artifacts
    .map((artifact, index) => {
      if (!artifact || typeof artifact !== 'object') return null
      const payload = artifact.graph || artifact.graph_data || artifact.data || artifact.payload
      const kind = artifact.kind || artifact.type || artifact.name || `artifact-${index}`
      return payload ? [`artifact:${kind}`, payload] : null
    })
    .filter(Boolean)
}

export function preferredGraphFromPayloads (...payloads) {
  const candidates = []
  for (const [payload, prefix] of payloads) {
    if (!payload || typeof payload !== 'object') continue
    candidates.push(
      [`${prefix}.graph`, payload.graph],
      [`${prefix}.graph_data`, payload.graph_data],
      [`${prefix}.artifact_graph`, payload.artifact_graph],
      [`${prefix}.runtime_graph`, payload.runtime_graph],
      [`${prefix}.knowledge_graph`, payload.knowledge_graph]
    )
    graphCandidatesFromArtifacts(payload.artifacts)
      .forEach(([source, graph]) => candidates.push([`${prefix}.${source}`, graph]))
  }
  for (const [source, candidate] of candidates) {
    const normalized = normalizeGraphCandidate(candidate, source)
    if (normalized) return normalized
  }
  return null
}

export function manifestGraph (manifest, runId, lifecyclePhase, statisticalHash) {
  const cohorts = manifest?.cohorts || []
  if (!cohorts.length) return null
  const rootId = runId || `${manifest?.lane_id || 'lane'}-draft`
  const nodes = [
    {
      uuid: rootId,
      name: runId || 'Draft run',
      labels: ['Entity', 'Run'],
      attributes: {
        lane: manifest?.lane_id,
        phase: lifecyclePhase,
        statistical_settings_hash: statisticalHash || 'draft'
      }
    },
    ...cohorts.map(cohort => ({
      uuid: `${rootId}-${cohort.id}`,
      name: cohort.label,
      labels: ['Entity', 'Cohort'],
      attributes: {
        short_label: cohort.short_label,
        question: cohort.question || cohort.cohort_question || ''
      },
      summary: cohort.description || cohort.summary || ''
    }))
  ]
  const edges = cohorts.map(cohort => ({
    uuid: `${rootId}-${cohort.id}-edge`,
    source_node_uuid: rootId,
    target_node_uuid: `${rootId}-${cohort.id}`,
    name: 'TESTS_AGAINST',
    fact_type: 'cockpit_manifest_slot',
    fact: `${manifest?.label || 'RawFish'} run includes ${cohort.label}`,
    episodes: [lifecyclePhase]
  }))
  return { source: 'manifest fallback', graphData: { nodes, edges } }
}

export function cockpitGraph (statusPayload, reportPayload, manifest, runId, lifecyclePhase, statisticalHash) {
  return preferredGraphFromPayloads(
    [statusPayload, 'status'],
    [reportPayload, 'report']
  ) || manifestGraph(manifest, runId, lifecyclePhase, statisticalHash)
}
