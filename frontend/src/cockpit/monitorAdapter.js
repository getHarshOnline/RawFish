function stringifyEvent (value) {
  if (typeof value === 'string') return value
  if (!value || typeof value !== 'object') return String(value || '')
  return value.message || value.copy || value.text || JSON.stringify(value)
}

function pushList (items, source, list, severity) {
  if (!Array.isArray(list)) return
  list.slice(0, 6).forEach((entry, index) => {
    items.push({
      id: `${source}-${severity}-${index}`,
      source,
      severity,
      copy: stringifyEvent(entry)
    })
  })
}

function claimCandidates (statusPayload, reportPayload) {
  const statusCandidates = statusPayload?.claim_candidates || statusPayload?.claims_forming || []
  if (Array.isArray(statusCandidates) && statusCandidates.length) {
    return statusCandidates.map((candidate, index) => ({
      id: `claim-candidate-${index}`,
      source: 'claim candidate',
      severity: 'claim',
      copy: stringifyEvent(candidate)
    }))
  }
  const reportClaims = []
  for (const section of reportPayload?.sections || []) {
    for (const claim of section.claims || []) {
      reportClaims.push(claim)
    }
  }
  return reportClaims.slice(0, 4).map((claim, index) => ({
    id: `report-claim-${claim.claim_id || index}`,
    source: 'report claim',
    severity: 'claim',
    copy: claim.text || claim.summary || claim.claim_id || 'Persisted claim'
  }))
}

function evidenceEvents (reportPayload) {
  const refs = []
  const pushRefs = list => {
    if (Array.isArray(list)) refs.push(...list)
  }
  pushRefs(reportPayload?.evidence_refs)
  for (const section of reportPayload?.sections || []) {
    pushRefs(section.evidence_refs)
    for (const claim of section.claims || []) pushRefs(claim.evidence_refs)
  }
  return refs.slice(0, 4).map((ref, index) => ({
    id: `evidence-${ref?.ref_id || index}`,
    source: 'evidence linked',
    severity: 'evidence',
    copy: ref?.ref_id || ref?.id || ref?.source || 'Persisted evidence reference'
  }))
}

export function cockpitTimeline (lifecycleProgress, statusPayload, reportPayload) {
  const items = lifecycleProgress.map(item => ({
    id: `phase-${item.id}`,
    source: item.label,
    severity: item.active ? 'active' : (item.done ? 'done' : 'waiting'),
    copy: item.active ? `current phase · ${item.label}` : (item.done ? 'completed' : 'waiting')
  }))
  pushList(items, 'warning', statusPayload?.warnings, 'warning')
  pushList(items, 'error', statusPayload?.errors, 'error')
  pushList(items, 'monitor', statusPayload?.monitor_events, 'info')
  items.push(...claimCandidates(statusPayload, reportPayload))
  items.push(...evidenceEvents(reportPayload))
  if (!statusPayload?.live_act_tags) {
    items.push({
      id: 'honest-act-tags-unavailable',
      source: 'act tagging',
      severity: 'info',
      copy: 'Live act-level hypothesis tagging is not emitted by this run status; claim and evidence signals are shown only when status or report artifacts include them.'
    })
  }
  return items
}
