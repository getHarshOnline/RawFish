<template>
  <!-- S3 · Cohorts — who reacts: priors per cohort and sampled agents.
       Scale lives in Preflight. Priors patch through fields; every edit resets readiness
       honestly (board 04). Identity is weight and position — never hue. -->
  <RfStepHeading num="03" title="Cohorts" hint="priors · agents · salts" />

  <RfPanel v-if="!activeRunId" title="Needs a built world">
    <p class="dock-prose">Cohorts sample against the world graph. Build creates the run and mounts the stage; then priors patch through these fields.</p>
    <template #foot>
      <RfButton role="primary" @click="goStep('build')">Build the world →</RfButton>
    </template>
  </RfPanel>

  <template v-else>
    <div v-if="!canEditWorkbench" class="chip-row">
      <RfChip variant="selected">{{ workbenchGateReason }}</RfChip>
      <RfButton v-if="isFrozen" role="quiet" @click="duplicateAsDraft">duplicate as draft →</RfButton>
    </div>

    <div class="cohort-grid" aria-label="Cohorts">
      <button
        v-for="card in cohortCards"
        :key="card.id"
        type="button"
        :class="['rf-card', 'cohort-card', card.id === selectedCohortId ? 'rf-card--active' : null]"
        @click="selectedCohortId = card.id"
      >
        <span class="cohort-card__title">{{ card.label }}</span>
        <RfMeter v-if="card.activity" label="activity" :display="card.activity.display" :pct="card.activity.pct" />
        <RfMeter v-if="card.influence" label="influence" :display="card.influence.display" :pct="card.influence.pct" />
        <RfSentimentTrack v-if="card.sentiment" label="sentiment" :value="card.sentiment" />
        <span class="rf-meta">{{ card.tuned }} / {{ card.total }} tuned · {{ card.note }}</span>
      </button>
    </div>

    <RfPanel v-if="selectedCohort && cohortStatisticalFields.length" :title="selectedCohort.label">
      <template #meta>{{ selectedCohort.id }} · {{ cohortStatisticalFields.length }} priors</template>
      <WorkbenchField
        v-for="field in cohortStatisticalFields"
        :key="field.id"
        :field="field"
        :frozen="!canEditWorkbench"
        :frozen-reason="workbenchGateReason || frozenReason"
      />
    </RfPanel>

    <RfPanel v-if="runStatisticalFields.length" title="Run settings">
      <template #meta>seed · salts · priors</template>
      <WorkbenchField
        v-for="field in runStatisticalFields"
        :key="field.id"
        :field="field"
        :frozen="!canEditWorkbench"
        :frozen-reason="workbenchGateReason || frozenReason"
      />
    </RfPanel>

    <RfPanel v-if="!statisticalFields.length" title="No statistical fields">
      <p class="dock-prose">This lane has no workbench fields; continue to Preflight with manifest defaults.</p>
    </RfPanel>

    <RfLabelRow v-if="statisticalError" label="workbench" :value="statisticalError" tone="danger" />
    <RfLabelRow v-if="statisticalVariantError" label="variants" :value="statisticalVariantError" tone="danger" />

    <div class="chip-row" aria-label="Settings provenance">
      <RfChip variant="selected">{{ shortHash }}</RfChip>
      <RfChip v-for="variant in variantChips" :key="variant.id">{{ variant.label }}</RfChip>
      <RfChip v-if="statisticalLineage.length">lineage · {{ statisticalLineage.length }}</RfChip>
    </div>

    <div class="dock-actions">
      <RfButton
        :disabled="!canEditWorkbench || statisticalSaveLoading"
        :reason="canEditWorkbench ? '' : workbenchGateReason"
        @click="saveWorkbenchPatch"
      >
        {{ statisticalSaveLoading ? 'Saving…' : 'Save patch' }}
      </RfButton>
      <RfButton
        :disabled="!canEditWorkbench || statisticalReseedLoading"
        :reason="canEditWorkbench ? '' : workbenchGateReason"
        @click="reseedWorkbench"
      >
        {{ statisticalReseedLoading ? 'Reseeding…' : 'Reseed variant' }}
      </RfButton>
      <span class="spacer"></span>
      <RfButton role="primary" @click="goStep('preflight')">Continue to preflight →</RfButton>
    </div>
  </template>
</template>

<script setup>
import { computed, inject } from 'vue'
import { RfButton, RfChip, RfLabelRow, RfMeter, RfPanel, RfSentimentTrack, RfStepHeading } from '../primitives/index.js'
import WorkbenchField from './WorkbenchField.vue'
import { RUN_STORE_KEY } from '../runStore.js'

const store = inject(RUN_STORE_KEY)
const {
  activeRunId,
  cohorts,
  selectedCohortId,
  selectedCohort,
  statisticalFields,
  cohortStatisticalFields,
  runStatisticalFields,
  fieldValueForCohort,
  statisticalError,
  statisticalVariantError,
  statisticalVariants,
  statisticalLineage,
  statisticalSaveLoading,
  statisticalReseedLoading,
  saveWorkbenchPatch,
  reseedWorkbench,
  canEditWorkbench,
  workbenchGateReason,
  shortHash,
  isFrozen,
  frozenReason,
  duplicateAsDraft,
  goStep
} = store

function meterFor (field, cohortId) {
  if (!field) return null
  const raw = Number(fieldValueForCohort(field, cohortId))
  if (!Number.isFinite(raw)) return null
  const min = Number.isFinite(Number(field.min)) ? Number(field.min) : 0
  const max = Number.isFinite(Number(field.max)) ? Number(field.max) : 1
  const pct = max > min ? Math.round(((raw - min) / (max - min)) * 100) : 0
  return { display: String(raw), pct }
}

const cohortCards = computed(() => cohorts.value.map(cohort => {
  const fields = cohortStatisticalFields.value
  const byId = id => fields.find(field => field.id === id)
  const sentimentRaw = Number(fieldValueForCohort(byId('cohort.sentiment_bias'), cohort.id))
  const tuned = fields.filter(field => fieldValueForCohort(field, cohort.id) !== undefined).length
  return {
    id: cohort.id,
    label: cohort.label,
    activity: meterFor(byId('cohort.activity_level'), cohort.id),
    influence: meterFor(byId('cohort.influence_weight'), cohort.id),
    sentiment: Number.isFinite(sentimentRaw) ? sentimentRaw : null,
    tuned,
    total: fields.length,
    note: fields.length ? 'priors patchable' : 'manifest defaults'
  }
}))

const variantChips = computed(() => statisticalVariants.value.slice(0, 4).map(variant => ({
  id: variant.variant_id || variant.statistical_settings_hash,
  label: `${variant.label || variant.variant_id} · ${short(variant.statistical_settings_hash)}`
})))

function short (hash) {
  if (!hash) return 'hash pending'
  return hash.length > 14 ? `${hash.slice(0, 12)}…` : hash
}
</script>
