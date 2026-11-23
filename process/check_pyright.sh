#!/usr/bin/env bash
# Hygiene gate: run pyright over the backend tree and fail on any
# typing error. Warnings are tolerated (the baseline carries a few
# unused-import / unused-variable warnings in tests that are not
# worth a churn-cycle to remove); errors are not.
#
# Usage (from anywhere):
#     bash process/check_pyright.sh
#
# The script delegates to the backend's pinned pyright (declared in
# ``backend/pyproject.toml`` ``[dependency-groups] dev``) so the
# version stays in lockstep with ``uv sync``. If pyright is not
# installed in the venv (a fresh checkout that has not yet run
# ``uv sync``), the script falls back to ``uv run --frozen pyright``
# so CI behaviour matches the documented invocation in
# ``process/README.md``.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/.." && pwd)"
BACKEND_DIR="${REPO_ROOT}/backend"

cd "${BACKEND_DIR}"

if [[ -x ".venv/bin/python" ]] && .venv/bin/python -m pyright --version >/dev/null 2>&1; then
    output="$(
        .venv/bin/python -m pyright 2>&1
    )"
    rc=$?
else
    # Fresh-checkout path: defer to uv to pick up the pinned pyright.
    output="$(
        uv run --frozen pyright 2>&1
    )"
    rc=$?
fi

printf "%s\n" "${output}"

summary="$(
    printf "%s\n" "${output}" | grep -E "[0-9]+ errors?," | tail -n 1 || true
)"
if [[ -n "${summary}" ]]; then
    errors="$(
        printf "%s\n" "${summary}" | awk '{print $1}'
    )"
    if [[ "${errors}" == "0" ]]; then
        exit 0
    fi
fi

exit "${rc}"
