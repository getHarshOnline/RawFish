<template>
  <!-- The cockpit shell (board 03): Z1 topbar · Z2 stage · Z3 dock · Z4
       console. One run per cockpit. Everything else is summoned. -->
  <main v-if="manifest" class="rf-shell">
    <header class="rf-z1">
      <router-link class="rf-z1__brand" to="/">rawfish</router-link>
      <RfStepSpine :items="spineItems" @go="onSpineGo" />
      <span class="rf-z1__ctx">{{ contextLine.lane }} · <strong>{{ contextLine.phase }}</strong> · {{ contextLine.env }}</span>
    </header>

    <section class="rf-body">
      <!-- Z2 — the world is the hero. Mounts at Build, never unmounts;
           absent on Scenario (boards 03/04). -->
      <aside v-if="stageMounted" v-show="stageVisible" class="rf-z2" aria-label="World stage">
        <GraphPanel
          chromeless
          :graph-data="runGraphData"
          :loading="isHydrating"
          :current-phase="graphPhaseIndex"
          :is-simulating="isStreaming"
          @refresh="refreshStage"
        />
      </aside>

      <!-- Z3 — the dock owns the active step. -->
      <section
        ref="dockRef"
        class="rf-z3"
        :class="{ 'rf-z3--wide': dockWide, 'rf-z3--solo': dockSolo }"
        aria-label="Step dock"
      >
        <component :is="stepComponent" />
      </section>

      <!-- O1 / O2 — summoned, filtered, trailed, reversible (board 06). -->
      <OverlayHost v-if="overlay" />
    </section>

    <!-- Z4 — machine truth only. 34px idle; expands while streaming. -->
    <footer class="rf-z4">
      <RfConsole
        :line="consoleLine"
        :dot="consoleDot"
        :open="consoleOpen"
        :logs="consoleLogs"
        @toggle="toggleConsole"
      />
    </footer>
  </main>

  <main v-else class="rf-shell">
    <header class="rf-z1">
      <router-link class="rf-z1__brand" to="/">rawfish</router-link>
      <span class="rf-z1__ctx">{{ isHydrating ? 'hydrating run' : 'run unavailable' }}</span>
    </header>
    <section class="rf-body">
      <div class="shell-empty">
        <span class="rf-label">{{ isHydrating ? 'Hydrating run' : 'Run unavailable' }}</span>
        <p class="dock-prose">{{ lifecycleError || 'Looking for this run across registered RawFish lanes.' }}</p>
        <RfButton role="quiet" @click="$router.push('/')">back to home →</RfButton>
      </div>
    </section>
    <footer class="rf-z4">
      <RfConsole line="idle · no run attached" dot="idle" :open="false" :logs="[]" />
    </footer>
  </main>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, provide, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import GraphPanel from '../components/GraphPanel.vue'
import { RfButton, RfConsole, RfStepSpine } from './primitives/index.js'
import { createRunStore, RUN_STORE_KEY } from './runStore.js'
import OverlayHost from './overlays/OverlayHost.vue'
import StepScenario from './steps/StepScenario.vue'
import StepBuild from './steps/StepBuild.vue'
import StepCohorts from './steps/StepCohorts.vue'
import StepPreflight from './steps/StepPreflight.vue'
import StepSimulate from './steps/StepSimulate.vue'
import StepFindings from './steps/StepFindings.vue'

const props = defineProps({
  runId: { type: String, default: null },
  step: { type: String, default: null },
  mode: { type: String, default: null },
  lane: { type: String, default: null }
})

const router = useRouter()
const route = useRoute()
const store = createRunStore(props, router, route)
provide(RUN_STORE_KEY, store)

const {
  manifest,
  spineItems,
  contextLine,
  currentStep,
  goStep,
  runGraphData,
  worldExists,
  isStreaming,
  isHydrating,
  isHalted,
  backendPhase,
  lifecycleError,
  refreshStage,
  consoleLine,
  consoleDot,
  consoleOpen,
  consoleLogs,
  toggleConsole,
  overlay,
  boot,
  teardown
} = store

const dockRef = ref(null)

const stepComponents = {
  scenario: StepScenario,
  build: StepBuild,
  cohorts: StepCohorts,
  preflight: StepPreflight,
  simulate: StepSimulate,
  findings: StepFindings
}
const stepComponent = computed(() => stepComponents[currentStep.value] || StepScenario)

/* dense steps widen the dock; Scenario is alone and centered (board 03) */
const dockWide = computed(() => ['cohorts', 'findings'].includes(currentStep.value))
const dockSolo = computed(() => currentStep.value === 'scenario')

/* the stage never unmounts once the world exists (board 03) */
const stageEverMounted = ref(false)
watch(worldExists, exists => {
  if (exists) stageEverMounted.value = true
}, { immediate: true })
const stageMounted = computed(() => stageEverMounted.value || worldExists.value)
const stageVisible = computed(() => currentStep.value !== 'scenario' && stageMounted.value)

const graphPhaseIndex = computed(() => {
  const order = ['created', 'prepared', 'starting', 'running', 'completed', 'reported']
  const index = order.indexOf(backendPhase.value)
  if (isHalted.value) return 3
  return index < 0 ? 0 : index
})

function onSpineGo (item) {
  goStep(item.id)
}

watch(currentStep, async () => {
  await nextTick()
  dockRef.value?.scrollTo({ top: 0, left: 0 })
})

onMounted(() => { boot() })
onUnmounted(() => { teardown() })
</script>
