import { createRouter, createWebHistory } from 'vue-router'
import { readDraft } from '../cockpit/draftStore.js'

const HomeView = () => import('../cockpit/HomeView.vue')
const CockpitShell = () => import('../cockpit/CockpitShell.vue')

/* The URL scheme (board 07):
     /                                     → S0 home
     /run/:id?step=scenario…findings       → the cockpit, one run
     /run/:id?step=findings&mode=read|ask|data
     #claim:c-03/acts · #node:n-18 · …     → summoned overlays
   Saved-run links address cockpit views. Draft contents stay in memory.

   There is NO compatibility surface: legacy /cockpit and /runs/:id/*
   URLs 404 — that is the entire migration posture. No rewriter exists,
   even as scaffolding. */

function runProps (route) {
  return {
    runId: route.params.runId || null,
    step: route.query.step || null,
    mode: route.query.mode || null,
    lane: route.params.runId ? null : (route.query.lane || null)
  }
}

const routes = [
  {
    path: '/',
    name: 'Home',
    component: HomeView
  },
  {
    path: '/run/:runId?',
    name: 'Run',
    component: CockpitShell,
    props: runProps
  }
]

export function createRawFishRouter (history = createWebHistory()) {
  const router = createRouter({ history, routes })
  router.beforeEach(to => {
    if (to.name !== 'Run') return
    if ('draft' in to.query) return { name: 'Home', replace: true }
    if (to.params.runId) {
      if ('lane' in to.query || 'source' in to.query) return { name: 'Home', replace: true }
    } else {
      const draft = readDraft()
      if (!draft || ('lane' in to.query && to.query.lane !== draft.lane) ||
          ('source' in to.query && (to.query.source !== 'paste' || draft.source !== 'paste'))) {
        return { name: 'Home', replace: true }
      }
    }
  })
  return router
}

export default createRawFishRouter()
