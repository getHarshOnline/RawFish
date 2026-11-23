import axios from 'axios'
import i18n from '../i18n'

// Create axios instance.
// The default baseURL is empty (same-origin) so /api/* calls flow through
// Vite's dev/preview proxy to the backend. A non-empty baseURL would
// bypass the proxy and trigger CORS rejection against the backend's
// origin allowlist. To target a remote backend (e.g. a containerised
// deployment), set VITE_API_BASE_URL to the absolute backend URL at
// build time.
const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '',
  timeout: 300000, // 5-minute timeout (ontology generation may take a while)
  headers: {
    'Content-Type': 'application/json'
  }
})

// API Key: the backend enforces authentication on /api/* by default,
// so the frontend must include X-API-Key on every request.
// Note: VITE_API_KEY bundled into the client is [extractable from the JS bundle by any visitor], so for
// "public deployments" it only blocks script-based abuse that doesn't load the page — it cannot serve as
// a multi-tenant isolation mechanism. True multi-tenant scenarios should switch to session-based login
// authentication, or have the gateway inject per-user issued tokens; this value is sufficient for
// single-machine / intranet / behind-gateway deployments.
const API_KEY = import.meta.env.VITE_API_KEY || ''

// Request interceptor
service.interceptors.request.use(
  config => {
    config.headers['Accept-Language'] = i18n.global.locale.value
    if (API_KEY) {
      config.headers['X-API-Key'] = API_KEY
    }
    return config
  },
  error => {
    console.error('Request error:', error)
    return Promise.reject(error)
  }
)

// Structured-error preservation.
//
// RawFish product APIs return `{success:false, error:{code, message, details}}`.
// The normalizer also accepts older flat-string errors so generic transport
// failures still surface as structured `ApiError` instances.
//
// `ApiError` normalises both into the same `{code, message, details}`
// triple so callers branch on `err.code` instead of parsing message
// text. `message` is intentionally kept as a STRING so existing call
// sites that do `t('errors.x', { error: err.message })` continue to
// work. Transport-layer failures (no `error.response`) are tagged with
// `code='TRANSPORT'` so call sites can distinguish "server said no"
// from "couldn't reach server".
export class ApiError extends Error {
  constructor({ message, code, details, apiError, cause }) {
    super(message)
    this.name = 'ApiError'
    this.code = code || 'UNKNOWN'
    this.details = details || {}
    this.apiError = apiError || null   // raw error payload (string OR object) from backend
    if (cause) this.cause = cause
  }
}

// Lift a backend error payload (`res.error` -- may be a string or a {code,message,details}
// object) into a normalized {code, message, details} triple. The single source of truth
// for ApiError construction below.
function _liftErrorPayload(apiError, fallbackMessage) {
  if (apiError && typeof apiError === 'object') {
    return {
      code: apiError.code || 'API_ERROR',
      message: apiError.message || fallbackMessage || 'Error',
      details: apiError.details || {},
    }
  }
  if (typeof apiError === 'string' && apiError.length > 0) {
    return { code: 'API_ERROR', message: apiError, details: {} }
  }
  return { code: 'API_ERROR', message: fallbackMessage || 'Error', details: {} }
}

// Turn an axios/native error into an ApiError. Used by the response interceptor's
// error branch.
function normalizeApiError(rawError) {
  // HTTP error with a body: prefer the server's structured envelope.
  // `respData` may be an OBJECT (normal JSON response) OR a STRING (when
  // the call site set `responseType: 'text'`, e.g. the markdown report
  // endpoint). Parse string bodies that look like JSON so callers still
  // get the structured `{code, message, details}` discriminator instead
  // of a flat TRANSPORT code -- the markdown deep-link in particular
  // depends on `err.code === 'REPORT_NOT_READY'` to choose the empty
  // state over an error banner.
  let respData = rawError && rawError.response && rawError.response.data
  if (typeof respData === 'string' && respData.trimStart().startsWith('{')) {
    try { respData = JSON.parse(respData) } catch (_e) { /* leave as string */ }
  }
  if (respData && typeof respData === 'object' && 'error' in respData) {
    const lifted = _liftErrorPayload(respData.error, rawError.message)
    return new ApiError({
      message: lifted.message,
      code: lifted.code,
      details: lifted.details,
      apiError: respData.error,
      cause: rawError,
    })
  }
  // Timeout
  if (rawError && rawError.code === 'ECONNABORTED' &&
      typeof rawError.message === 'string' && rawError.message.includes('timeout')) {
    return new ApiError({
      message: rawError.message || 'Request timeout',
      code: 'TIMEOUT',
      details: {},
      apiError: null,
      cause: rawError,
    })
  }
  // Transport (network unreachable, CORS, DNS, etc.)
  return new ApiError({
    message: (rawError && rawError.message) || 'Network error',
    code: 'TRANSPORT',
    details: {},
    apiError: null,
    cause: rawError,
  })
}

// Response interceptor (fault-tolerant retry mechanism)
service.interceptors.response.use(
  response => {
    const res = response.data

    // If the returned status is not success, throw an error
    if (!res.success && res.success !== undefined) {
      const lifted = _liftErrorPayload(res.error, res.message)
      console.error('API Error:', lifted.code, lifted.message)
      return Promise.reject(new ApiError({
        message: lifted.message,
        code: lifted.code,
        details: lifted.details,
        apiError: res.error,
      }))
    }

    return res
  },
  error => {
    const apiErr = normalizeApiError(error)
    if (apiErr.code === 'TIMEOUT') {
      console.error('Request timeout')
    } else if (apiErr.code === 'TRANSPORT') {
      console.error('Network/transport error:', apiErr.message)
    } else {
      console.error('Response error:', apiErr.code, apiErr.message)
    }
    return Promise.reject(apiErr)
  }
)

// Request function with retry
export const requestWithRetry = async (requestFn, maxRetries = 3, delay = 1000) => {
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await requestFn()
    } catch (error) {
      if (i === maxRetries - 1) throw error
      
      console.warn(`Request failed, retrying (${i + 1}/${maxRetries})...`)
      await new Promise(resolve => setTimeout(resolve, delay * Math.pow(2, i)))
    }
  }
}

export default service
