#!/usr/bin/env python3
"""RawFish design-system CSS hygiene gate.

The cockpit has shared primitives for panels, cards, buttons, chips,
meters, labels, fields, tabs, consoles, and status lines. Product UI may
compose those primitives, but must not hide legacy visual debt behind an
allowlist.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = ROOT / "frontend" / "src"

STYLE_EXTENSIONS = {".css", ".vue"}
COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
BOX_SHADOW_RE = re.compile(r"\bbox-shadow\s*:", re.IGNORECASE)
NEGATIVE_LETTER_SPACING_RE = re.compile(r"\bletter-spacing\s*:\s*-\S+", re.IGNORECASE)
VIEWPORT_TYPE_RE = re.compile(r"\bfont-size\s*:\s*[^;]*(vw|vh|vmin|vmax)", re.IGNORECASE)
RAW_FONT_SIZE_RE = re.compile(r"\bfont-size\s*:\s*\d+(?:\.\d+)?px\b", re.IGNORECASE)
RAW_RADIUS_RE = re.compile(r"\bborder-radius\s*:", re.IGNORECASE)
LOOSE_TOKEN_RE = re.compile(
    r"--rf-(?:shadow|blue|violet|purple|green|amber|lime|gold)\b",
    re.IGNORECASE,
)


def rel(path: Path) -> str:
    return path.relative_to(FRONTEND_SRC).as_posix()


def is_token_definition(key: str, line: str) -> bool:
    """Custom-property definitions are legal only in the token file
    (RAWFISH_UI_CONTRACT.md single source of truth)."""
    if key != "styles/rawfish.css":
        return False
    return line.lstrip().startswith("--")


def is_allowed_data_color(key: str, line: str) -> bool:
    """Lane manifests may carry cohort/lane colour fields as DATA-MARKS
    (backend/report metadata). They are never consumed as UI paint: the
    cockpit encodes identity with weight/opacity/position, and gate U9 in
    check_ui_contract.sh bans inline :style paint bindings that would
    inject them."""
    if not key.startswith("lanes/"):
        return False
    return '"color"' in line or '"accent"' in line


def css_value(line: str) -> str:
    return line.split(":", 1)[1].split(";", 1)[0].strip()


def is_box_shadow_violation(line: str) -> bool:
    if not BOX_SHADOW_RE.search(line):
        return False
    return css_value(line).lower() != "none"


def is_radius_violation(line: str) -> bool:
    """Radius is 0 everywhere (RAWFISH_UI_CONTRACT.md gate 4 — no pills,
    no rounded corners, not even 50% dots)."""
    if not RAW_RADIUS_RE.search(line):
        return False
    value = css_value(line).lower()
    return value not in {"0", "0px"}


def violations_for_text(key: str, text: str) -> list[str]:
    problems: list[str] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if is_token_definition(key, stripped):
            if LOOSE_TOKEN_RE.search(stripped):
                problems.append(f"{key}:{line_no}: loose hue/shadow token: {stripped}")
            continue
        if COLOR_RE.search(stripped) and is_allowed_data_color(key, stripped):
            continue
        checks = [
            ("non-token color", COLOR_RE),
            ("negative letter-spacing", NEGATIVE_LETTER_SPACING_RE),
            ("viewport-scaled type", VIEWPORT_TYPE_RE),
            ("raw font-size px", RAW_FONT_SIZE_RE),
            ("loose hue/shadow token", LOOSE_TOKEN_RE),
        ]
        for label, pattern in checks:
            if pattern.search(stripped):
                problems.append(f"{key}:{line_no}: {label}: {stripped}")
        if is_box_shadow_violation(stripped):
            problems.append(f"{key}:{line_no}: box-shadow: {stripped}")
        if is_radius_violation(stripped):
            problems.append(f"{key}:{line_no}: raw border-radius: {stripped}")
    return problems


def violations_for(path: Path) -> list[str]:
    return violations_for_text(rel(path), path.read_text(encoding="utf-8"))


def self_test() -> list[str]:
    failures: list[str] = []
    must_fail = [
        ("cockpit/Bad.vue", ".x { color: #123456; }", "non-token color"),
        ("cockpit/Bad.vue", ".x { font-size: 13px; }", "raw font-size px"),
        ("cockpit/Bad.vue", ".x { box-shadow: 0 1px 2px black; }", "box-shadow"),
        ("cockpit/Bad.vue", ".x { border-radius: 6px; }", "raw border-radius"),
        ("cockpit/Bad.vue", ".x { border-radius: 50%; }", "raw border-radius"),
        ("styles/rawfish.css", "--rf-blue: #1D4ED8;", "loose hue/shadow token"),
    ]
    for key, text, label in must_fail:
        if not any(label in problem for problem in violations_for_text(key, text)):
            failures.append(f"self-test missed {label!r} for {key}: {text}")

    must_pass = [
        ("styles/rawfish.css", "--accent: #FF5722;"),
        ("styles/rawfish.css", "--t2: rgba(20,20,16,0.60);"),
        ("lanes/creator/lane_manifest.json", '"color": "#E64A19"'),
        ("cockpit/Good.vue", ".x { box-shadow: none; }"),
        ("cockpit/Good.vue", ".x { border-radius: 0; }"),
    ]
    for key, text in must_pass:
        problems = violations_for_text(key, text)
        if problems:
            failures.append(f"self-test false-positive for {key}: {problems[0]}")
    return failures


def main() -> int:
    self_test_failures = self_test()
    if self_test_failures:
        print("check_design_system_css: FAIL")
        print("RawFish design gate self-test failed.")
        for failure in self_test_failures:
            print(failure)
        return 1

    files = [
        path
        for path in FRONTEND_SRC.rglob("*")
        if path.is_file()
        and path.suffix in STYLE_EXTENSIONS
        and "__tests__" not in path.parts
        and "coverage" not in path.parts
    ]
    failing: list[str] = []
    for path in files:
        problems = violations_for(path)
        if not problems:
            continue
        failing.extend(problems)

    if failing:
        print("check_design_system_css: FAIL")
        print("RawFish UI must use role tokens and shared primitives; no legacy debt allowlist exists.")
        for problem in failing:
            print(problem)
        return 1

    print("check_design_system_css: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
