/**
 * Tests for `utils/idempotency.js`.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  newIdempotencyKey,
  getOrCreateIdempotencyKey,
  idempotencyHeaders
} from '../idempotency.js'

describe('newIdempotencyKey', () => {
  it('produces a string prefixed with mf_', () => {
    const k = newIdempotencyKey()
    expect(typeof k).toBe('string')
    expect(k.startsWith('mf_')).toBe(true)
    expect(k.length).toBeGreaterThan(10)
  })

  it('produces distinct keys on successive calls', () => {
    const a = newIdempotencyKey()
    const b = newIdempotencyKey()
    expect(a).not.toBe(b)
  })
})

describe('getOrCreateIdempotencyKey', () => {
  let target

  beforeEach(() => {
    target = {}
  })

  it('mints a fresh key on first call and stashes it', () => {
    const k = getOrCreateIdempotencyKey(target)
    expect(typeof k).toBe('string')
    expect(target.idempotencyKey).toBe(k)
  })

  it('returns the cached key on subsequent calls', () => {
    const k1 = getOrCreateIdempotencyKey(target)
    const k2 = getOrCreateIdempotencyKey(target)
    expect(k2).toBe(k1)
  })

  it('honours a custom field name', () => {
    const k = getOrCreateIdempotencyKey(target, 'createKey')
    expect(target.createKey).toBe(k)
    expect(target.idempotencyKey).toBeUndefined()
  })

  it('returns a fresh key when target is null', () => {
    const k = getOrCreateIdempotencyKey(null)
    expect(typeof k).toBe('string')
    expect(k.startsWith('mf_')).toBe(true)
  })
})

describe('idempotencyHeaders', () => {
  it('builds an axios-style config with the header set', () => {
    const cfg = idempotencyHeaders('mf_test_123')
    expect(cfg.headers['Idempotency-Key']).toBe('mf_test_123')
  })

  it('merges with an existing axios config', () => {
    const cfg = idempotencyHeaders('mf_x', {
      timeout: 1000,
      headers: { 'X-Custom': 'y' }
    })
    expect(cfg.timeout).toBe(1000)
    expect(cfg.headers['X-Custom']).toBe('y')
    expect(cfg.headers['Idempotency-Key']).toBe('mf_x')
  })
})
