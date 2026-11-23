/**
 * Tests for `utils/jsonPatch.js`.
 *
 * The validator is consumed by BOTH the JsonPatchEditor (inline error
 * text per row) and `canSubmitCounterfactual` (gate the submit button).
 * These tests pin the contract both consumers depend on.
 */
import { describe, it, expect } from 'vitest'
import {
  validateJsonPatchOp, validateJsonPatchOps,
  ALLOWED_OPS, ALLOWED_PATH_RE, FORBIDDEN_PATH_SUBSTRINGS
} from '../jsonPatch.js'

describe('canonical surface', () => {
  it('exposes the 3-op RFC 6902 subset the backend allows', () => {
    expect(ALLOWED_OPS).toEqual(['add', 'replace', 'remove'])
  })
  it('pins the 4 forbidden substrings the backend rejects', () => {
    expect(FORBIDDEN_PATH_SUBSTRINGS).toEqual(
      ['..', '/diff', '/base_variant_id', '/variant_id']
    )
  })
  it('allowlist regex covers both /scenario/variants and /personas/traits families', () => {
    expect(ALLOWED_PATH_RE.test('/scenario/variants/0/name')).toBe(true)
    expect(ALLOWED_PATH_RE.test('/scenario/variants/0/description')).toBe(true)
    expect(ALLOWED_PATH_RE.test('/scenario/variants/2/kind')).toBe(true)
    expect(ALLOWED_PATH_RE.test('/scenario/variants/0/changed_assumptions')).toBe(true)
    expect(ALLOWED_PATH_RE.test('/personas/0/traits/big5.openness')).toBe(true)
    expect(ALLOWED_PATH_RE.test('/personas/3/traits/sociability')).toBe(true)
  })
  it('allowlist regex rejects siblings + free-form fields', () => {
    expect(ALLOWED_PATH_RE.test('/scenario/title')).toBe(false)
    expect(ALLOWED_PATH_RE.test('/scenario/variants/0/id')).toBe(false)
    expect(ALLOWED_PATH_RE.test('/personas/0/name')).toBe(false)
  })
})

describe('validateJsonPatchOp', () => {
  it('returns "" for a valid replace on the scenario family', () => {
    expect(validateJsonPatchOp({
      op: 'replace', path: '/scenario/variants/0/name', value: 'X'
    })).toBe('')
  })
  it('returns "" for a valid add on the personas/traits family', () => {
    expect(validateJsonPatchOp({
      op: 'add', path: '/personas/0/traits/openness', value: 0.5
    })).toBe('')
  })
  it('returns "" for a valid remove (no value)', () => {
    expect(validateJsonPatchOp({
      op: 'remove', path: '/scenario/variants/0/description'
    })).toBe('')
  })

  it('flags an unknown op', () => {
    expect(validateJsonPatchOp({ op: 'move', path: '/x' }))
      .toMatch(/Op must be one of/)
  })
  it('flags a missing/empty path', () => {
    expect(validateJsonPatchOp({ op: 'add', path: '', value: 1 }))
      .toMatch(/Path is required/)
  })
  it('flags every forbidden substring', () => {
    for (const sub of FORBIDDEN_PATH_SUBSTRINGS) {
      const result = validateJsonPatchOp({
        op: 'replace',
        path: `/scenario/variants/0/name${sub}`,
        value: 'X'
      })
      expect(result).toContain(sub)
    }
  })
  it('flags an off-allowlist path', () => {
    expect(validateJsonPatchOp({
      op: 'replace', path: '/scenario/title', value: 'X'
    })).toMatch(/Path does not match the allowlist/)
  })
  it('flags add/replace with empty value', () => {
    expect(validateJsonPatchOp({
      op: 'add', path: '/personas/0/traits/x', value: ''
    })).toMatch(/non-empty value/)
    expect(validateJsonPatchOp({
      op: 'replace', path: '/personas/0/traits/x', value: null
    })).toMatch(/non-empty value/)
    expect(validateJsonPatchOp({
      op: 'replace', path: '/personas/0/traits/x'
    })).toMatch(/non-empty value/)
  })
  it('treats a null op as the empty-op message', () => {
    expect(validateJsonPatchOp(null)).toBe('Op is empty.')
  })
})

describe('validateJsonPatchOps', () => {
  it('rejects an empty list', () => {
    const r = validateJsonPatchOps([])
    expect(r.valid).toBe(false)
    expect(r.issues[0].message).toMatch(/At least one op required/)
  })
  it('accepts a list where every op is valid', () => {
    const r = validateJsonPatchOps([
      { op: 'replace', path: '/scenario/variants/0/name', value: 'X' },
      { op: 'add', path: '/personas/1/traits/openness', value: 0.7 }
    ])
    expect(r.valid).toBe(true)
    expect(r.issues).toEqual([])
  })
  it('reports the index of every failing op', () => {
    const r = validateJsonPatchOps([
      { op: 'replace', path: '/scenario/variants/0/name', value: 'X' },
      { op: 'add', path: '/scenario/title', value: 'Bad' },
      { op: 'remove', path: '/scenario/variants/0/diff' }
    ])
    expect(r.valid).toBe(false)
    expect(r.issues.map((i) => i.index)).toEqual([1, 2])
    expect(r.issues[1].message).toContain('/diff')
  })
})
