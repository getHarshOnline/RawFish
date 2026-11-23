#!/usr/bin/env bash
# Audit guardrail: every docker-compose.yml in the repo must bind its
# published ports to 127.0.0.1 (loopback) and must NEVER use the bare
# "PORT:PORT" form that publishes on 0.0.0.0. The backend compose file
# additionally must require the host-side NEO4J_PASSWORD through
# NEO4J_AUTH's ${VAR:?...} fail-closed substitution. Do not also pass a
# container variable named NEO4J_PASSWORD: Neo4j interprets NEO4J_* env
# names as config keys and rejects PASSWORD.
#
# Scope:
#   * ``backend/docker-compose.yml`` -- standalone Neo4j 7474/7687 + password rules.
#   * ``docker-compose.local.yml`` -- full local stack ports + Neo4j password rules.
#   * ``docker-compose.yml`` at the repo root -- app 3000/5001.
#     The app process may bind to 0.0.0.0 inside the container; this
#     guard enforces the host-facing publish address.
#
# This script cannot exercise docker itself (no daemon in CI), so it
# greps the declarative shape that the audit blocker mandates.
#
# Exit 0 when all assertions hold. Exit 1 with a diagnostic otherwise.
#
# Usage (from anywhere):
#     bash process/check_docker_compose_loopback.sh

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE="$(cd "${HERE}/../backend" && pwd)/docker-compose.yml"
ROOT_COMPOSE="$(cd "${HERE}/.." && pwd)/docker-compose.yml"
LOCAL_COMPOSE="$(cd "${HERE}/.." && pwd)/docker-compose.local.yml"

if [[ ! -f "${COMPOSE}" ]]; then
    echo "docker-compose loopback check: MISSING ${COMPOSE}"
    exit 1
fi
if [[ ! -f "${ROOT_COMPOSE}" ]]; then
    echo "docker-compose loopback check: MISSING ${ROOT_COMPOSE}"
    exit 1
fi
if [[ ! -f "${LOCAL_COMPOSE}" ]]; then
    echo "docker-compose loopback check: MISSING ${LOCAL_COMPOSE}"
    exit 1
fi

fail=0

service_block() {
    local file="$1"
    local service="$2"
    awk -v service="${service}" '
        $0 ~ "^[[:space:]]{2}" service ":[[:space:]]*$" {
            in_block = 1
            print
            next
        }
        in_block && $0 ~ "^[[:space:]]{2}[A-Za-z0-9_-]+:[[:space:]]*$" {
            exit
        }
        in_block {
            print
        }
    ' "${file}"
}

BACKEND_NEO4J_BLOCK="$(service_block "${COMPOSE}" "neo4j-community")"
LOCAL_NEO4J_BLOCK="$(service_block "${LOCAL_COMPOSE}" "neo4j")"

if [[ -z "${BACKEND_NEO4J_BLOCK}" ]]; then
    echo "docker-compose loopback check: FAIL -- missing neo4j-community service in backend compose"
    fail=1
fi
if [[ -z "${LOCAL_NEO4J_BLOCK}" ]]; then
    echo "docker-compose loopback check: FAIL -- missing neo4j service in local compose"
    fail=1
fi

# 1. No bare 0.0.0.0 binding for 7474 or 7687. The grep targets the
#    "PORT:PORT" suffix without a leading IP -- the loopback-bound form
#    "127.0.0.1:7474:7474" does not match because the leading "127."
#    breaks the leading-quote anchor.
bare_bind="$(grep -nE '^[[:space:]]*-[[:space:]]*"(7474|7687):(7474|7687)"' "${COMPOSE}" || true)"
if [[ -n "${bare_bind}" ]]; then
    echo "docker-compose loopback check: FAIL -- bare 0.0.0.0 binding found:"
    echo "${bare_bind}"
    fail=1
fi

# 2. The loopback-bound form must be present for both Neo4j ports.
#    This pins the positive shape so a future refactor that drops the
#    port stanza entirely also trips the guard.
for port in 7474 7687; do
    if ! grep -qE "\"127\\.0\\.0\\.1:${port}:${port}\"" "${COMPOSE}"; then
        echo "docker-compose loopback check: FAIL -- missing 127.0.0.1:${port}:${port} binding"
        fail=1
    fi
done

# 2b. The root compose's app ports must follow the same rule.
root_bare_bind="$(grep -nE '^[[:space:]]*-[[:space:]]*"(3000|5001):(3000|5001)"' "${ROOT_COMPOSE}" || true)"
if [[ -n "${root_bare_bind}" ]]; then
    echo "docker-compose loopback check: FAIL -- bare 0.0.0.0 binding found in root compose:"
    echo "${root_bare_bind}"
    fail=1
fi
for port in 3000 5001; do
    if ! grep -qE "\"127\\.0\\.0\\.1:${port}:${port}\"" "${ROOT_COMPOSE}"; then
        echo "docker-compose loopback check: FAIL -- missing 127.0.0.1:${port}:${port} binding in root compose"
        fail=1
    fi
done

# 2c. The full local compose publishes both the app and Neo4j. It must
#     keep the same loopback-only posture as the smaller compose files.
local_bare_bind="$(grep -nE '^[[:space:]]*-[[:space:]]*"(3000|5001|7474|7687):(3000|5001|7474|7687)"' "${LOCAL_COMPOSE}" || true)"
if [[ -n "${local_bare_bind}" ]]; then
    echo "docker-compose loopback check: FAIL -- bare 0.0.0.0 binding found in local compose:"
    echo "${local_bare_bind}"
    fail=1
fi
for port in 3000 5001 7474 7687; do
    if ! grep -qE "\"127\\.0\\.0\\.1:${port}:${port}\"" "${LOCAL_COMPOSE}"; then
        echo "docker-compose loopback check: FAIL -- missing 127.0.0.1:${port}:${port} binding in local compose"
        fail=1
    fi
done

# 3. NEO4J_AUTH must derive from host-side NEO4J_PASSWORD via the
#    ${VAR:?...} fail-closed substitution.
if ! printf "%s\n" "${BACKEND_NEO4J_BLOCK}" | grep -qE '^[[:space:]]*NEO4J_AUTH:.*NEO4J_PASSWORD:\?'; then
    echo "docker-compose loopback check: FAIL -- NEO4J_AUTH must require host-side NEO4J_PASSWORD with \${NEO4J_PASSWORD:?...}"
    fail=1
fi

# 3b. A literal NEO4J_PASSWORD container env var breaks Neo4j 5 strict
#     config parsing. The app container may still receive NEO4J_PASSWORD;
#     this check targets only backend/docker-compose.yml's Neo4j service.
if printf "%s\n" "${BACKEND_NEO4J_BLOCK}" | grep -qE '^[[:space:]]*NEO4J_PASSWORD:'; then
    echo "docker-compose loopback check: FAIL -- do not pass NEO4J_PASSWORD into the Neo4j container; derive healthcheck auth from NEO4J_AUTH"
    fail=1
fi
if ! printf "%s\n" "${LOCAL_NEO4J_BLOCK}" | grep -qE '^[[:space:]]*NEO4J_AUTH:.*NEO4J_PASSWORD:\?'; then
    echo "docker-compose loopback check: FAIL -- local compose NEO4J_AUTH must require host-side NEO4J_PASSWORD with \${NEO4J_PASSWORD:?...}"
    fail=1
fi
if printf "%s\n" "${LOCAL_NEO4J_BLOCK}" | grep -qE '^[[:space:]]*NEO4J_PASSWORD:'; then
    echo "docker-compose loopback check: FAIL -- do not pass NEO4J_PASSWORD into the local Neo4j container"
    fail=1
fi

# 4. The historical dev-default must not resurface in NEO4J_AUTH. We
#    only flag the literal pattern in NEO4J_AUTH= lines so the safety
#    note in the header block can still mention the string by name.
if grep -nE '^[[:space:]]*NEO4J_AUTH:.*rawfish-local-dev' "${COMPOSE}"; then
    echo "docker-compose loopback check: FAIL -- NEO4J_AUTH still hard-codes the dev default password"
    fail=1
fi

if [[ ${fail} -ne 0 ]]; then
    exit 1
fi

echo "docker-compose loopback + fail-closed password: PASS"
exit 0
