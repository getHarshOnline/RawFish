const laneManifestModules = import.meta.glob(
  '../lanes/*/lane_manifest.json',
  { eager: true }
)

export const bundledLaneManifests = Object.values(laneManifestModules)
  .map(module => module.default || module)
  .filter(lane => lane && lane.lane_id)
  .sort((left, right) => left.lane_id.localeCompare(right.lane_id))

export const laneManifests = bundledLaneManifests
export const laneIds = laneManifests.map(lane => lane.lane_id)

export function getLaneManifest (laneId) {
  return laneManifests.find(lane => lane.lane_id === laneId) || null
}

export function findLaneManifest (laneId) {
  return getLaneManifest(laneId) || laneManifests[0]
}

export function mergeLaneManifest (laneId, backendManifest) {
  const bundledManifest = getLaneManifest(laneId)
  if (!bundledManifest && !backendManifest) return null
  if (!bundledManifest) return backendManifest
  if (!backendManifest) return bundledManifest
  return {
    ...bundledManifest,
    ...backendManifest,
    accent: bundledManifest.accent,
    cohorts: bundledManifest.cohorts,
    default_provider: bundledManifest.default_provider,
    run_timeline_platforms: bundledManifest.run_timeline_platforms,
    report_sections: backendManifest.report_sections?.length
      ? backendManifest.report_sections
      : bundledManifest.report_sections,
    analysis_panels: backendManifest.analysis_panels || bundledManifest.analysis_panels
  }
}

export function laneManifestsFromIndex (indexPayload) {
  const discovered = (indexPayload?.use_cases || [])
    .map(row => mergeLaneManifest(
      row.use_case_id || row.lane_manifest?.lane_id,
      row.lane_manifest
    ))
    .filter(lane => lane && lane.lane_id)
    .sort((left, right) => left.lane_id.localeCompare(right.lane_id))
  return discovered.length ? discovered : bundledLaneManifests
}

export function laneIdsFromManifests (manifests) {
  return (manifests || [])
    .map(lane => lane?.lane_id)
    .filter(Boolean)
}
