/**
 * Bundle-split gate: d3 MUST NOT land in lane/cockpit chunks.
 *
 * Static `import * as d3 from 'd3'` in any lane or cockpit module
 * would pull d3 into the first-view chunk (~80kB minified extra).
 * The lazy-load pattern in GraphPanel.vue keeps d3 out of every
 * modern lane bundle. This test asserts the property at the
 * source-level by grepping for static d3 imports inside lane code.
 *
 * Build-time verification (chunk inspection) lives in
 * `process/check_bundle_split.sh`; this test pins the source-level
 * rule so a `import * as d3 from 'd3'` reintroduction in lane
 * code fails CI before the build even runs.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

function _walk (dir) {
  const out = []
  for (const entry of readdirSync(dir)) {
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

const LANE_ROOTS = [
  'src/cockpit',
  'src/lanes',
]

const STATIC_D3_IMPORT_RE = /import\s+(?:\*\s+as\s+\w+\s+from\s+)?['"]d3['"]/

function _collect_lane_files () {
  const out = []
  for (const root of LANE_ROOTS) {
    try {
      const st = statSync(root)
      if (st.isDirectory()) {
        out.push(..._walk(root))
      } else if (st.isFile()) {
        out.push(root)
      }
    } catch {
      // Path does not exist (e.g. file deleted in a refactor) -> skip.
    }
  }
  return out
}

describe('bundle split: d3 stays out of every modern lane chunk', () => {
  it('no lane source imports d3 statically', () => {
    const offenders = []
    for (const file of _collect_lane_files()) {
      const src = readFileSync(file, 'utf-8')
      if (STATIC_D3_IMPORT_RE.test(src)) {
        offenders.push(file)
      }
    }
    expect(offenders).toEqual([])
  })

  it('GraphPanel.vue uses the lazy-load pattern, not static import', () => {
    // Belt-and-braces: the graph consumer is exempted from the
    // lane-source rule above, but they MUST use `await import('d3')`
    // rather than `import * as d3 from 'd3'` so the initial cockpit
    // chunk does not eager-load d3 when the user never opens graph
    // detail.
    const graphConsumers = [
      'src/components/GraphPanel.vue',
    ]
    for (const file of graphConsumers) {
      const src = readFileSync(file, 'utf-8')
      expect(
        STATIC_D3_IMPORT_RE.test(src),
        `${file} must not statically import d3 -- use the lazy loader`,
      ).toBe(false)
      expect(
        src.includes("await import('d3')") || src.includes('await import("d3")'),
        `${file} must lazy-load d3 via dynamic import`,
      ).toBe(true)
    }
  })
})
