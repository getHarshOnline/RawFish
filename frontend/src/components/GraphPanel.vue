<template>
  <div class="graph-panel">
    <!-- chromeless: the stage is the hero — the shell owns all chrome (board 03) -->
    <div v-if="!chromeless" class="panel-header">
      <span class="panel-title">relationship view</span>
      <div class="header-tools">
        <button class="tool-btn" @click="$emit('refresh')" :disabled="loading" :title="$t('graph.refreshGraph')">
          <span class="icon-refresh" :class="{ 'spinning': loading }">↻</span>
          <span class="btn-text">relayout</span>
        </button>
        <button class="tool-btn" @click="$emit('toggle-maximize')" :title="$t('graph.toggleMaximize')">
          <span class="icon-maximize">⛶</span>
        </button>
      </div>
    </div>
    
    <div class="graph-container" ref="graphContainer">
      <!-- Graph visualization -->
      <div v-if="graphData" class="graph-view">
        <svg ref="graphSvg" class="graph-svg"></svg>
        
        <!-- Building / simulating hint -->
        <div v-if="!chromeless && (currentPhase === 1 || isSimulating)" class="graph-building-hint">
          <div class="memory-icon-wrapper">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="memory-icon">
              <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 4.44-4.04z" />
              <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-4.44-4.04z" />
            </svg>
          </div>
          {{ isSimulating ? $t('graph.graphMemoryRealtime') : $t('graph.realtimeUpdating') }}
        </div>
        
        <!-- Hint shown after the simulation finishes -->
        <div v-if="showSimulationFinishedHint" class="graph-building-hint finished-hint">
          <div class="hint-icon-wrapper">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="hint-icon">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="16" x2="12" y2="12"></line>
              <line x1="12" y1="8" x2="12.01" y2="8"></line>
            </svg>
          </div>
          <span class="hint-text">{{ $t('graph.pendingContentHint') }}</span>
          <button class="hint-close-btn" @click="dismissFinishedHint" :title="$t('graph.closeHint')">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
        
        <!-- Node / edge detail panel -->
        <div v-if="selectedItem" class="detail-panel">
          <div class="detail-panel-header">
            <span class="detail-title">{{ selectedItem.type === 'node' ? $t('graph.nodeDetails') : $t('graph.relationship') }}</span>
            <!-- entity type via label, never hue (data-marks are not UI paint) -->
            <span v-if="selectedItem.type === 'node'" class="detail-type-badge">
              {{ selectedItem.entityType }}
            </span>
            <button class="detail-close" @click="closeDetailPanel">×</button>
          </div>
          
          <!-- Node details -->
          <div v-if="selectedItem.type === 'node'" class="detail-content">
            <div class="detail-row">
              <span class="detail-label">Name:</span>
              <span class="detail-value">{{ selectedItem.data.name }}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">Node ID:</span>
              <span class="detail-value uuid-text">{{ selectedItem.data.uuid }}</span>
            </div>
            <div class="detail-row" v-if="selectedItem.data.created_at">
              <span class="detail-label">Created:</span>
              <span class="detail-value">{{ formatDateTime(selectedItem.data.created_at) }}</span>
            </div>
            
            <div class="detail-section" v-if="selectedItem.data.attributes && Object.keys(selectedItem.data.attributes).length > 0">
              <div class="section-title">Signals:</div>
              <div class="properties-list">
                <div v-for="(value, key) in selectedItem.data.attributes" :key="key" class="property-item">
                  <span class="property-key">{{ key }}:</span>
                  <span class="property-value">{{ value || 'None' }}</span>
                </div>
              </div>
            </div>
            
            <!-- Summary -->
            <div class="detail-section" v-if="selectedItem.data.summary">
              <div class="section-title">Summary:</div>
              <div class="summary-text">{{ selectedItem.data.summary }}</div>
            </div>
            
            <!-- Labels -->
            <div class="detail-section" v-if="selectedItem.data.labels && selectedItem.data.labels.length > 0">
              <div class="section-title">Labels:</div>
              <div class="labels-list">
                <span v-for="label in selectedItem.data.labels" :key="label" class="label-tag">
                  {{ label }}
                </span>
              </div>
            </div>
          </div>
          
          <!-- Edge details -->
          <div v-else class="detail-content">
            <!-- Self-loop group details -->
            <template v-if="selectedItem.data.isSelfLoopGroup">
              <div class="edge-relation-header self-loop-header">
                {{ selectedItem.data.source_name }} - Self Relations
                <span class="self-loop-count">{{ selectedItem.data.selfLoopCount }} items</span>
              </div>
              
              <div class="self-loop-list">
                <div 
                  v-for="(loop, idx) in selectedItem.data.selfLoopEdges" 
                  :key="loop.uuid || idx" 
                  class="self-loop-item"
                  :class="{ expanded: expandedSelfLoops.has(loop.uuid || idx) }"
                >
                  <div 
                    class="self-loop-item-header"
                    @click="toggleSelfLoop(loop.uuid || idx)"
                  >
                    <span class="self-loop-index">#{{ idx + 1 }}</span>
                    <span class="self-loop-name">{{ loop.name || loop.fact_type || 'RELATED' }}</span>
                    <span class="self-loop-toggle">{{ expandedSelfLoops.has(loop.uuid || idx) ? '−' : '+' }}</span>
                  </div>
                  
                  <div class="self-loop-item-content" v-show="expandedSelfLoops.has(loop.uuid || idx)">
                    <div class="detail-row" v-if="loop.uuid">
                      <span class="detail-label">Node ID:</span>
                      <span class="detail-value uuid-text">{{ loop.uuid }}</span>
                    </div>
                    <div class="detail-row" v-if="loop.fact">
                      <span class="detail-label">Fact:</span>
                      <span class="detail-value fact-text">{{ loop.fact }}</span>
                    </div>
                    <div class="detail-row" v-if="loop.fact_type">
                      <span class="detail-label">Type:</span>
                      <span class="detail-value">{{ loop.fact_type }}</span>
                    </div>
                    <div class="detail-row" v-if="loop.created_at">
                      <span class="detail-label">Created:</span>
                      <span class="detail-value">{{ formatDateTime(loop.created_at) }}</span>
                    </div>
                    <div v-if="loop.episodes && loop.episodes.length > 0" class="self-loop-episodes">
                      <span class="detail-label">Episodes:</span>
                      <div class="episodes-list compact">
                        <span v-for="ep in loop.episodes" :key="ep" class="episode-tag small">{{ ep }}</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </template>
            
            <!-- Standard edge details -->
            <template v-else>
              <div class="edge-relation-header">
                {{ selectedItem.data.source_name }} → {{ selectedItem.data.name || 'RELATED_TO' }} → {{ selectedItem.data.target_name }}
              </div>
              
              <div class="detail-row">
                <span class="detail-label">Node ID:</span>
                <span class="detail-value uuid-text">{{ selectedItem.data.uuid }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Label:</span>
                <span class="detail-value">{{ selectedItem.data.name || 'RELATED_TO' }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">Type:</span>
                <span class="detail-value">{{ selectedItem.data.fact_type || 'Unknown' }}</span>
              </div>
              <div class="detail-row" v-if="selectedItem.data.fact">
                <span class="detail-label">Fact:</span>
                <span class="detail-value fact-text">{{ selectedItem.data.fact }}</span>
              </div>
              
              <!-- Episodes -->
              <div class="detail-section" v-if="selectedItem.data.episodes && selectedItem.data.episodes.length > 0">
                <div class="section-title">Episodes:</div>
                <div class="episodes-list">
                  <span v-for="ep in selectedItem.data.episodes" :key="ep" class="episode-tag">
                    {{ ep }}
                  </span>
                </div>
              </div>
              
              <div class="detail-row" v-if="selectedItem.data.created_at">
                <span class="detail-label">Created:</span>
                <span class="detail-value">{{ formatDateTime(selectedItem.data.created_at) }}</span>
              </div>
              <div class="detail-row" v-if="selectedItem.data.valid_at">
                <span class="detail-label">Valid From:</span>
                <span class="detail-value">{{ formatDateTime(selectedItem.data.valid_at) }}</span>
              </div>
            </template>
          </div>
        </div>
      </div>
      
      <!-- Loading state -->
      <div v-else-if="loading" class="graph-state">
        <div class="loading-spinner"></div>
        <p>{{ $t('graph.graphDataLoading') }}</p>
      </div>
      
      <!-- Waiting / empty state -->
      <div v-else class="graph-state">
        <div class="empty-icon">❖</div>
        <p class="empty-text">{{ $t('graph.waitingOntology') }}</p>
      </div>
    </div>

    <!-- Cohort and signal legend -->
    <div v-if="graphData && entityTypes.length" class="graph-legend">
      <span class="legend-title">size ∝ influence · click a node to inspect</span>
      <div class="legend-items">
        <!-- type.color is a token var from the ink-opacity ramp (not a hue);
             delivered as a custom prop so paint stays in CSS -->
        <div class="legend-item" v-for="type in entityTypes" :key="type.name">
          <span class="legend-dot" :style="{ '--mark': type.color }"></span>
          <span class="legend-label">{{ type.name }}</span>
        </div>
      </div>
    </div>
    
    <!-- Toggle for showing edge labels -->
    <div v-if="graphData" class="edge-labels-toggle">
      <label class="toggle-switch">
        <input type="checkbox" v-model="showEdgeLabels" />
        <span class="slider"></span>
      </label>
      <span class="toggle-label">edge labels</span>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch, nextTick, computed } from 'vue'
import { createLayoutWorker, LAYOUT_WORKER_THRESHOLD } from '../workers/createLayoutWorker.js'

// d3 is heavy (~80kB minified) and only the large graph view
// renders nodes. Lazy-load it inside renderGraph() so any component
// that transitively imports this file does not pull d3 into its
// own chunk. The resolved module is cached across renders.
let _d3Module = null
async function _loadD3 () {
  if (!_d3Module) {
    _d3Module = await import('d3')
  }
  return _d3Module
}

const props = defineProps({
  graphData: Object,
  loading: Boolean,
  currentPhase: Number,
  isSimulating: Boolean,
  /* stage mode: the shell owns chrome; the graph is the hero */
  chromeless: { type: Boolean, default: false }
})

const emit = defineEmits(['refresh', 'toggle-maximize'])

const graphContainer = ref(null)
const graphSvg = ref(null)
const selectedItem = ref(null)
const showEdgeLabels = ref(true) // Show edge labels by default
const expandedSelfLoops = ref(new Set()) // Expanded self-loop items
const showSimulationFinishedHint = ref(false) // Hint shown after the simulation finishes
const wasSimulating = ref(false) // Tracks whether the simulation was running on the prior tick
const GRAPH_FONT_FAMILY = 'var(--mono)'
const GRAPH_COLORS = {
  muted: 'var(--t2)',
  ink: 'var(--ink)',
  panel: 'var(--paper)',
  border: 'var(--line)',
  edge: 'var(--line)',
  accent: 'var(--accent)',
  selected: 'var(--danger)',
  labelBackground: 'var(--paper)',
  selectedFill: 'var(--accent-wash)'
}
/* Entity identity is encoded with an ink-opacity ramp — weight/opacity/
   position, never hue. --accent = active/primary only; --danger = error
   only (RAWFISH_UI_CONTRACT rule 4). */
const GRAPH_ENTITY_COLORS = [
  'var(--t1)',
  'var(--t2)',
  'var(--t3)',
  'var(--ink)',
  'var(--line-strong)',
  'var(--t4)',
  'var(--ink-80)',
  'var(--ink-50)'
]

// Dismiss the simulation-finished hint
const dismissFinishedHint = () => {
  showSimulationFinishedHint.value = false
}

// Watch isSimulating for changes to detect when the simulation ends
watch(() => props.isSimulating, (newValue, oldValue) => {
  if (wasSimulating.value && !newValue) {
    // Transitioned from simulating to not simulating; show the finished hint
    showSimulationFinishedHint.value = true
  }
  wasSimulating.value = newValue
}, { immediate: true })

// Toggle the expanded/collapsed state of a self-loop item
const toggleSelfLoop = (id) => {
  const newSet = new Set(expandedSelfLoops.value)
  if (newSet.has(id)) {
    newSet.delete(id)
  } else {
    newSet.add(id)
  }
  expandedSelfLoops.value = newSet
}

// Compute entity types for the legend
const entityTypes = computed(() => {
  if (!props.graphData?.nodes) return []
  const typeMap = {}

  props.graphData.nodes.forEach(node => {
    const type = node.labels?.find(l => l !== 'Entity') || 'Entity'
    if (!typeMap[type]) {
      typeMap[type] = { name: type, count: 0, color: GRAPH_ENTITY_COLORS[Object.keys(typeMap).length % GRAPH_ENTITY_COLORS.length] }
    }
    typeMap[type].count++
  })
  return Object.values(typeMap)
})

// Format a datetime string
const formatDateTime = (dateStr) => {
  if (!dateStr) return ''
  try {
    const date = new Date(dateStr)
    return date.toLocaleString('en-US', { 
      month: 'short', 
      day: 'numeric', 
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true 
    })
  } catch {
    return dateStr
  }
}

const closeDetailPanel = () => {
  selectedItem.value = null
  expandedSelfLoops.value = new Set() // Reset expanded state
}

let currentSimulation = null
let linkLabelsRef = null
let linkLabelBgRef = null
// Active layout worker for the current renderGraph() call. We keep
// a single in-flight handle so a rapid sequence of graphData
// updates (e.g. while the build task is streaming new nodes) does
// not stack worker computations -- the next renderGraph() aborts
// the prior worker before issuing a fresh layout request.
let activeLayoutWorker = null
// Reject callback paired with ``activeLayoutWorker``. ``terminate()``
// on a Web Worker does NOT trigger ``onmessage`` or ``onerror``, so
// the prior renderGraph()'s ``await new Promise(...)`` would
// otherwise hang indefinitely (the closures over the nodes/edges
// arrays leaking with it). Supersede paths call ``activeLayoutReject``
// with an AbortError-flavoured exception so the awaiting call wakes
// up, sees the rejection, and exits cleanly.
let activeLayoutReject = null
// Monotonic guard token: every renderGraph() call mints a new id,
// and the worker's "result" callback drops its message if the
// graph has since moved on. This is the worker equivalent of the
// poll-token staleness gate used by the simulation pollers.
let renderToken = 0

// Helper: terminate ``activeLayoutWorker`` AND reject its paired
// pending promise so the awaiting renderGraph() returns instead of
// hanging. Idempotent; safe to call when no worker is active.
function _abortActiveLayoutWorker () {
  const w = activeLayoutWorker
  const r = activeLayoutReject
  activeLayoutWorker = null
  activeLayoutReject = null
  if (w) {
    try { w.terminate() } catch { /* terminate is best-effort */ }
  }
  if (r) {
    const err = new Error('layout worker superseded')
    err.name = 'AbortError'
    try { r(err) } catch { /* reject handlers are diagnostic only */ }
  }
}

const renderGraph = async () => {
  if (!graphSvg.value || !props.graphData) return
  const d3 = await _loadD3()

  // Stop the previous simulation
  if (currentSimulation) {
    currentSimulation.stop()
  }
  // Cancel any in-flight worker layout from a prior render. Its
  // posted result would arrive with a stale renderToken and be
  // dropped anyway, but terminating here also frees the worker
  // process immediately rather than waiting for the GC. We also
  // reject the prior promise so the awaiting renderGraph() exits
  // its try/catch instead of leaking its closure-captured arrays.
  _abortActiveLayoutWorker()
  const thisRender = ++renderToken

  const container = graphContainer.value
  const width = container.clientWidth
  const height = container.clientHeight

  const svg = d3.select(graphSvg.value)
    .attr('width', width)
    .attr('height', height)
    .attr('viewBox', `0 0 ${width} ${height}`)

  const nodesData = props.graphData.nodes || []
  const edgesData = props.graphData.edges || []

  if (nodesData.length === 0) {
    svg.selectAll('*').remove()
    return
  }

  // Prep data
  const nodeMap = {}
  nodesData.forEach(n => nodeMap[n.uuid] = n)
  
  const nodes = nodesData.map(n => ({
    id: n.uuid,
    name: n.name || 'Unnamed',
    type: n.labels?.find(l => l !== 'Entity') || 'Entity',
    rawData: n
  }))
  
  const nodeIds = new Set(nodes.map(n => n.id))
  
  // Process edge data: count edges between the same node pair and assign an index
  const edgePairCount = {}
  const selfLoopEdges = {} // Self-loop edges grouped by node
  const tempEdges = edgesData
    .filter(e => nodeIds.has(e.source_node_uuid) && nodeIds.has(e.target_node_uuid))

  // Count the edges between each node pair and collect self-loop edges
  tempEdges.forEach(e => {
    if (e.source_node_uuid === e.target_node_uuid) {
      // Self-loop - collect into an array
      if (!selfLoopEdges[e.source_node_uuid]) {
        selfLoopEdges[e.source_node_uuid] = []
      }
      selfLoopEdges[e.source_node_uuid].push({
        ...e,
        source_name: nodeMap[e.source_node_uuid]?.name,
        target_name: nodeMap[e.target_node_uuid]?.name
      })
    } else {
      const pairKey = [e.source_node_uuid, e.target_node_uuid].sort().join('_')
      edgePairCount[pairKey] = (edgePairCount[pairKey] || 0) + 1
    }
  })
  
  // Track which edge index we are processing for each node pair
  const edgePairIndex = {}
  const processedSelfLoopNodes = new Set() // Nodes whose self-loops have already been processed

  const edges = []

  tempEdges.forEach(e => {
    const isSelfLoop = e.source_node_uuid === e.target_node_uuid

    if (isSelfLoop) {
      // Self-loop edge - add only one merged self-loop per node
      if (processedSelfLoopNodes.has(e.source_node_uuid)) {
        return // Already processed; skip
      }
      processedSelfLoopNodes.add(e.source_node_uuid)
      
      const allSelfLoops = selfLoopEdges[e.source_node_uuid]
      const nodeName = nodeMap[e.source_node_uuid]?.name || 'Unknown'
      
      edges.push({
        source: e.source_node_uuid,
        target: e.target_node_uuid,
        type: 'SELF_LOOP',
        name: `Self Relations (${allSelfLoops.length})`,
        curvature: 0,
        isSelfLoop: true,
        rawData: {
          isSelfLoopGroup: true,
          source_name: nodeName,
          target_name: nodeName,
          selfLoopCount: allSelfLoops.length,
          selfLoopEdges: allSelfLoops // Detailed info for every self-loop edge
        }
      })
      return
    }
    
    const pairKey = [e.source_node_uuid, e.target_node_uuid].sort().join('_')
    const totalCount = edgePairCount[pairKey]
    const currentIndex = edgePairIndex[pairKey] || 0
    edgePairIndex[pairKey] = currentIndex + 1
    
    // Keep the drawn arc direction stable regardless of payload ordering.
    const isReversed = e.source_node_uuid > e.target_node_uuid

    // Compute curvature: spread out when there are multiple edges; a single edge stays straight
    let curvature = 0
    if (totalCount > 1) {
      // Distribute curvature evenly so edges are clearly distinguishable
      // The curvature range grows with the edge count: more edges means a wider range
      const curvatureRange = Math.min(1.2, 0.6 + totalCount * 0.15)
      curvature = ((currentIndex / (totalCount - 1)) - 0.5) * curvatureRange * 2

      // If the edge direction is opposite to the canonical ordering, flip the curvature
      // This keeps all edges distributed in the same reference frame so they don't overlap when directions differ
      if (isReversed) {
        curvature = -curvature
      }
    }
    
    edges.push({
      source: e.source_node_uuid,
      target: e.target_node_uuid,
      type: e.fact_type || e.name || 'RELATED',
      name: e.name || e.fact_type || 'RELATED',
      curvature,
      isSelfLoop: false,
      pairIndex: currentIndex,
      pairTotal: totalCount,
      rawData: {
        ...e,
        source_name: nodeMap[e.source_node_uuid]?.name,
        target_name: nodeMap[e.target_node_uuid]?.name
      }
    })
  })
    
  // Color scale
  const colorMap = {}
  entityTypes.value.forEach(t => colorMap[t.name] = t.color)
  const getColor = (type) => colorMap[type] || GRAPH_COLORS.muted

  // Large-graph branch: offload the converging layout pass to a
  // dedicated Web Worker. d3.forceSimulation already respects
  // pre-seeded ``x`` / ``y`` on its node array, so the worker's
  // job is to produce those coordinates -- the main-thread
  // simulation then starts at low alpha and renders one settled
  // frame instead of running 300+ ticks on the UI thread. The
  // existing SVG stays mounted while the worker computes (no
  // flash); we only clear and repaint once positions are in hand.
  if (nodes.length > LAYOUT_WORKER_THRESHOLD) {
    // Worker construction can throw synchronously (CSP blocks module
    // workers, the worker URL fails to resolve, etc.). Wrap the
    // factory call so a throw degrades to the inline force-simulation
    // path instead of crashing the render -- slow layout is strictly
    // better than no graph at all.
    let worker = null
    try {
      worker = createLayoutWorker()
    } catch {
      worker = null
    }
    if (worker) {
      activeLayoutWorker = worker
      let seeded = false
      try {
        seeded = await new Promise((resolve, reject) => {
          // Pair the reject with the worker. A subsequent
          // renderGraph() supersede calls _abortActiveLayoutWorker
          // which terminates the worker AND rejects this promise so
          // the await below wakes up immediately; without the reject
          // hook, terminate() would leave this promise pending
          // forever and the closure-captured nodes/edges would leak.
          activeLayoutReject = reject
          worker.onmessage = (event) => {
            const msg = event && event.data
            if (!msg || msg.type === 'error') {
              resolve(false)
              return
            }
            if (msg.type !== 'result' || !msg.payload) {
              resolve(false)
              return
            }
            // Copy x / y from worker output onto the live node
            // objects -- d3 mutates its input in place, so the
            // worker returned the same shape we sent.
            const out = msg.payload.nodes_with_positions || []
            for (let i = 0; i < nodes.length && i < out.length; i++) {
              if (typeof out[i].x === 'number') nodes[i].x = out[i].x
              if (typeof out[i].y === 'number') nodes[i].y = out[i].y
            }
            resolve(true)
          }
          worker.onerror = () => { resolve(false) }
          // d3-force serializes edge.source / .target into node
          // references on first tick. Pass a shallow copy with
          // string ids so the worker round-trips cleanly across
          // structured-clone.
          const edgePayload = edges.map((e) => ({
            source: typeof e.source === 'object' ? e.source.id : e.source,
            target: typeof e.target === 'object' ? e.target.id : e.target,
            pairTotal: e.pairTotal,
          }))
          worker.postMessage({
            type: 'layout',
            payload: {
              nodes: nodes.map((n) => ({ id: n.id })),
              edges: edgePayload,
              options: { width, height },
            },
          })
        })
      } catch {
        // Supersede path: a newer renderGraph() rejected our promise
        // via _abortActiveLayoutWorker. Exit cleanly so the closure
        // over nodes/edges can be reclaimed; the newer call will
        // paint the next frame.
        return
      }
      // Drop if a newer render has superseded us mid-flight.
      if (thisRender !== renderToken) {
        if (activeLayoutWorker === worker) {
          activeLayoutWorker = null
          activeLayoutReject = null
        }
        try { worker.terminate() } catch { /* best-effort */ }
        return
      }
      try { worker.terminate() } catch { /* best-effort */ }
      if (activeLayoutWorker === worker) {
        activeLayoutWorker = null
        activeLayoutReject = null
      }
      // ``seeded === false`` means the worker errored; fall
      // through and let the inline simulation handle it.
      void seeded
    }
  }

  svg.selectAll('*').remove()

  // Simulation - adjusts node spacing dynamically based on edge count.
  // When the worker pre-seeded positions, d3 respects the existing
  // x / y on each node and starts in a converged state; we lower
  // ``alpha`` so the on-screen simulation settles in a few ticks
  // instead of re-doing the layout the worker already produced.
  const simulation = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(edges).id(d => d.id).distance(d => {
      // Adjust the distance based on the number of edges between this node pair
      // Base distance 150, +40 for each additional edge
      const baseDistance = 150
      const edgeCount = d.pairTotal || 1
      return baseDistance + (edgeCount - 1) * 50
    }))
    .force('charge', d3.forceManyBody().strength(-400))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collide', d3.forceCollide(50))
    // Add a pull toward the center so isolated node clusters gather near the center
    .force('x', d3.forceX(width / 2).strength(0.04))
    .force('y', d3.forceY(height / 2).strength(0.04))

  if (nodes.length > LAYOUT_WORKER_THRESHOLD && typeof nodes[0]?.x === 'number') {
    simulation.alpha(0.1)
  }

  currentSimulation = simulation

  const g = svg.append('g')
  
  // Zoom
  svg.call(d3.zoom().extent([[0, 0], [width, height]]).scaleExtent([0.1, 4]).on('zoom', (event) => {
    g.attr('transform', event.transform)
  }))

  // Links - use path to support curves
  const linkGroup = g.append('g').attr('class', 'links')

  // Compute the curve path
  const getLinkPath = (d) => {
    const sx = d.source.x, sy = d.source.y
    const tx = d.target.x, ty = d.target.y

    // Detect self-loop
    if (d.isSelfLoop) {
      // Self-loop: draw an arc that leaves the node and returns to it
      const loopRadius = 30
      // Start on the right side of the node and loop back
      const x1 = sx + 8  // Start offset
      const y1 = sy - 4
      const x2 = sx + 8  // End offset
      const y2 = sy + 4
      // Use an arc to render the self-loop (sweep-flag=1 clockwise)
      return `M${x1},${y1} A${loopRadius},${loopRadius} 0 1,1 ${x2},${y2}`
    }

    if (d.curvature === 0) {
      // Straight line
      return `M${sx},${sy} L${tx},${ty}`
    }

    // Compute the curve control point - adjusted dynamically by edge count and distance
    const dx = tx - sx, dy = ty - sy
    const dist = Math.sqrt(dx * dx + dy * dy)
    // Offset perpendicular to the connecting line, scaled by distance to keep the curve clearly visible
    // More edges means a larger offset relative to the distance
    const pairTotal = d.pairTotal || 1
    const offsetRatio = 0.25 + pairTotal * 0.05 // 25% base, +5% per additional edge
    const baseOffset = Math.max(35, dist * offsetRatio)
    const offsetX = -dy / dist * d.curvature * baseOffset
    const offsetY = dx / dist * d.curvature * baseOffset
    const cx = (sx + tx) / 2 + offsetX
    const cy = (sy + ty) / 2 + offsetY
    
    return `M${sx},${sy} Q${cx},${cy} ${tx},${ty}`
  }
  
  // Compute the curve midpoint (used to position labels)
  const getLinkMidpoint = (d) => {
    const sx = d.source.x, sy = d.source.y
    const tx = d.target.x, ty = d.target.y

    // Detect self-loop
    if (d.isSelfLoop) {
      // Self-loop label position: to the right of the node
      return { x: sx + 70, y: sy }
    }
    
    if (d.curvature === 0) {
      return { x: (sx + tx) / 2, y: (sy + ty) / 2 }
    }
    
    // Midpoint of the quadratic Bezier curve at t=0.5
    const dx = tx - sx, dy = ty - sy
    const dist = Math.sqrt(dx * dx + dy * dy)
    const pairTotal = d.pairTotal || 1
    const offsetRatio = 0.25 + pairTotal * 0.05
    const baseOffset = Math.max(35, dist * offsetRatio)
    const offsetX = -dy / dist * d.curvature * baseOffset
    const offsetY = dx / dist * d.curvature * baseOffset
    const cx = (sx + tx) / 2 + offsetX
    const cy = (sy + ty) / 2 + offsetY
    
    // Quadratic Bezier formula B(t) = (1-t)²P0 + 2(1-t)tP1 + t²P2, t=0.5
    const midX = 0.25 * sx + 0.5 * cx + 0.25 * tx
    const midY = 0.25 * sy + 0.5 * cy + 0.25 * ty
    
    return { x: midX, y: midY }
  }
  
  const link = linkGroup.selectAll('path')
    .data(edges)
    .enter().append('path')
    .attr('stroke', GRAPH_COLORS.edge)
    .attr('stroke-width', 1.5)
    .attr('fill', 'none')
    .style('cursor', 'pointer')
    .on('click', (event, d) => {
      event.stopPropagation()
      // Reset the style of any prior selected edge
      linkGroup.selectAll('path').attr('stroke', GRAPH_COLORS.edge).attr('stroke-width', 1.5)
      linkLabelBg.attr('fill', GRAPH_COLORS.labelBackground)
      linkLabels.attr('fill', GRAPH_COLORS.muted)
      // Highlight the currently selected edge
      d3.select(event.target).attr('stroke', GRAPH_COLORS.accent).attr('stroke-width', 3)

      selectedItem.value = {
        type: 'edge',
        data: d.rawData
      }
    })

  // Link labels background (white background to make text clearer)
  const linkLabelBg = linkGroup.selectAll('rect')
    .data(edges)
    .enter().append('rect')
    .attr('fill', GRAPH_COLORS.labelBackground)
    .attr('rx', 3)
    .attr('ry', 3)
    .style('cursor', 'pointer')
    .style('pointer-events', 'all')
    .style('display', showEdgeLabels.value ? 'block' : 'none')
    .on('click', (event, d) => {
      event.stopPropagation()
      linkGroup.selectAll('path').attr('stroke', GRAPH_COLORS.edge).attr('stroke-width', 1.5)
      linkLabelBg.attr('fill', GRAPH_COLORS.labelBackground)
      linkLabels.attr('fill', GRAPH_COLORS.muted)
      // Highlight the matching edge
      link.filter(l => l === d).attr('stroke', GRAPH_COLORS.accent).attr('stroke-width', 3)
      d3.select(event.target).attr('fill', GRAPH_COLORS.selectedFill)
      
      selectedItem.value = {
        type: 'edge',
        data: d.rawData
      }
    })

  // Link labels — type comes from the token file via .rf-svg-label
  const linkLabels = linkGroup.selectAll('text')
    .data(edges)
    .enter().append('text')
    .text(d => d.name)
    .attr('class', 'rf-svg-label')
    .attr('fill', GRAPH_COLORS.muted)
    .attr('text-anchor', 'middle')
    .attr('dominant-baseline', 'middle')
    .style('cursor', 'pointer')
    .style('pointer-events', 'all')
    .style('font-family', GRAPH_FONT_FAMILY)
    .style('display', showEdgeLabels.value ? 'block' : 'none')
    .on('click', (event, d) => {
      event.stopPropagation()
      linkGroup.selectAll('path').attr('stroke', GRAPH_COLORS.edge).attr('stroke-width', 1.5)
      linkLabelBg.attr('fill', GRAPH_COLORS.labelBackground)
      linkLabels.attr('fill', GRAPH_COLORS.muted)
      // Highlight the matching edge
      link.filter(l => l === d).attr('stroke', GRAPH_COLORS.accent).attr('stroke-width', 3)
      d3.select(event.target).attr('fill', GRAPH_COLORS.accent)
      
      selectedItem.value = {
        type: 'edge',
        data: d.rawData
      }
    })
  
  // Save references so external code can toggle visibility
  linkLabelsRef = linkLabels
  linkLabelBgRef = linkLabelBg

  // Nodes group
  const nodeGroup = g.append('g').attr('class', 'nodes')
  
  // Node circles
  const node = nodeGroup.selectAll('circle')
    .data(nodes)
    .enter().append('circle')
    .attr('r', 10)
    .attr('fill', d => getColor(d.type))
    .attr('stroke', GRAPH_COLORS.panel)
    .attr('stroke-width', 2.5)
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (event, d) => {
        // Just record the position; don't restart the simulation (to differentiate click vs drag)
        d.fx = d.x
        d.fy = d.y
        d._dragStartX = event.x
        d._dragStartY = event.y
        d._isDragging = false
      })
      .on('drag', (event, d) => {
        // Detect whether a real drag has begun (moved past the threshold)
        const dx = event.x - d._dragStartX
        const dy = event.y - d._dragStartY
        const distance = Math.sqrt(dx * dx + dy * dy)

        if (!d._isDragging && distance > 3) {
          // First real drag detected; only now restart the simulation
          d._isDragging = true
          simulation.alphaTarget(0.3).restart()
        }

        if (d._isDragging) {
          d.fx = event.x
          d.fy = event.y
        }
      })
      .on('end', (event, d) => {
        // Only let the simulation cool down if there was an actual drag
        if (d._isDragging) {
          simulation.alphaTarget(0)
        }
        d.fx = null
        d.fy = null
        d._isDragging = false
      })
    )
    .on('click', (event, d) => {
      event.stopPropagation()
      // Reset every node's style
      node.attr('stroke', GRAPH_COLORS.panel).attr('stroke-width', 2.5)
      linkGroup.selectAll('path').attr('stroke', GRAPH_COLORS.edge).attr('stroke-width', 1.5)
      // Highlight the selected node
      d3.select(event.target).attr('stroke', GRAPH_COLORS.selected).attr('stroke-width', 4)
      // Highlight the edges connected to this node
      link.filter(l => l.source.id === d.id || l.target.id === d.id)
        .attr('stroke', GRAPH_COLORS.selected)
        .attr('stroke-width', 2.5)
      
      selectedItem.value = {
        type: 'node',
        data: d.rawData,
        entityType: d.type,
        color: getColor(d.type)
      }
    })
    .on('mouseenter', (event, d) => {
      if (!selectedItem.value || selectedItem.value.data?.uuid !== d.rawData.uuid) {
        d3.select(event.target).attr('stroke', GRAPH_COLORS.ink).attr('stroke-width', 3)
      }
    })
    .on('mouseleave', (event, d) => {
      if (!selectedItem.value || selectedItem.value.data?.uuid !== d.rawData.uuid) {
        d3.select(event.target).attr('stroke', GRAPH_COLORS.panel).attr('stroke-width', 2.5)
      }
    })

  // Node Labels
  const nodeLabels = nodeGroup.selectAll('text')
    .data(nodes)
    .enter().append('text')
    .text(d => d.name.length > 8 ? d.name.substring(0, 8) + '…' : d.name)
    .attr('class', 'rf-svg-value')
    .attr('fill', GRAPH_COLORS.ink)
    .attr('dx', 14)
    .attr('dy', 4)
    .style('pointer-events', 'none')
    .style('font-family', GRAPH_FONT_FAMILY)

  simulation.on('tick', () => {
    // Update curve paths
    link.attr('d', d => getLinkPath(d))

    // Update edge label positions (no rotation; horizontal display is cleaner)
    linkLabels.each(function(d) {
      const mid = getLinkMidpoint(d)
      d3.select(this)
        .attr('x', mid.x)
        .attr('y', mid.y)
        .attr('transform', '') // Remove rotation; keep horizontal
    })

    // Update edge label backgrounds
    linkLabelBg.each(function(d, i) {
      const mid = getLinkMidpoint(d)
      const textEl = linkLabels.nodes()[i]
      const bbox = textEl.getBBox()
      d3.select(this)
        .attr('x', mid.x - bbox.width / 2 - 4)
        .attr('y', mid.y - bbox.height / 2 - 2)
        .attr('width', bbox.width + 8)
        .attr('height', bbox.height + 4)
        .attr('transform', '') // Remove rotation
    })

    node
      .attr('cx', d => d.x)
      .attr('cy', d => d.y)

    nodeLabels
      .attr('x', d => d.x)
      .attr('y', d => d.y)
  })
  
  // Clicking empty space closes the detail panel
  svg.on('click', () => {
    selectedItem.value = null
    node.attr('stroke', GRAPH_COLORS.panel).attr('stroke-width', 2.5)
    linkGroup.selectAll('path').attr('stroke', GRAPH_COLORS.edge).attr('stroke-width', 1.5)
    linkLabelBg.attr('fill', GRAPH_COLORS.labelBackground)
    linkLabels.attr('fill', GRAPH_COLORS.muted)
  })
}

watch(() => props.graphData, () => {
  nextTick(renderGraph)
}, { deep: true })

// Watch the edge-label visibility toggle
watch(showEdgeLabels, (newVal) => {
  if (linkLabelsRef) {
    linkLabelsRef.style('display', newVal ? 'block' : 'none')
  }
  if (linkLabelBgRef) {
    linkLabelBgRef.style('display', newVal ? 'block' : 'none')
  }
})

const handleResize = () => {
  nextTick(renderGraph)
}

onMounted(() => {
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  window.removeEventListener('resize', handleResize)
  if (currentSimulation) {
    currentSimulation.stop()
  }
  // Same supersede contract as renderGraph's entry: terminate the
  // worker AND reject the paired pending promise so the awaiting
  // render exits its try/catch cleanly.
  _abortActiveLayoutWorker()
})
</script>

<style scoped>
.graph-panel {
  position: relative;
  width: 100%;
  height: 100%;
  background-color: var(--bg);
  background-image: radial-gradient(var(--line) 1.3px, transparent 1.3px);
  background-size: 20px 20px;
  overflow: hidden;
}

.panel-header {
  position: absolute;
  top: 0;
  left: 0;
  right: auto;
  padding: 10px 12px;
  z-index: 10;
  display: flex;
  justify-content: flex-start;
  align-items: center;
  gap: 8px;
  background: transparent;
  pointer-events: none;
}

.panel-title {
  padding: 4px 7px;
  border: 1px solid transparent;
  background: var(--paper);
  font: var(--f-value);
  color: var(--t2);
  pointer-events: auto;
}

.header-tools {
  pointer-events: auto;
  display: flex;
  gap: 10px;
  align-items: center;
}

.tool-btn {
  height: 28px;
  padding: 0 9px;
  border: 1px solid var(--line);
  background: var(--paper);
  border-radius: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
  color: var(--ink);
  transition: background-color var(--mf-motion-fast), color var(--mf-motion-fast), border-color var(--mf-motion-fast);
  font: var(--f-value);
}

.tool-btn:hover {
  background: var(--bg);
  color: var(--ink);
  border-color: var(--line);
}

.tool-btn .btn-text {
  font: var(--f-label); letter-spacing: 1px;
}

.icon-refresh.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

.graph-container {
  width: 100%;
  height: 100%;
}

.graph-view, .graph-svg {
  width: 100%;
  height: 100%;
  display: block;
}

.graph-state {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
  color: var(--t2);
}

.empty-icon {
  font: var(--f-display);
  margin-bottom: 16px;
  opacity: 0.2;
}

/* Cohort and signal legend */
.graph-legend {
  position: absolute;
  bottom: 12px;
  left: 12px;
  background: var(--paper);
  padding: 8px 11px;
  border-radius: 0;
  border: 1px solid var(--ink);
  z-index: 10;
}

.legend-title {
  display: block;
  font: var(--f-label); letter-spacing: 1px;
  font-weight: 600;
  color: var(--t2);
  margin-bottom: 7px;
  letter-spacing: 0;
}

.legend-items {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
  max-width: 320px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font: var(--f-value);
  color: var(--t2);
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 0;
  flex-shrink: 0;
  background: var(--mark, var(--t2));
}

.legend-label {
  white-space: nowrap;
}

/* Edge Labels Toggle - Top Right */
.edge-labels-toggle {
  position: absolute;
  top: 10px;
  left: 178px;
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--paper);
  padding: 4px 7px;
  border-radius: 0;
  border: 1px solid var(--line);
  z-index: 10;
}

.toggle-switch {
  position: relative;
  display: inline-block;
  width: 40px;
  height: 22px;
}

.toggle-switch input {
  opacity: 0;
  width: 0;
  height: 0;
}

.slider {
  position: absolute;
  cursor: pointer;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background-color: var(--line);
  border-radius: 0;
  /* Toggle track only changes `background-color` when the underlying
   * input flips between checked/unchecked. Naming the property keeps
   * the browser off the layout/paint paths a bare-duration shorthand
   * (which defaults `transition-property` to the broad keyword)
   * would have opened. */
  transition: background-color 0.3s;
}

.slider:before {
  position: absolute;
  content: "";
  height: 16px;
  width: 16px;
  left: 3px;
  bottom: 3px;
  background-color: var(--paper);
  border-radius: 0;
  /* Knob slides via `transform: translateX(...)` on the checked-state
   * rule below; transitioning `transform` only keeps the animation on
   * the compositor and avoids the broad shorthand default. */
  transition: transform 0.3s;
}

input:checked + .slider {
  background-color: var(--accent);
}

input:checked + .slider:before {
  transform: translateX(18px);
}

.toggle-label {
  color: var(--t2);
  font: var(--f-label); letter-spacing: 1px;
}

/* Detail Panel - Right Side */
.detail-panel {
  position: absolute;
  top: 52px;
  right: 12px;
  width: 320px;
  max-height: calc(100% - 100px);
  background: var(--paper);
  border: 1px solid var(--ink);
  border-radius: 0;
  overflow: hidden;
  font-family: var(--mono);
  font: var(--f-body);
  z-index: 20;
  display: flex;
  flex-direction: column;
}

.detail-panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 16px;
  background: var(--ink);
  border-bottom: 1px solid var(--ink);
  flex-shrink: 0;
}

.detail-title {
  font-weight: 600;
  color: var(--paper);
  font: var(--f-body);
}

.detail-type-badge {
  padding: 4px 10px;
  border-radius: 0;
  font: var(--f-label); letter-spacing: 1px;
  margin-left: auto;
  margin-right: 12px;
  color: var(--paper);
  background: var(--ink);
}

.detail-close {
  background: none;
  border: none;
  font: var(--f-num);
  cursor: pointer;
  color: var(--paper);
  line-height: 1;
  padding: 0;
  transition: color 0.2s;
}

.detail-close:hover {
  color: var(--ink);
}

.detail-content {
  padding: 16px;
  overflow-y: auto;
  flex: 1;
}

.detail-row {
  margin-bottom: 12px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.detail-label {
  color: var(--t2);
  font: var(--f-value);
  font-weight: 400;
  min-width: 80px;
}

.detail-value {
  color: var(--ink);
  flex: 1;
  word-break: break-word;
}

.detail-value.uuid-text {
  font-family: var(--mono);
  font: var(--f-label); letter-spacing: 1px;
  color: var(--t2);
}

.detail-value.fact-text {
  line-height: 1.5;
  color: var(--ink);
}

.detail-section {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

.section-title {
  font: var(--f-value);
  font-weight: 600;
  color: var(--t2);
  margin-bottom: 10px;
}

.properties-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.property-item {
  display: flex;
  gap: 8px;
}

.property-key {
  color: var(--t2);
  font-weight: 400;
  min-width: 90px;
}

.property-value {
  color: var(--ink);
  flex: 1;
}

.summary-text {
  line-height: 1.6;
  color: var(--ink);
  font: var(--f-value);
}

.labels-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.label-tag {
  display: inline-block;
  padding: 4px 12px;
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: 0;
  font: var(--f-label); letter-spacing: 1px;
  color: var(--t2);
}

.episodes-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.episode-tag {
  display: inline-block;
  padding: 6px 10px;
  background: var(--bg);
  border: 1px solid var(--line);
  font-family: var(--mono);
  font: var(--f-label); letter-spacing: 1px;
  color: var(--t2);
  word-break: break-all;
}

/* Edge relation header */
.edge-relation-header {
  background: var(--bg);
  padding: 12px;
  border-radius: 0;
  margin-bottom: 16px;
  font: var(--f-body);
  font-weight: 400;
  color: var(--ink);
  line-height: 1.5;
  word-break: break-word;
}

/* Building hint */
.graph-building-hint {
  position: absolute;
  top: 48px;
  left: 12px;
  max-width: 360px;
  background: var(--ink);
  color: var(--paper);
  padding: 7px 10px;
  border-radius: 0;
  font: var(--f-label); letter-spacing: 1px;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--line-inv);
  letter-spacing: 0;
  z-index: 11;
}

.memory-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
  animation: breathe 2s ease-in-out infinite;
}

.memory-icon {
  width: 18px;
  height: 18px;
  color: var(--accent);
}

@keyframes breathe {
  0%, 100% { opacity: 0.7; transform: scale(1); }
  50% { opacity: 1; transform: scale(1.15); }
}

/* Styling for the simulation-finished hint */
.graph-building-hint.finished-hint {
  background: var(--overlay);
  border: 1px solid var(--line-inv);
}

.finished-hint .hint-icon-wrapper {
  display: flex;
  align-items: center;
  justify-content: center;
}

.finished-hint .hint-icon {
  width: 18px;
  height: 18px;
  color: var(--paper);
}

.finished-hint .hint-text {
  flex: 1;
  white-space: nowrap;
}

.hint-close-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  background: var(--overlay-button);
  border: none;
  border-radius: 0;
  cursor: pointer;
  color: var(--paper);
  transition: background-color var(--mf-motion-fast), transform var(--mf-motion-fast);
  margin-left: 8px;
  flex-shrink: 0;
}

.hint-close-btn:hover {
  background: var(--overlay-button-hover);
  transform: scale(1.1);
}

/* Loading spinner */
.loading-spinner {
  width: 40px;
  height: 40px;
  border: 3px solid var(--line);
  border-top-color: var(--accent);
  border-radius: 0;
  animation: spin 1s linear infinite;
  margin: 0 auto 16px;
}

/* Self-loop styles */
.self-loop-header {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--bg);
  border: 1px solid var(--line);
}

.self-loop-count {
  margin-left: auto;
  font: var(--f-label); letter-spacing: 1px;
  color: var(--t2);
  background: var(--paper);
  padding: 2px 8px;
  border-radius: 0;
}

.self-loop-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.self-loop-item {
  background: var(--bg);
  border: 1px solid var(--line);
  border-radius: 0;
}

.self-loop-item-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: var(--bg);
  cursor: pointer;
  transition: background 0.2s;
}

.self-loop-item-header:hover {
  background: var(--bg);
}

.self-loop-item.expanded .self-loop-item-header {
  background: var(--bg);
}

.self-loop-index {
  font: var(--f-label); letter-spacing: 1px;
  font-weight: 600;
  color: var(--t2);
  background: var(--line);
  padding: 2px 6px;
}

.self-loop-name {
  font: var(--f-value);
  font-weight: 400;
  color: var(--ink);
  flex: 1;
}

.self-loop-toggle {
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  font: var(--f-body);
  font-weight: 600;
  color: var(--t2);
  background: var(--line);
  transition: background-color var(--mf-motion-fast), color var(--mf-motion-fast);
}

.self-loop-item.expanded .self-loop-toggle {
  background: var(--line);
  color: var(--t2);
}

.self-loop-item-content {
  padding: 12px;
  border-top: 1px solid var(--line);
}

.self-loop-item-content .detail-row {
  margin-bottom: 8px;
}

.self-loop-item-content .detail-label {
  font: var(--f-label); letter-spacing: 1px;
  min-width: 60px;
}

.self-loop-item-content .detail-value {
  font: var(--f-value);
}

.self-loop-episodes {
  margin-top: 8px;
}

.episodes-list.compact {
  flex-direction: row;
  flex-wrap: wrap;
  gap: 4px;
}

.episode-tag.small {
  padding: 3px 6px;
  font: var(--f-label); letter-spacing: 1px;
}
</style>
