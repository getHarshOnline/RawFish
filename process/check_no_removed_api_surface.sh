#!/usr/bin/env bash
# RawFish frontend API-surface guardrail.
#
# Active frontend code may call only the generic lane dispatch under:
#   /api/sim/use-cases/<lane_id>/...
#
# Direct lane URLs under /api/sim/<segment>, imports of removed direct
# policy clients, and old browser route declarations are not a RawFish
# product API. Tests are included on purpose: stale tests should fail
# the gate instead of documenting a dead surface. The gate polices active
# cockpit, router, and lane code for accidental reintroduction.
#
# Usage:
#   bash process/check_no_removed_api_surface.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_SRC="$(cd "${HERE}/../frontend/src" && pwd)"
ACTIVE_ROOTS=(
    "${FRONTEND_SRC}/App.vue"
    "${FRONTEND_SRC}/main.js"
    "${FRONTEND_SRC}/router"
    "${FRONTEND_SRC}/cockpit"
    "${FRONTEND_SRC}/lanes"
)
EXISTING_ACTIVE_ROOTS=()
for root in "${ACTIVE_ROOTS[@]}"; do
    if [[ -e "${root}" ]]; then
        EXISTING_ACTIVE_ROOTS+=("${root}")
    fi
done

fail=0

hits_url="$(rg -n '/api/sim/(?!use-cases\b)[a-zA-Z0-9_-]' "${FRONTEND_SRC}" -P || true)"
if [[ -n "${hits_url}" ]]; then
    echo "removed direct /api/sim/* URL literal detected in frontend/src:"
    echo "${hits_url}"
    fail=1
fi

hits_const="$(rg -n "['\"]/api/sim['\"]" "${FRONTEND_SRC}" || true)"
if [[ -n "${hits_const}" ]]; then
    echo "/api/sim base constant detected in frontend/src:"
    echo "${hits_const}"
    fail=1
fi

POLICY_CLIENT="policy"
hits_policy_import="$(rg -n -P "(from\\s+['\"](?:@/api/${POLICY_CLIENT}(?:\\.js)?|(?:\\.\\./)+api/${POLICY_CLIENT}(?:\\.js)?)['\"]|import\\s*\\(\\s*['\"](?:@/api/${POLICY_CLIENT}(?:\\.js)?|(?:\\.\\./)+api/${POLICY_CLIENT}(?:\\.js)?)['\"]\\s*\\))" "${EXISTING_ACTIVE_ROOTS[@]}" || true)"
if [[ -n "${hits_policy_import}" ]]; then
    echo "removed direct policy API client import detected in active frontend surface:"
    echo "${hits_policy_import}"
    fail=1
fi

ROUTER="${FRONTEND_SRC}/router/index.js"
if [[ -f "${ROUTER}" ]]; then
    CREATOR_ROUTE="creator-audience"
    CREATOR_ROUTE+="-reaction"
    LEGACY_ROUTE_RE="path:\\s*['\"]/(policy|${CREATOR_ROUTE}|process|simulation|report|interaction)(?:['\"/]|$)"
    hits_legacy_routes="$(rg -n -P "${LEGACY_ROUTE_RE}" "${ROUTER}" || true)"
    if [[ -n "${hits_legacy_routes}" ]]; then
        echo "removed browser route declaration detected in frontend router:"
        echo "${hits_legacy_routes}"
        fail=1
    fi
fi

if [[ ${fail} -ne 0 ]]; then
    exit 1
fi

echo "no direct /api/sim/* or removed policy API client leak: PASS"
exit 0
