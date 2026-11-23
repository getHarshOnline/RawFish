#!/usr/bin/env bash
# UI contract acceptance gates — RAWFISH_UI_CONTRACT.md Step 4.
# Paint may only come from tokens/primitives; state is a variant, never a
# label rewrite. Run from the repository root.
set -u

FRONTEND="frontend/src"
FAIL=0

note() { printf '%s\n' "$*"; }

# Gate U1 — type comes only from the --f-* role tokens.
# (font: inherit is the form-control reset in the token file's base rules.)
HITS=$(grep -rn "font-size\|font:" "$FRONTEND" --include='*.vue' \
  | grep -v "var(--f" | grep -v "font: inherit" | grep -v tokens || true)
if [ -n "$HITS" ]; then
  note "U1 FAIL — raw font declarations (use --f-* role tokens):"
  note "$HITS"
  FAIL=1
else
  note "U1 OK — all type via --f-* role tokens"
fi

# Gate U2 — no raw hex colors in screens/components.
HITS=$(grep -rnE "#[0-9a-fA-F]{3,8}\b" "$FRONTEND" --include='*.vue' | grep -v tokens || true)
if [ -n "$HITS" ]; then
  note "U2 FAIL — raw hex colors outside the token file:"
  note "$HITS"
  FAIL=1
else
  note "U2 OK — no raw hex outside tokens"
fi

# Gate U3 — no shadows (print-minimal: hairline + whitespace only).
HITS=$(grep -rn "box-shadow" "$FRONTEND" | grep -v "none" || true)
if [ -n "$HITS" ]; then
  note "U3 FAIL — box-shadow found:"
  note "$HITS"
  FAIL=1
else
  note "U3 OK — no shadows"
fi

# Gate U4 — radius 0 everywhere.
HITS=$(grep -rn "border-radius" "$FRONTEND" | grep -vE "border-radius: ?0" || true)
if [ -n "$HITS" ]; then
  note "U4 FAIL — non-zero border-radius:"
  note "$HITS"
  FAIL=1
else
  note "U4 OK — radius 0 everywhere"
fi

# Gate U5 — strikethrough is banned product-wide.
HITS=$(grep -rn "line-through" "$FRONTEND" || true)
if [ -n "$HITS" ]; then
  note "U5 FAIL — line-through found:"
  note "$HITS"
  FAIL=1
else
  note "U5 OK — no strikethrough"
fi

# Gate U6 — no state words written into labels (state = variant + reason).
HITS=$(grep -rniE ">[^<{]*disabled[^<{]*</" "$FRONTEND" --include='*.vue' || true)
if [ -n "$HITS" ]; then
  note "U6 FAIL — state word rendered as label text:"
  note "$HITS"
  FAIL=1
else
  note "U6 OK — no state-as-content labels"
fi

# Gate U7 — the legacy --rf-* token namespace is dead.
HITS=$(grep -rn "var(--rf-" "$FRONTEND" || true)
if [ -n "$HITS" ]; then
  note "U7 FAIL — legacy --rf-* token usage:"
  note "$HITS"
  FAIL=1
else
  note "U7 OK — legacy token namespace fully retired"
fi

# Gate U8 — no raw color-function literals outside the token file.
# Tints/washes are derived tokens (--pt*, --line-inv*, --overlay*, --ink-*,
# --accent-wash) defined once in styles/rawfish.css.
HITS=$(grep -rnE "rgba?\(|hsla?\(|color-mix\(" "$FRONTEND" --include='*.vue' --include='*.js' \
  | grep -v __tests__ || true)
if [ -n "$HITS" ]; then
  note "U8 FAIL — raw color-function literal outside tokens (use a derived tint token):"
  note "$HITS"
  FAIL=1
else
  note "U8 OK — no rgba/hsl/color-mix literals outside tokens"
fi

# Gate U9 — no inline :style paint bindings (data hues injected from JS).
# Layout-only bindings (custom props like --agent-pct, insets, sizes) pass.
HITS=$(grep -rnE ':style="\{[^}]*(backgroundColor|background:|[^a-zA-Z-]color:)' "$FRONTEND" --include='*.vue' \
  | grep -v __tests__ || true)
if [ -n "$HITS" ]; then
  note "U9 FAIL — inline :style paint binding (identity/data must not be hue):"
  note "$HITS"
  FAIL=1
else
  note "U9 OK — no inline paint bindings"
fi

# Gate U10 — screens declare layout only (ratchet toward zero).
# Counts paint declarations in SCREEN files (everything outside
# cockpit/primitives/ and styles/). The count may only go DOWN: recomposing
# a screen onto primitives lowers it; new screen paint fails the gate.
# When the baseline reaches 0 the contract's layout-only rule is fully
# enforced. Baseline: process/ui_paint_baseline.txt
BASELINE_FILE="$(dirname "$0")/ui_paint_baseline.txt"
COUNT=$(grep -rhE "^\s*(color|background|background-color|border|border-top|border-right|border-bottom|border-left|border-color|border-block[a-z-]*|border-inline[a-z-]*|outline|text-decoration)\s*:" \
  "$FRONTEND"/cockpit/*.vue "$FRONTEND"/components/*.vue "$FRONTEND"/App.vue 2>/dev/null \
  | wc -l | tr -d ' ')
BASELINE=$(cat "$BASELINE_FILE" 2>/dev/null || echo 0)
if [ "$COUNT" -gt "$BASELINE" ]; then
  note "U10 FAIL — screen paint declarations grew: $COUNT > baseline $BASELINE."
  note "Screens declare layout only; move paint into a primitive in styles/rawfish.css."
  FAIL=1
elif [ "$COUNT" -lt "$BASELINE" ]; then
  note "U10 OK — screen paint debt reduced ($COUNT < baseline $BASELINE). Lower the baseline in $BASELINE_FILE to lock it in."
else
  note "U10 OK — screen paint debt at baseline ($COUNT). Target: 0 (full recomposition)."
fi

if [ "$FAIL" -ne 0 ]; then
  note "UI CONTRACT: FAIL"
  exit 1
fi
note "UI CONTRACT: all gates green"
