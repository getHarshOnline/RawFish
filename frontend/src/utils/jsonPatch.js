/**
 * Client-side JSON Patch validator for the counterfactual surface.
 *
 * The backend's `/scenarios/<sim_id>/counterfactual` route applies the
 * patch with RFC 6902 atomic-abort semantics and rejects ops that fall
 * outside a strict path allowlist:
 *
 *   - `/scenario/variants/<idx>/(name|description|kind|changed_assumptions)`
 *   - `/personas/<idx>/traits/<dotted-name>`
 *
 * It also rejects any tail containing `..`, `/diff`, `/base_variant_id`,
 * or `/variant_id` (defence against route-traversal / metadata
 * tampering). The op vocabulary is the three-op RFC 6902 subset:
 * `add`, `replace`, `remove`. `add` and `replace` require a non-empty
 * value; `remove` MUST NOT carry one.
 *
 * The frontend mirrors these checks so that:
 *   (a) the JsonPatchEditor surfaces inline error text per row, and
 *   (b) the canSubmitCounterfactual gate refuses to enable the submit
 *       button while ANY op is invalid. Both consumers MUST use this
 *       module so the inline message and the disabled-button state
 *       never disagree.
 *
 * Every function is pure; no DOM, no store reads.
 */

/** Forbidden path substrings -- mirrors the backend allow-deny pair. */
export const FORBIDDEN_PATH_SUBSTRINGS = Object.freeze([
  '..', '/diff', '/base_variant_id', '/variant_id'
])

/** Strict regex matching the two allowlisted path families. */
export const ALLOWED_PATH_RE =
  /^\/scenario\/variants\/\d+\/(name|description|kind|changed_assumptions)$|^\/personas\/\d+\/traits\/[A-Za-z0-9_.]+$/

/** Allowed RFC 6902 op vocabulary on this surface. */
export const ALLOWED_OPS = Object.freeze(['add', 'replace', 'remove'])

/**
 * Validate a single op. Returns either '' (op is valid) or a short
 * human-readable string describing the FIRST violation found. The
 * caller controls the rendering (an inline cell, a tooltip, a console
 * warning, etc.).
 *
 * @param {object} op    `{op, path, value?}` JSONPatchOp candidate.
 * @returns {string}     empty string when valid; error message otherwise.
 */
export function validateJsonPatchOp (op) {
  if (!op || typeof op !== 'object') return 'Op is empty.'
  if (!ALLOWED_OPS.includes(op.op)) {
    return `Op must be one of ${ALLOWED_OPS.join(', ')}.`
  }
  if (typeof op.path !== 'string' || op.path.length === 0) {
    return 'Path is required.'
  }
  for (const sub of FORBIDDEN_PATH_SUBSTRINGS) {
    if (op.path.includes(sub)) {
      return `Path contains forbidden substring "${sub}".`
    }
  }
  if (!ALLOWED_PATH_RE.test(op.path)) {
    return 'Path does not match the allowlist.'
  }
  if (op.op === 'add' || op.op === 'replace') {
    if (op.value === undefined || op.value === null || op.value === '') {
      return 'add/replace require a non-empty value.'
    }
  }
  // RFC 6902: remove must NOT carry a value. Tolerate `value === ''`
  // because the editor stashes that on op-kind transitions; the
  // counterfactual panel's coerce step strips it before submit.
  return ''
}

/**
 * Validate a list of ops. Returns `{valid, issues}` where:
 *   - `valid`   is `true` ONLY if every op passed AND the list is non-empty.
 *   - `issues`  is `[{index, message}]` for every failing op.
 *
 * @param {Array} ops
 * @returns {{valid: boolean, issues: Array<{index: number, message: string}>}}
 */
export function validateJsonPatchOps (ops) {
  const list = Array.isArray(ops) ? ops : []
  if (list.length === 0) {
    return { valid: false, issues: [{ index: -1, message: 'At least one op required.' }] }
  }
  const issues = []
  list.forEach((op, index) => {
    const msg = validateJsonPatchOp(op)
    if (msg) issues.push({ index, message: msg })
  })
  return { valid: issues.length === 0, issues }
}
