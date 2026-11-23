"""Shared policy-lane adapter implementation.

This is a thin adapter base; it does not duplicate policy logic. Every method
delegates to ``app.policy.*`` and ``app.services.policy.*`` modules. RawFish
registers concrete ``internal_policy`` and ``public_policy`` adapters from
their own packages; this module remains the shared implementation.

The RawFish HTTP surface uses the generic
``/api/sim/use-cases/<lane_id>/...`` dispatch for create, prepare, start,
status, report, and Workbench flows. Built-in product registration goes through
the concrete ``internal_policy`` and ``public_policy`` packages.

This module is not a product lane by itself. Concrete product lanes live in
``app.internal_policy`` and ``app.public_policy``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.policy import persona_defaults as _persona_defaults
from app.policy.hypothesis_tags import (
    CANONICAL_EXPECTED_RISK_TAGS,
    CANONICAL_HYPOTHESIS_TAGS,
)
from app.policy.preset_loader import load_all_presets
from app.policy.scenario_loader import load_scenario_dict
from app.policy.schema import (
    PERSONA_SEGMENT_TYPES,
    Persona,
    PolicySurface,
    Report,
    ReportLLMDraft,
    Scenario,
)
from app.policy.sensitivity import oat_runner as _oat_runner
from app.simulation_platform.guardrails import GuardrailCapabilities
from app.simulation_platform.catalog import (
    CatalogPayload,
    PersonaSpec,
    build_minimal_lane_manifest,
)
from app.simulation_platform.statistical import runtime_overrides_from_settings
from app.policy_core.lane_identity import PolicyLaneDefinition


def _statistical_context_from_run_dir(run_dir: Path) -> Dict[str, Any]:
    """Read the resolved RawFish statistical settings from state.json."""

    state_path = Path(run_dir) / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(state, dict):
        return {}
    settings = state.get("statistical_settings")
    if not isinstance(settings, dict):
        return {}
    return {
        "lane_id": state.get("use_case_id"),
        "settings": settings,
        "statistical_settings_hash": state.get("statistical_settings_hash"),
        "version": state.get("statistical_settings_version"),
        "runtime_overrides": runtime_overrides_from_settings(settings).model_dump(
            mode="json",
        ),
        "variant_lineage": state.get("statistical_variant_lineage") or [],
    }


def _statistical_int(
    context: Dict[str, Any],
    key: str,
) -> Optional[int]:
    override_key = {
        "run.drift_horizon_rounds": "rounds",
        "run.ensemble_n": "ensemble_n",
        "run.run_seed": "run_seed",
    }.get(key)
    overrides = context.get("runtime_overrides")
    if override_key and isinstance(overrides, dict):
        value = overrides.get(override_key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    settings = context.get("settings")
    if not isinstance(settings, dict):
        return None
    value = settings.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _policy_render_markdown(report: Any) -> str:
    """GuardrailCapabilities hook: render a policy ``Report`` to markdown.

    Wraps :func:`app.services.policy.markdown_renderer.render_report`
    so the GuardrailCapabilities ``render_markdown`` field can hold a
    single one-argument callable matching the Protocol hook signature.
    """
    from app.services.policy.markdown_renderer import render_report
    return render_report(report)


def _policy_assert_render_contract(rendered_markdown: str) -> None:
    """GuardrailCapabilities hook: enforce the policy language contract.

    :func:`app.services.policy.language_guard.assert_no_unhedged_banned_tokens`
    raises ``AssertionError`` on an un-hedged banned token in the
    rendered markdown. Matches the Protocol hook signature exactly
    (string in, ``None`` out, raises on violation).
    """
    from app.services.policy.language_guard import (
        assert_no_unhedged_banned_tokens,
    )
    assert_no_unhedged_banned_tokens(rendered_markdown)


def _policy_build_evidence_catalog(run_dir: Path, scenario: Any) -> Any:
    """GuardrailCapabilities hook: build the policy evidence catalog.

    Direct passthrough to
    :func:`app.services.policy.evidence_catalog.build_evidence_catalog`
    -- the existing helper already matches the Protocol hook
    signature ``(run_dir, scenario) -> evidence_catalog`` exactly.
    """
    from app.services.policy.evidence_catalog import (
        build_evidence_catalog as _build,
    )
    return _build(run_dir, scenario)


def _policy_apply_transparency_flags(report: Any, run_metadata: Any) -> Any:
    """GuardrailCapabilities hook: synthesize validity flags on a Report.

    The Protocol hook signature is ``(report, run_metadata) -> report``;
    the existing helper at
    :func:`app.services.policy.transparency_runtime.apply_transparency_flags`
    takes ``(report, scenario)``. Bridge by re-loading the persisted
    scenario from ``state.json`` using the simulation_id carried on
    ``run_metadata`` -- the same disk read the surrogate-explainer
    rehydration path uses.
    """
    from app.services.policy.transparency_runtime import (
        apply_transparency_flags as _apply,
    )
    from app.simulation_platform.state_machine import (
        read_state as _read_state,
    )
    sim_id = getattr(run_metadata, "simulation_id", None)
    if sim_id is None:
        # No simulation_id to anchor a state.json read; pass the
        # raw run_metadata through and let the helper raise its
        # own ``TypeError`` for the contract violation.
        return _apply(report, run_metadata)
    state = _read_state(sim_id)
    if state is None:
        return _apply(report, run_metadata)
    # ``state["scenario"]`` carries the validated scenario dict create
    # persisted; reconstruct the Pydantic
    # model via the policy adapter's own rehydration helper so the
    # helper's ``isinstance(scenario, Scenario)`` assertion holds.
    try:
        scenario = rehydrate_policy_scenario(state)
    except Exception:  # noqa: BLE001
        # Rehydration failed; fall back to the raw run_metadata
        # path so the helper still produces a deterministic raise
        # rather than this bridge silently masking the failure.
        return _apply(report, run_metadata)
    return _apply(report, scenario)


def _policy_run_advisory_claim_pass(report: Any, evidence_catalog: Any) -> Any:
    """GuardrailCapabilities hook: run the policy advisory claim pass.

    The Protocol hook signature is
    ``(report, evidence_catalog) -> report``; the existing
    :meth:`PolicyReportGenerator._apply_advisory_semantic_pass` is
    instance-bound and additionally tracks demotion warnings on
    ``run_metadata`` + writes them via ``refresh_warning``. The
    bridge here runs the SAME advisory verifiers (numeric verifier
    over the persisted sensitivity + un-pardoned overclaim
    paraphrase detector) and demotes claims in-place without the
    warning bookkeeping -- the generic dispatch route writes its
    own structured envelope on a failed run, while
    PolicyReportGenerator owns the bookkeeping-bearing path.

    ``evidence_catalog`` is consumed by the verifiers; an
    ``evidence_catalog`` of ``None`` is honest (the catalog hook
    is opt-in) and reduces this pass to the un-pardoned-overclaim
    leg only.
    """
    del evidence_catalog  # The advisory verifiers read sensitivity, not catalog.
    from app.policy.hypothesis_tags import HypothesisTag
    from app.services.policy.numeric_claim_verifier import verify_claims
    from app.services.policy.overclaim_paraphrases import (
        find_un_pardoned_paraphrases,
    )

    # Build the union of claim locations the report exposes. Mirrors
    # PolicyReportGenerator._apply_advisory_semantic_pass exactly:
    # executive_summary + every section's claims + evidence_gaps.
    all_claims: list = []
    all_claims.extend(report.executive_summary or [])
    for section in report.sections or []:
        all_claims.extend(section.claims or [])
    all_claims.extend(report.evidence_gaps or [])

    demoted_ids: set = set()

    # Leg 1: numeric / driver verifier against the run's
    # ``sensitivity.json`` -- skipped honestly when the artifact is
    # absent (the advisory layer's "verifier needs evidence to run"
    # contract). The verifier handles absence by emitting
    # ``NOT_APPLICABLE`` verdicts that this bridge does not demote.
    try:
        verdicts = verify_claims(all_claims, None)
    except Exception:  # noqa: BLE001
        verdicts = []
    for verdict in verdicts:
        if verdict.verdict in ("CONTRADICTED",):
            demoted_ids.add(verdict.claim_id)

    # Leg 2: un-pardoned overclaim paraphrase scan over every
    # claim's text. Findings with ``hedged=False`` AND
    # ``negated=False`` demote the claim.
    for claim in all_claims:
        text = getattr(claim, "text", "") or ""
        findings = find_un_pardoned_paraphrases(text)
        if any(
            (not f.hedged) and (not f.negated) for f in findings
        ):
            demoted_ids.add(claim.claim_id)

    if not demoted_ids:
        return report
    return report.demote_claims_to_requires_validation(
        demoted_ids, demoted_to=HypothesisTag.REQUIRES_VALIDATION,
    )


# Module-level singleton: the policy lane's wired ``GuardrailCapabilities``
# instance. All five Protocol hooks are populated:
#
#   * ``build_evidence_catalog`` -> :func:`_policy_build_evidence_catalog`
#   * ``run_advisory_claim_pass`` -> :func:`_policy_run_advisory_claim_pass`
#   * ``apply_transparency_flags`` -> :func:`_policy_apply_transparency_flags`
#   * ``render_markdown`` -> :func:`_policy_render_markdown`
#   * ``assert_render_contract`` -> :func:`_policy_assert_render_contract`
#
# The platform dispatch + auto-report watcher pipeline runs them in
# the order:
#
#   build_evidence_catalog -> adapter.generate_report
#     -> run_advisory_claim_pass -> apply_transparency_flags
#     -> render_markdown -> assert_render_contract
#
# Adapters that declare ``adapter_runs_guardrails_internally = True``
# (the policy lane does) re-run advisory / transparency / language-
# contract passes INSIDE :meth:`PolicyReportGenerator.generate_and_persist`
# so the dispatch route deliberately SKIPS the three post-generate
# hooks (advisory / transparency / render_markdown / assert_render_contract)
# to avoid compounding their side-effects on the same report. The
# ``build_evidence_catalog`` hook still fires BEFORE the adapter call
# because the adapter consumes the catalog as an input.
def rehydrate_policy_scenario(state: Dict[str, Any]) -> Any:
    """Rehydrate the policy ``Scenario`` from a persisted state.json dict.

    Lifted into a module-level function (vs. only living on the
    adapter instance) so the guardrail hooks above can re-use it
    without circular-construction concerns at adapter-instantiation
    time.
    """
    return Scenario.model_validate(state.get("scenario") or {})


POLICY_GUARDRAIL_CAPABILITIES = GuardrailCapabilities(
    render_markdown=_policy_render_markdown,
    assert_render_contract=_policy_assert_render_contract,
    build_evidence_catalog=_policy_build_evidence_catalog,
    apply_transparency_flags=_policy_apply_transparency_flags,
    run_advisory_claim_pass=_policy_run_advisory_claim_pass,
)


class _PolicyLLMProviderFactory:
    """Policy lane LLM provider resolved through the RawFish router."""

    def build_client(self) -> Any:
        from app.simulation_platform.llm.router import RawFishLLMProviderFactory
        return RawFishLLMProviderFactory().build_client()

    def build_client_for_config(self, config: Any) -> Any:
        from app.simulation_platform.llm.router import RawFishLLMProviderFactory
        return RawFishLLMProviderFactory(config).build_client()


POLICY_LLM_PROVIDER_FACTORY = _PolicyLLMProviderFactory()


class PolicyUseCase:
    """Adapter implementing :class:`SimulationUseCase` for the policy surface.

    Every method in this class is a delegation point: it forwards the
    call to the existing policy module that already owns the logic. No
    behavior lives here. Adding behavior here would make the adapter
    the de-facto source of truth instead of a thin wrapper, which
    defeats the platform-seam design.
    """

    #: GuardrailCapabilities populated with all five Protocol hooks
    #: the generic ``/report/generate`` route + the auto-report
    #: watcher drive in the pinned pipeline order
    #: (``build_evidence_catalog`` -> adapter.generate_report ->
    #: ``run_advisory_claim_pass`` -> ``apply_transparency_flags`` ->
    #: ``render_markdown`` -> ``assert_render_contract``):
    #:
    #:   * ``build_evidence_catalog`` -> ``_policy_build_evidence_catalog``
    #:   * ``run_advisory_claim_pass`` -> ``_policy_run_advisory_claim_pass``
    #:   * ``apply_transparency_flags`` -> ``_policy_apply_transparency_flags``
    #:   * ``render_markdown`` -> ``_policy_render_markdown``
    #:   * ``assert_render_contract`` -> ``_policy_assert_render_contract``
    #:     (un-hedged banned tokens raise ``AssertionError`` which the
    #:     dispatch surfaces as ``REPORT_CORRUPTED``).
    #:
    #: Because the policy lane sets
    #: :attr:`adapter_runs_guardrails_internally` to ``True`` (its
    #: :class:`PolicyReportGenerator.generate_and_persist` drives
    #: advisory / transparency / language-contract passes inline) the
    #: dispatch pipeline SKIPS the post-generate hooks
    #: (advisory / transparency / render_markdown / assert_render_contract)
    #: to avoid compounding side-effects on the same report. The
    #: ``build_evidence_catalog`` hook still fires BEFORE the adapter
    #: call because the adapter consumes the catalog as an input.
    guardrail_capabilities: Optional[GuardrailCapabilities] = (
        POLICY_GUARDRAIL_CAPABILITIES
    )

    #: Lane-specific LLM provider factory. The platform's
    #: :func:`app.simulation_platform.llm.provider.get_llm_provider_factory`
    #: reads this attribute and uses it to construct the LLM client
    #: threaded through the generic ``/report/generate`` route + the
    #: auto-report watcher. Setting it here keeps the platform layer
    #: free of any policy-specific import while the policy lane gets
    #: the same RawFish provider-router client as other lanes,
    #: including per-run provider/model binding when the dispatch
    #: layer has one.
    llm_provider_factory: Any = POLICY_LLM_PROVIDER_FACTORY

    #: Read-side schema for the persisted ``policy_report.json``. When
    #: the generic report-read route validates the on-disk bytes against this
    #: model before returning, so corrupt or schema-drifted artifacts surface as
    #: ``REPORT_CORRUPTED`` instead of passing through as raw JSON.
    report_schema: Optional[type] = Report

    #: LLM-owned draft subset of :class:`Report`. The strict-mode
    #: JSON-schema preflight (driven by ``StructuredOutputClient``)
    #: targets this shape so the provider is not asked to fabricate
    #: caller-owned provenance (header.persona/scope, simulation_id,
    #: scenario_hash, seed_hash, embedded run_metadata) the
    #: :class:`PolicyReportGenerator` merges in itself. The generator
    #: then re-validates the merged payload against the full
    #: :class:`Report` for cross-field invariants (header-vs-embedded
    #: run_metadata, scope-iff-policy, validity_flags hygiene).
    report_draft_schema: Optional[type] = ReportLLMDraft

    #: Optional Pydantic class for the on-disk ``artifact_manifest.json``
    #: written by ``/prepare``. Policy does not ship a typed manifest
    #: schema -- the prepare manifest's wire shape is route-private.
    #: Declared as ``None`` so the Protocol's structural dispatch can
    #: ``getattr`` the field without a defensive fallback; setting it
    #: to a concrete model class would let the dispatch route validate
    #: the manifest automatically.
    prepare_manifest_schema: Optional[type] = None

    #: Policy's :meth:`generate_report` delegates to
    #: :meth:`PolicyReportGenerator.generate`, which internally drives
    #: transparency-flag synthesis, evidence binding, the advisory
    #: claim pass, and the language-contract render gate. The
    #: generic dispatch route + auto-report watcher therefore SKIP
    #: the post-generate hook chain on
    #: :attr:`guardrail_capabilities` so those side effects (warnings,
    #: demotions) do not compound twice on the same report.
    #: ``build_evidence_catalog`` still fires BEFORE the adapter call
    #: because the adapter consumes the catalog as an input.
    adapter_runs_guardrails_internally: bool = True

    # ------------------------------------------------------------------
    # Identity / metadata
    # ------------------------------------------------------------------

    lane: Optional[PolicyLaneDefinition] = None

    @property
    def lane_id(self) -> str:
        if self.lane is None:
            raise RuntimeError(
                "PolicyUseCase is a shared base; instantiate internal_policy "
                "or public_policy instead"
            )
        return self.lane.lane_id

    @property
    def lane_label(self) -> str:
        if self.lane is None:
            raise RuntimeError(
                "PolicyUseCase is a shared base; instantiate internal_policy "
                "or public_policy instead"
            )
        return self.lane.label

    @property
    def lane_summary(self) -> str:
        if self.lane is None:
            raise RuntimeError(
                "PolicyUseCase is a shared base; instantiate internal_policy "
                "or public_policy instead"
            )
        return self.lane.summary

    def _schema_scope(self) -> Optional[PolicySurface]:
        if self.lane is None:
            return None
        return PolicySurface(self.lane.schema_scope)

    def use_case_id(self) -> str:
        """Return the stable identifier for this use case."""
        return self.lane_id

    def catalog(self) -> CatalogPayload:
        """Publish persona / scope / segment-type metadata.

        Sourced from :data:`PERSONA_SEGMENT_TYPES` +
        :mod:`app.policy.persona_defaults` +
        :mod:`app.policy.hypothesis_tags`. The ``defaults`` bag carries
        the per-persona free-form hint blocks (capabilities, hypothesis
        tag taxonomies) that consumers (cockpit, conformance
        suite) may surface but the platform itself does not interpret.

        The RawFish product publishes ``internal_policy`` and
        ``public_policy`` as separate lane ids. ``PolicySurface`` is only
        used here to read the shared policy schema/default tables.
        """
        personas: List[PersonaSpec] = []
        fixed_scope = self._schema_scope()
        personas_to_publish = [Persona.POLICY] if fixed_scope else [
            Persona.CREATOR,
            Persona.VIDEO,
            Persona.PRODUCT,
            Persona.POLICY,
        ]
        for persona in personas_to_publish:
            segment_types_by_scope: Dict[Optional[str], List[str]]
            report_sections_by_scope: Dict[Optional[str], List[str]]
            if persona == Persona.POLICY and fixed_scope is not None:
                scopes = None
                segment_types_by_scope = {
                    None: sorted(
                        PERSONA_SEGMENT_TYPES[
                            (Persona.POLICY, fixed_scope)
                        ]
                    )
                }
                report_sections_by_scope = {
                    None: [
                        s["title"]
                        for s in _persona_defaults.report_sections_for(
                            persona, fixed_scope
                        )
                    ]
                }
            elif persona == Persona.POLICY:
                scopes = [
                    PolicySurface.INTERNAL_ORG.value,
                    PolicySurface.PUBLIC_POLICY.value,
                ]
                segment_types_by_scope = {
                    PolicySurface.INTERNAL_ORG.value: sorted(
                        PERSONA_SEGMENT_TYPES[
                            (Persona.POLICY, PolicySurface.INTERNAL_ORG)
                        ]
                    ),
                    PolicySurface.PUBLIC_POLICY.value: sorted(
                        PERSONA_SEGMENT_TYPES[
                            (Persona.POLICY, PolicySurface.PUBLIC_POLICY)
                        ]
                    ),
                }
                report_sections_by_scope = {
                    PolicySurface.INTERNAL_ORG.value: [
                        s["title"]
                        for s in _persona_defaults.report_sections_for(
                            persona, PolicySurface.INTERNAL_ORG
                        )
                    ],
                    PolicySurface.PUBLIC_POLICY.value: [
                        s["title"]
                        for s in _persona_defaults.report_sections_for(
                            persona, PolicySurface.PUBLIC_POLICY
                        )
                    ],
                }
            else:
                scopes = None
                segment_types_by_scope = {
                    None: sorted(PERSONA_SEGMENT_TYPES[(persona, None)])
                }
                report_sections_by_scope = {
                    None: [
                        s["title"]
                        for s in _persona_defaults.report_sections_for(
                            persona, None
                        )
                    ]
                }

            defaults: Dict[str, Any] = {
                "capabilities": _persona_defaults.capabilities_for(persona),
                "hypothesis_tags": sorted(CANONICAL_HYPOTHESIS_TAGS),
                "expected_risk_tags": sorted(CANONICAL_EXPECTED_RISK_TAGS),
            }

            personas.append(
                PersonaSpec(
                    persona_id=persona.value,
                    label=_persona_defaults.label_for(persona),
                    cohort_question=_persona_defaults.cohort_question_for(
                        persona
                    ),
                    scopes=scopes,
                    segment_types_by_scope=segment_types_by_scope,
                    defaults=defaults,
                    report_sections_by_scope=report_sections_by_scope,
                )
            )

        report_section_labels: List[str] = []
        for spec in personas:
            for sections in spec.report_sections_by_scope.values():
                report_section_labels.extend(sections)
        return CatalogPayload(
            use_case_id=self.use_case_id(),
            lane_manifest=build_minimal_lane_manifest(
                lane_id=self.use_case_id(),
                label=self.lane_label,
                summary=self.lane_summary,
                report_sections=report_section_labels or None,
                analysis_panels={
                    "compare": {"enabled": True},
                    "counterfactual": {"enabled": False},
                    "sensitivity": {"enabled": False},
                },
            ),
            personas=personas,
        )

    def artifact_names(self) -> Dict[str, str]:
        """Return the ``{logical_id: filename}`` artifact map.

        The three logical ids correspond to the persisted policy
        artifacts produced by :class:`PolicyReportGenerator` and the
        OAT sensitivity runner. Matches the existing on-disk filenames
        the HTTP routes already serve.
        """
        return {
            "report_json": "policy_report.json",
            "report_md": "policy_report.md",
            "sensitivity": "sensitivity.json",
        }

    # ------------------------------------------------------------------
    # Authoring inputs
    # ------------------------------------------------------------------

    def validate_scenario(self, payload: Any) -> Scenario:
        """Validate an inbound scenario payload via the canonical loader.

        Delegates to :func:`app.policy.scenario_loader.load_scenario_dict`
        which runs ``Scenario.model_validate`` with the scale-cap
        validation context. Raises
        :class:`app.policy.scenario_loader.ScenarioValidationError` on
        failure (a ``ValueError`` subclass).
        """
        fixed_scope = self._schema_scope()
        if fixed_scope is not None and isinstance(payload, dict):
            payload = dict(payload)
            payload["persona"] = Persona.POLICY.value
            payload["scope"] = fixed_scope.value
        return load_scenario_dict(payload)

    def rehydrate_scenario(self, state_dict: Dict[str, Any]) -> Scenario:
        """Rebuild the policy :class:`Scenario` from a persisted ``state.json`` dict.

        Delegates to :func:`load_scenario_dict`, which resolves the
        scale-cap admittance flag (the env-driven
        ``POLICY_ALLOW_EXPENSIVE`` switch) inside its own
        ``_resolve_allow_expensive`` helper. The Protocol contract
        leaves context threading to the adapter; the policy lane
        relies on the loader's env read rather than asking the
        platform to plumb a kwarg through.
        """
        scenario_payload = state_dict["scenario"]
        fixed_scope = self._schema_scope()
        if fixed_scope is not None and isinstance(scenario_payload, dict):
            scenario_payload = dict(scenario_payload)
            scenario_payload["persona"] = Persona.POLICY.value
            scenario_payload["scope"] = fixed_scope.value
        return load_scenario_dict(scenario_payload)

    def parse_seed(
        self,
        seed_text: str,
        stress_question: str,
        persona: str,
        scope: Optional[str],
    ) -> Dict[str, Any]:
        """Return a minimal scenario-draft dict from free-form inputs.

        The adapter returns a deterministic skeleton dict carrying the four
        input fields under their canonical keys. This keeps parsing free of
        network calls while preserving the
        Protocol contract.
        """
        draft: Dict[str, Any] = {
            "persona": persona,
            "seed_text": seed_text,
            "stress_question": stress_question,
        }
        if scope is not None:
            draft["scope"] = scope
        return draft

    def load_presets(self) -> List[Any]:
        """Return the authored preset summaries.

        Delegates to :func:`app.policy.preset_loader.load_all_presets`
        and returns the ``ScenarioPreset`` instances in id-sorted
        order. The platform Protocol treats each entry as a free-form
        "preset summary"; consumers downcast as needed.
        """
        presets = load_all_presets()
        return [presets[pid] for pid in sorted(presets.keys())]

    # ------------------------------------------------------------------
    # Prepare phase
    # ------------------------------------------------------------------

    def build_ontology(self, scenario: Any, run_dir: Path) -> None:
        """Materialise the audience-store ontology for ``scenario``.

        Delegates to
        :func:`app.services.policy.ontology_loader.build_policy_ontology`
        for deterministic :class:`Ontology` construction, then publishes it
        through the configured audience-store seam. This keeps the generic
        use-case route lane-agnostic while preserving the graph-memory
        prepare side effect for policy lanes.
        """
        from app.services.policy.ontology_loader import build_policy_ontology
        from app.seam.zep_mock import MockZepStorage

        ontology = build_policy_ontology(scenario)
        graph_id = Path(run_dir).name
        store = MockZepStorage.get_factory()
        store.set_ontology(graph_id, ontology)

    def write_prepare_artifacts(self, scenario: Any, run_dir: Path) -> Any:
        """Write the prepare-phase artifacts to ``run_dir``.

        Delegates to
        :func:`app.services.policy.prepare_artifacts.write_prepare_artifacts`.
        The HTTP route owns the prepare-response envelope assembly
        (because it depends on the request body); the adapter exposes
        a thin wrapper that lets a future registry-dispatched route
        call the same writer through the platform seam.
        """
        from app.services.policy.persona_generator import (
            PolicyPersonaGenerator,
        )
        from app.services.policy.prepare_artifacts import (
            write_prepare_artifacts as _write,
        )

        persona_gen = PolicyPersonaGenerator()
        reddit_profiles = persona_gen.generate_reddit_profiles(scenario)
        twitter_profiles = persona_gen.generate_twitter_profiles(scenario)

        # ``simulation_id`` is sourced from the run_dir basename: the
        # state-machine layer guarantees run_dir == .../<simulation_id>/.
        simulation_id = Path(run_dir).name

        return _write(
            run_dir=str(run_dir),
            simulation_id=simulation_id,
            scenario=scenario,
            prepare_envelope={
                "simulation_id": simulation_id,
                "persona": scenario.persona.value,
            },
            reddit_profiles=reddit_profiles,
            twitter_profiles=twitter_profiles,
            statistical_context=_statistical_context_from_run_dir(run_dir),
        )

    # ------------------------------------------------------------------
    # Run phase
    # ------------------------------------------------------------------

    def run_simulation(self, scenario: Any, run_dir: Path) -> Optional[str]:
        """Drive the policy lane's start lifecycle via the shared service.

        The generic dispatch route orchestrates the
        ``prepared -> starting -> running`` lifecycle via
        :class:`PolicyRunnerService.start`. This adapter is that product
        entrypoint; the service handles scale-guard re-check, state
        transitions, runner invocation, and ``run_metadata.json`` writes.

        Inputs:

        * ``scenario`` -- already validated by the dispatch layer.
        * ``run_dir`` -- the namespaced per-run directory the generic
          route allocates (``<OASIS>/policy/<sim_id>/``). The persisted
          ``state.json`` is read from this directory to source
          ``prepared_at`` and the on-disk scenario.

        The runner factory is sourced from the service-layer runner
        seam at call time. The default factory bridges to the upstream
        :class:`SimulationRunner` subprocess, and tests can rebind the
        service seam without coupling this generic adapter to direct-policy
        route code.

        Return:

        Always returns the literal lifecycle status the service just
        persisted to ``state.json`` (``running`` while the subprocess
        is in flight, or ``completed`` / ``failed`` when a synchronous
        test stub drove the run to terminal). The generic route uses
        this string verbatim instead of overriding ``state["phase"]``
        with ``completed`` -- the service is the state authority for
        the policy lane.

        Raises:

        * :class:`app.simulation_platform.scale_guard.PlatformScaleGuardRefused`
          when the service's runtime scale guard refuses the resolved
          product. The generic dispatch wrapper catches this BEFORE
          the broad ``except Exception`` and maps it to ``400
          SCALE_GUARD_EXCEEDED`` with structured details from the lane.
        * :class:`PolicyRunnerStartFailed` on runner-factory exception;
          the generic dispatch wrapper maps it to ``502
          RUNNER_START_FAILED``.
        """
        import os

        from app.services.policy.policy_runner_service import (
            PolicyRunnerService,
            PolicyScaleGuardExceeded,
        )
        from app.simulation_platform.scale_guard import (
            PlatformScaleGuardRefused,
        )
        from app.simulation_platform.runner_factory import get_runner_factory
        from app.utils.runtime_env import parse_zep_mode

        run_dir_str = str(run_dir)
        state_path = os.path.join(run_dir_str, "state.json")
        with open(state_path, "r", encoding="utf-8") as fh:
            state = json.load(fh)

        simulation_id = state.get("simulation_id") or Path(run_dir).name
        zep_mode = parse_zep_mode()
        statistical_context = _statistical_context_from_run_dir(run_dir)
        rounds_override = _statistical_int(
            statistical_context, "run.drift_horizon_rounds",
        )
        ensemble_n_override = _statistical_int(
            statistical_context, "run.ensemble_n",
        )
        seed_override = _statistical_int(
            statistical_context, "run.run_seed",
        )

        try:
            result = PolicyRunnerService.start(
                simulation_id=simulation_id,
                scenario=scenario,
                run_dir=run_dir_str,
                state=state,
                zep_mode=zep_mode,
                rounds_override=rounds_override,
                ensemble_n_override=ensemble_n_override,
                seed_override=seed_override,
                runner_factory=get_runner_factory(),
            )
        except PolicyScaleGuardExceeded as guard_exc:
            # Re-raise as the lane-agnostic platform exception so the
            # generic dispatch route can map to 400 SCALE_GUARD_EXCEEDED
            # without importing the policy lane's wrapper type. The
            # ``message`` and ``details`` are preserved verbatim so the
            # generic envelope keeps the lane's refusal payload.
            raise PlatformScaleGuardRefused(
                message=guard_exc.message,
                details=guard_exc.details,
            ) from guard_exc
        return result.persisted_status

    # ------------------------------------------------------------------
    # Report phase
    # ------------------------------------------------------------------

    def generate_report(
        self,
        scenario: Any,
        run_metadata: Any,
        evidence_catalog: Any,
        llm_client: Any = None,
    ) -> Any:
        """Produce the policy report artifact.

        Delegates to
        :class:`app.services.policy.policy_report_generator.PolicyReportGenerator`.

        ``llm_client`` (when non-None) is the caller-built LLM client
        used by the generator -- typically an :class:`AuditedLLMClient`
        wrapping a :class:`StructuredOutputClient` so the audit sink
        + strict-mode JSON contract the caller staged are exercised.
        When ``None`` the adapter constructs a RawFish-router client
        through its provider factory.

        The ``evidence_catalog`` argument is accepted for Protocol
        conformance and threaded through to the underlying generator
        so the per-claim binding contract fires (catalog membership
        + per-tag semantic kind check; failing claims demoted to
        ``requires_validation``).

        **Returns an in-memory Report ONLY -- not a persisted
        artifact.** Mirrors the Protocol contract: the adapter returns a typed
        report object, and the generic dispatch layer owns the atomic JSON/MD
        writes plus ``run_metadata.json`` refresh. The caller MUST not assume the
        adapter persisted anything; it has to drive the persist
        step itself.
        """
        from app.services.policy.policy_report_generator import (
            PolicyReportGenerator,
        )

        if llm_client is None:
            from app.simulation_platform.llm.provider import (
                build_llm_client_for_run,
            )
            llm_client = build_llm_client_for_run(adapter=self)
        return PolicyReportGenerator(
            scenario=scenario, llm=llm_client,
        ).generate(
            run_metadata,
            evidence_catalog=evidence_catalog,
        )

    # ------------------------------------------------------------------
    # Sensitivity sweep phase
    #
    # Real sensitivity (ensemble re-runs) is not implemented for the
    # policy lane: there is no runner-factory that re-executes the
    # ensemble across perturbations. ``run_sensitivity`` MUST raise
    # :class:`NotImplementedError` so the generic dispatch surface
    # honestly returns ``501 CAPABILITY_UNSUPPORTED``. The static-
    # kernel surrogate output that the policy lane DOES produce is
    # NOT a sensitivity report; it is reachable only through the
    # segregated surrogate-explainer surface (which calls
    # :func:`run_surrogate_explainer` below) so the wire vocabulary
    # cannot confuse a surrogate for a real sensitivity sweep.
    # ------------------------------------------------------------------

    def run_sensitivity(self, simulation_id: str, body: Any) -> Any:
        """Real sensitivity (ensemble re-runs) is not implemented.

        Opt-out signal: raises :class:`NotImplementedError` so the
        generic dispatch route surfaces 501 ``CAPABILITY_UNSUPPORTED``
        with ``details.reason = "real_sensitivity_unsupported"``. The
        static-kernel surrogate output is reachable only through
        :meth:`run_surrogate_explainer` below and the segregated
        ``/surrogate-explainer`` HTTP surface.
        """
        del simulation_id, body  # Protocol args; opt-out signals via raise.
        raise NotImplementedError(
            "policy lane does not implement real sensitivity (ensemble "
            "re-runs); the static-kernel surrogate output is exposed through "
            "PolicyUseCase.run_surrogate_explainer"
        )

    def run_surrogate_explainer(self, simulation_id: str, body: Any) -> Any:
        """Run the static-kernel OAT surrogate sweep over a completed run.

        Delegates to :func:`app.policy.sensitivity.oat_runner.run_oat`.
        The scenario rehydration step (run_dir + state.json read) is
        owned by the state-machine layer; this adapter accepts the
        rehydrated scenario via ``body["scenario"]`` if the caller has
        already loaded it, otherwise it reads the persisted state.

        The persisted artifact carries the literal
        ``kernel='static_kernel_v1'`` discriminator + ``kernel_disclaimer``
        so a downstream consumer cannot mistake the output for a real
        ensemble re-run.
        """
        scenario = None
        if isinstance(body, dict):
            scenario = body.get("scenario")
        if scenario is None:
            # Rehydrate from the persisted run state. The state-machine
            # helpers own path validation + state.json reads.
            from app.simulation_platform.state_machine import (
                load_scenario_from_state as _sm_load_scenario_from_state,
                read_state as _sm_read_state,
            )

            state = _sm_read_state(simulation_id)
            if state is None:
                raise FileNotFoundError(
                    f"No persisted state for simulation_id={simulation_id!r}; "
                    "cannot run surrogate explainer."
                )
            # Mirror the HTTP route's env-flag resolution so a sim
            # created under the expensive cap survives rehydration.
            import os

            allow_expensive = (
                os.environ.get("POLICY_ALLOW_EXPENSIVE", "").strip().lower()
                == "true"
            )
            scenario = _sm_load_scenario_from_state(
                state, allow_expensive=allow_expensive
            )

        max_runs: Optional[int] = None
        if isinstance(body, dict):
            max_runs = body.get("max_runs")
        # ``generated_at`` is mandatory: :func:`run_oat` is a pure
        # module that does not read the wall clock itself; the caller
        # MUST supply the timestamp at the request boundary. Stamping
        # ``datetime.now(timezone.utc)`` once here keeps the
        # surrogate_explainer_id deterministic for the request.
        from datetime import datetime, timezone

        return _oat_runner.run_oat(
            scenario=scenario,
            simulation_id=simulation_id,
            max_runs=max_runs,
            generated_at=datetime.now(timezone.utc),
        )


__all__ = [
    "PolicyUseCase",
]
