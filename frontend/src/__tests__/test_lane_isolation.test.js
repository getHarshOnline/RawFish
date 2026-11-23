/**
 * Lane-isolation lint gate.
 *
 * Any lane component (anything under `src/lanes/`) MUST NOT:
 *   * import removed direct policy API clients,
 *   * import removed policy styles,
 *   * use `pol-*` CSS classes in templates or `<style>` blocks.
 *
 * The same rules apply to shared platform modules if that directory
 * exists. RawFish's active cockpit reads lane manifests and the
 * generic use-case API client instead of the old policy mini-app.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


function _walk (dir) {
  const out = []
  let entries
  try {
    entries = readdirSync(dir)
  } catch {
    return out
  }
  for (const entry of entries) {
    const p = join(dir, entry)
    const st = statSync(p)
    if (st.isDirectory()) {
      out.push(..._walk(p))
    } else if (
      entry.endsWith('.vue')
      || entry.endsWith('.js')
      || entry.endsWith('.mjs')
    ) {
      out.push(p)
    }
  }
  return out
}

// Lanes that are scoped by the lane-isolation rules.
const LANE_ROOTS = [
  'src/lanes',
]

// Shared platform code that MUST stay lane-agnostic.
const PLATFORM_ROOTS = [
  'src/platform',
]

const POLICY_API_IMPORT_RE = /from\s+['"](?:\.\.\/)*api\/policy(?:\.js)?['"]/
const POLICY_CSS_IMPORT_RE = /(?:from|@import)\s+['"](?:[./]*)styles\/policy\.css['"]/
const POL_CLASS_RE = /\bpol-[a-zA-Z0-9_-]+\b/


function _collect (roots) {
  const out = []
  for (const root of roots) {
    try {
      const st = statSync(root)
      if (st.isDirectory()) out.push(..._walk(root))
    } catch {
      // Path does not exist (e.g. directory deleted) -> skip.
    }
  }
  return out
}


describe('lane isolation: lanes avoid removed direct-policy surfaces', () => {
  it('lane components do not import removed policy API clients', () => {
    const offenders = []
    for (const file of _collect(LANE_ROOTS)) {
      const src = readFileSync(file, 'utf-8')
      if (file.includes('__tests__')) continue
      if (POLICY_API_IMPORT_RE.test(src)) {
        offenders.push(file)
      }
    }
    expect(offenders).toEqual([])
  })

  it('lane components do not import styles/policy.css', () => {
    const offenders = []
    for (const file of _collect(LANE_ROOTS)) {
      const src = readFileSync(file, 'utf-8')
      if (POLICY_CSS_IMPORT_RE.test(src)) {
        offenders.push(file)
      }
    }
    expect(offenders).toEqual([])
  })

  it('lane components do not use pol-* CSS selectors', () => {
    const offenders = []
    for (const file of _collect(LANE_ROOTS)) {
      const src = readFileSync(file, 'utf-8')
      if (file.includes('__tests__')) continue
      if (POL_CLASS_RE.test(src)) {
        offenders.push(file)
      }
    }
    expect(offenders).toEqual([])
  })
})


describe('platform shell stays lane-agnostic', () => {
  it('src/platform/* does not import removed policy API clients', () => {
    const offenders = []
    for (const file of _collect(PLATFORM_ROOTS)) {
      const src = readFileSync(file, 'utf-8')
      if (file.includes('__tests__')) continue
      if (POLICY_API_IMPORT_RE.test(src)) {
        offenders.push(file)
      }
    }
    expect(offenders).toEqual([])
  })

  it('src/platform/* does not import policy.css', () => {
    const offenders = []
    for (const file of _collect(PLATFORM_ROOTS)) {
      const src = readFileSync(file, 'utf-8')
      if (POLICY_CSS_IMPORT_RE.test(src)) {
        offenders.push(file)
      }
    }
    expect(offenders).toEqual([])
  })

  it('src/platform/* does not use pol-* CSS selectors (mf-* only)', () => {
    // The shared shell + composables MUST stay lane-agnostic. A
    // `pol-*` class in a shared shell/composable would
    // silently bake policy-styled assumptions into the shared
    // primitives.
    const offenders = []
    for (const file of _collect(PLATFORM_ROOTS)) {
      const src = readFileSync(file, 'utf-8')
      if (file.includes('__tests__')) continue
      if (POL_CLASS_RE.test(src)) {
        offenders.push(file)
      }
    }
    expect(offenders).toEqual([])
  })
})


describe('removed frontend source is absent', () => {
  it('keeps removed wizard and policy mini-app files out of src', () => {
    const removedPaths = [
      `src/api/${'graph'}.js`,
      `src/api/${'policy'}.js`,
      `src/api/${'report'}.js`,
      `src/api/${'simulation'}.js`,
      'src/components/HistoryDatabase.vue',
      'src/components/Step1GraphBuild.vue',
      'src/components/Step2EnvSetup.vue',
      'src/components/Step3Simulation.vue',
      'src/components/Step4Report.vue',
      'src/components/Step5Interaction.vue',
      'src/store/policyDraft.js',
      'src/styles/mf-primitives.css',
      'src/styles/policy.css',
      'src/utils/policyReport.js',
      'src/utils/policyScenario.js',
      'src/utils/useMobileLayout.js'
    ]
    const present = removedPaths.filter(path => {
      try {
        statSync(path)
        return true
      } catch {
        return false
      }
    })
    expect(present).toEqual([])
  })
})
