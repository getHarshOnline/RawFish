/**
 * Idempotency-Key generation + reuse helpers.
 *
 * The backend's policy POST surface honours the `Idempotency-Key`
 * header on every mutating endpoint. The fingerprint folds method +
 * route pattern + path params (`view_args`) + body bytes, so:
 *
 *   - Two POSTs with the SAME key + SAME body + SAME path params
 *     replay the cached response (200 → 200, identical bytes).
 *   - SAME key + DIFFERENT body → 422 `IDEMPOTENCY_KEY_MISMATCH`.
 *   - SAME key + SAME body but DIFFERENT path param (e.g. a different
 *     `sim_id`) → 422 `IDEMPOTENCY_KEY_MISMATCH` (NOT a silent
 *     cross-resource replay; the view_args fold makes this safe).
 *
 * Frontend contract: every user action that mutates state generates
 * ONE key and reuses it across retries of that same action. Distinct
 * actions get distinct keys. A "Run again" button that re-submits the
 * same logical operation generates a fresh key (the user explicitly
 * wants a new run, not a cached replay).
 *
 * The generator prefers `crypto.randomUUID()` (Web Crypto, available in
 * every Vite-supported Node/browser version) and falls back to a
 * timestamp + Math.random hybrid for ancient environments. The fallback
 * is NOT cryptographically random; the server-side gate uses the key
 * only as an opaque slot identifier so collision probability inside a
 * single user session is the only consideration.
 */

/**
 * Generate a fresh Idempotency-Key.
 *
 * @returns {string} a key suitable for the `Idempotency-Key` header.
 *
 * The key starts with `mf_` so an operator inspecting backend
 * `.idempotency/` cache files can trivially distinguish this client's
 * traffic from any other consumer that might share the surface in the
 * future (e.g. a CLI tool, a CI smoke test).
 */
export function newIdempotencyKey () {
  if (typeof globalThis !== 'undefined'
      && globalThis.crypto
      && typeof globalThis.crypto.randomUUID === 'function') {
    return `mf_${globalThis.crypto.randomUUID()}`
  }
  // Fallback for old envs. Combine wall clock + two Math.random draws
  // so a same-tick burst still produces distinct keys.
  const ts = Date.now().toString(36)
  const r1 = Math.random().toString(36).slice(2, 10)
  const r2 = Math.random().toString(36).slice(2, 10)
  return `mf_${ts}_${r1}${r2}`
}

/**
 * Lazily cache an Idempotency-Key on a target object under a stable
 * property name, returning the cached value on every subsequent call.
 * Useful for retry flows: the first call mints + stashes the key; every
 * retry pulls the same key so the server replays the cached response
 * instead of double-executing.
 *
 * @param {object} target  the object to stash the key on (e.g. a
 *                         component-local refs object or a store slot).
 * @param {string} field   the property name (defaults to `idempotencyKey`).
 * @returns {string}       the cached or newly minted key.
 */
export function getOrCreateIdempotencyKey (target, field = 'idempotencyKey') {
  if (target && typeof target === 'object') {
    if (typeof target[field] === 'string' && target[field].length > 0) {
      return target[field]
    }
    const key = newIdempotencyKey()
    target[field] = key
    return key
  }
  // Defensive: if no target was given, just mint and return; the caller
  // is responsible for stashing it where appropriate.
  return newIdempotencyKey()
}

/**
 * Build an axios-style config object whose `headers` carries the given
 * Idempotency-Key. Convenience wrapper so callers do not repeat the
 * header shape inline.
 *
 * @param {string} key   the Idempotency-Key value.
 * @param {object} [extra]  optional additional headers / axios config
 *                          to merge with the result.
 * @returns {object}    `{ headers: { 'Idempotency-Key': key, ...extra.headers } }`.
 */
export function idempotencyHeaders (key, extra = {}) {
  const baseHeaders = extra && extra.headers ? extra.headers : {}
  return {
    ...extra,
    headers: {
      ...baseHeaders,
      'Idempotency-Key': key
    }
  }
}
