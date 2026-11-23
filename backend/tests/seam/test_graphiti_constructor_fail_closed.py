"""Constructor must fail-closed for empty/cloud LLM endpoints.

Companion to ``test_graphiti_factory_fail_closed.py``: the factory enforces
the rule one layer up via env-var inspection; this file proves the rule
also holds when a caller bypasses the factory and constructs
:class:`GraphitiAudienceStore` directly. A ``rstrip("/")``-only check
admits ``/v1`` and ``/v1/`` suffixes as bypasses; the constructor +
factory + skip helpers share one hostname-based predicate so they
cannot drift apart.

Also pins the predicate itself (:func:`llm_endpoint_is_openai_cloud`)
against every URL variation: bare hostname, ``/v1``, ``/v1/``,
schemeless, port, local Ollama, corp-proxy.
"""
from __future__ import annotations

import pytest

from app.seam.graphiti_store import (
    GraphitiAudienceStore,
    GraphitiInitError,
    llm_endpoint_is_openai_cloud,
)


# ----------------------------------------------------------------------
# Predicate: refusal targets (helper must return True)
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "",                                       # empty falls through to OpenAI default
        "https://api.openai.com",                 # bare host, scheme'd
        "https://api.openai.com/",                # trailing slash
        "https://api.openai.com/v1",              # /v1 — the original bypass
        "https://api.openai.com/v1/",             # /v1/ trailing slash
        "http://api.openai.com",                  # http (not https)
        "http://api.openai.com/v1",
        "http://api.openai.com:80/v1",            # explicit port
        "https://api.openai.com:443/v1",          # explicit https port
        "api.openai.com/v1",                      # schemeless — urlparse parks in .path
        "API.OPENAI.COM/v1",                      # case-insensitive
    ],
)
def test_llm_endpoint_predicate_refuses_cloud_variants(url: str) -> None:
    """Helper must return True (the "refuse" signal) for every cloud shape."""
    assert llm_endpoint_is_openai_cloud(url) is True


# ----------------------------------------------------------------------
# Predicate: acceptance targets (helper must return False)
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",              # canonical local Ollama
        "http://127.0.0.1:11434/v1",              # IP form of localhost
        "https://localhost:8000/v1",              # local LM Studio https
        "https://my-corp-proxy.example.com/openai/v1",  # corp proxy
        "https://api.openai.org/v1",              # .org is a different host
        "https://openai.com/v1",                  # not api.openai.com
        "http://api.openai.com.example/v1",       # different host with prefix match
    ],
)
def test_llm_endpoint_predicate_accepts_local_first_variants(url: str) -> None:
    """Helper must return False (the "accept" signal) for non-cloud URLs."""
    assert llm_endpoint_is_openai_cloud(url) is False


# ----------------------------------------------------------------------
# Direct constructor: every refusal target must raise GraphitiInitError
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://api.openai.com",
        "https://api.openai.com/v1",
        "https://api.openai.com/v1/",
        "api.openai.com/v1",
    ],
)
def test_constructor_refuses_cloud_endpoint(url: str) -> None:
    """Direct ``GraphitiAudienceStore(...)`` must raise on cloud / empty URL.

    Same refusal predicate as the factory; the constructor enforces it
    independently so a caller that bypasses ``MockZepStorage.get_factory``
    (integration test, custom embedder) cannot silently route prompts to
    the cloud. The error message names ``llm_base_url`` so the offending
    arg is obvious from the traceback.
    """
    with pytest.raises(GraphitiInitError) as excinfo:
        GraphitiAudienceStore(
            "bolt://localhost:7687",
            "neo4j",
            "test-password",
            llm_base_url=url,
            llm_api_key="test-key",
        )
    msg = str(excinfo.value)
    assert "local LLM endpoint" in msg
    assert repr(url) in msg


# ----------------------------------------------------------------------
# Direct constructor: local endpoint must succeed (no Neo4j reach needed)
# ----------------------------------------------------------------------


def test_constructor_accepts_local_endpoint_without_calling_neo4j() -> None:
    """A non-cloud URL constructs successfully without touching Neo4j.

    The constructor is lazy-init: Neo4j is only contacted on the first
    method call (via ``_ensure_client``). Construction with a local URL
    must succeed even when Neo4j is unreachable -- the failure deferral
    is itself part of the contract (gives integration tests a chance to
    set up the container after the store is wired into the factory).
    """
    store = GraphitiAudienceStore(
        "bolt://localhost:7687",
        "neo4j",
        "test-password",
        llm_base_url="http://localhost:11434/v1",
        llm_api_key="test-key",
    )
    # Pinned attributes survive — no client work has happened yet.
    assert store._llm_base_url == "http://localhost:11434/v1"
    assert store._client is None
