/**
 * Tests for `i18n/index.js`.
 *
 * Pins the module-load invariant: importing i18n must NEVER throw,
 * regardless of whether `globalThis.localStorage` is available. A bare
 * `localStorage.getItem('locale')` at module top-level throws
 * `ReferenceError` in test runners where the DOM mock does not surface
 * a window-bound localStorage on module evaluation. That break would
 * propagate into every test file that transitively imports i18n (e.g.
 * component suites may import it through Vue Router
 * → `i18n`), cascading one failure into many broken suites.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

describe('i18n module', () => {
  // Each test mutates `globalThis.localStorage` and re-imports the i18n
  // module so the top-level read happens against the test-specific
  // value. vitest's `vi.resetModules()` clears the module registry so
  // `await import(...)` evaluates the source fresh.
  let originalLocalStorage

  beforeEach(() => {
    originalLocalStorage = globalThis.localStorage
    vi.resetModules()
  })
  afterEach(() => {
    // Restore whatever the test env had.
    try { globalThis.localStorage = originalLocalStorage } catch (_) {}
  })

  it('imports cleanly when localStorage is completely absent', async () => {
    // Remove the global outright. The defensive read must NOT throw.
    try { delete globalThis.localStorage } catch (_) { globalThis.localStorage = undefined }
    const mod = await import('../index.js')
    expect(mod.default).toBeDefined()
    // Default locale falls through to 'en' (the only locale shipped).
    expect(mod.default.global.locale.value).toBe('en')
  }, 15000)

  it('imports cleanly when localStorage.getItem throws', async () => {
    globalThis.localStorage = {
      getItem: () => { throw new Error('SecurityError: sandboxed') },
      setItem: () => { throw new Error('SecurityError: sandboxed') }
    }
    const mod = await import('../index.js')
    expect(mod.default.global.locale.value).toBe('en')
  })

  it('honours a valid saved locale when present', async () => {
    let value = 'en'
    globalThis.localStorage = {
      getItem: (k) => (k === 'locale' ? value : null),
      setItem: (k, v) => { if (k === 'locale') value = v }
    }
    const mod = await import('../index.js')
    expect(mod.default.global.locale.value).toBe('en')
  })

  it('rewrites a stale locale (e.g. removed zh) back to en', async () => {
    let value = 'zh'  // zh.json was deleted in the CJK scrub
    globalThis.localStorage = {
      getItem: (k) => (k === 'locale' ? value : null),
      setItem: (k, v) => { if (k === 'locale') value = v }
    }
    const mod = await import('../index.js')
    expect(mod.default.global.locale.value).toBe('en')
    // And the persistence path wrote 'en' back.
    expect(value).toBe('en')
  })
})
