<template>
  <!-- S6 · Findings — read | ask | data. The report never outruns its
       evidence: claims cite acts, targets flash, hashes prove the rest
       (boards 04/11). -->
  <RfStepHeading num="06" title="Findings">
    <template #aside>
      <RfEnumChips
        :model-value="findingsMode"
        :options="modeOptions"
        @update:model-value="goStep('findings', $event)"
      />
    </template>
  </RfStepHeading>

  <!-- ······································································ read -->
  <template v-if="findingsMode === 'read'">
    <div class="chip-row" aria-label="Provenance">
      <RfChip variant="selected">config · {{ shortHash }}</RfChip>
      <RfChip>{{ phase }}</RfChip>
      <RfChip v-if="reportPayload" variant="ok">report artifact loaded</RfChip>
    </div>

    <RfPanel v-if="!reportPayload" title="Report">
      <p class="dock-prose">{{ reportNotice || 'The report streams once the run completes — tagged claims, validity flags, evidence links into the world.' }}</p>
      <RfLabelRow v-if="reportError" label="report" :value="reportError" tone="danger" />
      <template #foot>
        <RfButton
          role="primary"
          :disabled="isBusy || Boolean(reportGateReason)"
          :reason="reportGateReason"
          @click="generateReport"
        >
          {{ isBusy ? 'Generating…' : 'Generate report →' }}
        </RfButton>
        <RfButton role="quiet" :disabled="!activeRunId" @click="loadReport()">refresh →</RfButton>
      </template>
    </RfPanel>

    <template v-else>
      <RfPanel
        v-for="claim in reportClaims"
        :key="claim.claim_id"
        :title="claim.title"
        hairline
      >
        <template #meta>{{ claim.claim_id }}</template>
        <div class="chip-row">
          <RfChip variant="selected">{{ claim.tag }}</RfChip>
          <RfChip
            v-for="flag in flagsFor(claim.claim_id)"
            :key="flag.flag_id || flag.code || flag.text"
            variant="caution"
            :reason="flag.text || flag.message"
          >
            {{ flag.code || flag.flag_id || 'validity' }}
          </RfChip>
        </div>
        <p class="claim__text">{{ claim.text }}</p>
        <template #foot>
          <RfButton
            role="quiet"
            :disabled="!(claim.evidence_refs || []).length"
            @click="summon(`claim:${encodeURIComponent(claim.claim_id)}/acts`, $event)"
          >
            evidence · {{ (claim.evidence_refs || []).length }} →
          </RfButton>
          <RfChip v-if="(claim.driver_hints || []).length">drivers · {{ claim.driver_hints.join(' · ') }}</RfChip>
        </template>
      </RfPanel>
      <div class="dock-actions">
        <RfButton role="quiet" @click="loadReport()">refresh report →</RfButton>
      </div>
    </template>
  </template>

  <!-- ······································································· ask -->
  <template v-else-if="findingsMode === 'ask'">
    <RfPanel title="Interview the run">
      <template #meta>report agent · grounded in this run</template>
      <div v-if="askHistory.length" class="ask-thread">
        <div
          v-for="(turn, index) in askHistory"
          :key="index"
          :class="['ask-turn', turn.role === 'user' ? 'ask-turn--user' : null]"
        >
          <span :class="['ask-turn__role', turn.role !== 'user' ? 'ask-turn__role--agent' : null]">{{ turn.role === 'user' ? 'you' : 'agent' }}</span>
          <p class="ask-turn__text">{{ turn.content }}</p>
        </div>
      </div>
      <p v-else class="dock-prose">Ask the ReportAgent anything about this run — it answers from the persisted artifacts, never beyond them.</p>
      <RfLabelRow v-if="askError" label="ask" :value="askError" tone="danger" />
      <RfField v-if="canAsk" label="Question">
        <textarea
          v-model="askInput"
          rows="3"
          placeholder="Which cohort drove the objection, and when did it become the dominant frame?"
          @keydown.enter.exact.prevent="sendAsk()"
        ></textarea>
      </RfField>
      <template #foot>
        <RfButton
          role="primary"
          :disabled="!canAsk || askSending || !askInput.trim()"
          :reason="canAsk ? (askInput.trim() ? '' : 'type a question first') : 'needs a loaded report — open read first'"
          @click="sendAsk()"
        >
          {{ askSending ? 'Asking…' : 'Ask →' }}
        </RfButton>
      </template>
    </RfPanel>
  </template>

  <!-- ······································································ data -->
  <template v-else>
    <RfPanel title="Ensemble & provenance">
      <RfLabelRow label="settings" :value="statisticalHash || 'unknown'" />
      <RfLabelRow label="variants" :value="String(statisticalVariants.length)" />
      <RfLabelRow label="lineage" :value="String(statisticalLineage.length)" tone="dim" />
      <div v-if="statisticalVariants.length" class="chip-row">
        <RfChip v-for="variant in statisticalVariants.slice(0, 6)" :key="variant.variant_id">
          {{ variant.label || variant.variant_id }}
        </RfChip>
      </div>
    </RfPanel>

    <RfPanel title="Compare">
      <template #meta>{{ latestVariant ? `base vs ${latestVariant.variant_id}` : 'base only' }}</template>
      <template v-if="latestVariant && analysisComparePayload">
        <RfLabelRow label="stat diffs" :value="String(compareDiffCount)" />
        <RfLabelRow label="present" :value="String(bucket('present'))" />
        <RfLabelRow label="missing" :value="String(bucket('missing'))" :tone="bucket('missing') ? 'danger' : 'dim'" />
        <RfLabelRow label="corrupted" :value="String(bucket('corrupted'))" :tone="bucket('corrupted') ? 'danger' : 'dim'" />
      </template>
      <p v-else class="dock-prose">Only the base settings are present. Reseed from Cohorts to record a variant, then compare lands here.</p>
      <RfLabelRow v-if="analysisError" label="compare" :value="analysisError" tone="danger" />
      <template #foot>
        <RfButton role="quiet" :disabled="analysisLoading || !activeRunId" @click="loadAnalysisArtifacts()">
          {{ analysisLoading ? 'comparing…' : 'refresh compare →' }}
        </RfButton>
      </template>
    </RfPanel>

    <RfPanel title="Sensitivity">
      <template #meta>OAT · real-ensemble re-runs</template>
      <template v-if="sensitivityPayload">
        <RfLabelRow
          v-for="row in sensitivityRows"
          :key="row.label"
          :label="row.label"
          :value="row.value"
        />
      </template>
      <p v-else class="dock-prose">A capped sweep re-runs the ensemble one factor at a time and ranks what moves the outcome.</p>
      <RfLabelRow v-if="sensitivityError" label="sweep" :value="sensitivityError" tone="danger" />
      <template #foot>
        <RfButton
          role="quiet"
          :disabled="sensitivityLoading || !activeRunId"
          @click="runSensitivitySweep"
        >
          {{ sensitivityLoading ? 'sweeping…' : 'run sweep →' }}
        </RfButton>
      </template>
    </RfPanel>

    <RfPanel title="Counterfactual fork">
      <p class="dock-prose">A fork is a guarded patch that births a <strong>new draft run</strong> — same world recipe, edited priors, fresh provenance.</p>
      <template #foot>
        <RfButton @click="duplicateAsDraft">Duplicate as draft →</RfButton>
      </template>
    </RfPanel>
  </template>
</template>

<script setup>
import { computed, inject } from 'vue'
import { RfButton, RfChip, RfEnumChips, RfField, RfLabelRow, RfPanel, RfStepHeading } from '../primitives/index.js'
import { RUN_STORE_KEY } from '../runStore.js'

const store = inject(RUN_STORE_KEY)
const {
  activeRunId,
  phase,
  findingsMode,
  goStep,
  summon,
  reportPayload,
  reportError,
  reportNotice,
  reportClaims,
  validityFlags,
  canGenerateReport,
  reportGateReason,
  generateReport,
  loadReport,
  isBusy,
  askInput,
  askHistory,
  askError,
  askSending,
  canAsk,
  sendAsk,
  statisticalHash,
  shortHash,
  statisticalVariants,
  statisticalLineage,
  latestVariant,
  analysisComparePayload,
  analysisError,
  analysisLoading,
  loadAnalysisArtifacts,
  compareDiffCount,
  sensitivityPayload,
  sensitivityError,
  sensitivityLoading,
  runSensitivitySweep,
  duplicateAsDraft
} = store

const modeOptions = [
  { id: 'read', label: 'read' },
  { id: 'ask', label: 'ask' },
  { id: 'data', label: 'data' }
]

function flagsFor (claimId) {
  return (validityFlags.value?.[claimId] || []).slice(0, 3)
}

function bucket (name) {
  const findings = analysisComparePayload.value?.findings_diff || {}
  return Array.isArray(findings[name]) ? findings[name].length : 0
}

const sensitivityRows = computed(() => {
  const payload = sensitivityPayload.value
  if (!payload || typeof payload !== 'object') return []
  const rows = []
  const factors = payload.factors || payload.rankings || payload.results
  if (Array.isArray(factors)) {
    factors.slice(0, 8).forEach((factor, index) => {
      rows.push({
        label: factor.factor || factor.id || factor.label || `factor ${index + 1}`,
        value: factor.effect != null ? String(factor.effect) : (factor.delta != null ? String(factor.delta) : 'recorded')
      })
    })
  } else {
    Object.entries(payload).slice(0, 8).forEach(([key, value]) => {
      if (['string', 'number'].includes(typeof value)) rows.push({ label: key.replace(/_/g, ' '), value: String(value) })
    })
  }
  return rows
})
</script>
