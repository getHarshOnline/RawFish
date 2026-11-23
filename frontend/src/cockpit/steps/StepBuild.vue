<template>
  <!-- S2 · Build — ontology extracted, GraphRAG assembles the world. The
       stage mounts here, takes the width it deserves, and never leaves
       (board 04). -->
  <RfStepHeading num="02" title="Build" hint="ontology → graphrag world" />

  <template v-if="!activeRunId">
    <RfPanel title="What build does">
      <p class="dock-prose"><strong>Build creates the run.</strong> The seed becomes an ontology, GraphRAG assembles the knowledge-graph world, and the stage mounts beside this dock. The recipe stays editable until launch.</p>
      <RfLabelRow label="lane" :value="manifest?.label || '—'" />
      <RfLabelRow label="cohorts" :value="String(cohorts.length)" />
      <RfLabelRow label="posture" :value="sourceMode" />
      <RfLabelRow v-if="lifecycleError" label="halted" :value="lifecycleError" tone="danger" />
    </RfPanel>

    <div class="dock-actions">
      <RfButton role="quiet" @click="goStep('scenario')">revise scenario →</RfButton>
      <span class="spacer"></span>
      <RfButton
        role="primary"
        :disabled="isBusy || Boolean(buildGate)"
        :reason="buildGate"
        @click="buildWorld"
      >
        {{ isBusy ? 'Building the world…' : 'Build the world →' }}
      </RfButton>
    </div>
  </template>

  <template v-else>
    <RfPanel title="World">
      <template #meta>{{ runGraphSource?.source || 'manifest graph' }}</template>
      <div class="cohort-grid">
        <RfMetric :value="graphStats.nodes" label="entity nodes" />
        <RfMetric :value="graphStats.edges" label="relation edges" />
      </div>
      <RfLabelRow label="run" :value="activeRunId" />
      <RfLabelRow label="phase" :value="phase" />
      <RfLabelRow label="schema types" :value="String(graphStats.schemaTypes)" tone="dim" />
      <RfLabelRow v-if="graphError" label="stage" :value="graphError" tone="danger" />
      <p class="dock-prose">Click a node on the stage to inspect it. The world evolves as rounds commit.</p>
    </RfPanel>

    <div class="dock-actions">
      <RfButton role="quiet" @click="refreshStage">refresh stage →</RfButton>
      <span class="spacer"></span>
      <RfButton role="primary" @click="goStep('cohorts')">Tune cohorts →</RfButton>
    </div>
  </template>
</template>

<script setup>
import { computed, inject } from 'vue'
import { RfButton, RfLabelRow, RfMetric, RfPanel, RfStepHeading } from '../primitives/index.js'
import { RUN_STORE_KEY } from '../runStore.js'

const store = inject(RUN_STORE_KEY)
const {
  activeRunId,
  manifest,
  cohorts,
  sourceMode,
  lifecycleError,
  isBusy,
  buildWorld,
  goStep,
  stepGateReason,
  graphStats,
  graphError,
  runGraphSource,
  phase,
  refreshStage
} = store

const buildGate = computed(() => stepGateReason('build'))
</script>
