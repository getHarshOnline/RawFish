<template>
  <!-- O1 · Evidence — a filterable sheet, always opened by a summoner and
       filtered to it. O2 · Inspector — one node, agent or act in full.
       Read-only drills; acting means returning to the step that owns the
       action (board 06). -->
  <RfSheet
    v-if="overlay"
    :badge="overlay.kind === 'claim' ? 'O1' : 'O2'"
    :title="overlay.kind === 'claim' ? 'Evidence' : 'Inspector'"
    :trail="overlayTrail"
    :address="'#' + overlay.hash"
    @back="popOverlay"
    @close="dismissOverlays"
  >
    <!-- O1 — evidence for one claim -->
    <template v-if="overlay.kind === 'claim'">
      <button
        v-for="ref in claimEvidence"
        :key="ref._key"
        type="button"
        class="rf-sheet__row"
        @click="drill(ref, $event)"
      >
        <span class="rf-sheet__row-tag">{{ ref.ref_type }}</span>
        <span class="rf-sheet__row-text">{{ refText(ref) }}</span>
        <span class="rf-sheet__row-meta">{{ ref.ref_id }}</span>
      </button>
      <div v-if="!claimEvidence.length" class="rf-sheet__pad">
        <p class="dock-prose">No evidence references are attached to this claim in the persisted report artifact.</p>
      </div>
    </template>

    <!-- O2 — one evidence ref / act in full -->
    <template v-else-if="overlay.kind === 'act' || overlay.kind === 'evidence'">
      <div class="rf-sheet__pad">
        <RfLabelRow label="ref" :value="overlay.id" />
        <RfLabelRow v-if="inspectedRef" label="type" :value="inspectedRef.ref_type || 'evidence'" />
        <RfLabelRow v-if="inspectedRef?.source" label="source" :value="inspectedRef.source" />
        <RfLabelRow v-if="inspectedRef?.path" label="path" :value="inspectedRef.path" />
        <template v-for="[key, value] in inspectedRefDetails" :key="key">
          <RfLabelRow :label="key" :value="value" tone="dim" />
        </template>
        <p v-if="!inspectedRef" class="dock-prose">This reference is not part of the loaded report artifact. Open Findings · read to load the report, then follow an evidence link.</p>
        <RfLabelRow v-if="citingClaims.length" label="cited by" :value="citingClaims.join(' · ')" tone="dim" />
      </div>
    </template>

    <!-- O2 — one graph node in full -->
    <template v-else-if="overlay.kind === 'node'">
      <div class="rf-sheet__pad">
        <RfLabelRow label="node" :value="inspectedNode?.name || overlay.id" />
        <RfLabelRow v-if="inspectedNode?.labels?.length" label="labels" :value="inspectedNode.labels.join(' · ')" tone="dim" />
        <RfLabelRow v-if="inspectedNode" label="degree" :value="String(nodeEdges.length)" tone="dim" />
        <p v-if="!inspectedNode" class="dock-prose">This node is not in the current world graph.</p>
      </div>
      <button
        v-for="edge in nodeEdges"
        :key="edge.uuid"
        type="button"
        class="rf-sheet__row"
        @click="drillNode(edge, $event)"
      >
        <span class="rf-sheet__row-tag">{{ edge.name }}</span>
        <span class="rf-sheet__row-text">{{ edge.fact || edge.fact_type }}</span>
        <span class="rf-sheet__row-meta">{{ edge.otherName }}</span>
      </button>
    </template>

    <!-- O2 — one cohort (the statistical layer) in full -->
    <template v-else>
      <div class="rf-sheet__pad">
        <RfLabelRow label="cohort" :value="inspectedCohort?.label || overlay.id" />
        <template v-for="row in cohortPriors" :key="row.id">
          <RfLabelRow :label="row.label" :value="row.value" tone="dim" />
        </template>
        <p v-if="!inspectedCohort" class="dock-prose">This cohort is not part of the current lane manifest.</p>
        <p v-else class="dock-prose">Priors are read-only here — tune them at Cohorts while the recipe is editable.</p>
      </div>
    </template>
  </RfSheet>
</template>

<script setup>
import { computed, inject } from 'vue'
import { RfLabelRow, RfSheet } from '../primitives/index.js'
import { RUN_STORE_KEY } from '../runStore.js'

const store = inject(RUN_STORE_KEY)
const {
  overlay,
  overlayTrail,
  popOverlay,
  dismissOverlays,
  summon,
  reportClaims,
  evidenceItems,
  runGraphData,
  cohorts,
  cohortStatisticalFields,
  fieldValueForCohort
} = store

/* O1 — the sheet opens already filtered to its summoner */
const claimEvidence = computed(() => {
  if (overlay.value?.kind !== 'claim') return []
  const claim = reportClaims.value.find(item => item.claim_id === overlay.value.id)
  const refs = Array.isArray(claim?.evidence_refs) ? claim.evidence_refs : []
  return refs.map((ref, index) => ({
    ...ref,
    ref_type: ref?.ref_type || ref?.type || 'evidence',
    ref_id: ref?.ref_id || ref?.id || ref?.path || `ref-${index}`,
    _key: `${overlay.value.id}-${index}`
  }))
})

function refText (ref) {
  return ref?.summary || ref?.text || ref?.fact || ref?.description || ref?.source || 'persisted evidence reference'
}

function drill (ref, event) {
  summon(`act:${encodeURIComponent(ref.ref_id)}`, event)
}

/* O2 — evidence ref detail */
const inspectedRef = computed(() => {
  if (!overlay.value || !['act', 'evidence'].includes(overlay.value.kind)) return null
  return evidenceItems.value.find(item => String(item.ref_id) === String(overlay.value.id)) || null
})

const inspectedRefDetails = computed(() => {
  if (!inspectedRef.value) return []
  const skip = new Set(['ref_id', 'ref_type', 'source', 'path', '_key', 'type', 'id'])
  return Object.entries(inspectedRef.value)
    .filter(([key, value]) => !skip.has(key) && ['string', 'number'].includes(typeof value))
    .slice(0, 6)
    .map(([key, value]) => [key.replace(/_/g, ' '), String(value)])
})

const citingClaims = computed(() => {
  if (!overlay.value || !['act', 'evidence'].includes(overlay.value.kind)) return []
  return reportClaims.value
    .filter(claim => (claim.evidence_refs || []).some(ref => (
      String(ref?.ref_id || ref?.id || ref?.path) === String(overlay.value.id)
    )))
    .map(claim => claim.claim_id)
    .slice(0, 4)
})

/* O2 — node detail from the live world graph */
const inspectedNode = computed(() => {
  if (overlay.value?.kind !== 'node') return null
  const nodes = runGraphData.value?.nodes || []
  return nodes.find(node => String(node.uuid) === String(overlay.value.id) || node.name === overlay.value.id) || null
})

const nodeEdges = computed(() => {
  if (!inspectedNode.value) return []
  const nodes = runGraphData.value?.nodes || []
  const byId = new Map(nodes.map(node => [node.uuid, node]))
  return (runGraphData.value?.edges || [])
    .filter(edge => edge.source_node_uuid === inspectedNode.value.uuid || edge.target_node_uuid === inspectedNode.value.uuid)
    .slice(0, 24)
    .map(edge => {
      const otherId = edge.source_node_uuid === inspectedNode.value.uuid ? edge.target_node_uuid : edge.source_node_uuid
      return { ...edge, otherName: byId.get(otherId)?.name || otherId }
    })
})

function drillNode (edge, event) {
  const otherId = edge.source_node_uuid === inspectedNode.value?.uuid ? edge.target_node_uuid : edge.source_node_uuid
  summon(`node:${encodeURIComponent(otherId)}`, event)
}

/* O2 — cohort priors, read-only */
const inspectedCohort = computed(() => {
  if (!overlay.value || !['cohort', 'agent'].includes(overlay.value.kind)) return null
  return cohorts.value.find(cohort => cohort.id === overlay.value.id) || null
})

const cohortPriors = computed(() => {
  if (!inspectedCohort.value) return []
  return cohortStatisticalFields.value.slice(0, 8).map(field => {
    const value = fieldValueForCohort(field, inspectedCohort.value.id)
    return {
      id: field.id,
      label: field.label || field.id,
      value: value === undefined || value === null || value === ''
        ? 'inherits manifest default'
        : Array.isArray(value) ? `${value.length} entries` : String(value)
    }
  })
})
</script>
