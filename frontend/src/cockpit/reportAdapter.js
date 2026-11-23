export function claimId (claim, fallback) {
  return claim?.claim_id || claim?.id || fallback
}

export function collectReportClaims (reportPayload, manifest) {
  if (!reportPayload) {
    return (manifest?.report_sections || []).map(section => ({
      claim_id: section.id,
      title: section.label,
      text: 'Waiting for a persisted report artifact from the generic backend.',
      tag: 'requires_validation',
      evidence_refs: [],
      driver_hints: []
    }))
  }

  const claims = []
  const sections = Array.isArray(reportPayload.sections) ? reportPayload.sections : []
  sections.forEach((section, sectionIndex) => {
    const sectionId = section.section_id || section.id || `section-${sectionIndex}`
    const sectionLabel = section.title || section.label || section.section_id || `Section ${sectionIndex + 1}`
    const sectionClaims = Array.isArray(section.claims) ? section.claims : []
    if (!sectionClaims.length) {
      claims.push({
        claim_id: sectionId,
        title: sectionLabel,
        text: 'Persisted report section with no claim cards.',
        tag: 'requires_validation',
        evidence_refs: section.evidence_refs || [],
        driver_hints: section.driver_hints || []
      })
      return
    }
    sectionClaims.forEach((claim, claimIndex) => {
      claims.push({
        ...claim,
        claim_id: claimId(claim, `${sectionId}-claim-${claimIndex}`),
        title: `${sectionLabel}${claim.tag ? ` · ${claim.tag}` : ''}`,
        text: claim.text || claim.summary || claim.copy || 'Persisted report claim',
        tag: claim.tag || claim.hypothesis_tag || 'requires_validation',
        evidence_refs: claim.evidence_refs || [],
        driver_hints: claim.driver_hints || []
      })
    })
  })

  const summary = Array.isArray(reportPayload.executive_summary) ? reportPayload.executive_summary : []
  summary.forEach((claim, index) => {
    claims.push({
      ...claim,
      claim_id: claimId(claim, `summary-${index}`),
      title: claim.title || claim.claim_id || `Claim ${index + 1}`,
      text: claim.text || claim.summary || 'Persisted executive summary claim',
      tag: claim.tag || claim.hypothesis_tag || 'requires_validation',
      evidence_refs: claim.evidence_refs || [],
      driver_hints: claim.driver_hints || []
    })
  })

  if (claims.length) return claims
  return [{
    claim_id: 'report-json',
    title: 'Report JSON',
    text: 'Persisted report artifact loaded.',
    tag: 'requires_validation',
    evidence_refs: [],
    driver_hints: []
  }]
}

export function collectEvidenceRefs (reportPayload) {
  if (!reportPayload) return []
  const candidates = []
  const pushRefs = (refs, prefix) => {
    if (!Array.isArray(refs)) return
    refs.forEach((ref, index) => {
      if (!ref) return
      candidates.push({
        ...ref,
        ref_type: ref.ref_type || ref.type || prefix,
        ref_id: ref.ref_id || ref.id || ref.path || ref.source || 'persisted evidence',
        _key: `${prefix}-${index}-${ref.ref_id || ref.id || ref.path || index}`
      })
    })
  }
  pushRefs(reportPayload.evidence_refs, 'report')
  for (const section of reportPayload.sections || []) {
    pushRefs(section.evidence_refs, section.section_id || section.id || 'section')
    for (const claim of section.claims || []) {
      pushRefs(claim.evidence_refs, claim.claim_id || claim.id || 'claim')
    }
  }
  for (const claim of reportPayload.executive_summary || []) {
    pushRefs(claim.evidence_refs, claim.claim_id || claim.id || 'summary')
  }
  return candidates.slice(0, 24)
}

export function validityFlagsMap (reportPayload) {
  const map = {}
  const flags = reportPayload?.validity_flags || reportPayload?.validityFlags || []
  if (!Array.isArray(flags)) return map
  flags.forEach(flag => {
    const claimRefs = Array.isArray(flag.claim_refs) ? flag.claim_refs : []
    claimRefs.forEach(ref => {
      if (!map[ref]) map[ref] = []
      map[ref].push(flag)
    })
  })
  return map
}
