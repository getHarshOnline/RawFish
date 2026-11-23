<template>
  <!-- S1 · Scenario — the recipe: stress question, source posture, cohort
       plan, readiness. The dock is alone and centered — no world exists yet
       (board 04). -->
  <RfStepHeading num="01" title="Scenario" hint="question · posture · cohorts" />

  <div v-if="isFrozen" class="chip-row">
    <RfChip variant="selected">{{ frozenReason }}</RfChip>
    <RfButton role="quiet" @click="duplicateAsDraft">duplicate as draft →</RfButton>
  </div>

  <RfPanel title="Stress question">
    <template #meta>{{ seedChars }} chars · {{ parseState }}</template>
    <RfField
      v-if="!isFrozen"
      meta-left="commit on blur · esc reverts"
      :error="parseError"
    >
      <textarea
        v-model="scenarioText"
        rows="6"
        placeholder="We're shipping… worried long-time fans read the change as abandonment while we chase a younger audience."
      ></textarea>
    </RfField>
    <p v-else class="dock-prose">{{ scenarioText }}</p>
    <template #foot>
      <RfButton
        v-if="!isFrozen"
        role="quiet"
        :disabled="parseLoading || !scenarioText.trim()"
        :reason="scenarioText.trim() ? '' : 'paste a draft first'"
        @click="parseSeed"
      >
        {{ parseLoading ? 'parsing…' : 'parse seed →' }}
      </RfButton>
      <RfChip v-if="scenarioParse" variant="ok">parsed · {{ scenarioParse.parse_source }} · {{ scenarioParse.parser }}</RfChip>
    </template>
  </RfPanel>

  <RfPanel title="Source posture">
    <template #meta>one concept, one control</template>
    <RfEnumChips v-model="sourceMode" :options="sourceOptions" :disabled="isFrozen" />
    <p class="dock-prose">Paste is the honest default. A prepared graph reuses an existing world in the same cockpit.</p>
  </RfPanel>

  <RfPanel title="Cohort plan">
    <template #meta>{{ cohorts.length }} cohorts · lane manifest</template>
    <RfLabelRow
      v-for="cohort in cohorts"
      :key="cohort.id"
      :label="cohort.shortLabel"
      :value="cohort.label"
    />
    <p class="dock-prose">Priors tune at Cohorts once the world is built. Every committed edit resets readiness honestly.</p>
  </RfPanel>

  <div class="chip-row" aria-label="Readiness">
    <RfChip
      v-for="item in readiness"
      :key="item.id"
      :variant="item.ok ? 'ok' : 'pending'"
    >
      {{ item.label }}
    </RfChip>
    <RfChip v-if="catalogError" variant="caution" :reason="catalogError">backend catalog</RfChip>
  </div>

  <div class="dock-actions">
    <span class="spacer"></span>
    <RfButton
      role="primary"
      :disabled="Boolean(buildGate)"
      :reason="buildGate"
      @click="goStep('build')"
    >
      Build the world →
    </RfButton>
  </div>
</template>

<script setup>
import { computed, inject } from 'vue'
import { RfButton, RfChip, RfEnumChips, RfField, RfLabelRow, RfPanel, RfStepHeading } from '../primitives/index.js'
import { RUN_STORE_KEY } from '../runStore.js'

const store = inject(RUN_STORE_KEY)
const {
  scenarioText,
  scenarioParse,
  parseLoading,
  parseError,
  parseSeed,
  sourceModes,
  sourceMode,
  cohorts,
  readiness,
  catalogError,
  isFrozen,
  frozenReason,
  duplicateAsDraft,
  goStep,
  stepGateReason
} = store

const sourceOptions = sourceModes
const seedChars = computed(() => scenarioText.value.trim().length)
const parseState = computed(() => {
  if (scenarioParse.value) return 'parsed'
  return seedChars.value > 0 ? 'parse-ready' : 'seed pending'
})
const buildGate = computed(() => stepGateReason('build'))
</script>
