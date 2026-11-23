import { expect, test } from '@playwright/test'

async function captureUiFrame (page, name) {
  const dir = process.env.RAWFISH_CAPTURE_UI_DIR
  if (!dir) return
  const root = dir.endsWith('/') ? dir.slice(0, -1) : dir
  await page.screenshot({ path: `${root}/${name}.png`, fullPage: true })
}

const laneManifest = {
  lane_id: 'creator',
  label: 'Creator',
  summary: 'Stress-test creator audience response.',
  accent: '#d65438',
  cohorts: [
    { id: 'fans', label: 'Fans' },
    { id: 'skeptics', label: 'Skeptics' }
  ],
  report_sections: [{ id: 'risk', label: 'Risk' }],
  analysis_panels: { compare: { enabled: true } },
  eval_instruments: { cost_preflight: { budget_cap: 500 } },
  default_provider: { mode: 'local_ollama', model: 'qwen3-coder:30b' },
  connector_capabilities: [
    { connector_id: 'creator.seed', label: 'Smart console paste', role: 'seed' }
  ]
}

const statisticalPayload = {
  manifest: {
    lane_id: 'creator',
    version: '0.1',
    fields: [
      {
        id: 'run.ensemble_n',
        label: 'Ensemble size',
        kind: 'integer',
        control: 'slider_number',
        default: 5,
        min: 1,
        max: 40,
        step: 1,
        impact_hint: 'Higher values cost more.',
        provenance: ['operator']
      },
      {
        id: 'cohort.active_hours',
        label: 'Active hours',
        kind: 'active_hours',
        control: 'active_hours_grid',
        default: [9, 10],
        impact_hint: 'Constrains timing.',
        provenance: ['operator']
      }
    ]
  },
  default_config: {
    lane_id: 'creator',
    version: '0.1',
    field_ids: ['run.ensemble_n', 'cohort.active_hours'],
    settings: {
      'run.ensemble_n': 5,
      'cohort.active_hours': [9, 10]
    },
    statistical_settings_hash: 'sha256:browser-default'
  }
}

const statusPayload = {
  simulation_id: 'sim_browser',
  use_case_id: 'creator',
  phase: 'completed',
  warnings: [],
  errors: [],
  graph_data: {
    nodes: [
      { id: 'sim_browser', label: 'Browser run' },
      { id: 'fans', label: 'Fans' }
    ],
    edges: [{ source: 'sim_browser', target: 'fans', label: 'observed' }]
  }
}

const reportPayload = {
  report_id: 'report_browser',
  simulation_id: 'sim_browser',
  executive_summary: [
    {
      claim_id: 'claim_browser_1',
      tag: 'inference',
      text: 'Browser smoke claim: ownership ambiguity is the sharpest risk.',
      evidence_refs: [{ ref_type: 'simulation_action', ref_id: 'action-browser-1' }]
    }
  ],
  sections: [
    {
      id: 'risk',
      title: 'Risk',
      claims: [
        {
          claim_id: 'claim_browser_2',
          tag: 'requires_validation',
          text: 'Skeptics ask for clearer rollout ownership.',
          evidence_refs: [{ ref_type: 'seed_text_line', ref_id: 'seed:1' }]
        }
      ]
    }
  ],
  evidence_gaps: [],
  validity_flags: []
}

function envelope (data) {
  return { success: true, data }
}

async function fulfillJson (route, data, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(data)
  })
}

async function mockRawFishApi (page) {
  let runPhase = 'created'
  let reportReady = false
  await page.route('**/api/sim/use-cases**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    const method = request.method()

    if (method === 'GET' && path === '/api/sim/use-cases') {
      return fulfillJson(route, envelope({
        lane_ids: ['creator'],
        use_cases: [{ use_case_id: 'creator', lane_manifest: laneManifest }]
      }))
    }

    if (method === 'GET' && path === '/api/sim/use-cases/creator/catalog') {
      return fulfillJson(route, envelope({
        use_case_id: 'creator',
        lane_manifest: laneManifest,
        personas: [{
          persona_id: 'video_creator',
          persona: 'video_creator',
          segment_types_by_scope: { null: ['loyal_subscriber'] }
        }],
        segment_types_by_persona: { video_creator: ['loyal_subscriber'] }
      }))
    }

    if (method === 'POST' && path === '/api/sim/use-cases/creator/scenario/parse') {
      const body = request.postDataJSON()
      return fulfillJson(route, envelope({
        use_case_id: 'creator',
        parse_source: 'fallback',
        parser: 'adapter.parse_seed',
        scenario_draft: {
          seed_text: body.seed_text,
          stress_question: body.stress_question || body.seed_text
        }
      }))
    }

    if (method === 'GET' && path === '/api/sim/use-cases/creator/statistical-manifest') {
      return fulfillJson(route, envelope(statisticalPayload))
    }

    if (method === 'POST' && path === '/api/sim/use-cases/creator/evals/preflight_meter') {
      return fulfillJson(route, envelope({
        scale_product: 160,
        scale_cap: 500,
        scale_ok: true,
        token_estimate: 24000,
        provider: { mode: 'local_ollama', model: 'qwen3-coder:30b' },
        provider_readiness: { ready: true, status: 'configured', reason: null },
        estimated_latency_ms: null,
        estimated_external_cost: null
      }))
    }

    if (method === 'POST' && path === '/api/sim/use-cases/creator/simulation/create') {
      runPhase = 'created'
      return fulfillJson(route, envelope({
        simulation_id: 'sim_browser',
        use_case_id: 'creator',
        phase: 'created'
      }), 201)
    }

    if (method === 'POST' && path === '/api/sim/use-cases/creator/simulation/sim_browser/prepare') {
      runPhase = 'prepared'
      return fulfillJson(route, envelope({
        simulation_id: 'sim_browser',
        use_case_id: 'creator',
        phase: 'prepared'
      }))
    }

    if (method === 'POST' && path === '/api/sim/use-cases/creator/simulation/sim_browser/start') {
      runPhase = 'completed'
      return fulfillJson(route, envelope({
        ...statusPayload,
        phase: runPhase
      }))
    }

    if (method === 'GET' && path === '/api/sim/use-cases/creator/simulation/sim_browser/status') {
      return fulfillJson(route, envelope({
        ...statusPayload,
        phase: runPhase
      }))
    }

    if (method === 'GET' && path === '/api/sim/use-cases/creator/simulation/sim_browser/graph') {
      return fulfillJson(route, envelope({
        graph_data: statusPayload.graph_data,
        source: 'browser-smoke'
      }))
    }

    if (method === 'GET' && path === '/api/sim/use-cases/creator/simulation/sim_browser/statistical-config') {
      return fulfillJson(route, envelope({
        ...statisticalPayload.default_config,
        statistical_settings_hash: 'sha256:browser-run'
      }))
    }

    if (method === 'GET' && path === '/api/sim/use-cases/creator/simulation/sim_browser/statistical-variants') {
      return fulfillJson(route, envelope({
        variants: []
      }))
    }

    if (method === 'GET' && path === '/api/sim/use-cases/creator/simulation/sim_browser/statistical-lineage') {
      return fulfillJson(route, envelope({
        lineage: []
      }))
    }

    if (method === 'POST' && path === '/api/sim/use-cases/creator/simulation/sim_browser/report/generate') {
      reportReady = false
      return fulfillJson(route, envelope({
        job_id: 'job_browser',
        use_case_id: 'creator',
        simulation_id: 'sim_browser',
        status: 'queued'
      }), 202)
    }

    if (method === 'GET' && path === '/api/sim/use-cases/creator/simulation/sim_browser/report/jobs/job_browser') {
      reportReady = true
      return fulfillJson(route, envelope({
        job_id: 'job_browser',
        use_case_id: 'creator',
        simulation_id: 'sim_browser',
        status: 'succeeded',
        response: { success: true }
      }))
    }

    if (method === 'GET' && path === '/api/sim/use-cases/creator/simulation/sim_browser/report') {
      if (!reportReady) {
        return fulfillJson(route, {
          success: false,
          error: {
            code: 'REPORT_NOT_READY',
            message: 'report is not ready yet'
          }
        }, 404)
      }
      return fulfillJson(route, envelope(reportPayload))
    }

    if (method === 'POST' && path === '/api/sim/use-cases/creator/simulation/sim_browser/report/chat') {
      return fulfillJson(route, envelope({
        response: 'Ownership ambiguity is the sharpest risk.',
        sources: []
      }))
    }

    return route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ error: `unmocked ${method} ${path}` })
    })
  })
}

test.beforeEach(async ({ page }) => {
  await mockRawFishApi(page)
})

test('launches, runs preflight, generates report, and chats', async ({ page }) => {
  await page.goto('/')

  /* S0 · Home — draft console, source posture chips, lane rail */
  await expect(page.getByRole('heading', { name: 'What do you want to stress-test?' })).toBeVisible()
  await expect(page.getByLabel('Source posture')).toBeVisible()
  await page.getByLabel('Source posture').getByRole('button', { name: 'prepared graph', exact: true }).click()
  await page.getByRole('textbox').fill('Browser smoke seed: clarify rollout ownership.')
  await page.getByRole('button', { name: /preview parse/ }).click()
  await expect(page.getByText('fallback · adapter.parse_seed')).toBeVisible()
  await captureUiFrame(page, 'rawfish-home')
  await page.getByRole('button', { name: /Draft scenario/ }).click()

  /* S1 · Scenario — dock solo, no stage, idle console */
  await expect(page).toHaveURL(/\/run\?/)
  await expect(page.getByRole('heading', { name: 'Scenario' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Split' })).toHaveCount(0)
  await expect(page.locator('.rf-z2')).toHaveCount(0)
  await expect(page.locator('.rf-z3')).toBeVisible()
  await expect(page.getByLabel('System console')).toBeVisible()
  await captureUiFrame(page, 'rawfish-cockpit-scenario')

  /* S1 → S2 · Build step, then create → prepare (idempotency-keyed) */
  await page.getByRole('button', { name: /Build the world/ }).click()
  await expect(page).toHaveURL(/\/run\?step=build/)
  await expect(page.getByRole('heading', { name: 'Build' })).toBeVisible()
  await page.getByRole('button', { name: /Build the world/ }).click()
  await expect(page).toHaveURL(/\/run\/sim_browser\?step=build/)
  await expect(page.locator('.rf-z2')).toBeVisible()
  await captureUiFrame(page, 'rawfish-cockpit-build')

  /* S3 · Cohorts */
  await page.getByRole('button', { name: /Tune cohorts/ }).click()
  await expect(page.getByRole('heading', { name: 'Cohorts' })).toBeVisible()
  await captureUiFrame(page, 'rawfish-cockpit-cohorts')

  /* S4 · Preflight — the deliberate act */
  await page.getByRole('button', { name: /Continue to preflight/ }).click()
  await expect(page.getByRole('heading', { name: 'Preflight' })).toBeVisible()
  await captureUiFrame(page, 'rawfish-cockpit-preflight')
  await page.getByRole('button', { name: /Launch swarm/ }).click()

  /* S5 · Simulate */
  await expect(page).toHaveURL(/\/run\/sim_browser\?step=simulate/)
  await expect(page.getByRole('heading', { name: 'Simulate' })).toBeVisible()
  await captureUiFrame(page, 'rawfish-cockpit-simulate')

  /* S6 · Findings · read — generate, claims, evidence links */
  await page.getByRole('button', { name: /06\s*Findings/ }).click()
  await expect(page.getByRole('heading', { name: 'Findings' })).toBeVisible()
  await page.getByRole('button', { name: /Generate report/ }).click()
  await expect(page.getByText('Browser smoke claim: ownership ambiguity is the sharpest risk.')).toBeVisible()
  await expect(page.getByRole('button', { name: /evidence · 1/ })).toHaveCount(2)
  await captureUiFrame(page, 'rawfish-cockpit-findings')

  /* S6 · Findings · ask — the ReportAgent grounded in this run.
     The mode switch is an enum (role=radiogroup), never buttons/tabs. */
  await page.getByRole('radio', { name: 'ask' }).click()
  await page.getByRole('textbox').fill('What is the sharpest risk?')
  await page.getByRole('button', { name: /Ask/ }).click()
  await expect(page.getByText('Ownership ambiguity is the sharpest risk.', { exact: true })).toBeVisible()
  await captureUiFrame(page, 'rawfish-cockpit-ask')
})
