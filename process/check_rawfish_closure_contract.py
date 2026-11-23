#!/usr/bin/env python3
"""RawFish closure-contract gate.

This gate is intentionally stricter than the hygiene gates. It does not
prove behavior by itself, but it blocks a false "closure" signal when the
codebase still carries known plan-level gaps:

* Statistical Workbench JSON-Patch routes and allowlist semantics.
* RunMetadata statistical configuration fields.
* Rich statistical manifest metadata.
* Provider preflight probe and provider-budget/external-budget warning surfaces.
* Exact route-surface assertions instead of subset checks.
* Active token/font/accent/old-brand drift.

Expected posture during the cutover: this gate can be red while backend and
frontend implementation agents are still landing their owned changes. A green
result means "closure checks no longer see these known static gaps"; it does
not replace targeted backend/frontend tests, smoke, build, or full app
behavior.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PRODUCT_ROOT.parent


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PRODUCT_ROOT))
    except ValueError:
        try:
            return str(path.relative_to(WORKSPACE_ROOT))
        except ValueError:
            return str(path)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def add(failures: list[str], path: Path, message: str) -> None:
    failures.append(f"{rel(path)}: {message}")


def require_text(
    failures: list[str],
    path: Path,
    needle: str,
    message: str,
) -> None:
    text = read_text(path) if path.exists() else ""
    if needle not in text:
        add(failures, path, message)


def check_statistical_workbench_routes(failures: list[str]) -> None:
    use_cases = PRODUCT_ROOT / "backend/app/api/use_cases.py"
    statistical = PRODUCT_ROOT / "backend/app/simulation_platform/statistical.py"
    if not use_cases.exists():
        add(failures, use_cases, "missing generic use-case dispatch module")
        return
    text = read_text(use_cases)

    if '"/<use_case_id>/statistical-manifest", methods=["GET"]' not in text:
        add(failures, use_cases, "missing lane-generic Statistical Workbench manifest route")
    if '"/<use_case_id>/simulation/<sim_id>/statistical-config", methods=["PATCH"]' not in text:
        add(
            failures,
            use_cases,
            "Workbench settings must use PATCH statistical-config, not a POST-only save route",
        )
    if '"/<use_case_id>/simulation/<sim_id>/statistical-config", methods=["POST"]' in text:
        add(
            failures,
            use_cases,
            "POST statistical-config remains mounted; closure route shape requires PATCH",
        )
    if "/<use_case_id>/simulation/<sim_id>/variants" not in text:
        add(failures, use_cases, "missing GET statistical/scenario variants route")
    if "/<use_case_id>/simulation/<sim_id>/statistical-config/reseed" not in text:
        add(failures, use_cases, "missing Workbench reseed route for variant lineage")
    compare_routes = (
        "/<use_case_id>/simulation/compare",
        "/<use_case_id>/simulation/<sim_id>/statistical-config/compare",
    )
    if not any(route in text for route in compare_routes):
        add(
            failures,
            use_cases,
            "missing Workbench compare route; expected lane-level or run-scoped compare surface",
        )
    if "statistical-lineage" in text and "/variants" not in text:
        add(
            failures,
            use_cases,
            "statistical-lineage exists without the planned variants route",
        )
    for needle, label in (
        ("expected_hash", "expected_hash stale-config guard"),
        ("stale_config", "409 stale_config response"),
        ("path_not_in_allowlist", "422 path_not_in_allowlist response"),
        ("JSON Pointer", "JSON Pointer patch vocabulary"),
    ):
        if needle not in text:
            add(failures, use_cases, f"missing Workbench {label}")
    if "apply_statistical_json_patch" not in text:
        add(
            failures,
            use_cases,
            "Workbench PATCH route must call apply_statistical_json_patch, not a plain settings merge",
        )

    if not statistical.exists():
        add(failures, statistical, "missing statistical platform module")
        return
    stat_text = read_text(statistical)
    if "apply_statistical_json_patch" not in stat_text or "_field_for_workbench_path" not in stat_text:
        add(
            failures,
            statistical,
            "missing JSON-Patch allowlist mapper for Workbench patch semantics",
        )
    if "next_settings.update(patch)" in stat_text and "apply_statistical_json_patch" not in stat_text:
        add(
            failures,
            statistical,
            "Workbench patch appears to rely only on plain dict merge; JSON-Patch writes must use allowlist semantics",
        )
    for needle, label in (
        ('"/run/run_seed"', "/run/run_seed"),
        ('"/run/ensemble_n"', "/run/ensemble_n"),
        ('"/run/ensemble_seeds"', "/run/ensemble_seeds"),
        ('"/run/t_structure"', "/run/t_structure"),
        ('"/run/t_narrative"', "/run/t_narrative"),
        ('"/run/drift_horizon_rounds"', "/run/drift_horizon_rounds"),
        ('"/run/assumption_confirmation_ratio"', "/run/assumption_confirmation_ratio"),
        ('tokens[:2] == ["run", "persona_salts"]', "/run/persona_salts/*"),
        ('tokens[0] == "cohorts"', "/cohorts/* namespace"),
        ('section == "statistical"', "/cohorts/*/statistical/*"),
        ('section == "priors"', "/cohorts/*/priors/*"),
        ('section == "drift"', "/cohorts/*/drift/*"),
    ):
        if needle not in stat_text:
            add(failures, statistical, f"missing Workbench allowlist support for {label}")
    if "statistical manifest field ids must be flat" in stat_text:
        add(
            failures,
            statistical,
            "statistical manifest validator still forbids dotted contract ids",
        )
    for needle, label in (
        ("citation_key", "required citation_key metadata"),
        ("why_it_matters", "why_it_matters metadata"),
        ('source: "runSurrogateExplainer"', "structured impact_hint.source"),
        ("lock_state_per_field", "lock-state metadata"),
        ("provenance_per_field", "per-field provenance metadata"),
    ):
        if needle not in stat_text:
            add(failures, statistical, f"missing rich manifest support for {label}")


def check_run_metadata_statistical_snapshot(failures: list[str]) -> None:
    path = PRODUCT_ROOT / "backend/app/simulation_platform/run_metadata.py"
    required = (
        "resolved_statistical_config",
        "statistical_settings_hash",
        "provenance_per_field",
        "lock_state_per_field",
        "persona_salts_resolved",
        "prior_attachments",
        "variant_lineage",
    )
    if not path.exists():
        add(failures, path, "missing platform RunMetadata model")
        return
    text = read_text(path)
    for field in required:
        if field not in text:
            add(failures, path, f"missing RunMetadata statistical field {field}")


def _load_json(path: Path, failures: list[str]) -> Any:
    try:
        return json.loads(read_text(path))
    except FileNotFoundError:
        add(failures, path, "missing JSON file")
    except json.JSONDecodeError as exc:
        add(failures, path, f"invalid JSON: {exc}")
    return None


def check_rich_statistical_manifests(failures: list[str]) -> None:
    lanes_root = PRODUCT_ROOT / "frontend/src/lanes"
    required_field_keys = {
        "id",
        "label",
        "why_it_matters",
        "citation_key",
        "impact_hint",
        "provenance",
        "lock_supported",
        "lane_applicability",
    }
    for lane_id in ("creator", "internal_policy", "public_policy"):
        path = lanes_root / lane_id / "statistical.manifest.json"
        payload = _load_json(path, failures)
        if not isinstance(payload, dict):
            continue
        fields = payload.get("fields")
        if not isinstance(fields, list) or not fields:
            add(failures, path, "statistical manifest must contain non-empty fields[]")
            continue
        for idx, field in enumerate(fields):
            if not isinstance(field, dict):
                add(failures, path, f"fields[{idx}] must be an object")
                continue
            field_id = str(field.get("id", ""))
            if field_id.startswith("statistical_workbench_"):
                add(
                    failures,
                    path,
                    f"fields[{idx}] uses compact id {field_id!r}; closure requires dotted ids",
                )
            missing = sorted(required_field_keys - set(field))
            if missing:
                add(failures, path, f"fields[{idx}] missing rich metadata keys {missing}")
            impact = field.get("impact_hint")
            if not isinstance(impact, dict) or impact.get("source") != "runSurrogateExplainer":
                add(
                    failures,
                    path,
                    f"fields[{idx}] impact_hint.source must be runSurrogateExplainer",
                )


def check_provider_preflight_contract(failures: list[str]) -> None:
    # UI v2: preflight lives in its own step dock; the store owns provider
    # state. Scan the composed surface, not the layout-only shell.
    cockpit = PRODUCT_ROOT / "frontend/src/cockpit/CockpitShell.vue"
    preflight_step = PRODUCT_ROOT / "frontend/src/cockpit/steps/StepPreflight.vue"
    run_store = PRODUCT_ROOT / "frontend/src/cockpit/runStore.js"
    use_cases = PRODUCT_ROOT / "backend/app/api/use_cases.py"
    if cockpit.exists():
        text = "\n".join(
            read_text(path)
            for path in (cockpit, preflight_step, run_store)
            if path.exists()
        )
        if "probe_provider: false" in text:
            add(failures, cockpit, "preflight hard-codes probe_provider: false")
        if "provider.base_url" not in text:
            add(failures, preflight_step, "provider base URL is not operator-editable in preflight")
    if use_cases.exists():
        text = read_text(use_cases)
        if "provider_budget_state" not in text:
            add(
                failures,
                use_cases,
                "preflight does not evaluate provider_budget_state for external provider budget",
            )
        if "external_budget_status" not in text:
            add(
                failures,
                use_cases,
                "preflight response must surface external_budget_status/provider-budget warning state",
            )


def check_route_and_skip_honesty(failures: list[str]) -> None:
    router = PRODUCT_ROOT / "frontend/src/router/index.js"
    if router.exists():
        text = read_text(router)
        route_paths = re.findall(r"path:\s*['\"]([^'\"]+)['\"]", text)
        # IA + Design Language v2 (board 04/07): S0 home + one run per
        # cockpit; findings modes replace the report/analysis routes.
        # Legacy URLs 404 — no compatibility surface.
        expected = ["/", "/run/:runId?"]
        if route_paths != expected:
            add(
                failures,
                router,
                f"browser route surface must be exactly {expected}, got {route_paths}; backend Workbench API routes do not count as browser routes",
            )

    use_case_test = PRODUCT_ROOT / "backend/tests/platform/test_use_cases_blueprint.py"
    if use_case_test.exists():
        text = read_text(use_case_test)
        if "expected.issubset(actual)" in text:
            add(
                failures,
                use_case_test,
                "generic route test uses subset assertion; closure requires exact route-surface pins",
            )
        if "actual == expected" not in text and "expected == actual" not in text:
            add(failures, use_case_test, "generic route test lacks exact equality assertion")

    conftest = PRODUCT_ROOT / "backend/tests/conftest.py"
    removed_skip_hook = "RETIREMENT" + "_REPLACEMENT_TESTS"
    if conftest.exists() and removed_skip_hook in read_text(conftest):
        add(failures, conftest, "removed-route skip hook must be removed")


def check_token_font_and_brand_drift(failures: list[str]) -> None:
    # RAWFISH_UI_CONTRACT.md retired the --rf-* namespace: the canonical
    # accent token is --accent and the token sheet is the only place that
    # may define custom properties.
    rawfish_css = PRODUCT_ROOT / "frontend/src/styles/rawfish.css"
    if rawfish_css.exists():
        text = read_text(rawfish_css)
        if "--accent: #FF5722" not in text:
            add(failures, rawfish_css, "canonical --accent must be #FF5722 in the RawFish token sheet")
        if "--rf-" in text:
            add(failures, rawfish_css, "retired --rf-* namespace must not reappear in the token sheet")
    styles_root = PRODUCT_ROOT / "frontend/src/styles"
    font_sources = [PRODUCT_ROOT / "frontend/index.html"]
    if styles_root.exists():
        font_sources.extend(sorted(styles_root.glob("*.css")))
    font_text = "\n".join(read_text(path) for path in font_sources if path.exists())
    docs_ack = any(
        "RAWFISH_FONT_FALLBACK_ACK" in read_text(path)
        for path in (
            PRODUCT_ROOT / "README.md",
            PRODUCT_ROOT / "RUNBOOK.md",
            PRODUCT_ROOT / "docs/POLICY_PRODUCT_SPEC.md",
        )
        if path.exists()
    )
    has_self_hosted_fonts = (
        "@font-face" in font_text
        and "Noto Sans" in font_text
        and "JetBrains Mono" in font_text
    )
    font_dir = PRODUCT_ROOT / "frontend/public/fonts"
    woff2_files = list(font_dir.glob("*.woff2")) if font_dir.exists() else []
    if "fonts.googleapis.com" in font_text or "fonts.gstatic.com" in font_text:
        add(
            failures,
            PRODUCT_ROOT / "frontend/index.html",
            "external Google font fetch remains; closure requires self-hosted fonts only",
        )
    if not has_self_hosted_fonts:
        suffix = "; RAWFISH_FONT_FALLBACK_ACK is informational only" if docs_ack else ""
        add(
            failures,
            rawfish_css,
            "self-hosted font contract missing: add @font-face for Noto Sans and JetBrains Mono backed by .woff2 files under frontend/public/fonts"
            + suffix,
        )
    elif len(woff2_files) < 2:
        add(
            failures,
            font_dir,
            "self-hosted font files missing: expected Noto Sans and JetBrains Mono .woff2 files under frontend/public/fonts",
        )

    active_scan_roots = [
        PRODUCT_ROOT / "backend/app",
        PRODUCT_ROOT / "backend/tests",
        PRODUCT_ROOT / "frontend/src",
    ]
    accent_scan_roots = [
        PRODUCT_ROOT / "frontend/index.html",
        PRODUCT_ROOT / "frontend/src",
    ]
    for root in accent_scan_roots:
        if not root.exists():
            continue
        paths = [root] if root.is_file() else root.rglob("*")
        for path in paths:
            if not path.is_file():
                continue
            if any(part in {"node_modules", "dist", "coverage"} for part in path.parts):
                continue
            if path.suffix not in {".html", ".js", ".vue", ".json", ".css", ".md"}:
                continue
            text = read_text(path)
            if "#FF4500" in text or "#ff4500" in text:
                add(failures, path, "legacy #FF4500 accent remains; closure requires canonical #FF5722")

    for root in active_scan_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in {"__pycache__", "node_modules", "dist", "coverage"} for part in path.parts):
                continue
            if path.suffix not in {".py", ".js", ".vue", ".json", ".css", ".md"}:
                continue
            text = read_text(path)
            old_upper_prefix = ("MIRO" + "FISH") + "_TEST_"
            old_dev_slug = ("miro" + "fish") + "-local-dev"
            if old_upper_prefix in text or old_dev_slug in text:
                add(failures, path, "active old-brand runtime/test identifier remains")
            stale_engine_badge = "RawFish" + "-V1.0"
            stale_old_badge = ("Miro" + "Fish") + "-V1.0"
            if stale_engine_badge in text or stale_old_badge in text:
                add(failures, path, "stale V1.0 badge remains")


def check_docs_honesty(failures: list[str]) -> None:
    required_ack = "make ci"
    for path in (
        PRODUCT_ROOT / "README.md",
        PRODUCT_ROOT / "RUNBOOK.md",
        PRODUCT_ROOT / "docs/POLICY_PRODUCT_SPEC.md",
    ):
        if not path.exists():
            add(failures, path, "required honesty doc missing")
            continue
        if required_ack not in read_text(path):
            add(failures, path, "must name make ci as the local closure bundle")


def main() -> int:
    failures: list[str] = []
    check_docs_honesty(failures)
    check_statistical_workbench_routes(failures)
    check_run_metadata_statistical_snapshot(failures)
    check_rich_statistical_manifests(failures)
    check_provider_preflight_contract(failures)
    check_route_and_skip_honesty(failures)
    check_token_font_and_brand_drift(failures)

    if failures:
        print("RawFish closure contract gate: FAILED")
        for item in failures:
            print(f"- {item}")
        return 1
    print("RawFish closure contract gate: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
