#!/usr/bin/env bash
# Motion-system hygiene gate: forbid `transition: all` in new or touched
# code under frontend/src.
#
# Rationale (web.dev animations guide + the project's motion-system
# direction): `transition: all` cannot be optimised by the browser to
# the GPU compositor and may animate layout/paint properties the author
# did not intend. The motion system defines specific reusable classes
# (`mf-fade`, `mf-step`, `mf-card-lift`, `mf-accordion`, etc.) that
# transition only `transform` and `opacity`. New code MUST adopt those
# primitives.
#
# This gate is intentionally bounded: it scans frontend/src/* but a
# allowlist block below names specific files that pre-date the
# motion system and have not been migrated yet. Every entry in the
# allowlist must carry an inline reason describing why it has not
# moved to the shared primitives. New entries require operator
# review at the same time as the file is touched.
#
# Exit 0 on PASS, 1 on any violation.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_SRC="$(cd "${HERE}/../frontend/src" && pwd)"

# Legacy allowlist. Each entry: relative path under frontend/src/. A
# match on `transition: all` in any of these files is exempt. The
# inline reason on each allowlist entry documents why it remains exempt.
#
# The allowlist is empty: no `transition: all` form is permitted anywhere under
# frontend/src.
ALLOWLISTED_PATHS=()

if ! command -v rg >/dev/null 2>&1; then
    echo "error: ripgrep (rg) not installed" >&2
    exit 2
fi

# Scan all .vue and .css files under frontend/src for three related
# defects:
#
#   (1) The literal `transition: all ...` form. Broad enough to catch
#       `transition: all`, `transition:all`, and the longhand
#       `transition: all 200ms ease`.
#   (2) The shorthand `transition: <duration>;` form (e.g.
#       `transition: 0.3s;` or `transition:200ms ease;`) where no
#       property is named. The CSS transition shorthand defaults
#       `transition-property` to `all` when only a duration / easing /
#       delay is supplied, so this form has the SAME compositor cost
#       and surprising-property-animation hazard as `transition: all`.
#   (3) The longhand `transition-property: all`. Equivalent compositor
#       behavior to (1); a developer who reaches for the longhand
#       might think they have escaped the shorthand gate.
#
# The combined regex covers all three patterns by matching either
# ``transition:`` (cases 1 + 2) or ``transition-property:`` (case 3)
# followed by ``all`` or a numeric-duration literal.
hits="$(rg -nP "(transition|transition-property):\s*(all\b|-?\d+(\.\d+)?(s|ms)\b)" "${FRONTEND_SRC}" \
    --type-add 'vue:*.vue' --type vue --type css \
    --glob '!**/__tests__/**' \
    --glob '!**/coverage/**' \
    2>/dev/null || true)"

if [[ -z "${hits}" ]]; then
    echo "check_no_transition_all: PASS"
    exit 0
fi

is_allowlisted() {
    local line="$1"
    for entry in "${ALLOWLISTED_PATHS[@]}"; do
        if [[ "${line}" == *"${entry}"* ]]; then
            return 0
        fi
    done
    return 1
}

offending=""
while IFS= read -r line; do
    [[ -z "${line}" ]] && continue
    if ! is_allowlisted "${line}"; then
        offending+="${line}"$'\n'
    fi
done <<< "${hits}"

if [[ -z "${offending}" ]]; then
    echo "check_no_transition_all: PASS (all matches allowlisted)"
    exit 0
fi

echo "FAIL: 'transition: all' (including the bare-duration shorthand"
echo "      'transition: <duration>;' which defaults to 'all') is"
echo "      forbidden in new motion-system code."
echo "Migrate the offending site to a shared mf-* motion class, or"
echo "rewrite the declaration to name the property explicitly (e.g."
echo "'transition: background-color 0.3s;', 'transition: transform"
echo "0.3s;'). If the file genuinely needs it, add an inline allowlist"
echo "entry to process/check_no_transition_all.sh with an explicit reason."
echo ""
echo "${offending}"
exit 1
