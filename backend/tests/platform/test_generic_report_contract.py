"""Generic ``/report/generate`` contract pins.

The dispatch at
``POST /api/sim/use-cases/<use_case_id>/simulation/<sim_id>/report/generate``
plus the auto-report watcher's ``production_generator_factory`` are
the two surfaces the platform layer uses to drive a registered
adapter's ``generate_report`` to disk. The contracts pinned here:

* When an adapter declares
  ``adapter_runs_guardrails_internally = True`` the route loads the
  real ``RunMetadata`` from ``run_metadata.json`` and passes it to
  the adapter. Missing / invalid metadata surfaces as
  ``STATE_CORRUPTED`` 500 with ``details.reason`` discriminator;
  the SimpleNamespace fallback is reserved for lanes that do not
  carry a typed RunMetadata.

* The ``StructuredOutputClient`` target schema is the
  LLM-owned ``report_draft_schema`` (when set) so the strict-mode
  JSON enforced on the LLM is the subset the model is permitted to
  emit, not the persisted shape with caller-owned provenance the
  adapter merges in afterwards. Adapters that leave the field at
  ``None`` fall back to ``report_schema``.

* When ``adapter_runs_guardrails_internally = True`` the
  route + watcher SKIP the post-generate hook chain
  (``run_advisory_claim_pass``, ``apply_transparency_flags``,
  ``render_markdown``, ``assert_render_contract``). The
  ``build_evidence_catalog`` hook still fires BEFORE the adapter
  call because the adapter consumes it as an input.

* After writing the JSON (and optional markdown) the
  route calls ``refresh_output_path`` so
  ``run_metadata.output_paths`` / ``artifact_hashes`` reflect the
  freshly-written bytes.

* The watcher mirrors the route's write-time
  ``report_schema`` validation: a schema-invalid payload is
  rejected with ``reason="generated_report_schema_invalid"``
  before the atomic-write lands.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest
from pydantic import BaseModel

from app.simulation_platform import registry as platform_registry
from app.simulation_platform.auto_report_watcher import (
    production_generator_factory,
)
from app.simulation_platform.catalog import CatalogPayload, PersonaSpec
from app.simulation_platform.guardrails import GuardrailCapabilities
from tests._helpers import catalog_payload, make_app


@pytest.fixture
def app(monkeypatch):
    return make_app(monkeypatch, AUTH_ENABLED="false")


def _registry_snapshot():
    return dict(platform_registry._REGISTRY)  # noqa: SLF001


def _registry_restore(snapshot):
    platform_registry._REGISTRY.clear()  # noqa: SLF001
    platform_registry._REGISTRY.update(snapshot)  # noqa: SLF001


# ----------------------------------------------------------------------
# Shared adapter scaffolding
# ----------------------------------------------------------------------


def _persona_spec():
    return PersonaSpec(
        persona_id="p", label="P", cohort_question="q?",
        segment_types_by_scope={None: ["t"]},
    )


# ----------------------------------------------------------------------
# adapter_runs_guardrails_internally requires typed RunMetadata
# ----------------------------------------------------------------------


class _TypedRMAdapter:
    """Adapter that records the ``run_metadata`` it was handed."""

    report_schema = None
    report_draft_schema = None
    prepare_manifest_schema = None
    guardrail_capabilities = None
    adapter_runs_guardrails_internally = True

    def __init__(self) -> None:
        self.received_rm: Any = None

    def use_case_id(self) -> str:
        return "typed_rm_lane"

    def catalog(self) -> CatalogPayload:
        return catalog_payload(
            use_case_id=self.use_case_id(), personas=[_persona_spec()],
        )

    def artifact_names(self) -> Dict[str, str]:
        return {"report_json": "typed_rm_report.json"}

    def validate_scenario(self, payload):
        return payload

    def rehydrate_scenario(self, state_dict):
        return state_dict.get("scenario", state_dict)

    def parse_seed(self, **kwargs):
        return {}

    def load_presets(self):
        return []

    def build_ontology(self, scenario, run_dir):
        return None

    def write_prepare_artifacts(self, scenario, run_dir):
        return {}

    def run_simulation(self, scenario, run_dir):
        return None

    def run_sensitivity(self, simulation_id, body):
        raise NotImplementedError

    def generate_report(
        self, scenario, run_metadata, evidence_catalog, llm_client=None,
    ):
        self.received_rm = run_metadata
        return {"report_id": "typed_001"}


def _walk_to_completed(client, base, scenario_body):
    created = client.post(f"{base}/simulation/create", json=scenario_body)
    assert created.status_code == 201, created.get_data(as_text=True)
    sim_id = created.get_json()["data"]["simulation_id"]
    prep = client.post(f"{base}/simulation/{sim_id}/prepare")
    assert prep.status_code == 200, prep.get_data(as_text=True)
    started = client.post(f"{base}/simulation/{sim_id}/start")
    assert started.status_code == 200, started.get_data(as_text=True)
    return sim_id


def _write_valid_run_metadata(run_dir: Path) -> None:
    """Write a minimal valid ``run_metadata.json`` to ``run_dir``."""
    from app.simulation_platform.run_metadata import RunMetadata, ScaleCaps
    from app.policy.schema import Persona

    rm = RunMetadata(
        simulation_id=run_dir.name,
        created_at=datetime.now(timezone.utc),
        persona=Persona.CREATOR,
        scenario_hash="a" * 64,
        seed_hash="b" * 64,
        prepared_at=datetime.now(timezone.utc),
        artifact_paths={"manifest.json": "manifest.json"},
        artifact_hashes={"manifest.json": "c" * 64},
        zep_mode="mock",
        scale_caps=ScaleCaps(
            ensemble_n=1, stakeholders=1, rounds=1, cap_value=100,
        ),
    )
    (run_dir / "run_metadata.json").write_text(
        rm.model_dump_json(indent=2), encoding="utf-8",
    )


def test_generic_route_loads_real_run_metadata_when_adapter_requires(app):
    """When ``adapter_runs_guardrails_internally`` is True the route
    parses ``run_metadata.json`` via ``RunMetadata.model_validate_json``
    and hands the typed instance to the adapter -- NOT a SimpleNamespace.
    """
    from app.simulation_platform.run_metadata import RunMetadata

    snap = _registry_snapshot()
    adapter = _TypedRMAdapter()
    base = "/api/sim/use-cases/typed_rm_lane"
    try:
        platform_registry.register(adapter)  # type: ignore[arg-type]
        client = app.test_client()
        sim_id = _walk_to_completed(
            client, base, {"scenario": {"persona": "p"}},
        )
        # The route's start path does NOT write run_metadata.json for a
        # fake adapter; we synthesise it inline to mirror what the policy
        # lane's start service writes on the real path.
        from app.config import Config
        run_dir = Path(Config.OASIS_SIMULATION_DATA_DIR) / (
            f"typed_rm_lane/{sim_id}"
        )
        _write_valid_run_metadata(run_dir)
        res = client.post(f"{base}/simulation/{sim_id}/report/generate")
        assert res.status_code == 200, res.get_data(as_text=True)
        assert isinstance(adapter.received_rm, RunMetadata), (
            f"adapter received {type(adapter.received_rm).__name__}, "
            "expected RunMetadata"
        )
        assert adapter.received_rm.simulation_id == sim_id
    finally:
        _registry_restore(snap)


def test_async_report_job_status_persists_to_run_dir(tmp_path):
    """Async report jobs survive memory-table loss as run-local state."""
    from app.api import use_cases as use_cases_api

    run_dir = tmp_path / "creator" / "run_async_001"
    run_dir.mkdir(parents=True)
    use_cases_api._set_report_job(  # noqa: SLF001
        "job_async_001",
        {
            "job_id": "job_async_001",
            "use_case_id": "creator",
            "simulation_id": "run_async_001",
            "status": "queued",
        },
        run_dir=run_dir,
    )
    use_cases_api._REPORT_JOBS.pop("job_async_001", None)  # noqa: SLF001

    loaded = use_cases_api._load_report_job(  # noqa: SLF001
        run_dir,
        "job_async_001",
    )

    assert loaded is not None
    assert loaded["status"] == "queued"
    assert loaded["use_case_id"] == "creator"


def test_generic_route_500s_when_run_metadata_missing_under_typed_contract(
    app,
):
    """Missing ``run_metadata.json`` under
    ``adapter_runs_guardrails_internally=True`` surfaces as
    STATE_CORRUPTED 500 with ``details.reason="run_metadata_missing"``.
    """
    snap = _registry_snapshot()
    adapter = _TypedRMAdapter()
    base = "/api/sim/use-cases/typed_rm_lane"
    try:
        platform_registry.register(adapter)  # type: ignore[arg-type]
        client = app.test_client()
        sim_id = _walk_to_completed(
            client, base, {"scenario": {"persona": "p"}},
        )
        # Deliberately do NOT write run_metadata.json.
        res = client.post(f"{base}/simulation/{sim_id}/report/generate")
        assert res.status_code == 500, res.get_data(as_text=True)
        err = res.get_json()["error"]
        assert err["code"] == "STATE_CORRUPTED"
        assert err["details"]["reason"] == "run_metadata_missing"
    finally:
        _registry_restore(snap)


def test_generic_route_500s_when_run_metadata_corrupt_under_typed_contract(
    app,
):
    """Corrupt ``run_metadata.json`` surfaces as STATE_CORRUPTED with
    ``details.reason="run_metadata_corrupt"`` (vs. ``run_metadata_missing``).
    """
    snap = _registry_snapshot()
    adapter = _TypedRMAdapter()
    base = "/api/sim/use-cases/typed_rm_lane"
    try:
        platform_registry.register(adapter)  # type: ignore[arg-type]
        client = app.test_client()
        sim_id = _walk_to_completed(
            client, base, {"scenario": {"persona": "p"}},
        )
        from app.config import Config
        run_dir = Path(Config.OASIS_SIMULATION_DATA_DIR) / (
            f"typed_rm_lane/{sim_id}"
        )
        (run_dir / "run_metadata.json").write_text(
            "{not valid json", encoding="utf-8",
        )
        res = client.post(f"{base}/simulation/{sim_id}/report/generate")
        assert res.status_code == 500, res.get_data(as_text=True)
        err = res.get_json()["error"]
        assert err["code"] == "STATE_CORRUPTED"
        assert err["details"]["reason"] == "run_metadata_corrupt"
    finally:
        _registry_restore(snap)


# ----------------------------------------------------------------------
# StructuredOutputClient targets report_draft_schema when set
# ----------------------------------------------------------------------


def test_structured_output_target_prefers_report_draft_schema(monkeypatch):
    """When the adapter declares ``report_draft_schema``, the
    structured-output wrapper is built against the DRAFT shape -- not
    the persisted ``report_schema`` (which would force the LLM to
    fabricate caller-owned provenance the adapter merges in itself).

    Asserted by capturing the constructor call to
    ``StructuredOutputClient`` via monkeypatch.
    """
    class _DraftSchema(BaseModel):
        title: str

    class _PersistedSchema(BaseModel):
        title: str
        provenance: str

    class _Adapter:
        report_schema = _PersistedSchema
        report_draft_schema = _DraftSchema
        prepare_manifest_schema = None
        guardrail_capabilities = None
        adapter_runs_guardrails_internally = False

        def use_case_id(self): return "draft_target_lane"

        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(), personas=[_persona_spec()],
            )

        def artifact_names(self): return {"report_json": "r.json"}
        def validate_scenario(self, payload): return payload

        def rehydrate_scenario(self, state_dict):
            return state_dict.get("scenario", state_dict)

        def parse_seed(self, **kw): return {}
        def load_presets(self): return []
        def build_ontology(self, scenario, run_dir): return None
        def write_prepare_artifacts(self, scenario, run_dir): return {}
        def run_simulation(self, scenario, run_dir): return None

        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError

        def generate_report(self, scenario, run_metadata, evidence_catalog,
                            llm_client=None):
            return _PersistedSchema(title="t", provenance="p")

    captured: List[type] = []

    class _StubStructuredOutputClient:
        def __init__(self, inner, target_schema, schema_name):
            captured.append(target_schema)
            self._inner = inner

        def __getattr__(self, item):
            return getattr(self._inner, item)

    class _StubAuditedLLMClient:
        def __init__(self, inner, sink, phase):
            self._inner = inner

        def __getattr__(self, item):
            return getattr(self._inner, item)

    class _StubSink:
        def __init__(self, run_dir):
            self.run_dir = run_dir

    monkeypatch.setenv("LLM_API_KEY", "test-stub-key-not-used")
    # The route lazy-imports from app.simulation_platform.llm /
    # llm.provider; monkeypatch the symbols the lazy import resolves to.
    from app.simulation_platform import llm as _llm_mod
    from app.simulation_platform.llm import provider as _provider_mod

    monkeypatch.setattr(_llm_mod, "StructuredOutputClient",
                        _StubStructuredOutputClient)
    monkeypatch.setattr(_llm_mod, "AuditedLLMClient", _StubAuditedLLMClient)
    monkeypatch.setattr(_llm_mod, "RunMetadataAuditSink", _StubSink)
    monkeypatch.setattr(
        _provider_mod, "build_llm_client_for_run",
        lambda **kwargs: type("_Raw", (), {})(),
    )

    app = make_app(monkeypatch, AUTH_ENABLED="false")
    snap = _registry_snapshot()
    base = "/api/sim/use-cases/draft_target_lane"
    try:
        platform_registry.register(_Adapter())  # type: ignore[arg-type]
        client = app.test_client()
        sim_id = _walk_to_completed(
            client, base, {"scenario": {"persona": "p"}},
        )
        res = client.post(f"{base}/simulation/{sim_id}/report/generate")
        assert res.status_code == 200, res.get_data(as_text=True)
        assert captured == [_DraftSchema], (
            f"strict-mode JSON target should be the draft schema, "
            f"got {captured!r}"
        )
    finally:
        _registry_restore(snap)


def test_structured_output_target_falls_back_to_report_schema(monkeypatch):
    """When ``report_draft_schema`` is None the wrapper falls back to
    ``report_schema``. Adapters without a provenance-merge step set
    only the persisted schema and the LLM is asked for that shape.
    """
    class _PersistedSchema(BaseModel):
        title: str

    class _Adapter:
        report_schema = _PersistedSchema
        report_draft_schema = None
        prepare_manifest_schema = None
        guardrail_capabilities = None
        adapter_runs_guardrails_internally = False

        def use_case_id(self): return "fallback_target_lane"

        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(), personas=[_persona_spec()],
            )

        def artifact_names(self): return {"report_json": "r.json"}
        def validate_scenario(self, payload): return payload

        def rehydrate_scenario(self, state_dict):
            return state_dict.get("scenario", state_dict)

        def parse_seed(self, **kw): return {}
        def load_presets(self): return []
        def build_ontology(self, scenario, run_dir): return None
        def write_prepare_artifacts(self, scenario, run_dir): return {}
        def run_simulation(self, scenario, run_dir): return None

        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError

        def generate_report(self, scenario, run_metadata, evidence_catalog,
                            llm_client=None):
            return _PersistedSchema(title="t")

    captured: List[type] = []

    class _StubStructuredOutputClient:
        def __init__(self, inner, target_schema, schema_name):
            captured.append(target_schema)
            self._inner = inner

        def __getattr__(self, item):
            return getattr(self._inner, item)

    class _StubAuditedLLMClient:
        def __init__(self, inner, sink, phase):
            self._inner = inner

        def __getattr__(self, item):
            return getattr(self._inner, item)

    class _StubSink:
        def __init__(self, run_dir):
            self.run_dir = run_dir

    monkeypatch.setenv("LLM_API_KEY", "test-stub-key-not-used")
    from app.simulation_platform import llm as _llm_mod
    from app.simulation_platform.llm import provider as _provider_mod

    monkeypatch.setattr(_llm_mod, "StructuredOutputClient",
                        _StubStructuredOutputClient)
    monkeypatch.setattr(_llm_mod, "AuditedLLMClient", _StubAuditedLLMClient)
    monkeypatch.setattr(_llm_mod, "RunMetadataAuditSink", _StubSink)
    monkeypatch.setattr(
        _provider_mod, "build_llm_client_for_run",
        lambda **kwargs: type("_Raw", (), {})(),
    )

    app = make_app(monkeypatch, AUTH_ENABLED="false")
    snap = _registry_snapshot()
    base = "/api/sim/use-cases/fallback_target_lane"
    try:
        platform_registry.register(_Adapter())  # type: ignore[arg-type]
        client = app.test_client()
        sim_id = _walk_to_completed(
            client, base, {"scenario": {"persona": "p"}},
        )
        res = client.post(f"{base}/simulation/{sim_id}/report/generate")
        assert res.status_code == 200, res.get_data(as_text=True)
        assert captured == [_PersistedSchema], (
            f"strict-mode JSON target should fall back to report_schema, "
            f"got {captured!r}"
        )
    finally:
        _registry_restore(snap)


# ----------------------------------------------------------------------
# post-generate hook chain is skipped under
#         adapter_runs_guardrails_internally
# ----------------------------------------------------------------------


def _build_hook_recording_caps(
    invocations: List[str],
) -> GuardrailCapabilities:
    """Construct a ``GuardrailCapabilities`` whose hooks log invocation."""

    def _build_evidence_catalog(run_dir, scenario):
        invocations.append("build_evidence_catalog")
        return {"catalog": "ok"}

    def _run_advisory(report, evidence_catalog):
        invocations.append("run_advisory_claim_pass")
        return report

    def _apply_transparency(report, run_metadata):
        invocations.append("apply_transparency_flags")
        return report

    def _render(report):
        invocations.append("render_markdown")
        return "# md"

    def _assert_render(rendered):
        invocations.append("assert_render_contract")

    return GuardrailCapabilities(
        build_evidence_catalog=_build_evidence_catalog,
        run_advisory_claim_pass=_run_advisory,
        apply_transparency_flags=_apply_transparency,
        render_markdown=_render,
        assert_render_contract=_assert_render,
    )


def test_generic_route_skips_post_generate_chain_when_adapter_runs_guardrails(
    app,
):
    """An adapter that declares
    ``adapter_runs_guardrails_internally = True`` gets the
    ``build_evidence_catalog`` hook (pre-generate) but NOT the four
    post-generate hooks. Recorded via a hook-instrumented adapter.
    """
    invocations: List[str] = []

    class _SkippingAdapter:
        report_schema = None
        report_draft_schema = None
        prepare_manifest_schema = None
        adapter_runs_guardrails_internally = True
        guardrail_capabilities = _build_hook_recording_caps(invocations)

        def use_case_id(self): return "skip_chain_lane"

        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(), personas=[_persona_spec()],
            )

        def artifact_names(self): return {"report_json": "skip_chain.json"}
        def validate_scenario(self, payload): return payload

        def rehydrate_scenario(self, state_dict):
            return state_dict.get("scenario", state_dict)

        def parse_seed(self, **kw): return {}
        def load_presets(self): return []
        def build_ontology(self, scenario, run_dir): return None
        def write_prepare_artifacts(self, scenario, run_dir): return {}
        def run_simulation(self, scenario, run_dir): return None

        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError

        def generate_report(self, scenario, run_metadata, evidence_catalog,
                            llm_client=None):
            invocations.append("generate_report")
            assert evidence_catalog == {"catalog": "ok"}
            return {"report_id": "skip_001"}

    snap = _registry_snapshot()
    base = "/api/sim/use-cases/skip_chain_lane"
    try:
        platform_registry.register(_SkippingAdapter())  # type: ignore[arg-type]
        client = app.test_client()
        sim_id = _walk_to_completed(
            client, base, {"scenario": {"persona": "p"}},
        )
        from app.config import Config
        run_dir = Path(Config.OASIS_SIMULATION_DATA_DIR) / (
            f"skip_chain_lane/{sim_id}"
        )
        _write_valid_run_metadata(run_dir)
        res = client.post(f"{base}/simulation/{sim_id}/report/generate")
        assert res.status_code == 200, res.get_data(as_text=True)
        assert invocations == ["build_evidence_catalog", "generate_report"], (
            f"post-generate hooks must be skipped; got {invocations!r}"
        )
    finally:
        _registry_restore(snap)


def test_generic_route_runs_post_generate_chain_when_flag_false(app):
    """An adapter that declares
    ``adapter_runs_guardrails_internally = False`` still gets the
    full hook pipeline. Pins the inverse contract so the gate cannot
    silently flip on by default.
    """
    invocations: List[str] = []

    class _RunsChainAdapter:
        report_schema = None
        report_draft_schema = None
        prepare_manifest_schema = None
        adapter_runs_guardrails_internally = False
        guardrail_capabilities = _build_hook_recording_caps(invocations)

        def use_case_id(self): return "runs_chain_lane"

        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(), personas=[_persona_spec()],
            )

        def artifact_names(self):
            return {
                "report_json": "runs_chain.json",
                "report_md": "runs_chain.md",
            }

        def validate_scenario(self, payload): return payload

        def rehydrate_scenario(self, state_dict):
            return state_dict.get("scenario", state_dict)

        def parse_seed(self, **kw): return {}
        def load_presets(self): return []
        def build_ontology(self, scenario, run_dir): return None
        def write_prepare_artifacts(self, scenario, run_dir): return {}
        def run_simulation(self, scenario, run_dir): return None

        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError

        def generate_report(self, scenario, run_metadata, evidence_catalog,
                            llm_client=None):
            invocations.append("generate_report")
            return {"report_id": "runs_001"}

    snap = _registry_snapshot()
    base = "/api/sim/use-cases/runs_chain_lane"
    try:
        platform_registry.register(_RunsChainAdapter())  # type: ignore[arg-type]
        client = app.test_client()
        sim_id = _walk_to_completed(
            client, base, {"scenario": {"persona": "p"}},
        )
        res = client.post(f"{base}/simulation/{sim_id}/report/generate")
        assert res.status_code == 200, res.get_data(as_text=True)
        assert invocations == [
            "build_evidence_catalog",
            "generate_report",
            "run_advisory_claim_pass",
            "apply_transparency_flags",
            "render_markdown",
            "assert_render_contract",
        ], f"full hook pipeline missing: {invocations!r}"
    finally:
        _registry_restore(snap)


def test_policy_adapter_declares_runs_guardrails_internally_true():
    """Policy adapter declares the typed-metadata + skip contract."""
    from app.internal_policy.adapter import INTERNAL_POLICY_USE_CASE

    assert INTERNAL_POLICY_USE_CASE.adapter_runs_guardrails_internally is True


def test_policy_adapter_declares_report_draft_schema_as_llm_draft():
    """Policy adapter pins ``report_draft_schema`` to
    :class:`ReportLLMDraft` so the structured-output preflight targets
    the LLM-owned subset of :class:`Report`. Separate pin from the
    generic prefer-draft-over-persisted contract above.
    """
    from app.internal_policy.adapter import INTERNAL_POLICY_USE_CASE
    from app.policy.schema import ReportLLMDraft

    assert INTERNAL_POLICY_USE_CASE.report_draft_schema is ReportLLMDraft


def test_creator_adapter_declares_report_draft_schema_as_llm_draft():
    """Creator adapter pins ``report_draft_schema`` to
    :class:`CreatorReportLLMDraft` so the structured-output preflight
    targets the LLM-owned subset of :class:`CreatorReport`. The
    persisted :class:`CreatorReport` requires caller-stamped
    provenance (``report_id`` / ``simulation_id`` / ``persona`` /
    ``scenario_hash`` / ``seed_hash`` / ``generated_at``) which is
    merged in AFTER the LLM response; using the full schema as the
    strict-mode target would reject every valid response.
    """
    from app.creator.adapter import CREATOR_USE_CASE
    from app.creator.report_generator import (
        CreatorReportLLMDraft,
    )

    assert (
        CREATOR_USE_CASE.report_draft_schema
        is CreatorReportLLMDraft
    )


def test_creator_report_llm_draft_rejects_provenance_fields():
    """The draft schema MUST forbid the caller-owned provenance keys.
    If a draft schema accidentally tolerated them, the strict-mode
    pre-merge validation would mask LLM drift on the persisted
    surface (and the post-merge :class:`CreatorReport` validation
    would then face conflicting values).
    """
    from pydantic import ValidationError

    from app.creator.report_generator import (
        CreatorReportLLMDraft,
    )

    minimal_section = {
        "section_id": "s1",
        "title": "Section",
        "claims": [],
    }
    base_payload = {
        "executive_summary": [],
        "sections": [minimal_section],
        "evidence_gaps": [],
        "field_validation_questions": [],
    }
    CreatorReportLLMDraft.model_validate(base_payload)

    for forbidden in (
        "report_id", "simulation_id", "persona",
        "scenario_hash", "seed_hash", "generated_at",
    ):
        with pytest.raises(ValidationError):
            CreatorReportLLMDraft.model_validate(
                {**base_payload, forbidden: "value"},
            )


# ----------------------------------------------------------------------
# generic /report/generate refreshes run_metadata.output_paths
# ----------------------------------------------------------------------


def test_generic_route_refreshes_run_metadata_output_paths(app):
    """After writing the JSON artifact the route calls
    ``refresh_output_path`` so ``run_metadata.output_paths`` /
    ``artifact_hashes`` reflect the freshly-written file. Verified
    by reading the metadata back through ``RunMetadata.model_validate_json``
    and asserting the artifact entry landed.
    """
    from app.simulation_platform.run_metadata import RunMetadata

    class _MinimalAdapter:
        report_schema = None
        report_draft_schema = None
        prepare_manifest_schema = None
        guardrail_capabilities = None
        adapter_runs_guardrails_internally = False

        def use_case_id(self): return "refresh_lane"

        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(), personas=[_persona_spec()],
            )

        def artifact_names(self): return {"report_json": "refresh_report.json"}
        def validate_scenario(self, payload): return payload

        def rehydrate_scenario(self, state_dict):
            return state_dict.get("scenario", state_dict)

        def parse_seed(self, **kw): return {}
        def load_presets(self): return []
        def build_ontology(self, scenario, run_dir): return None
        def write_prepare_artifacts(self, scenario, run_dir): return {}
        def run_simulation(self, scenario, run_dir): return None

        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError

        def generate_report(self, scenario, run_metadata, evidence_catalog,
                            llm_client=None):
            return {"report_id": "refresh_001"}

    snap = _registry_snapshot()
    base = "/api/sim/use-cases/refresh_lane"
    try:
        platform_registry.register(_MinimalAdapter())  # type: ignore[arg-type]
        client = app.test_client()
        sim_id = _walk_to_completed(
            client, base, {"scenario": {"persona": "p"}},
        )
        from app.config import Config
        run_dir = Path(Config.OASIS_SIMULATION_DATA_DIR) / (
            f"refresh_lane/{sim_id}"
        )
        _write_valid_run_metadata(run_dir)
        res = client.post(f"{base}/simulation/{sim_id}/report/generate")
        assert res.status_code == 200, res.get_data(as_text=True)

        updated = RunMetadata.model_validate_json(
            (run_dir / "run_metadata.json").read_text(encoding="utf-8"),
        )
        assert "refresh_report.json" in updated.output_paths
        assert updated.output_paths["refresh_report.json"] == (
            "refresh_report.json"
        )
        assert "refresh_report.json" in updated.artifact_hashes
        # The sha matches the JSON payload that landed on disk.
        on_disk = (run_dir / "refresh_report.json").read_bytes()
        import hashlib
        assert updated.artifact_hashes["refresh_report.json"] == (
            hashlib.sha256(on_disk).hexdigest()
        )
    finally:
        _registry_restore(snap)


def test_generic_report_chat_audits_and_refreshes_cost_summary(
    monkeypatch,
    tmp_path,
) -> None:
    """Report chat uses the run provider through the audit/cost wrapper."""
    from app.api import use_cases as use_cases_mod
    from app.simulation_platform.run_metadata import RunMetadata
    from app.simulation_platform.llm.protocols import UsageInfo
    from app.simulation_platform.llm.router import ProviderReadiness

    app = make_app(monkeypatch, AUTH_ENABLED="false")
    monkeypatch.setattr(
        use_cases_mod.Config,
        "OASIS_SIMULATION_DATA_DIR",
        str(tmp_path),
    )
    app.config["OASIS_SIMULATION_DATA_DIR"] = str(tmp_path)

    class _ChatAuditAdapter:
        report_schema = None
        report_draft_schema = None
        prepare_manifest_schema = None
        guardrail_capabilities = None
        adapter_runs_guardrails_internally = False

        def use_case_id(self): return "chat_audit_lane"

        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(), personas=[_persona_spec()],
            )

        def artifact_names(self): return {"report_json": "chat_report.json"}
        def validate_scenario(self, payload): return payload

        def rehydrate_scenario(self, state_dict):
            return state_dict.get("scenario", state_dict)

        def parse_seed(self, **kw): return {}
        def load_presets(self): return []
        def build_ontology(self, scenario, run_dir): return None

        def write_prepare_artifacts(self, scenario, run_dir):
            (Path(run_dir) / "prepare.json").write_text(
                json.dumps({"prepared": True}), encoding="utf-8",
            )
            return {"artifacts": {"prepare": "prepare.json"}}

        def run_simulation(self, scenario, run_dir): return None

        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError

        def generate_report(self, scenario, run_metadata, evidence_catalog,
                            llm_client=None):
            return {
                "report_id": "chat_report_001",
                "summary": "Prepared report for chat.",
            }

    class _ChatLLM:
        model = "chat-audit-model"
        json_mode = "loose"
        last_usage = None

        def chat_json(self, messages, temperature=0.3, max_tokens=4096):
            self.last_usage = UsageInfo(tokens_in=88, tokens_out=12)
            return {"response": "Grounded answer", "sources": ["summary"]}

    import app.simulation_platform.llm.provider as provider_mod

    monkeypatch.setattr(
        use_cases_mod,
        "check_provider_readiness",
        lambda *args, **kwargs: ProviderReadiness(
            ready=True,
            status="configured_unprobed",
            configured=True,
            probed=False,
        ),
    )
    monkeypatch.setattr(
        provider_mod,
        "build_llm_client_for_run",
        lambda **kwargs: _ChatLLM(),
    )

    snap = _registry_snapshot()
    base = "/api/sim/use-cases/chat_audit_lane"
    try:
        platform_registry.register(_ChatAuditAdapter())  # type: ignore[arg-type]
        client = app.test_client()
        sim_id = _walk_to_completed(
            client, base, {"scenario": {"persona": "p"}},
        )
        report_res = client.post(f"{base}/simulation/{sim_id}/report/generate")
        assert report_res.status_code == 200, report_res.get_data(as_text=True)

        chat_res = client.post(
            f"{base}/simulation/{sim_id}/report/chat",
            json={"message": "What does the report say?"},
        )
        assert chat_res.status_code == 200, chat_res.get_data(as_text=True)
        assert chat_res.get_json()["data"]["response"] == "Grounded answer"

        metadata = RunMetadata.model_validate_json(
            (tmp_path / "chat_audit_lane" / sim_id / "run_metadata.json").read_text(
                encoding="utf-8",
            ),
        )
        assert metadata.llm_audit is not None
        assert len(metadata.llm_audit) == 1
        assert metadata.llm_audit[0].phase == "report"
        assert metadata.llm_audit[0].tokens_in == 88
        assert metadata.cost_summary is not None
        assert metadata.cost_summary.total_calls == 1
        assert metadata.cost_summary.total_tokens_out == 12
    finally:
        _registry_restore(snap)


# ----------------------------------------------------------------------
# watcher rejects schema-invalid payload before atomic write
# ----------------------------------------------------------------------


def test_watcher_rejects_schema_invalid_payload_before_write(
    tmp_path, monkeypatch,
):
    """The watcher's ``production_generator_factory`` validates the
    serialised payload against ``adapter.report_schema`` BEFORE the
    atomic write lands. Schema-invalid payloads return a
    :class:`GenerationOutcome` with
    ``reason="generated_report_schema_invalid"`` and the artifact
    is NOT written to disk.
    """

    class _StrictSchema(BaseModel):
        report_id: str
        required_other: str  # adapter will omit this -> ValidationError

    class _BadAdapter:
        report_schema = _StrictSchema
        report_draft_schema = None
        prepare_manifest_schema = None
        guardrail_capabilities = None
        adapter_runs_guardrails_internally = False

        def use_case_id(self): return "watcher_bad_schema_lane"

        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(), personas=[_persona_spec()],
            )

        def artifact_names(self):
            return {"report_json": "watcher_bad.json"}

        def validate_scenario(self, payload): return payload

        def rehydrate_scenario(self, state_dict):
            return state_dict.get("scenario", state_dict)

        def parse_seed(self, **kw): return {}
        def load_presets(self): return []
        def build_ontology(self, scenario, run_dir): return None
        def write_prepare_artifacts(self, scenario, run_dir): return {}
        def run_simulation(self, scenario, run_dir): return None

        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError

        def generate_report(self, scenario, run_metadata, evidence_catalog,
                            llm_client=None):
            # Missing ``required_other`` -- intentionally invalid.
            return {"report_id": "watcher_bad_001"}

    # Lay down a valid state.json + run_metadata.json so the watcher's
    # earlier eligibility / scenario / metadata gates all pass and the
    # write-time schema gate is what trips.
    sim_id = "watcher_sim_g5"
    run_dir = tmp_path / sim_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id,
            "use_case_id": "watcher_bad_schema_lane",
            "phase": "completed",
            "status": "completed",
            "scenario": {"persona": "p"},
        }),
        encoding="utf-8",
    )
    _write_valid_run_metadata(run_dir)

    # Stub the LLM stack so the watcher stays hermetic. The factory's
    # generate_report call is what triggers the schema-invalid payload return.
    from app.simulation_platform import llm as _llm_mod
    from app.simulation_platform.llm import provider as _provider_mod

    class _Stub:
        def __init__(self, *a, **kw): pass

        def __getattr__(self, item):
            return lambda *a, **kw: None

    monkeypatch.setattr(_llm_mod, "StructuredOutputClient", _Stub)
    monkeypatch.setattr(_llm_mod, "AuditedLLMClient", _Stub)
    monkeypatch.setattr(_llm_mod, "RunMetadataAuditSink", _Stub)
    monkeypatch.setattr(
        _provider_mod, "build_llm_client_for_run",
        lambda **kwargs: type("_Raw", (), {})(),
    )

    snap = _registry_snapshot()
    try:
        platform_registry.register(_BadAdapter())  # type: ignore[arg-type]
        outcome = production_generator_factory(
            simulation_id=sim_id,
            run_dir=run_dir,
            use_case_id="watcher_bad_schema_lane",
        )
        assert outcome.report_written is False
        assert outcome.reason == "generated_report_schema_invalid", (
            f"watcher must reject schema-invalid payloads with the "
            f"discriminator reason; got {outcome.reason!r}"
        )
        assert not (run_dir / "watcher_bad.json").exists(), (
            "schema-invalid payload must NOT be persisted to disk"
        )
    finally:
        _registry_restore(snap)
