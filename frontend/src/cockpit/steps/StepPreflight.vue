<template>
  <!-- S4 · Preflight — the deliberate act: scale budget, provider, cost
       estimate, then the boot sequence — create → prepare → start,
       idempotency-keyed, retry-safe (board 04). -->
  <RfStepHeading num="04" title="Preflight" hint="a costed, deliberate act" />

  <RfPanel title="Scale budget">
    <template #meta>{{ preflightScaleProduct }} / {{ scaleCap }}</template>
    <RfMeter
      label="scale"
      variant="budget"
      :pct="scalePct"
      :over="scaleOver"
    >
      <template #value><strong>{{ preflightScaleProduct }}</strong> / {{ scaleCap }}</template>
    </RfMeter>
    <RfField label="Ensemble N" :frozen="launchFrozen" :frozen-value="preflight.ensemble_n" :frozen-reason="frozenReason">
      <RfNumberField
        :model-value="preflight.ensemble_n"
        :min="1"
        :max="40"
        @update:model-value="preflight.ensemble_n = $event"
      />
    </RfField>
    <RfField label="Rounds" :frozen="launchFrozen" :frozen-value="preflight.rounds" :frozen-reason="frozenReason">
      <RfNumberField
        :model-value="preflight.rounds"
        :min="4"
        :max="64"
        @update:model-value="preflight.rounds = $event"
      />
    </RfField>
    <RfLabelRow label="cohorts" :value="String(stakeholderCount)" />
    <RfLabelRow label="token est." :value="tokenEstimate.toLocaleString()" />
  </RfPanel>

  <RfPanel title="Provider">
    <template #meta>{{ providerReadiness }}</template>
    <RfField label="Provider" :frozen="launchFrozen" :frozen-value="provider.mode" :frozen-reason="frozenReason">
      <RfEnumChips v-model="provider.mode" :options="providerOptions" />
    </RfField>
    <RfField v-if="!launchFrozen" label="Model">
      <input v-model="provider.model" type="text" class="rf-input--machine">
    </RfField>
    <RfField v-if="!launchFrozen && provider.mode === 'external'" label="Provider id">
      <input v-model="provider.provider" type="text" class="rf-input--machine" placeholder="anthropic · openai · azure">
    </RfField>
    <RfField v-if="!launchFrozen" label="Base url">
      <input
        v-model="provider.base_url"
        type="text"
        class="rf-input--machine"
        :placeholder="provider.mode === 'local_ollama' ? 'http://localhost:11434' : 'http://localhost:1234/v1'"
      >
    </RfField>
    <div class="rf-checks">
      <label>
        <input v-model="provider.probe_provider" type="checkbox" :disabled="launchFrozen">
        Probe the provider before launch
      </label>
    </div>
    <RfLabelRow label="latency" :value="latencyCopy" tone="dim" />
    <RfLabelRow label="ext. cost" :value="costCopy" tone="dim" />
    <RfLabelRow label="budget" :value="budgetCopy" tone="dim" />
    <RfLabelRow v-if="preflightError" label="preflight" :value="preflightError" tone="danger" />
  </RfPanel>

  <RfPanel title="Boot sequence">
    <template #meta>idempotency-keyed · retry-safe</template>
    <div>
      <div v-for="step in bootSteps" :key="step.verb" class="boot-row">
        <span class="boot-row__num">{{ step.num }}</span>
        <span class="boot-row__verb">{{ step.verb }}</span>
        <span :class="['boot-row__state', step.state === 'done' ? 'boot-row__state--done' : null, step.state === 'active' ? 'boot-row__state--active' : null]">{{ step.state }}</span>
        <span class="boot-row__note">{{ step.note }}</span>
      </div>
    </div>
    <RfLabelRow v-if="lifecycleError" label="halted" :value="lifecycleError" tone="danger" />
  </RfPanel>

  <div class="dock-actions">
    <RfButton role="quiet" :disabled="preflightLoading" @click="refreshPreflight()">
      {{ preflightLoading ? 'checking…' : 'refresh preflight →' }}
    </RfButton>
    <span class="spacer"></span>
    <RfButton
      role="primary"
      :disabled="isBusy || preflightLoading || Boolean(launchGateReason)"
      :reason="launchGateReason"
      @click="launchRun"
    >
      {{ isBusy ? 'Launching…' : 'Launch swarm →' }}
    </RfButton>
  </div>
</template>

<script setup>
import { computed, inject } from 'vue'
import { RfButton, RfEnumChips, RfField, RfLabelRow, RfMeter, RfNumberField, RfPanel, RfStepHeading } from '../primitives/index.js'
import { RUN_STORE_KEY } from '../runStore.js'

const store = inject(RUN_STORE_KEY)
const {
  preflight,
  provider,
  preflightLoading,
  preflightError,
  refreshPreflight,
  stakeholderCount,
  scaleCap,
  preflightScaleProduct,
  tokenEstimate,
  providerReadiness,
  latencyCopy,
  costCopy,
  budgetCopy,
  launchGateReason,
  launchRun,
  isBusy,
  isFrozen,
  frozenReason,
  lifecycleError,
  backendPhase
} = store

const providerOptions = [
  { id: 'local_ollama', label: 'local ollama' },
  { id: 'local_openai_compatible', label: 'local OAI' },
  { id: 'external', label: 'external' }
]

const scalePct = computed(() => (scaleCap.value > 0 ? (preflightScaleProduct.value / scaleCap.value) * 100 : 0))
const scaleOver = computed(() => preflightScaleProduct.value > scaleCap.value)
const launchFrozen = computed(() => isFrozen.value)

const bootSteps = computed(() => {
  const phaseNow = backendPhase.value
  const doneCreate = Boolean(phaseNow) && phaseNow !== null
  const doneStates = ['prepared', 'starting', 'running', 'failed', 'completed', 'reported']
  const startedStates = ['starting', 'running', 'failed', 'completed', 'reported']
  return [
    {
      num: '1',
      verb: 'create',
      state: doneCreate && phaseNow !== 'unknown' ? 'done' : 'waiting',
      note: 'recipe locked into a run record'
    },
    {
      num: '2',
      verb: 'prepare',
      state: doneStates.includes(phaseNow) ? 'done' : (phaseNow === 'created' ? 'active' : 'waiting'),
      note: 'world + provider readied'
    },
    {
      num: '3',
      verb: 'start',
      state: startedStates.includes(phaseNow) ? 'done' : (phaseNow === 'prepared' ? 'active' : 'waiting'),
      note: 'runner attaches · rounds begin'
    }
  ]
})
</script>
