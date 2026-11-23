#!/usr/bin/env python3
"""Not-a-Predictor scrub.

Hygiene gate that fails if predict/forecast/will-happen language leaks into
user-facing copy: README, package.json, frontend Vue/JS, locales, backend
Flask LLM prompts / API errors / persona vocab, repository-owned contracts,
and frontend/index.html. The allowlist is small by design: the deny-list
files themselves and separately pinned policy-report output from
``PolicyReportGenerator``. New exemptions require an explicit edit here
naming why.

The regex covers both case-sensitivity and morphology:
    predict / Predict / Prediction / Predicted / Predicting
    forecast / Forecast / Forecasting / Forecasted
    "prediction engine", "will happen", "will result", "future prediction"

This is a PROCESS gate, not a product test. It is intentionally NOT collected
by pytest. Invoke directly, or via ``process/run_hygiene_gates.sh``:

    python3 process/check_no_predictor_language.py

Exit code 0 on pass, 1 on any violation, 2 on tool error.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Case-insensitive (rg -i flag) and morphology-aware: covers predict, Predict,
# Prediction, Predicted, Predicting, forecast, Forecasting, Forecasted, and
# the noun phrase "prediction engine". The will-happen/will-result phrasings
# stay literal because they're already lowercase in product copy.
BANNED_RE = (
    r"\bpredict(?:s|ed|ing|ion|ions|or|ors|ive)?\b"
    r"|\bforecast(?:s|ed|ing|er|ers)?\b"
    r"|prediction engine"
    r"|will happen"
    r"|will result"
    r"|future prediction"
)

SCAN_PATHS = [
    "README.md",
    "RUNBOOK.md",
    "package.json",
    "frontend/src",
    "frontend/index.html",
    "backend/app",
    "locales",
    "docs",
    "process/contracts/rawfish_hygiene_contract.json",
    "docs/RAWFISH_UI_CONTRACT.md",
]

# Allowlist entries. Two forms are supported:
#
#   1. A bare string -- the WHOLE file is exempted. Use only for files
#      whose entire purpose is defining the deny-list (the spec, the
#      banned-tokens helper, etc.).
#
#   2. A 2-tuple ``(path, sentinel_substring)`` -- a line is
#      exempted ONLY when it comes from that path and carries the sentinel. Use this for
#      files that have legitimate non-predictor content alongside a few
#      contract-mirror lines (e.g. parser comments that quote upstream
#      marker strings). The sentinel guarantees future, unrelated
#      predictor-language leaks into the same file still trip the gate.
ALLOWLISTED_PATHS = [
    # Files that DEFINE the Not-a-Predictor deny-list (must quote the words):
    "README.md",  # the canonical "Not a Predictor" section
    "RUNBOOK.md",  # operator runbook, restates the contract
    "docs/POLICY_PRODUCT_SPEC.md",  # canonical product contract spec
    "docs/GUARDRAILS.md",  # documents the banned-token gate by name
    # S0 hero restates the product contract verbatim — "a pre-mortem, never
    # a prediction" is the Not-a-Predictor disclaimer itself (voice board:
    # findings speak pre-mortem language). Sentinel-scoped so any OTHER
    # predictor leak into HomeView still trips the gate.
    ("frontend/src/cockpit/HomeView.vue", "never a prediction"),
    "docs/extending-rawfish.md",  # describes the not-a-predictor seam
    # (persona_defaults.py was previously allowlisted for predictor-
    # language strings; a later refactor scrubbed both to neutral
    # phrasing -- "numeric point estimate" / "Hypothesized retention
    # drop sections" -- so the file no longer needs an exemption and
    # the gate's pass on persona_defaults is a real pass now, not
    # allowlisted.)
    # The policy-report markdown renderer's caveat block is the canonical
    # surface for the "not a forecast" wording -- it MUST appear at the
    # top of every rendered report so a reader scanning the doc sees the
    # non-predictive framing before any claim. The file's docstring +
    # caveat comments also discuss the banned-token gate itself (which
    # words it forbids, which hedges allow them). Allowlisted for the
    # same reason as persona_defaults.py: it defines the contract rather
    # than violating it. The eventual banned-token regression test runs
    # against the rendered OUTPUT (not this source) so the contract is
    # still enforced where it matters.
    "backend/app/services/policy/markdown_renderer.py",
    # The policy-report language guard is the canonical
    # BANNED_PATTERNS / HEDGE_PATTERNS module -- it MUST name every
    # forbidden phrase verbatim so the regexes can match. Lives in
    # ``app/`` (not ``tests/``) so the generator can call it before
    # persisting policy_report.json (runtime contract, not test-only).
    # Same posture as markdown_renderer.py / persona_defaults.py:
    # defines the contract.
    "backend/app/services/policy/language_guard.py",
    # The policy-report prompt builder MUST name every banned phrase
    # verbatim in the LLM-facing system prompt, because the prompt is
    # instructing the model what NOT to emit. Comments + docstring also
    # discuss the non-predictive framing of the product contract. Same
    # allowlist posture as the markdown renderer + persona_defaults.py:
    # defines the contract rather than violating it. The banned-token
    # regression gate runs against the rendered report OUTPUT (not this
    # source) so the contract is still enforced where it matters.
    "backend/app/services/policy/prompt_builder.py",
    # The advisory paraphrase-overclaim detector module: owns the
    # COMPLEMENT corpus to language_guard.py (paraphrased overclaims
    # that slip the exact-string banned-token regex). The module
    # docstring + corpus literals MUST name the banned vocabulary
    # verbatim so the detector can specify what to flag. Same posture
    # as language_guard.py: defines the contract rather than violating
    # it; the regression gate runs against rendered report OUTPUT.
    "backend/app/services/policy/overclaim_paraphrases.py",
    # The numeric / comparative claim verifier docstring draws the
    # boundary between its own scope (numeric + superlative claims)
    # and the predictive-overclaim corpus handled by language_guard +
    # overclaim_paraphrases. Naming the other module's domain is
    # required to specify what this verifier does NOT cover. Contract-
    # definition file; runtime output is enforced by the language
    # guard against the rendered report.
    "backend/app/services/policy/numeric_claim_verifier.py",
    # The PolicyReportGenerator's advisory-pass section names the
    # paraphrase-overclaim detector module by its feature name so a
    # reader navigating the lifecycle can locate the corresponding
    # helper. Two comment sites only; the rest of the file is neutral.
    # Narrowed allowlist: ONLY lines that quote the verbatim feature
    # name "predictive-overclaim paraphrase detector" are exempt, so a
    # genuine predictor-language leak elsewhere in this file still
    # trips the gate.
    (
        "backend/app/services/policy/policy_report_generator.py",
        "predictive-overclaim paraphrase detector",
    ),
    (
        "backend/app/services/policy/policy_report_generator.py",
        "Predictive-overclaim paraphrase detector",
    ),
    # The banned-token regression helper that DEFINES the BANNED_PATTERNS
    # list itself -- the module has to name every forbidden phrase
    # verbatim so the regexes can match. Same allowlist posture as the
    # markdown renderer + persona_defaults.py: defines the contract.
    "backend/tests/policy/_banned_tokens.py",
    # The test file that exercises the regression helper -- it constructs
    # planted-failure inputs containing each banned phrase ("will happen",
    # "predicts", etc.) so the negative-case assertions can verify the
    # gate bites. Without this allowlist entry every planted-failure
    # string in the test source would fail the hygiene gate, defeating
    # the test's purpose.
    "backend/tests/policy/test_report_banned_tokens.py",
    # The prompt-builder test file exercises the paraphrase contract by
    # asserting that the rendered system prompt CONTAINS the banned
    # phrases (the prompt teaches the LLM what not to emit). It also
    # carries planted "verbatim forbidden" sentinels drawn from the
    # source planning paragraph the prompt is conceptually derived
    # from. Same posture as test_report_banned_tokens.py.
    "backend/tests/services/policy/test_prompt_builder.py",
]


def main() -> int:
    if shutil.which("rg") is None:
        print("error: ripgrep (rg) not installed", file=sys.stderr)
        return 2

    # -i for case-insensitive; -P for PCRE (word boundaries + non-capturing).
    missing = [path for path in SCAN_PATHS if not (REPO_ROOT / path).exists()]
    if missing:
        print("FAIL: Not-a-Predictor scrub — required scan inputs are missing:")
        for path in missing:
            print(f"  {path}")
        return 1
    scan_paths = list(SCAN_PATHS)

    proc = subprocess.run(
        ["rg", "-niP", BANNED_RE, *scan_paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # rg exit codes: 0=matches found, 1=no matches, 2=error
    if proc.returncode not in (0, 1):
        print(f"error: rg failed rc={proc.returncode} stderr={proc.stderr[:500]}", file=sys.stderr)
        return 2
    if proc.returncode == 1:
        print("check_no_predictor_language: PASS")
        return 0

    def is_allowlisted(line: str) -> bool:
        for entry in ALLOWLISTED_PATHS:
            if isinstance(entry, tuple):
                path_sub, sentinel_sub = entry
                if line.startswith(path_sub + ":") and sentinel_sub in line:
                    return True
            else:
                if line.startswith(entry + ":"):
                    return True
        return False

    offending = [line for line in proc.stdout.splitlines() if not is_allowlisted(line)]
    if not offending:
        print("check_no_predictor_language: PASS (all matches allowlisted)")
        return 0

    print(
        "FAIL: Not-a-Predictor scrub — these lines must be reworded "
        "or explicitly allowlisted with a comment:"
    )
    for line in offending:
        print(f"  {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
