from __future__ import annotations

import pytest

from app.simulation_platform.llm.provider import (
    RunScopedProviderConfigRequired,
    build_llm_client_for_run,
)
from app.simulation_platform.llm.router import ProviderConfig


class _BuildOnlyFactory:
    def __init__(self) -> None:
        self.built = False

    def build_client(self):
        self.built = True
        return object()


class _RunScopedFactory(_BuildOnlyFactory):
    def __init__(self) -> None:
        super().__init__()
        self.configs = []

    def build_client_for_config(self, config: ProviderConfig):
        self.configs.append(config)
        return object()


def test_lane_owned_factory_must_accept_run_scoped_provider_config():
    factory = _BuildOnlyFactory()

    class _Adapter:
        llm_provider_factory = factory

    with pytest.raises(RunScopedProviderConfigRequired):
        build_llm_client_for_run(
            adapter=_Adapter(),
            provider_config=ProviderConfig(
                mode="external",
                provider="openai",
                model="gpt-4o-mini",
                api_key="runtime-secret",
            ),
        )
    assert factory.built is False


def test_lane_owned_run_scoped_factory_receives_provider_config():
    factory = _RunScopedFactory()

    class _Adapter:
        llm_provider_factory = factory

    client = build_llm_client_for_run(
        adapter=_Adapter(),
        provider_config=ProviderConfig(
            mode="external",
            provider="openai",
            model="gpt-4o-mini",
            api_key="runtime-secret",
        ),
    )

    assert client is not None
    assert len(factory.configs) == 1
    assert factory.configs[0].provider == "openai"
    assert factory.built is False


def test_non_lane_owned_build_only_factory_fails_closed_for_run_config(monkeypatch):
    from app.simulation_platform.llm import provider as provider_mod

    factory = _BuildOnlyFactory()
    monkeypatch.setattr(
        provider_mod,
        "get_llm_provider_factory",
        lambda adapter=None: factory,
    )

    with pytest.raises(RunScopedProviderConfigRequired):
        build_llm_client_for_run(
            adapter=object(),
            provider_config=ProviderConfig(
                mode="local_ollama",
                model="qwen3-coder:30b",
                base_url="http://localhost:11434/v1",
            ),
        )

    assert factory.built is False
