#!/usr/bin/env bash
# Run every hygiene gate in process/. The closure-contract checker is
# intentionally separate; run `make gates` from the repository root for hygiene plus
# closure-contract drift checks. Neither signal exercises full app behavior.
#
# Usage (from anywhere):
#     bash process/run_hygiene_gates.sh
#
# Adding a hygiene gate: drop a ``check_*.py`` or ``check_*.sh`` next to this
# script, then add its invocation to PY_GATES or SH_GATES below.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY_GATES=(
    "${HERE}/check_rawfish_contract.py"
    "${HERE}/check_no_planning_terminology.py"
    "${HERE}/check_no_predictor_language.py"
    "${HERE}/check_design_system_css.py"
)

SH_GATES=(
    "${HERE}/check_no_removed_api_surface.sh"
    "${HERE}/check_docker_compose_loopback.sh"
    "${HERE}/check_frontend_guard_usage.sh"
    "${HERE}/check_no_transition_all.sh"
    "${HERE}/check_ui_contract.sh"
    "${HERE}/check_pyright.sh"
)

rc=0
for gate in "${PY_GATES[@]}"; do
    name="$(basename "${gate}")"
    echo "=== ${name} ==="
    if ! python3 -B "${gate}"; then
        rc=1
    fi
    echo
done

for gate in "${SH_GATES[@]}"; do
    name="$(basename "${gate}")"
    echo "=== ${name} ==="
    if ! bash "${gate}"; then
        rc=1
    fi
    echo
done

if [ "${rc}" -ne 0 ]; then
    echo "hygiene gates: FAILED"
else
    echo "hygiene gates: all PASS"
    echo "hygiene gates: closure contract and product behavior not run"
fi
exit ${rc}
