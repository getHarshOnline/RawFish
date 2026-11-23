#!/usr/bin/env bash
# Boot smoke for the RawFish stack. Brings up backend + frontend in
# detached background processes, hits a handful of canonical URLs to
# confirm both are reachable + serving the right routes, and tears
# down cleanly on exit.
#
# Usage (from the repository root):
#   bash process/boot_smoke.sh
#
# Optional env:
#   API_KEY               -- backend auth header value (default: 'smoke-key')
#   AUTH_ENABLED          -- set to 'false' to skip the X-API-Key header (default: 'true')
#   ZEP_MODE              -- 'mock' (default) | 'graphiti' | 'experimental_cloud'
#   SMOKE_BACKEND_PORT    -- default 5001
#   SMOKE_FRONTEND_PORT   -- default 3000
#
# Exits 0 on success, 1 on any failed assertion. Logs land under
# .boot-smoke/{backend.log,frontend.log,trap.log}.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/.." && pwd)"
LOG_DIR="${ROOT}/.boot-smoke"
mkdir -p "${LOG_DIR}"

# Activate the project's required Node version if nvm is installed.
# Vite 7 requires Node >= 20.19; without this the spawned `npm run dev`
# can pick up the system Node and fail on `crypto.hash`.
#
# nvm mutates PATH in the CURRENT shell only; sourcing it inside a
# subshell would not propagate to the parent. So we source it here at
# top level + run `nvm use` in this shell.
if [[ -s "${HOME}/.nvm/nvm.sh" ]]; then
    # shellcheck source=/dev/null
    \. "${HOME}/.nvm/nvm.sh"
    if [[ -f "${ROOT}/.nvmrc" ]]; then
        nvm use --silent "$(cat "${ROOT}/.nvmrc")" >/dev/null 2>&1 \
            || nvm use --silent lts/iron >/dev/null 2>&1 \
            || true
    fi
fi
echo "boot-smoke: node $(command -v node) ($(node -v 2>/dev/null || echo 'not on PATH'))"

API_KEY="${API_KEY:-smoke-key}"
AUTH_ENABLED="${AUTH_ENABLED:-true}"
ZEP_MODE="${ZEP_MODE:-mock}"
SMOKE_BACKEND_PORT="${SMOKE_BACKEND_PORT:-5001}"
SMOKE_FRONTEND_PORT="${SMOKE_FRONTEND_PORT:-3000}"

backend_url="http://127.0.0.1:${SMOKE_BACKEND_PORT}"
frontend_url="http://127.0.0.1:${SMOKE_FRONTEND_PORT}"
backend_pid=""
frontend_pid=""

cleanup() {
    if [[ -n "${backend_pid}" ]] && kill -0 "${backend_pid}" 2>/dev/null; then
        kill "${backend_pid}" 2>/dev/null || true
    fi
    if [[ -n "${frontend_pid}" ]] && kill -0 "${frontend_pid}" 2>/dev/null; then
        kill "${frontend_pid}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

# Auth header helper.
_auth_args=()
if [[ "${AUTH_ENABLED}" == "true" ]]; then
    _auth_args=(-H "X-API-Key: ${API_KEY}")
fi

# Wait-until-up helper.
_wait_for() {
    local url="$1"
    local label="$2"
    local timeout_s="${3:-60}"
    local start now
    start="$(date +%s)"
    while :; do
        if curl -sf -o /dev/null "${url}"; then
            return 0
        fi
        now="$(date +%s)"
        if (( now - start > timeout_s )); then
            echo "boot-smoke: ${label} did not come up at ${url} within ${timeout_s}s"
            return 1
        fi
        sleep 1
    done
}

# 1. Start the backend (foreground gunicorn would be best, but for an
#    automatable smoke we use the dev runner; production-mode boot
#    requires SECRET_KEY + AUTH_ENABLED + API_KEY).
echo "boot-smoke: starting backend (ZEP_MODE=${ZEP_MODE}, AUTH_ENABLED=${AUTH_ENABLED})..."
(
    cd "${ROOT}/backend" || exit 1
    export AUTH_ENABLED ZEP_MODE
    export API_KEY
    export FLASK_DEBUG=true     # avoid the gunicorn / SECRET_KEY hard-fail on bare invocation
    export SECRET_KEY="${SECRET_KEY:-smoke-secret-not-for-production}"
    uv run --frozen python run.py
) > "${LOG_DIR}/backend.log" 2>&1 &
backend_pid="$!"

if ! _wait_for "${backend_url}/health" "backend" 60; then
    echo "boot-smoke: tail of backend log:"
    tail -40 "${LOG_DIR}/backend.log" || true
    exit 1
fi
echo "boot-smoke: backend up (pid=${backend_pid})"

# 2. Start the frontend dev server. Vite's --host binds 0.0.0.0; we
#    keep it on localhost via --host 127.0.0.1.
echo "boot-smoke: starting frontend..."
(
    cd "${ROOT}/frontend" || exit 1
    npm run dev -- --host 127.0.0.1 --port "${SMOKE_FRONTEND_PORT}"
) > "${LOG_DIR}/frontend.log" 2>&1 &
frontend_pid="$!"

if ! _wait_for "${frontend_url}/" "frontend" 60; then
    echo "boot-smoke: tail of frontend log:"
    tail -40 "${LOG_DIR}/frontend.log" || true
    exit 1
fi
echo "boot-smoke: frontend up (pid=${frontend_pid})"

# 3. Smoke assertions.
fail=0
_assert_route() {
    local label="$1"
    local url="$2"
    local expected="$3"
    local body
    body="$(curl -s "${_auth_args[@]}" "${url}" || true)"
    if [[ -z "${body}" ]]; then
        echo "boot-smoke: ${label} returned an empty body (${url})"
        fail=1
        return
    fi
    if ! echo "${body}" | grep -qE "${expected}"; then
        echo "boot-smoke: ${label} did NOT match expected pattern '${expected}'"
        echo "  url:  ${url}"
        echo "  body (first 200 chars): ${body:0:200}"
        fail=1
        return
    fi
    echo "boot-smoke: ${label} OK (matched '${expected}')"
}

# Backend smoke
_assert_route \
    "/api/sim/use-cases/creator/catalog" \
    "${backend_url}/api/sim/use-cases/creator/catalog" \
    '"success":\s*true'
_assert_route \
    "/api/sim/use-cases/creator/personas" \
    "${backend_url}/api/sim/use-cases/creator/personas" \
    '"success":\s*true'

# Frontend smoke
_assert_route \
    "/ (cockpit launch)" \
    "${frontend_url}/" \
    'RawFish|RAWFISH|<!DOCTYPE'
_assert_route \
    "/run (cockpit shell)" \
    "${frontend_url}/run" \
    'RawFish|RAWFISH|<!DOCTYPE'

if [[ ${fail} -ne 0 ]]; then
    echo "boot-smoke: FAILED"
    exit 1
fi

echo "boot-smoke: all routes OK"
echo "boot-smoke: logs at ${LOG_DIR}/"
exit 0
