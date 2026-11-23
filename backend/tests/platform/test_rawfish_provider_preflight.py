from __future__ import annotations

import importlib
import json
import sys
from types import SimpleNamespace

import pytest

from app.simulation_platform.catalog import (
    CatalogPayload,
    PersonaSpec,
    build_minimal_lane_manifest,
)
from app.simulation_platform.llm.router import (
    ProviderConfig,
    RawFishLiteLLMClient,
    check_provider_readiness,
    estimate_external_cost,
    provider_budget_state,
    provider_config_from_payload,
    provider_config_from_state,
)
from tests._helpers import make_app


_COST_OVERRIDE_ENVS = (
    "RAWFISH_LLM_MODEL_COST_OVERRIDES_PATH",
    "RAWFISH_MODEL_COST_OVERRIDES_PATH",
    "LLM_MODEL_COST_OVERRIDES_PATH",
)


def _clear_cost_override_env(monkeypatch) -> None:
    for name in _COST_OVERRIDE_ENVS:
        monkeypatch.delenv(name, raising=False)


def test_catalog_payload_requires_matching_lane_manifest():
    payload = CatalogPayload(
        use_case_id="creator",
        lane_manifest=build_minimal_lane_manifest(
            lane_id="creator",
            label="Creator",
            summary="Creator lane",
        ),
        personas=[
            PersonaSpec(
                persona_id="default",
                label="Default",
                cohort_question="Who reacts?",
            )
        ],
    )

    assert payload.lane_manifest.lane_id == "creator"
    assert payload.personas[0].use_case_id == "creator"


def test_local_provider_readiness_does_not_require_external_key():
    config = provider_config_from_payload({
        "mode": "local_ollama",
        "model": "qwen3-coder:30b",
        "base_url": "http://localhost:11434/v1",
    })

    readiness = check_provider_readiness(config, probe=False)

    assert readiness.ready is True
    assert readiness.status == "configured_unprobed"
    assert readiness.configured is True
    assert readiness.probed is False
    assert readiness.latency_ms is None
    assert "missing_external_key" not in readiness.status


def test_probe_failure_marks_actual_probe_and_latency(monkeypatch):
    from app.simulation_platform.llm import router

    class _FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            del kwargs
            raise RuntimeError("local endpoint refused connection")

    monkeypatch.setattr(router, "_import_litellm", lambda: (_FakeLiteLLM, None))
    config = ProviderConfig(
        mode="local_ollama",
        model="qwen3-coder:30b",
        base_url="http://localhost:11434/v1",
    )

    readiness = check_provider_readiness(config, probe=True)

    assert readiness.ready is False
    assert readiness.configured is True
    assert readiness.probed is True
    assert readiness.status == "failed"
    assert readiness.latency_ms is not None


def test_local_ollama_litellm_calls_use_ollama_root_base_url(monkeypatch):
    from app.simulation_platform.llm import router

    calls = []

    class _FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"ok": true}'),
                    ),
                ],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
            )

    monkeypatch.setattr(router, "_import_litellm", lambda: (_FakeLiteLLM, None))
    client = RawFishLiteLLMClient(ProviderConfig(
        mode="local_ollama",
        model="qwen3-coder:30b",
        base_url="http://localhost:11434/v1",
    ))

    assert client.chat_json([{"role": "user", "content": "ping"}]) == {"ok": True}
    assert calls[-1]["api_base"] == "http://localhost:11434"


def test_local_openai_compatible_litellm_calls_keep_v1_base_url(monkeypatch):
    from app.simulation_platform.llm import router

    calls = []

    class _FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"ok": true}'),
                    ),
                ],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
            )

    monkeypatch.setattr(router, "_import_litellm", lambda: (_FakeLiteLLM, None))
    client = RawFishLiteLLMClient(ProviderConfig(
        mode="local_openai_compatible",
        model="local-model",
        base_url="http://localhost:1234/v1",
    ))

    assert client.chat_json([{"role": "user", "content": "ping"}]) == {"ok": True}
    assert calls[-1]["api_base"] == "http://localhost:1234/v1"


def test_semantic_failure_does_not_claim_probe_even_when_requested():
    readiness = check_provider_readiness(
        ProviderConfig(mode="external", provider="openai", model=""),
        probe=True,
    )

    assert readiness.ready is False
    assert readiness.configured is False
    assert readiness.probed is False
    assert readiness.status == "missing_model"


def test_external_provider_without_key_is_not_ready(monkeypatch):
    monkeypatch.delenv("RAWFISH_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    config = provider_config_from_payload({
        "mode": "external",
        "provider": "openai",
        "model": "gpt-4o-mini",
    })

    readiness = check_provider_readiness(config, probe=False)

    assert readiness.ready is False
    assert readiness.status == "missing_external_key"
    assert readiness.configured is False
    assert readiness.probed is False


def test_external_provider_without_provider_id_is_not_ready(monkeypatch):
    monkeypatch.setenv("RAWFISH_LLM_API_KEY", "runtime-secret")
    monkeypatch.delenv("RAWFISH_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    config = provider_config_from_payload({
        "mode": "external",
        "model": "gpt-4o-mini",
    })

    readiness = check_provider_readiness(config, probe=False)

    assert readiness.ready is False
    assert readiness.status == "missing_external_provider"
    assert readiness.configured is False


def test_external_provider_without_model_is_not_ready(monkeypatch):
    monkeypatch.setenv("RAWFISH_LLM_API_KEY", "runtime-secret")
    monkeypatch.setenv("RAWFISH_LLM_PROVIDER", "openai")
    monkeypatch.delenv("RAWFISH_LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
    config = provider_config_from_payload({
        "mode": "external",
        "provider": "openai",
    })

    readiness = check_provider_readiness(config, probe=False)

    assert config.model == ""
    assert readiness.ready is False
    assert readiness.status == "missing_model"
    assert readiness.configured is False


def test_external_provider_does_not_inherit_local_ollama_base_url(monkeypatch):
    monkeypatch.setenv("RAWFISH_LLM_API_KEY", "runtime-secret")
    monkeypatch.delenv("RAWFISH_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    config = provider_config_from_payload({
        "mode": "external",
        "provider": "openai",
        "model": "gpt-4o-mini",
    })

    assert config.base_url is None


def test_preflight_rejects_inline_external_key(monkeypatch):
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    client = app.test_client()

    response = client.post(
        "/api/sim/use-cases/creator/evals/preflight_meter",
        json={
            "provider": {
                "mode": "external",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key": "inline-secret",
            },
        },
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["error"]["details"]["reason"] == "inline_external_secret_unsupported"


def test_preflight_rejects_blank_inline_api_key_field(monkeypatch):
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    client = app.test_client()

    response = client.post(
        "/api/sim/use-cases/creator/evals/preflight_meter",
        json={
            "provider": {
                "mode": "external",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "api_key": "",
            },
        },
    )

    assert response.status_code == 400
    body = response.get_json()
    assert body["error"]["details"]["reason"] == "inline_external_secret_unsupported"


def test_rawfish_litellm_client_exposes_report_token_budget(monkeypatch):
    monkeypatch.setenv("POLICY_MAX_TOKENS_REPORT", "7777")

    client = RawFishLiteLLMClient(ProviderConfig(
        mode="local_ollama",
        model="qwen3-coder:30b",
        base_url="http://localhost:11434/v1",
    ))

    assert client.policy_max_tokens_report == 7777


def test_local_ollama_model_with_slashes_is_still_prefixed():
    client = RawFishLiteLLMClient(ProviderConfig(
        mode="local_ollama",
        model="hf.co/some-org/some-model",
        base_url="http://localhost:11434/v1",
    ))

    assert client.model == "ollama_chat/hf.co/some-org/some-model"


def test_provider_public_dict_excludes_keys_and_state_uses_runtime_secret(
    monkeypatch,
):
    monkeypatch.setenv("RAWFISH_LLM_API_KEY", "runtime-secret")
    config = provider_config_from_payload({
        "mode": "external",
        "provider": "openai",
        "model": "gpt-4o-mini",
    })

    assert config.api_key == "runtime-secret"
    assert "api_key" not in config.public_dict()

    state_config = provider_config_from_state({
        "mode": "external",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_key": "persisted-secret-must-be-ignored",
    })

    assert state_config.api_key == "runtime-secret"
    assert state_config.provider == "openai"
    assert state_config.model == "gpt-4o-mini"


def test_provider_payload_rejects_inline_external_key_before_readiness(
    monkeypatch,
):
    monkeypatch.delenv("RAWFISH_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(ValueError, match="api_key is not accepted"):
        provider_config_from_payload({
            "mode": "external",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "inline-secret-must-not-be-used",
        })


def test_provider_payload_rejects_blank_inline_api_key_field(monkeypatch):
    monkeypatch.delenv("RAWFISH_LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    with pytest.raises(ValueError, match="api_key is not accepted"):
        provider_config_from_payload({
            "mode": "external",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "",
        })


def test_preflight_payload_marks_configured_without_probe(monkeypatch):
    app = make_app(
        monkeypatch,
        AUTH_ENABLED="false",
        RAWFISH_LLM_API_KEY="runtime-secret",
    )
    client = app.test_client()

    response = client.post(
        "/api/sim/use-cases/creator/evals/preflight_meter",
        json={
            "provider": {
                "mode": "external",
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
            "probe_provider": False,
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    readiness = response.get_json()["data"]["provider_readiness"]
    assert readiness["ready"] is True
    assert readiness["configured"] is True
    assert readiness["probed"] is False
    assert readiness["status"] == "configured_unprobed"
    assert readiness["latency_ms"] is None


def test_unknown_external_model_cost_is_unavailable_not_faked(monkeypatch):
    from app.simulation_platform.llm import router

    _clear_cost_override_env(monkeypatch)
    fake_litellm = SimpleNamespace(model_cost={})
    monkeypatch.setattr(router, "_import_litellm", lambda: (fake_litellm, None))
    config = ProviderConfig(
        mode="external",
        provider="openai",
        model="unknown-model-for-cost-test",
        api_key="runtime-secret",
    )

    cost = estimate_external_cost(config, token_estimate=12345)

    assert cost == {
        "status": "cost_unavailable",
        "currency": "USD",
        "amount": None,
        "model": "openai/unknown-model-for-cost-test",
    }


def test_external_model_cost_estimate_uses_stable_schema(monkeypatch):
    from app.simulation_platform.llm import router

    _clear_cost_override_env(monkeypatch)
    fake_litellm = SimpleNamespace(model_cost={
        "openai/gpt-cost-test": {
            "input_cost_per_token": 0.000001,
            "output_cost_per_token": 0.000003,
        }
    })
    monkeypatch.setattr(router, "_import_litellm", lambda: (fake_litellm, None))
    config = ProviderConfig(
        mode="external",
        provider="openai",
        model="gpt-cost-test",
        api_key="runtime-secret",
    )

    cost = estimate_external_cost(config, token_estimate=1000)

    assert cost == {
        "status": "estimated",
        "currency": "USD",
        "model": "openai/gpt-cost-test",
        "amount": 0.002,
    }


def test_external_model_cost_estimate_uses_env_override_catalog(
    monkeypatch,
    tmp_path,
):
    from app.simulation_platform.llm import router

    _clear_cost_override_env(monkeypatch)
    fake_litellm = SimpleNamespace(model_cost={})
    monkeypatch.setattr(router, "_import_litellm", lambda: (fake_litellm, None))
    catalog_path = tmp_path / "model_cost_overrides.json"
    catalog_path.write_text(
        json.dumps({
            "models": {
                "openai/gpt-override-test": {
                    "input_cost_per_token": 0.001,
                    "output_cost_per_token": 0.003,
                },
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "RAWFISH_LLM_MODEL_COST_OVERRIDES_PATH",
        str(catalog_path),
    )
    config = ProviderConfig(
        mode="external",
        provider="openai",
        model="gpt-override-test",
        api_key="runtime-secret",
    )

    cost = estimate_external_cost(config, token_estimate=1000)

    assert cost == {
        "status": "estimated",
        "currency": "USD",
        "model": "openai/gpt-override-test",
        "amount": 2.0,
    }


def test_external_model_cost_override_unknown_model_remains_unavailable(
    monkeypatch,
    tmp_path,
):
    from app.simulation_platform.llm import router

    _clear_cost_override_env(monkeypatch)
    fake_litellm = SimpleNamespace(model_cost={})
    monkeypatch.setattr(router, "_import_litellm", lambda: (fake_litellm, None))
    catalog_path = tmp_path / "model_cost_overrides.json"
    catalog_path.write_text(
        json.dumps({
            "models": {
                "openai/some-known-model": {
                    "input_cost_per_token": 0.001,
                    "output_cost_per_token": 0.003,
                },
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "RAWFISH_LLM_MODEL_COST_OVERRIDES_PATH",
        str(catalog_path),
    )
    config = ProviderConfig(
        mode="external",
        provider="openai",
        model="not-in-override-catalog",
        api_key="runtime-secret",
    )

    cost = estimate_external_cost(config, token_estimate=1000)

    assert cost == {
        "status": "cost_unavailable",
        "currency": "USD",
        "amount": None,
        "model": "openai/not-in-override-catalog",
    }


def test_external_model_cost_override_bad_row_is_unavailable(
    monkeypatch,
    tmp_path,
):
    from app.simulation_platform.llm import router

    _clear_cost_override_env(monkeypatch)
    fake_litellm = SimpleNamespace(model_cost={
        "openai/gpt-bad-override-test": {
            "input_cost_per_token": 0.001,
            "output_cost_per_token": 0.003,
        }
    })
    monkeypatch.setattr(router, "_import_litellm", lambda: (fake_litellm, None))
    catalog_path = tmp_path / "model_cost_overrides.json"
    catalog_path.write_text(
        json.dumps({
            "models": {
                "openai/gpt-bad-override-test": {
                    "input_cost_per_token": "not-a-number",
                },
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "RAWFISH_LLM_MODEL_COST_OVERRIDES_PATH",
        str(catalog_path),
    )
    config = ProviderConfig(
        mode="external",
        provider="openai",
        model="gpt-bad-override-test",
        api_key="runtime-secret",
    )

    cost = estimate_external_cost(config, token_estimate=1000)

    assert cost is not None
    assert cost["status"] == "cost_unavailable"
    assert cost["currency"] == "USD"
    assert cost["amount"] is None
    assert cost["model"] == "openai/gpt-bad-override-test"
    assert "input_cost_per_token must be numeric" in cost["reason"]


def test_provider_budget_state_flags_warn_at_threshold():
    budget = provider_budget_state(
        {
            "status": "estimated",
            "currency": "USD",
            "amount": 1.25,
            "model": "openai/gpt-4o-mini",
        },
        warn_at=1.00,
    )

    assert budget.status == "warn_at_exceeded"
    assert budget.warning is True
    assert budget.amount == 1.25
    assert budget.warn_at == 1.00


def test_config_validate_reuses_provider_readiness_for_external_shape(
    monkeypatch,
):
    monkeypatch.setenv("FLASK_DEBUG", "true")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("SECRET_KEY", "test-secret-32chars-test-secret-32")
    monkeypatch.setenv("RAWFISH_LLM_PROVIDER_MODE", "external")
    monkeypatch.setenv("RAWFISH_LLM_API_KEY", "runtime-secret")
    monkeypatch.delenv("RAWFISH_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("RAWFISH_LLM_MODEL", "gpt-4o-mini")

    if "app.config" in sys.modules:
        importlib.reload(sys.modules["app.config"])
    from app.config import Config

    errors = Config.validate()

    assert any("external provider mode requires an explicit provider id" in e for e in errors)


def test_config_validate_reuses_provider_readiness_for_local_base_url(
    monkeypatch,
):
    monkeypatch.setenv("FLASK_DEBUG", "true")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("SECRET_KEY", "test-secret-32chars-test-secret-32")
    monkeypatch.setenv("RAWFISH_LLM_PROVIDER_MODE", "local_openai_compatible")
    monkeypatch.setenv("RAWFISH_LLM_MODEL", "local-model")
    monkeypatch.delenv("RAWFISH_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    if "app.config" in sys.modules:
        importlib.reload(sys.modules["app.config"])
    from app.config import Config

    errors = Config.validate()

    assert any("local provider modes require a base_url" in e for e in errors)
