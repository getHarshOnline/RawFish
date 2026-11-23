#!/usr/bin/env bash
# Frontend transition-guard hygiene.
#
# Legacy wizard guards used to be Vue computed refs. The cockpit no longer
# exposes that store, but this gate still forbids the old foot-gun anywhere in
# active frontend code: treating a guard ref as a callable function.
#
# This gate forbids both shapes everywhere outside the store itself:
#
#   1. `:disabled="!canFoo"` in template context is safe when the value is a
#      ref and Vue auto-unwraps it.
#   2. `:disabled="!canFoo()"` treats the ref as callable and is unsafe.
#   3. `canFoo()` in script code is unsafe for the same reason.
#   4. `canFoo.value` in script code is safe.
#
# The gate scans Vue + JS sources under frontend/src and fails on
# patterns (2) or (3). Pattern (1) is the intended idiom.
#
# Exit 0 on PASS, 1 on any violation.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_SRC="$(cd "${HERE}/../frontend/src" && pwd)"

# Historical guard names that must not be revived as callable refs.
GUARDS=(canPickPreset canCreate canPrepare canStart canGenerateReport
        canSubmitCounterfactual canUploadPriors)

fail=0

# (a) Forbid `!canFoo()` in TEMPLATE attribute bindings. The pattern
#     `:disabled="!canFoo("` or `:[a-z]+="!canFoo("` is unsafe -- the
#     ref is a non-callable proxy object. Restrict to .vue files so
#     plain JS uses (which legitimately call functions) are not flagged.
for g in "${GUARDS[@]}"; do
    bad="$(rg -nP "['\"]!?${g}\(" "${FRONTEND_SRC}" \
        --type-add 'vue:*.vue' --type vue \
        --glob '!**/__tests__/**' \
        2>/dev/null || true)"
    if [[ -n "${bad}" ]]; then
        echo "frontend guard usage: FAIL -- ${g}() used as a function in a template:"
        echo "${bad}"
        fail=1
    fi
done

# (b) Forbid `canFoo()` in <script> blocks of .vue + .js sources. We
#     conservatively flag every site where the guard name is followed
#     by `(`. Allowed exceptions: the store itself defines the guards;
#     tests can call them via `.value` (the `(` pattern does not match
#     `.value`).
for g in "${GUARDS[@]}"; do
    bad="$(rg -nP "\b${g}\s*\(" "${FRONTEND_SRC}" \
        --glob '!**/__tests__/**' \
        2>/dev/null || true)"
    if [[ -n "${bad}" ]]; then
        echo "frontend guard usage: FAIL -- ${g}() called as a function:"
        echo "${bad}"
        fail=1
    fi
done

if [[ ${fail} -ne 0 ]]; then
    echo "frontend guard usage: FAILED"
    exit 1
fi

echo "frontend guard usage: PASS"
exit 0
