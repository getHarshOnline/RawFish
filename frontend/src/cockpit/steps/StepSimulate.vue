<template>
  <!-- S5 · Simulate — rounds commit and the stage pulses; the timeline
       streams acts; the console is live (board 04). Run metrics live in
       the console, not here. -->
  <RfStepHeading num="05" title="Simulate" :hint="phase" />

  <div v-if="isHalted" class="chip-row">
    <RfChip variant="caution">halted — the console owns the reason</RfChip>
    <RfButton role="quiet" @click="duplicateAsDraft">duplicate as draft →</RfButton>
  </div>

  <RfPanel v-if="!activeRunId" title="No run attached">
    <p class="dock-prose">Launch is the deliberate act — it lives at Preflight. Once the swarm starts, rounds stream here and the stage pulses with acts.</p>
    <template #foot>
      <RfButton role="primary" @click="goStep('preflight')">Go to preflight →</RfButton>
    </template>
  </RfPanel>

  <RfPanel v-else title="Timeline">
    <template #meta>{{ feedRows.length }} events · newest last</template>
    <div class="feed">
      <template v-for="row in feedRows" :key="row.id">
        <button
          v-if="row.hash"
          type="button"
          class="feed__row"
          @click="summon(row.hash, $event)"
        >
          <span :class="['feed__tag', row.kindClass]">{{ row.tag }}</span>
          <span class="feed__msg">{{ row.msg }}</span>
        </button>
        <div v-else class="feed__row">
          <span :class="['feed__tag', row.kindClass]">{{ row.tag }}</span>
          <span class="feed__msg">{{ row.msg }}</span>
        </div>
      </template>
    </div>
    <template #foot>
      <RfButton role="quiet" @click="refreshStatus()">refresh status →</RfButton>
      <RfChip v-if="isStreaming" variant="accent" dot>streaming</RfChip>
    </template>
  </RfPanel>

  <div v-if="activeRunId" class="dock-actions">
    <span class="spacer"></span>
    <RfButton
      role="primary"
      :disabled="Boolean(findingsGate)"
      :reason="findingsGate"
      @click="goStep('findings', 'read')"
    >
      Open findings →
    </RfButton>
  </div>
</template>

<script setup>
import { computed, inject } from 'vue'
import { RfButton, RfChip, RfPanel, RfStepHeading } from '../primitives/index.js'
import { RUN_STORE_KEY } from '../runStore.js'

const store = inject(RUN_STORE_KEY)
const {
  activeRunId,
  phase,
  isHalted,
  isStreaming,
  timeline,
  refreshStatus,
  goStep,
  stepGateReason,
  duplicateAsDraft,
  summon
} = store

const findingsGate = computed(() => stepGateReason('findings'))

const feedRows = computed(() => timeline.value.map((event, index) => {
  let hash = null
  if (event.severity === 'claim' && String(event.id).startsWith('report-claim-')) {
    hash = `claim:${encodeURIComponent(String(event.id).slice('report-claim-'.length))}/acts`
  }
  if (event.severity === 'evidence' && String(event.id).startsWith('evidence-')) {
    hash = `act:${encodeURIComponent(String(event.id).slice('evidence-'.length))}`
  }
  return {
    id: event.id || `event-${index}`,
    tag: event.source,
    msg: event.copy,
    hash,
    kindClass: ['error', 'warning'].includes(event.severity)
      ? 'feed__tag--danger'
      : ['active', 'claim', 'evidence'].includes(event.severity) ? 'feed__tag--accent' : null
  }
}))
</script>
