#!/usr/bin/env python3
"""Hygiene gate — fail if planning terminology re-enters the codebase.

Historical planning documents may describe code; live code does not cite a
retiring planning-document corpus. The codebase speaks in its own vocabulary
(class names, module paths, schema fields), while iteration markers, gate IDs,
annex citations and decision labels belong only in historical recovery evidence.
This script enforces that boundary without treating legitimate domain vocabulary
as a path match.

This is a PROCESS gate, not a product test. It is intentionally NOT collected
by pytest. Invoke directly, or via ``process/run_hygiene_gates.sh``:

    python3 process/check_no_planning_terminology.py

Exit code 0 on pass, 1 on any violation, 2 on tool error.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Each pattern is a focused regex for one category of planning leak.
# Keep these narrow so a routine comment that happens to mention "phase"
# of a state machine (lowercase, no number) does not false-positive.
BANNED_PATTERNS = [
    r"plan/rawfish/",                       # direct citation of the retiring planning-document corpus
    r"\bW[1-9](?:\.[0-9]+|[A-Z])?\b",        # W1, W2, W3, W4, W1.5, W2A, W2B, W3B (workstream tokens;
                                             # verified zero false positives in this codebase)
    r"\bW[1-9] (step|GATE)\b",               # W1 step N, W1 GATE N
    r"\bGATE[ _-][0-9A-Z]",                  # GATE 4, GATE-1, GATE_4, GATE 5b.2 (uppercase only;
                                             # lowercase 'gate' is a legit English word in upstream code)
    r"\bG[0-9]+\b",                          # bare gate markers G2, G3, G6, G7 (verified zero false
                                             # positives in this codebase; planning gate IDs only)
    r"\bannex(?:ure)? [0-9]+\b",             # annex 11, annexure 13
    r"\bannex(?:ure)? §",                    # annex §, annexure §
    r"annexures?/[0-9]+",                    # annexures/07-..., annexure/13-... (the path-form
                                             # citation that the previous regex set missed)
    r"§[0-9]+(?:\.[0-9]+)*",                 # bare section markers like §9, §7.2, §9.8 -- these are
                                             # planning-doc back-references regardless of any prefixing
                                             # `annex` token. RFC citations are still legitimate
                                             # (`RFC 6902 §5` etc.) and pass via the prefix-allowlist
                                             # check at the call site.
    r"\bDecision [A-Z]\b",                   # Decision A..Z (widened from A-F to catch Decision G)
    r"\bdecision #[0-9]+\b",                 # decision #17
    r"\b[Pp]hase[ -][0-9]+(?:\.[0-9]+)?\b",  # Phase 5, phase 7, phase-7, phase 7.3 (case + dash + decimal)
]

# Filename patterns that leak planning terminology. find -iname semantics.
# Distinct from BANNED_PATTERNS because the gate must scan file NAMES too —
# a future contributor creating ``test_annex11_pinning.py`` or ``W2_runner.py``
# would otherwise sneak the terminology in via the filename without the
# content-grep ever firing.
BANNED_FILENAME_PATTERNS = [
    r"_vs_annex[0-9]+",   # test_persona_defaults_vs_annex11.py shape
    r"annex[0-9]+",       # annex11_*, *_annex13
    r"\bW[0-9](?:\.[0-9]+)?_",  # W2_runner.py, W1.5_helpers.py
    r"_gate[0-9]+",       # _gate1.py, _gate5b.py
    r"_step[0-9]+_",      # _step18_*, _step4_* helper/file markers.
    r"decision_[a-f]\b",  # decision_a.py
    r"\bphase[0-9]+_",    # phase4_helper.py style file-name markers.
]

# Filename matching follows find -iname semantics. Keep the expression and the
# candidate path case-preserving and let IGNORECASE handle both W2_runner.py and
# w2_runner.py. Lowercasing only the candidate would make the uppercase-W pattern
# silently ineffective.
BANNED_FILENAME_RE = re.compile("|".join(BANNED_FILENAME_PATTERNS), re.IGNORECASE)

# Focused executable fixtures for the filename contract. These run as part of
# this native process gate, which is intentionally not collected by pytest.
FILENAME_PATTERN_CASES = {
    "backend/app/W2_runner.py": True,
    "backend/app/w2_runner.py": True,
    "backend/tests/test_annex11_pinning.py": True,
    "backend/app/plan.py": False,
    "backend/app/planning.py": False,
    "backend/app/planner.py": False,
    "backend/app/plan_id.py": False,
    "backend/app/state_phase_machine.py": False,
}

# Paths the gate scans. Code + infrastructure manifests (pyproject.toml,
# docker-compose.yml) — manifests ship to users and their comments must
# also speak code-vocabulary, not planning-vocabulary.
SCAN_PATHS = [
    "backend/app",
    "backend/tests",
    "backend/scripts",
    "backend/pyproject.toml",
    "backend/docker-compose.yml",
    "frontend/src",
]

# Files that MAY mention planning terminology (very narrow allowlist).
# If you add an entry here, you owe a comment explaining why.
ALLOWLISTED_PATHS: list[str] = []

# Filenames that the BANNED_FILENAME_PATTERNS catch as false-positives.
# Each entry MUST carry a comment naming why it is exempt.
ALLOWLISTED_FILENAMES: list[str] = [
]


def check_content() -> list[str]:
    """Run ripgrep across the code-only scan paths; return offending lines.

    A hit means a comment or docstring (or, less likely, a string literal)
    is citing retired orchestration vocabulary rather than native code vocabulary.
    Either rewrite the comment in code-vocabulary terms, or add a narrowly
    justified entry to ALLOWLISTED_PATHS.
    """
    if shutil.which("rg") is None:
        print("error: ripgrep (rg) not installed", file=sys.stderr)
        sys.exit(2)

    combined = "|".join(BANNED_PATTERNS)
    proc = subprocess.run(
        ["rg", "-nP", combined, *SCAN_PATHS],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if proc.returncode == 1:  # rg: no matches
        return []
    if proc.returncode != 0:
        print(f"error: rg failed rc={proc.returncode} stderr={proc.stderr[:500]}", file=sys.stderr)
        sys.exit(2)

    return [
        line for line in proc.stdout.splitlines()
        if not any(allow in line for allow in ALLOWLISTED_PATHS)
    ]


def check_filenames() -> list[str]:
    """File-name gate — return paths that leak planning terminology.

    The content-grep above only catches comments/docstrings; a contributor who
    creates ``backend/tests/test_annex11_pinning.py`` or ``backend/app/W2_runner.py``
    would otherwise slip the terminology in via the filename. This walks
    every scan path and matches every file's relative path against
    :data:`BANNED_FILENAME_PATTERNS`.
    """
    offending: list[str] = []
    for scan_path in SCAN_PATHS:
        root = REPO_ROOT / scan_path
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            # Skip caches + venvs — never user-authored.
            if any(part in {"__pycache__", ".venv", "node_modules", "dist"}
                   for part in path.parts):
                continue
            rel = str(path.relative_to(REPO_ROOT))
            if rel in ALLOWLISTED_FILENAMES:
                continue
            if BANNED_FILENAME_RE.search(rel):
                offending.append(rel)

    return offending


def check_filename_pattern_contract() -> list[str]:
    """Return fixture failures for the narrow filename matcher contract."""
    failures: list[str] = []
    for path, should_match in FILENAME_PATTERN_CASES.items():
        matched = bool(BANNED_FILENAME_RE.search(path))
        if matched != should_match:
            failures.append(
                f"{path}: expected match={should_match}, observed match={matched}"
            )
    return failures


def main() -> int:
    content_offenders = check_content()
    filename_offenders = check_filenames()
    filename_contract_failures = check_filename_pattern_contract()

    if not content_offenders and not filename_offenders and not filename_contract_failures:
        print("check_no_planning_terminology: PASS")
        return 0

    if content_offenders:
        print(
            "FAIL: planning terminology leaked into code "
            "(rewrite in code-vocabulary terms, or add a justified "
            "ALLOWLISTED_PATHS entry):"
        )
        for line in content_offenders[:50]:
            print(f"  {line}")
        if len(content_offenders) > 50:
            print(f"  ... and {len(content_offenders) - 50} more")

    if filename_offenders:
        print("FAIL: planning terminology leaked into a filename (rename the file):")
        for line in filename_offenders[:50]:
            print(f"  {line}")
        if len(filename_offenders) > 50:
            print(f"  ... and {len(filename_offenders) - 50} more")

    if filename_contract_failures:
        print("FAIL: planning filename-pattern regression fixture failed:")
        for line in filename_contract_failures:
            print(f"  {line}")

    return 1


if __name__ == "__main__":
    sys.exit(main())
