import { createI18n } from 'vue-i18n'
import languages from '../../../locales/languages.json'

const localeFiles = import.meta.glob('../../../locales/!(languages).json', { eager: true })

const messages = {}
const availableLocales = []

for (const path in localeFiles) {
  const key = path.match(/\/([^/]+)\.json$/)[1]
  if (languages[key]) {
    messages[key] = localeFiles[path].default
    availableLocales.push({ key, label: languages[key].label })
  }
}

// Default to English. Sanitize stale 'zh' values from the persisted
// locale store -- zh.json was removed in the CJK scrub, so a stale
// 'zh' would render raw i18n keys. Reset to 'en' silently if the
// saved locale has no corresponding messages bundle.
//
// `localStorage` is intentionally accessed through `globalThis` and a
// try/catch: this module imports during BOTH browser runtime AND test
// runtime (vitest with happy-dom). Some test environments do not
// surface a `window.localStorage` global on the module-evaluation tick
// (e.g. vitest workers that load the module before the dom
// environment fully attaches), and a top-level `localStorage.getItem`
// in that window throws ReferenceError and breaks the import for
// every downstream test file. The guard below means i18n's module
// import is side-effect-safe in any environment.
function _readLocale () {
  try {
    const store = globalThis.localStorage
    if (store && typeof store.getItem === 'function') {
      return store.getItem('locale') || 'en'
    }
  } catch (_) { /* sandboxed iframe / SSR / test runner without DOM */ }
  return 'en'
}
function _writeLocale (value) {
  try {
    const store = globalThis.localStorage
    if (store && typeof store.setItem === 'function') {
      store.setItem('locale', value)
    }
  } catch (_) { /* swallow; persistence is best-effort */ }
}

let savedLocale = _readLocale()
if (!messages[savedLocale]) {
  savedLocale = 'en'
  _writeLocale('en')
}

const i18n = createI18n({
  legacy: false,
  locale: savedLocale,
  fallbackLocale: 'en',
  messages
})

export { availableLocales }
export default i18n
