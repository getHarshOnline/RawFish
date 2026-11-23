"""Factory must fail-closed when the LLM endpoint is missing / OpenAI Cloud.

Pins the local-first hardening of :meth:`MockZepStorage.get_factory` in
``ZEP_MODE=graphiti``: the factory now refuses to construct a
:class:`GraphitiAudienceStore` if ``OPENAI_BASE_URL`` is unset or points
at the OpenAI public endpoint. The historical behaviour silently
defaulted to ``https://api.openai.com/v1`` -- entity extraction would
then leak every prompt to OpenAI Cloud despite the user believing they
were running a local-only ``ZEP_MODE=graphiti`` boot. The audit packet
flags this as the same class of issue as a hard-coded credential; the
factory now
raises :class:`GraphitiInitError` with a remediation message instead of
silently routing cloud-ward.

These tests stay hermetic: they exercise only the factory dispatch +
the env-var check, never touch Neo4j, and patch ``os.environ`` via
``monkeypatch`` so they leave no residue.
"""
from __future__ import annotations

import pytest

from app.seam.graphiti_store import GraphitiInitError
from app.seam.zep_mock import MockZepStorage


# All cases below set ZEP_MODE=graphiti + a non-empty NEO4J_URI so the
# factory passes the pre-LLM checks and reaches the OPENAI_BASE_URL gate;
# the test isolates that gate's behaviour from the surrounding ladder.
_NEO4J_URI = "bolt://localhost:7687"


def _arm_graphiti_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set the env vars the factory needs *before* the LLM check fires."""
    monkeypatch.setenv("ZEP_MODE", "graphiti")
    monkeypatch.setenv("NEO4J_URI", _NEO4J_URI)
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-only-not-a-real-secret")


def test_factory_fails_closed_when_openai_base_url_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset ``OPENAI_BASE_URL`` must raise ``GraphitiInitError``."""
    _arm_graphiti_env(monkeypatch)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

    with pytest.raises(GraphitiInitError) as excinfo:
        MockZepStorage.get_factory()

    # The remediation string must name OPENAI_BASE_URL so an operator
    # reading the traceback knows exactly which env var to set; we also
    # pin the "local LLM endpoint" phrasing so the message stays
    # actionable across future refactors.
    msg = str(excinfo.value)
    assert "OPENAI_BASE_URL" in msg
    assert "local LLM endpoint" in msg


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.openai.com",
        "https://api.openai.com/v1",
        "https://api.openai.com/v1/",  # trailing slash variant
    ],
)
def test_factory_fails_closed_for_openai_cloud_endpoint(
    monkeypatch: pytest.MonkeyPatch, endpoint: str
) -> None:
    """Explicit OpenAI Cloud URL must also raise -- not silently accepted."""
    _arm_graphiti_env(monkeypatch)
    monkeypatch.setenv("OPENAI_BASE_URL", endpoint)

    with pytest.raises(GraphitiInitError) as excinfo:
        MockZepStorage.get_factory()

    assert "OPENAI_BASE_URL" in str(excinfo.value)


def test_factory_accepts_local_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-empty, non-OpenAI-Cloud endpoint must pass the gate.

    We can't fully construct a :class:`GraphitiAudienceStore` here
    (no live Neo4j, no graphiti-core in the mock test env), but the
    store is lazy-init: ``__init__`` only stashes connection params
    and returns. Reaching the return statement proves the LLM gate
    did not fire on a legitimate local endpoint.
    """
    _arm_graphiti_env(monkeypatch)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "ollama")  # any non-empty string

    # If the LLM gate misfires this raises; if it passes, the lazy
    # GraphitiAudienceStore.__init__ stashes params without touching
    # Neo4j or graphiti-core.
    store = MockZepStorage.get_factory()
    assert store is not None
