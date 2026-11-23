#!/usr/bin/env python3
"""RawFish contract and drift gate.

Scans product docs, env templates, process scripts, Docker files, frontend
tests, route declarations, manifests, and the repository-owned hygiene contract. The point is
to fail on stale RawFish cutover claims instead of giving confidence from a
router-only check.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PRODUCT_ROOT.parent
HYGIENE_CONTRACT = PRODUCT_ROOT / "process/contracts/rawfish_hygiene_contract.json"

TEXT_EXTS = {
    "",
    ".cff",
    ".css",
    ".env",
    ".example",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".vue",
    ".yaml",
    ".yml",
}

SKIP_DIRS = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "coverage",
    "dist",
    "node_modules",
    "uploads",
}

# IA + Design Language v2 (boards 04/07): S0 home + one run per cockpit.
# Findings modes (read | ask | data) replace the report/analysis routes.
# Legacy /cockpit and /runs/:id/* URLs 404 — no compatibility surface.
CANONICAL_BROWSER_ROUTES = {
    "/",
    "/run/:runId?",
}

REMOVED_CREATOR_ROUTE = "creator-" + "audience-reaction"
REMOVED_CREATOR_PACKAGE = "creator_" + "audience_reaction"

LEGACY_BROWSER_PATTERNS = (
    re.compile(rf"http://localhost:3000/(?:process|policy|{REMOVED_CREATOR_ROUTE})\b"),
    re.compile(rf"(?<![\w./-])/(?:process|policy|{REMOVED_CREATOR_ROUTE})(?:/|`|\s|$)"),
    re.compile(r"(?<![\w-])/(?:simulation|report|interaction)/:[A-Za-z]"),
)

FORBIDDEN_PRODUCT_IDS = (
    REMOVED_CREATOR_PACKAGE,
    REMOVED_CREATOR_ROUTE,
)

FORBIDDEN_CONTRACT_TERMS = (
    "defineLane",
    "lane_plugin",
    "wizard_shell",
    "PolicySurfacePicker",
)

FONT_DRIFT_TERMS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "Space Grotesk",
)

STATISTICAL_WORKBENCH_SLOTS = {
    "statistical_workbench_inline_editor",
    "statistical_workbench_override_badge",
    "statistical_workbench_provenance_chip",
    "statistical_workbench_validity_strip",
    "statistical_workbench_variant_strip",
    "statistical_workbench_impact_tooltip",
    "statistical_workbench_preflight_meter",
    "statistical_workbench_drift_subpanel",
    "statistical_workbench_salt_seed_panel",
    "statistical_workbench_priors_attach_panel",
    "statistical_workbench_active_hours_grid",
    "statistical_workbench_manifest_chip",
}

PRESERVED_LEGACY_FRONTEND_DIRS = {
    PRODUCT_ROOT / "frontend/src/store",
    PRODUCT_ROOT / "frontend/src/utils",
    PRODUCT_ROOT / "frontend/src/api",
}


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


def is_text_file(path: Path) -> bool:
    return path.suffix in TEXT_EXTS or path.name.startswith(".env")


def iter_text_files(*roots: Path):
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if is_text_file(root):
                yield root
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if is_text_file(path):
                yield path


def line_iter(path: Path):
    for lineno, line in enumerate(read_text(path).splitlines(), start=1):
        yield lineno, line


def is_allowed_upstream_provenance(path: Path, line: str) -> bool:
    allowed_files = {
        PRODUCT_ROOT / "README.md",
        PRODUCT_ROOT / "LICENSE-ADDITIONS.md",
        PRODUCT_ROOT / "CITATION.cff",
    }
    if path not in allowed_files and path.suffix != ".md":
        return False
    # Remove only the attributed mentions, so another brand use on the same
    # line still fails. A documentation file is not a provenance exemption.
    remaining = re.sub(
        r"\b(?:upstream MiroFish|MiroFish upstream|public MiroFish fork)\b",
        "",
        line,
    )
    if path in allowed_files:
        remaining = remaining.replace(
            "[MiroFish](https://github.com/666ghj/MiroFish)", ""
        )
        remaining = remaining.replace("666ghj/MiroFish", "")
    if path == PRODUCT_ROOT / "docs/POLICY_PRODUCT_SPEC.md":
        remaining = re.sub(
            r"^### Where the name MiroFish may appear$", "", remaining
        )
        remaining = re.sub(
            r"^MiroFish's name may appear only where the text is genuinely "
            r"about the upstream work:",
            "",
            remaining,
        )
    return re.search("mirofish", remaining, re.I) is None


def explicit_external_endpoint_lines(path: Path) -> set[int]:
    """Locate endpoint assignments in Markdown examples that select external mode."""
    if path.suffix != ".md":
        return set()
    text = read_text(path)
    allowed: set[int] = set()
    for block in re.finditer(
        r"(?m)^(`{3,}|~{3,})env[ \t]*\n([\s\S]*?)^\1[ \t]*$", text
    ):
        body = block.group(2)
        modes = re.findall(
            r"(?m)^\s*(?:export\s+)?RAWFISH_LLM_PROVIDER_MODE\s*=([^\n]*)$", body
        )
        if len(modes) != 1 or modes[0].strip() not in {
            "external", '"external"', "'external'"
        }:
            continue
        first_line = text[:block.start(2)].count("\n") + 1
        for lineno, line in enumerate(body.splitlines(), first_line):
            if re.fullmatch(
                r"RAWFISH_(?:CONTAINER_)?LLM_BASE_URL=https://api\.openai\.com/v1",
                line.strip(),
            ):
                allowed.add(lineno)
    return allowed


def check_router(failures: list[str]) -> None:
    router = PRODUCT_ROOT / "frontend/src/router/index.js"
    if not router.exists():
        failures.append("frontend/src/router/index.js is missing")
        return
    text = read_text(router)
    for route in CANONICAL_BROWSER_ROUTES:
        if route not in text:
            failures.append(f"router missing canonical browser route {route!r}")
    for lineno, line in line_iter(router):
        for pattern in LEGACY_BROWSER_PATTERNS:
            if pattern.search(line):
                failures.append(
                    f"removed browser route in router at {rel(router)}:{lineno}: {line.strip()}"
                )
    if "import.meta.glob('../lanes/*/index.js'" in text or "defineLane" in text:
        failures.append("router still discovers lane plugins; RawFish uses manifests")


def check_text_drift(failures: list[str]) -> None:
    scan_roots = [
        PRODUCT_ROOT / "README.md",
        PRODUCT_ROOT / "RUNBOOK.md",
        PRODUCT_ROOT / "LICENSE-ADDITIONS.md",
        PRODUCT_ROOT / "CITATION.cff",
        PRODUCT_ROOT / ".env.example",
        PRODUCT_ROOT / ".env.local.template",
        PRODUCT_ROOT / "docker-compose.yml",
        PRODUCT_ROOT / "docker-compose.local.yml",
        PRODUCT_ROOT / "backend/docker-compose.yml",
        PRODUCT_ROOT / "docs",
        PRODUCT_ROOT / "process",
        PRODUCT_ROOT / "frontend/src/__tests__",
        PRODUCT_ROOT / "frontend/src/cockpit/__tests__",
    ]
    detector_allowlist = {
        Path(__file__).resolve(),
        (PRODUCT_ROOT / "process/check_rawfish_closure_contract.py").resolve(),
    }
    for path in iter_text_files(*scan_roots):
        if path.resolve() in detector_allowlist:
            continue
        external_endpoints = explicit_external_endpoint_lines(path)
        for lineno, line in line_iter(path):
            stripped = line.strip()
            if "MiroFish" in line or "MIROFISH" in line or "mirofish" in line:
                if not is_allowed_upstream_provenance(path, line):
                    failures.append(f"non-provenance old product string at {rel(path)}:{lineno}: {stripped}")
            for pattern in LEGACY_BROWSER_PATTERNS:
                if pattern.search(line):
                    failures.append(f"removed browser route doc/test leak at {rel(path)}:{lineno}: {stripped}")
            for needle in FORBIDDEN_PRODUCT_IDS:
                if needle in line:
                    failures.append(f"stale long-form creator lane id at {rel(path)}:{lineno}: {stripped}")
            for needle in FORBIDDEN_CONTRACT_TERMS:
                if needle in line:
                    failures.append(f"stale lane/plugin contract term {needle!r} at {rel(path)}:{lineno}: {stripped}")
            for needle in FONT_DRIFT_TERMS:
                if needle in line:
                    failures.append(f"external/old font drift {needle!r} at {rel(path)}:{lineno}: {stripped}")
            if "RawFish-V1.0" in line or "MiroFish-V1.0" in line:
                failures.append(f"stale engine badge at {rel(path)}:{lineno}: {stripped}")
            if "#FF4500" in line:
                failures.append(f"old orange token at {rel(path)}:{lineno}: {stripped}")
            if re.search(r"LLM_API_KEY.*\b(REQUIRED|required|must be present|must be set)\b", line):
                failures.append(f"key-required LLM posture at {rel(path)}:{lineno}: {stripped}")
            if "Default: OpenAI" in line or "OpenAI-only" in line or "OpenAI only" in line:
                failures.append(f"OpenAI-only/default posture at {rel(path)}:{lineno}: {stripped}")
            if re.search(
                r"\b(provider\.api_key|inline[- ](?:provider[- ])?(?:api_)?key|per-run api_key)\b",
                line,
                re.I,
            ):
                failures.append(f"inline provider-key posture at {rel(path)}:{lineno}: {stripped}")
            if "api.openai.com/v1" in line and lineno not in external_endpoints:
                failures.append(f"direct OpenAI cloud default at {rel(path)}:{lineno}: {stripped}")


def check_frontend_runtime_terms(failures: list[str]) -> None:
    scan_roots = [
        PRODUCT_ROOT / "frontend/src/App.vue",
        PRODUCT_ROOT / "frontend/src/main.js",
        PRODUCT_ROOT / "frontend/src/router",
        PRODUCT_ROOT / "frontend/src/cockpit",
        PRODUCT_ROOT / "frontend/src/lanes",
    ]
    for path in iter_text_files(*scan_roots):
        if path == Path(__file__).resolve():
            continue
        text = read_text(path)
        for needle in FORBIDDEN_CONTRACT_TERMS + FORBIDDEN_PRODUCT_IDS:
            if needle in text:
                for lineno, line in line_iter(path):
                    if needle in line:
                        failures.append(
                            f"frontend contract drift {needle!r} at {rel(path)}:{lineno}: {line.strip()}"
                        )


def check_fonts_global(failures: list[str]) -> None:
    for path in iter_text_files(PRODUCT_ROOT / "frontend"):
        if path == Path(__file__).resolve():
            continue
        if any(parent in path.parents for parent in PRESERVED_LEGACY_FRONTEND_DIRS):
            continue
        for lineno, line in line_iter(path):
            for needle in FONT_DRIFT_TERMS:
                if needle in line:
                    failures.append(f"frontend font drift {needle!r} at {rel(path)}:{lineno}: {line.strip()}")
            if "#FF4500" in line:
                failures.append(f"frontend old orange token at {rel(path)}:{lineno}: {line.strip()}")


def check_backend_contract(failures: list[str]) -> None:
    catalog = PRODUCT_ROOT / "backend/app/simulation_platform/catalog.py"
    if not catalog.exists():
        failures.append("backend/app/simulation_platform/catalog.py is missing")
    else:
        text = read_text(catalog)
        if "lane_manifest" not in text:
            failures.append("CatalogPayload must expose required lane_manifest")

    builtin_lanes = PRODUCT_ROOT / "backend/app/simulation_platform/builtin_lanes.py"
    if builtin_lanes.exists():
        text = read_text(builtin_lanes)
        if f"from app.{REMOVED_CREATOR_PACKAGE}" in text:
            failures.append(
                "built-in creator lane must register from app.creator, not "
                "a removed implementation package"
            )
        if "from app.policy.adapter import" in text:
            failures.append(
                "built-in policy lanes must register from app.internal_policy/"
                "app.public_policy, not app.policy.adapter"
            )
    else:
        failures.append("backend/app/simulation_platform/builtin_lanes.py is missing")

    for lane_module in (
        PRODUCT_ROOT / "backend/app/internal_policy/adapter.py",
        PRODUCT_ROOT / "backend/app/public_policy/adapter.py",
        PRODUCT_ROOT / "backend/app/creator/adapter.py",
        PRODUCT_ROOT / "backend/app/policy_core/lane_identity.py",
    ):
        if not lane_module.exists():
            failures.append(f"missing split policy architecture module: {rel(lane_module)}")

    backend_active_import_re = re.compile(
        r"(?:from\s+app\.api\.policy\s+import|import\s+app\.api\.policy)"
    )
    for path in iter_text_files(
        PRODUCT_ROOT / "backend/app/services",
        PRODUCT_ROOT / "backend/app/simulation_platform",
        PRODUCT_ROOT / "backend/app/internal_policy",
        PRODUCT_ROOT / "backend/app/public_policy",
        PRODUCT_ROOT / "backend/app/creator",
        PRODUCT_ROOT / "backend/app/api/use_cases.py",
    ):
        if backend_active_import_re.search(read_text(path)):
            failures.append(
                f"active RawFish path imports direct-policy blueprint at {rel(path)}"
            )

    for path in iter_text_files(
        PRODUCT_ROOT / "backend/app/services/policy",
        PRODUCT_ROOT / "backend/app/simulation_platform",
        PRODUCT_ROOT / "backend/app/internal_policy",
        PRODUCT_ROOT / "backend/app/public_policy",
        PRODUCT_ROOT / "backend/app/creator",
        PRODUCT_ROOT / "backend/app/api/use_cases.py",
    ):
        if "PolicySurface" in read_text(path):
            failures.append(
                f"active RawFish path leaks policy schema surface type at {rel(path)}"
            )

    for path in iter_text_files(PRODUCT_ROOT / "backend/app"):
        text = read_text(path)
        if "LLM_API_KEY is not configured" in text:
            failures.append(f"key-required LLM code path at {rel(path)}")
        graphiti_cloud_denylist = (
            path.name == "graphiti_store.py"
            and "llm_endpoint_is_openai_cloud" in text
        )
        if (
            "api.openai.com/v1" in text
            and "OPENAI_BASE_URL" not in text
            and not graphiti_cloud_denylist
        ):
            failures.append(f"direct OpenAI cloud default at {rel(path)}")


def check_behavioral_seams(failures: list[str]) -> None:
    # UI v2 (IA + Design Language boards): the cockpit surface is the
    # composed set — layout shell + runStore (single state owner) + step
    # docks + overlays. Behavioral seams live in the store; paint-free
    # step files compose it.
    cockpit = PRODUCT_ROOT / "frontend/src/cockpit/CockpitShell.vue"
    surface_paths = [
        cockpit,
        PRODUCT_ROOT / "frontend/src/cockpit/runStore.js",
        PRODUCT_ROOT / "frontend/src/cockpit/HomeView.vue",
    ]
    for directory in (
        PRODUCT_ROOT / "frontend/src/cockpit/steps",
        PRODUCT_ROOT / "frontend/src/cockpit/overlays",
    ):
        if directory.exists():
            surface_paths.extend(sorted(directory.glob("*.vue")))
    if cockpit.exists():
        text = "\n".join(read_text(path) for path in surface_paths if path.exists())
        cockpit_surface = text
        policy_builder = re.search(
            r"function\s+buildPolicyStakeholders\s*\([^)]*\)\s*\{(?P<body>.*?)\n\}",
            text,
            re.S,
        )
        if not policy_builder or "what_changes_their_mind" not in policy_builder.group("body"):
            failures.append(
                "CockpitShell buildPolicyStakeholders must include "
                "AudienceSegment.what_changes_their_mind"
            )
        if "const canGenerateReport" not in text or "backendPhase.value === 'completed'" not in text:
            failures.append("CockpitShell must gate report generation on completed phase")
        # v2 law 07: report generation is a primary RfButton whose disabled
        # state carries its reason (reportGateReason) beside the control.
        report_button = re.search(
            r"<RfButton(?P<body>[^>]*?@click=\"generateReport\"[^>]*?)>",
            cockpit_surface,
            re.S,
        )
        report_button_body = report_button.group("body") if report_button else ""
        disables_report_until_ready = (
            ":disabled=" in report_button_body
            and "reportGateReason" in report_button_body
            and ":reason=" in report_button_body
        )
        if not disables_report_until_ready:
            failures.append(
                "Cockpit report generation must be hidden or disabled until canGenerateReport"
            )
        for needle, label in (
            # Lifecycle + machine truth live in the Z4 console (board 03):
            # RfConsole renders the 34px status line and the streaming log.
            ("RfConsole", "the Z4 console (lifecycle home)"),
            ("reportClaims", "persisted report cards"),
            ("evidenceItems", "persisted evidence rail"),
            ("report-chat", "cockpit-local report notes panel"),
        ):
            if needle not in cockpit_surface:
                failures.append(f"CockpitShell must render {label}")
        if "statistical_settings" not in text or "loadStatisticalConfigForRun" not in text:
            failures.append("CockpitShell must create and hydrate run-bound Statistical Workbench settings")
        for needle in (
            "getStatisticalVariants",
            "compareSimulations",
            "loadAnalysisArtifacts",
            "probe_provider: Boolean(probeProvider)",
        ):
            if needle not in text:
                failures.append(f"CockpitShell must wire Workbench/provider analysis via {needle}")

    vite = PRODUCT_ROOT / "frontend/vite.config.js"
    if vite.exists():
        text = read_text(vite)
        if "src/components/policy/__tests__/**/*" in text:
            failures.append("Vitest must not include deleted policy component tests")
        exclude_block = re.search(
            r"exclude\s*:\s*\[(?P<body>.*?)\]",
            text,
            re.S,
        )
        if (
            exclude_block
            and "src/components/policy/__tests__/**" in exclude_block.group("body")
        ):
            failures.append("Vitest must not silently exclude policy component display tests")

    router = PRODUCT_ROOT / "backend/app/simulation_platform/llm/router.py"
    if router.exists():
        text = read_text(router)
        permissive_inline_default = re.search(
            r"allow_inline_secret\s*:\s*bool\s*=\s*True",
            text,
        )
        inline_assignment = re.search(
            r"base\[[\"']api_key[\"']\]\s*=\s*payload",
            text,
        )
        if permissive_inline_default or inline_assignment:
            failures.append("provider_config_from_payload must not accept inline api_key")
        explicit_inline_reject = (
            '"api_key" in payload' in text
            and "raise ValueError" in text
            and (
                "provider api_key is not accepted" in text
                or "inline provider api_key is not supported" in text
            )
        )
        if not explicit_inline_reject:
            failures.append("provider_config_from_payload must explicitly reject inline api_key")
        if "per-run api_key" in text:
            failures.append("provider readiness must not advertise inline per-run api_key as a durable path")
        if "missing_external_provider" not in text:
            failures.append("external provider readiness must require an explicit provider")

    use_cases = PRODUCT_ROOT / "backend/app/api/use_cases.py"
    if use_cases.exists():
        text = read_text(use_cases)
        fail_open = re.search(
            r"except\s+Exception[^\n]*:\n(?:[ \t]+[^\n]*\n){0,8}[ \t]+llm_client\s*=\s*None",
            text,
        )
        if fail_open:
            failures.append("generic report provider setup must fail closed, not set llm_client=None")
        for needle in (
            "_resolve_statistical_config_or_err",
            "_apply_statistical_config_to_state",
            "_persist_statistical_prepare_artifacts",
            "apply_statistical_json_patch",
            "path_not_in_allowlist",
            "stale_config",
            "/<use_case_id>/simulation/<sim_id>/variants",
            "/<use_case_id>/simulation/compare",
            "statistical_settings_changed_after_prepare",
            "prepared_statistical_settings_hash",
            "inline_external_secret_unsupported",
            "provider_client_build_failed",
        ):
            if needle not in text:
                failures.append(f"generic lifecycle must resolve statistical settings via {needle}")

    policy_adapter = PRODUCT_ROOT / "backend/app/policy/adapter.py"
    if policy_adapter.exists():
        text = read_text(policy_adapter)
        for needle in (
            "run.drift_horizon_rounds",
            "run.ensemble_n",
            "run.run_seed",
        ):
            if needle not in text:
                failures.append(f"policy adapter must consume {needle} from statistical settings")

    prepare_artifacts = PRODUCT_ROOT / "backend/app/services/policy/prepare_artifacts.py"
    if prepare_artifacts.exists():
        text = read_text(prepare_artifacts)
        for needle in ("statistical_context", "statistical_settings.json", "cohort.active_hours"):
            if needle not in text:
                failures.append(f"policy prepare artifacts must consume/write {needle}")

    removed_frontend_sources = [
        "frontend/src/api/" + "policy.js",
        "frontend/src/components/Step1GraphBuild.vue",
        "frontend/src/components/Step2EnvSetup.vue",
        "frontend/src/components/Step3Simulation.vue",
        "frontend/src/components/Step4Report.vue",
        "frontend/src/components/Step5Interaction.vue",
        "frontend/src/components/HistoryDatabase.vue",
        "frontend/src/components/policy",
        "frontend/src/store/policyDraft.js",
        "frontend/src/styles/policy.css",
        "frontend/src/styles/mf-primitives.css",
    ]
    for source in removed_frontend_sources:
        path = PRODUCT_ROOT / source
        if path.exists():
            failures.append(f"removed frontend source still present: {source}")


def check_manifests(failures: list[str]) -> None:
    for lane_id in ("creator", "internal_policy", "public_policy"):
        manifest = PRODUCT_ROOT / f"frontend/src/lanes/{lane_id}/lane_manifest.json"
        statistical = PRODUCT_ROOT / f"frontend/src/lanes/{lane_id}/statistical.manifest.json"
        if not manifest.exists():
            failures.append(f"missing lane manifest: {rel(manifest)}")
        else:
            try:
                lane_payload = json.loads(read_text(manifest))
            except json.JSONDecodeError as exc:
                failures.append(f"invalid lane manifest JSON at {rel(manifest)}: {exc}")
            else:
                slots = set(lane_payload.get("cockpit_slots") or [])
                missing = STATISTICAL_WORKBENCH_SLOTS - slots
                if missing:
                    failures.append(
                        f"lane manifest missing statistical workbench slots at "
                        f"{rel(manifest)}: {sorted(missing)}"
                    )
        if not statistical.exists():
            failures.append(f"missing statistical manifest: {rel(statistical)}")
            continue
        try:
            payload = json.loads(read_text(statistical))
        except json.JSONDecodeError as exc:
            failures.append(f"invalid statistical manifest JSON at {rel(statistical)}: {exc}")
            continue
        if payload.get("lane_id") != lane_id:
            failures.append(f"statistical manifest lane mismatch at {rel(statistical)}")
        fields = payload.get("fields")
        if not isinstance(fields, list) or len(fields) == 0:
            failures.append(f"empty statistical manifest fields at {rel(statistical)}")


def check_repository_contract(failures: list[str]) -> None:
    """Validate the RawFish-owned successor to the retired planning checks."""
    if not HYGIENE_CONTRACT.is_file():
        failures.append(f"missing repository hygiene contract: {rel(HYGIENE_CONTRACT)}")
        return
    try:
        contract = json.loads(read_text(HYGIENE_CONTRACT))
    except json.JSONDecodeError as exc:
        failures.append(f"invalid repository hygiene contract JSON: {exc}")
        return

    if contract.get("schema_version") != 1:
        failures.append("repository hygiene contract schema_version must be 1")
    if contract.get("legacy_decision_count_labels_allowed") is not False:
        failures.append("repository hygiene contract must reject legacy decision-count labels")

    active = contract.get("active_registry_kinds")
    non_registry = contract.get("non_registry_concepts")
    if not isinstance(active, list) or not active:
        failures.append("repository hygiene contract must name active registry kinds")
    if not isinstance(non_registry, list) or not non_registry:
        failures.append("repository hygiene contract must name non-registry concepts")
    if isinstance(active, list) and isinstance(non_registry, list) and set(active) & set(non_registry):
        failures.append("repository hygiene contract cannot classify one concept as both registry and non-registry")

    source_documents = contract.get("source_documents")
    if not isinstance(source_documents, list) or not source_documents:
        failures.append("repository hygiene contract must name repository-owned source documents")
        return
    native_sources: list[Path] = []
    for relative in source_documents:
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            failures.append(f"invalid repository source document path: {relative!r}")
            continue
        path = PRODUCT_ROOT / relative
        if not path.is_file():
            failures.append(f"missing repository source document: {relative}")
            continue
        native_sources.append(path)

    for path in iter_text_files(*native_sources):
        external_endpoints = explicit_external_endpoint_lines(path)
        for lineno, line in line_iter(path):
            stripped = line.strip()
            if re.search(r"LLM_API_KEY.*\b(REQUIRED|required|must be present|must be set)\b", line):
                failures.append(f"repository contract key-required LLM posture at {rel(path)}:{lineno}: {stripped}")
            if "Default: OpenAI" in line or "OpenAI-only" in line or "OpenAI only" in line:
                failures.append(f"repository contract OpenAI-only/default posture at {rel(path)}:{lineno}: {stripped}")
            if "api.openai.com/v1" in line and lineno not in external_endpoints:
                failures.append(f"repository contract direct OpenAI cloud default at {rel(path)}:{lineno}: {stripped}")
            if re.search(
                r"\b(provider\.api_key|inline[- ](?:provider[- ])?(?:api_)?key|per-run api_key)\b",
                line,
                re.I,
            ):
                failures.append(f"repository contract inline provider-key posture at {rel(path)}:{lineno}: {stripped}")

    combined_text = "\n".join(read_text(path) for path in native_sources)
    if "Decide-Now (8)" in combined_text or "Decide Now (8)" in combined_text:
        failures.append("repository source documents contain a legacy decision-count label")
    if "preset / connector / eval / memory-mode / history" in combined_text:
        failures.append("repository source documents list removed registry categories")
    if "product dir is still `MiroFish/`" in combined_text or "proof commands target `MiroFish/`" in combined_text:
        failures.append("repository source documents frame proof gates as pre-rename")
    stale_command = re.search(
        r"(?m)^\s*(?:rg|bash|python3|test|docker|cd|make|npm|uv|for)\b[^\n]*(?:\bMiroFish/| MiroFish\b)",
        combined_text,
    )
    if stale_command:
        failures.append(
            "repository source documents contain a stale executable proof command: "
            f"{stale_command.group(0).strip()}"
        )


def check_path_names(failures: list[str]) -> None:
    for path in PRODUCT_ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if "MiroFish" in path.name or "MIROFISH" in path.name or "mirofish" in path.name:
            failures.append(f"old product string in path name: {rel(path)}")


def main() -> int:
    failures: list[str] = []
    check_router(failures)
    check_text_drift(failures)
    check_frontend_runtime_terms(failures)
    check_fonts_global(failures)
    check_backend_contract(failures)
    check_behavioral_seams(failures)
    check_manifests(failures)
    check_repository_contract(failures)
    check_path_names(failures)

    if failures:
        print("RawFish contract gate: FAILED")
        for item in failures:
            print(f"- {item}")
        return 1
    print("RawFish contract gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
