from __future__ import annotations


def test_legacy_openai_compatible_client_defaults_local_first(monkeypatch):
    from app.utils import llm_client as llm_client_module

    monkeypatch.delenv("RAWFISH_LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL_NAME", raising=False)
    monkeypatch.setattr(llm_client_module.Config, "LLM_API_KEY", None)
    monkeypatch.setattr(
        llm_client_module.Config,
        "LLM_BASE_URL",
        "http://localhost:11434/v1",
    )
    monkeypatch.setattr(llm_client_module.Config, "RAWFISH_LLM_MODEL", "qwen3-coder:30b")
    monkeypatch.setattr(llm_client_module.Config, "LLM_MODEL_NAME", "qwen3-coder:30b")

    client = llm_client_module.LLMClient()

    assert client.api_key is None
    assert client.base_url == "http://localhost:11434/v1"
    assert client.model == "qwen3-coder:30b"
