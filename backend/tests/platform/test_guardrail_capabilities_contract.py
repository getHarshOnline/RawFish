"""Pins for the opt-in :class:`GuardrailCapabilities` contract.

The base :class:`app.simulation_platform.use_case.SimulationUseCase`
Protocol covers routing / persistence / catalog. The richer
guardrail surfaces (language contract, evidence binding, transparency
flags, markdown render, advisory claim demotions) are documented as
opt-in via :class:`app.simulation_platform.guardrails.GuardrailCapabilities`.

This test file pins:

  1. Every registered adapter declares ``guardrail_capabilities``
     (None is allowed; the attribute MUST exist so the dispatch
     layer's future-read does not need a defensive ``getattr``).
  2. The ``NO_GUARDRAILS`` sentinel is a fully-default
     :class:`GuardrailCapabilities` so adapters that opt out
     explicitly can pin against it.
  3. A populated :class:`GuardrailCapabilities` round-trips through
     attribute access for every documented hook field.
"""
from __future__ import annotations

from app.creator.adapter import CREATOR_USE_CASE
from app.internal_policy.adapter import INTERNAL_POLICY_USE_CASE
from app.simulation_platform.guardrails import (
    NO_GUARDRAILS,
    GuardrailCapabilities,
)
from app.simulation_platform.tests_helpers import build_fake_use_case


def test_policy_adapter_declares_guardrail_capabilities_slot() -> None:
    """Policy adapter ships ``GuardrailCapabilities`` with all five
    hooks wired: ``render_markdown``, ``assert_render_contract``,
    ``build_evidence_catalog``, ``apply_transparency_flags``, and
    ``run_advisory_claim_pass``. The last three bridge to the
    existing policy helpers via thin shims that adapt the Protocol
    hook signatures (``apply_transparency_flags`` re-loads scenario
    from state.json given the run_metadata's simulation_id;
    ``run_advisory_claim_pass`` runs the verifier + paraphrase legs
    without HTTP-layer bookkeeping)."""
    assert hasattr(INTERNAL_POLICY_USE_CASE, "guardrail_capabilities")
    caps = INTERNAL_POLICY_USE_CASE.guardrail_capabilities
    assert caps is not None
    assert caps.render_markdown is not None
    assert caps.assert_render_contract is not None
    assert caps.build_evidence_catalog is not None
    assert caps.run_advisory_claim_pass is not None
    assert caps.apply_transparency_flags is not None


def test_creator_adapter_declares_guardrail_capabilities_slot() -> None:
    """Creator adapter ships ``GuardrailCapabilities`` with the two
    language-side hooks (``render_markdown`` + ``assert_render_contract``).
    The remaining hooks stay ``None`` until the lane ships its
    evidence-store + transparency-flag surface."""
    assert hasattr(CREATOR_USE_CASE, "guardrail_capabilities")
    caps = CREATOR_USE_CASE.guardrail_capabilities
    assert caps is not None
    assert caps.render_markdown is not None
    assert caps.assert_render_contract is not None
    assert caps.build_evidence_catalog is None
    assert caps.run_advisory_claim_pass is None
    assert caps.apply_transparency_flags is None


def test_fake_use_case_declares_guardrail_capabilities_slot() -> None:
    """The FakeUseCase used by the conformance suite also declares
    the slot so a new dispatch-layer call site reading it does not
    blow up under test fixtures."""
    fake = build_fake_use_case()
    assert hasattr(fake, "guardrail_capabilities")
    assert fake.guardrail_capabilities is None


def test_no_guardrails_sentinel_is_empty_guardrail_capabilities() -> None:
    """``NO_GUARDRAILS`` is the canonical "every hook off" value."""
    assert isinstance(NO_GUARDRAILS, GuardrailCapabilities)
    for field in (
        "assert_render_contract",
        "apply_transparency_flags",
        "build_evidence_catalog",
        "render_markdown",
        "run_advisory_claim_pass",
    ):
        assert getattr(NO_GUARDRAILS, field) is None


def test_guardrail_capabilities_round_trip_with_hooks() -> None:
    """Every documented hook is independently settable. An adapter
    that ships ONLY markdown rendering and the language contract
    leaves the other three at ``None``; the dispatch layer keys off
    presence at each call site."""
    def _render(report): return "# ok"
    def _assert(rendered): return None

    caps = GuardrailCapabilities(
        render_markdown=_render,
        assert_render_contract=_assert,
    )
    assert caps.render_markdown is _render
    assert caps.assert_render_contract is _assert
    # The unmentioned hooks stay None.
    assert caps.apply_transparency_flags is None
    assert caps.build_evidence_catalog is None
    assert caps.run_advisory_claim_pass is None


def test_generic_report_route_invokes_guardrail_hooks_when_present(
    tmp_path, monkeypatch,
):
    """When an adapter wires ``guardrail_capabilities`` with concrete
    hooks, the generic ``POST .../report/generate`` route invokes them
    in the pinned order: build_evidence_catalog -> run_advisory_claim_pass
    -> apply_transparency_flags -> render_markdown -> assert_render_contract.
    The rendered markdown is persisted alongside the JSON artifact;
    the wire response carries ``report_md_path`` + ``sha256_md``.

    A failure in any hook surfaces as a structured ``REPORT_CORRUPTED``
    or ``ADAPTER_REPORT_FAILED`` envelope -- never a silent skip.
    """
    from app.simulation_platform import registry as platform_registry
    from app.simulation_platform.guardrails.capabilities import (
        GuardrailCapabilities,
    )
    from tests._helpers import catalog_payload, make_app

    invocations: list[str] = []

    def _build_catalog(run_dir, scenario):
        invocations.append("evidence")
        return {"resolved": ["s1", "s2"]}

    def _advisory(report, catalog):
        invocations.append("advisory")
        report["claims_after_advisory"] = True
        return report

    def _transparency(report, run_metadata):
        invocations.append("transparency")
        report["flags"] = ["ok"]
        return report

    def _render(report):
        invocations.append("render")
        return "# rendered\n"

    def _assert_render(rendered):
        invocations.append("render-contract")
        if "FORBIDDEN" in rendered:
            raise AssertionError("forbidden token in rendered output")

    class _GuardedAdapter:
        report_schema = None
        report_draft_schema = None
        prepare_manifest_schema = None
        adapter_runs_guardrails_internally = False
        guardrail_capabilities = GuardrailCapabilities(
            build_evidence_catalog=_build_catalog,
            run_advisory_claim_pass=_advisory,
            apply_transparency_flags=_transparency,
            render_markdown=_render,
            assert_render_contract=_assert_render,
        )

        def use_case_id(self): return "guarded_lane"

        def catalog(self):
            from app.simulation_platform.catalog import (
                PersonaSpec,
            )
            return catalog_payload(
                use_case_id=self.use_case_id(),
                personas=[PersonaSpec(
                    persona_id="x", label="X", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            )

        def artifact_names(self):
            return {"report_json": "guarded_report.json",
                    "report_md": "guarded_report.md"}

        def validate_scenario(self, payload): return payload
        def rehydrate_scenario(self, state_dict):
            return state_dict.get("scenario", state_dict)
        def parse_seed(self, **kwargs): return {}
        def load_presets(self): return []
        def build_ontology(self, scenario, run_dir): return None
        def write_prepare_artifacts(self, scenario, run_dir):
            return {"artifacts": {}}
        def run_simulation(self, scenario, run_dir): pass
        def generate_report(self, scenario, run_metadata, evidence_catalog, llm_client=None):
            # Capture that the catalog made it through.
            assert evidence_catalog == {"resolved": ["s1", "s2"]}
            invocations.append("generate")
            return {"claims": [], "evidence_catalog_seen": True}
        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError("not under test")

    snap = dict(platform_registry._REGISTRY)
    try:
        platform_registry.register(_GuardedAdapter())  # type: ignore[arg-type]
        app = make_app(monkeypatch, AUTH_ENABLED="false")
        client = app.test_client()
        created = client.post(
            "/api/sim/use-cases/guarded_lane/simulation/create",
            json={"scenario": {"persona": "x"}},
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        sim_id = created.get_json()["data"]["simulation_id"]
        client.post(
            f"/api/sim/use-cases/guarded_lane/simulation/{sim_id}/prepare",
        )
        client.post(
            f"/api/sim/use-cases/guarded_lane/simulation/{sim_id}/start",
        )
        res = client.post(
            f"/api/sim/use-cases/guarded_lane/simulation/{sim_id}/report/generate",
        )
        assert res.status_code == 200, res.get_data(as_text=True)
        data = res.get_json()["data"]
        assert data["phase"] == "reported"
        assert data["report_md_path"] == "guarded_report.md"
        assert "sha256_md" in data
    finally:
        platform_registry._REGISTRY.clear()
        platform_registry._REGISTRY.update(snap)

    # Pinned order. Catalog precedes generate (because the catalog
    # is built BEFORE the adapter so the adapter can consume it).
    # Advisory + transparency + render happen post-generate, in that
    # order, with render-contract last.
    assert invocations == [
        "evidence",
        "generate",
        "advisory",
        "transparency",
        "render",
        "render-contract",
    ], invocations
