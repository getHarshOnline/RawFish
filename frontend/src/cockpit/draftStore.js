// One unsaved draft for this page session. The router never owns its contents.
let draft = null

export function readDraft () {
  return draft
}

export function setDraft (value) {
  draft = value
}
