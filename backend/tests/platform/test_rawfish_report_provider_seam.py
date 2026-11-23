from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from app.simulation_platform import registry as platform_registry
from app.simulation_platform.auto_report_watcher import production_generator_factory
from app.simulation_platform.catalog import CatalogPayload, PersonaSpec
from app.simulation_platform.llm.router import ProviderConfig
from app.simulation_platform.llm import provider as llm_provider
from app.simulation_platform.tests_helpers import FakeUseCase
from tests._helpers import catalog_payload, make_app


class _RawClient:
    def __init__(self, config: ProviderConfig):
        self.config = config
        self.model = f"{config.provider or config.mode}/{config.model}"
        self.json_mode = "loose"
        self.policy_max_tokens_report = 4096
        self.last_usage = None

    def chat_json(self, messages, temperature=0.3, max_tokens=4096):
        del messages, temperature, max_tokens
        return {"ok": True}


class _CapturingFactory:
    def __init__(self) -> None:
        self.configs: List[ProviderConfig] = []

    def build_client(self):
        raise AssertionError("per-run report generation must pass ProviderConfig")

    def build_client_for_config(self, config: ProviderConfig):
        self.configs.append(config)
        return _RawClient(config)


class _FailingFactory:
    def build_client(self):
        raise AssertionError("per-run config should be attempted first")

    def build_client_for_config(self, config: ProviderConfig):
        del config
        raise RuntimeError("provider setup exploded")


class _BuildOnlyFactory:
    def build_client(self):
        raise AssertionError("build_client must not ignore run provider_config")


class _LegacyChatClient:
    model = "rawfish-provider/default"
    json_mode = "loose"
    last_usage = None

    def chat_json(self, messages, temperature=0.3, max_tokens=4096):
        del messages, temperature, max_tokens
        return {"ok": True}

    def chat(
        self,
        messages,
        temperature=0.7,
        max_tokens=4096,
        response_format=None,
    ):
        del messages, temperature, max_tokens, response_format
        return "ok"


class _LegacyDefaultFactory:
    def __init__(self) -> None:
        self.calls = 0
        self.client = _LegacyChatClient()

    def build_client(self):
        self.calls += 1
        return self.client


class _ReportAdapter(FakeUseCase):
    report_schema = None
    report_draft_schema = None
    prepare_manifest_schema = None
    guardrail_capabilities = None
    adapter_runs_guardrails_internally = False

    def __init__(self, uid: str, factory: Any) -> None:
        super().__init__(uid=uid)
        self.llm_provider_factory = factory

    def catalog(self) -> CatalogPayload:
        return catalog_payload(
            use_case_id=self.use_case_id(),
            personas=[
                PersonaSpec(
                    persona_id="p",
                    label="Persona",
                    cohort_question="Who is involved?",
                )
            ],
        )

    def artifact_names(self) -> Dict[str, str]:
        return {"report_json": "rawfish_report.json"}

    def generate_report(
        self,
        scenario: Any,
        run_metadata: Any,
        evidence_catalog: Any,
        llm_client: Any = None,
    ) -> Any:
        del scenario, run_metadata, evidence_catalog
        return {
            "report": "ok",
            "model": getattr(llm_client, "model", "missing"),
        }


def _registry_snapshot() -> dict:
    return dict(platform_registry._REGISTRY)  # noqa: SLF001


def _registry_restore(snap: dict) -> None:
    platform_registry._REGISTRY.clear()  # noqa: SLF001
    platform_registry._REGISTRY.update(snap)  # noqa: SLF001


def _replace_registry_with(adapter: _ReportAdapter) -> None:
    platform_registry._REGISTRY.clear()  # noqa: SLF001
    platform_registry.register(adapter)  # type: ignore[arg-type]


def _redirect_data_dir(monkeypatch, tmp_path: Path) -> None:
    from app.config import Config

    monkeypatch.setattr(Config, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))
    for modname in (
        "app.api.use_cases",
        "app.simulation_platform.state_machine",
    ):
        module = sys.modules.get(modname)
        if module is not None and getattr(module, "Config", Config) is not Config:
            monkeypatch.setattr(
                module.Config,
                "OASIS_SIMULATION_DATA_DIR",
                str(tmp_path),
            )


def test_generic_report_route_uses_run_provider_without_persisting_key(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("RAWFISH_LLM_API_KEY", "runtime-secret")
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    _redirect_data_dir(monkeypatch, tmp_path)
    factory = _CapturingFactory()
    adapter = _ReportAdapter("creator", factory)
    snap = _registry_snapshot()
    try:
        _replace_registry_with(adapter)
        client = app.test_client()
        base = "/api/sim/use-cases/creator"
        create = client.post(
            f"{base}/simulation/create",
            json={
                "scenario": {"persona": "p"},
                "provider": {
                    "mode": "external",
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                },
            },
        )
        assert create.status_code == 201, create.get_data(as_text=True)
        sim_id = create.get_json()["data"]["simulation_id"]
        state_path = tmp_path / "creator" / sim_id / "state.json"
        persisted = json.loads(state_path.read_text(encoding="utf-8"))
        assert persisted["llm_provider"]["provider"] == "openai"
        assert "api_key" not in persisted["llm_provider"]

        assert client.post(f"{base}/simulation/{sim_id}/prepare").status_code == 200
        assert client.post(f"{base}/simulation/{sim_id}/start").status_code == 200
        report = client.post(f"{base}/simulation/{sim_id}/report/generate")

        assert report.status_code == 200, report.get_data(as_text=True)
        assert len(factory.configs) == 1
        assert factory.configs[0].provider == "openai"
        assert factory.configs[0].model == "gpt-4o-mini"
        assert factory.configs[0].api_key == "runtime-secret"
    finally:
        _registry_restore(snap)


def test_create_rejects_inline_external_key_without_persisting_run(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv("RAWFISH_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    _redirect_data_dir(monkeypatch, tmp_path)
    factory = _CapturingFactory()
    adapter = _ReportAdapter("provider_inline_reject_lane", factory)
    snap = _registry_snapshot()
    try:
        platform_registry.register(adapter)  # type: ignore[arg-type]
        client = app.test_client()
        base = "/api/sim/use-cases/provider_inline_reject_lane"
        create = client.post(
            f"{base}/simulation/create",
            json={
                "scenario": {"persona": "p"},
                "provider": {
                    "mode": "external",
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "api_key": "inline-secret",
                },
            },
        )

        assert create.status_code == 400
        body = create.get_json()
        assert body["error"]["details"]["reason"] == "inline_external_secret_unsupported"
        assert not (tmp_path / "provider_inline_reject_lane").exists()
    finally:
        _registry_restore(snap)


def test_manual_report_fails_closed_when_provider_client_setup_fails(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("RAWFISH_LLM_API_KEY", "runtime-secret")
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    _redirect_data_dir(monkeypatch, tmp_path)
    adapter = _ReportAdapter("creator", _FailingFactory())
    snap = _registry_snapshot()
    try:
        _replace_registry_with(adapter)
        client = app.test_client()
        base = "/api/sim/use-cases/creator"
        create = client.post(
            f"{base}/simulation/create",
            json={
                "scenario": {"persona": "p"},
                "provider": {
                    "mode": "external",
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                },
            },
        )
        assert create.status_code == 201, create.get_data(as_text=True)
        sim_id = create.get_json()["data"]["simulation_id"]
        assert client.post(f"{base}/simulation/{sim_id}/prepare").status_code == 200
        assert client.post(f"{base}/simulation/{sim_id}/start").status_code == 200

        report = client.post(f"{base}/simulation/{sim_id}/report/generate")

        assert report.status_code == 502
        body = report.get_json()
        assert body["error"]["details"]["reason"] == "provider_client_build_failed"
        assert not (
            tmp_path / "creator" / sim_id / "rawfish_report.json"
        ).exists()
    finally:
        _registry_restore(snap)


def test_build_llm_client_for_run_fails_closed_for_build_only_factory():
    adapter = type(
        "Adapter",
        (),
        {"llm_provider_factory": _BuildOnlyFactory()},
    )()

    try:
        llm_provider.build_llm_client_for_run(
            adapter=adapter,
            provider_config=ProviderConfig(
                mode="external",
                provider="openai",
                model="gpt-4o-mini",
                api_key="runtime-secret",
            ),
        )
    except RuntimeError as exc:
        assert "run-scoped ProviderConfig" in str(exc)
    else:
        raise AssertionError("build-only provider factory should fail closed")


def test_override_factory_must_declare_run_scoped_provider_support():
    factory = _BuildOnlyFactory()
    llm_provider.set_override_factory(factory)
    try:
        try:
            llm_provider.build_llm_client_for_run(
                provider_config=ProviderConfig(
                    mode="external",
                    provider="openai",
                    model="gpt-4o-mini",
                    api_key="runtime-secret",
                ),
            )
        except RuntimeError as exc:
            assert "run-scoped ProviderConfig" in str(exc)
        else:
            raise AssertionError("build-only override should fail closed")
    finally:
        llm_provider.set_override_factory(None)


def test_auto_report_factory_uses_state_provider_and_ignores_persisted_key(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("RAWFISH_LLM_API_KEY", "runtime-secret")
    factory = _CapturingFactory()
    adapter = _ReportAdapter("provider_watcher_lane", factory)
    snap = _registry_snapshot()
    sim_id = "sim_provider_watcher"
    run_dir = tmp_path / sim_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id,
            "use_case_id": adapter.use_case_id(),
            "status": "completed",
            "scenario": {"persona": "p"},
            "llm_provider": {
                "mode": "external",
                "provider": "anthropic",
                "model": "claude-3-5-haiku-latest",
                "api_key": "persisted-secret-must-be-ignored",
            },
        }),
        encoding="utf-8",
    )
    try:
        platform_registry.register(adapter)  # type: ignore[arg-type]
        outcome = production_generator_factory(
            simulation_id=sim_id,
            run_dir=run_dir,
            use_case_id=adapter.use_case_id(),
        )

        assert outcome.report_written is True
        assert (run_dir / "rawfish_report.json").is_file()
        assert len(factory.configs) == 1
        assert factory.configs[0].provider == "anthropic"
        assert factory.configs[0].model == "claude-3-5-haiku-latest"
        assert factory.configs[0].api_key == "runtime-secret"
    finally:
        _registry_restore(snap)


def test_auto_report_factory_fails_closed_when_provider_not_ready(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delenv("RAWFISH_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    factory = _CapturingFactory()
    adapter = _ReportAdapter("provider_watcher_not_ready_lane", factory)
    snap = _registry_snapshot()
    sim_id = "sim_provider_watcher_not_ready"
    run_dir = tmp_path / sim_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({
            "simulation_id": sim_id,
            "use_case_id": adapter.use_case_id(),
            "status": "completed",
            "scenario": {"persona": "p"},
            "llm_provider": {
                "mode": "external",
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        }),
        encoding="utf-8",
    )
    try:
        platform_registry.register(adapter)  # type: ignore[arg-type]
        outcome = production_generator_factory(
            simulation_id=sim_id,
            run_dir=run_dir,
            use_case_id=adapter.use_case_id(),
        )

        assert outcome.report_written is False
        assert outcome.reason == "llm_provider_not_ready"
        assert factory.configs == []
        assert not (run_dir / "rawfish_report.json").exists()
    finally:
        _registry_restore(snap)
