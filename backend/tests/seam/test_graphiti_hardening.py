"""Acceptance tests for Graphiti seam hardening:

  * ``add_episode`` ALWAYS passes a non-None ``reference_time`` kwarg
    (Graphiti's ``add_episode`` requires it; omitting the kwarg when
    ``episode.reference_time is None`` lets some upstream versions
    fail before any memory lands).
  * ``_sanitize_uri`` strips userinfo (``user:password@``) from
    URIs included in error messages while preserving credential-free
    URIs verbatim (so operator-facing error strings stay
    troubleshootable without leaking secrets).
  * ``GraphitiInitError`` raised by the seam's lazy client init only
    references the sanitised URI + the exception class name, never
    the raw driver exception text that may quote credentials.
  * Missing ``NEO4J_URI`` -> 503 ``ZEP_GRAPHITI_UNAVAILABLE`` with
    ``details.reason='neo4j_uri_missing'`` (not a bare 500
    ``INTERNAL_ERROR``).
  * ``ZEP_MODE=experimental_cloud`` emits a ``DeprecationWarning``
    per /prepare call surfacing the lifecycle (deprecated, scheduled
    for removal, no further implementation planned, migrate to
    ``ZEP_MODE=graphiti``).
"""
from __future__ import annotations

import warnings
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

from app.seam.graphiti_store import _sanitize_uri
from tests._helpers import make_app


def _minimal_internal_policy_scenario() -> dict:
    return {
        "persona": "policy",
        "scope": "internal_org",
        "title": "Graphiti seam hardening",
        "summary": "Validate Graphiti prepare failures through the generic API.",
        "seed_text": "An internal process change is being tested.",
        "stress_question": "Which stakeholder concerns surface first?",
        "stakeholders": [
            {
                "segment_id": "employees",
                "name": "Employees",
                "type": "employee_cohort",
                "interests": ["clarity"],
                "incentives": ["avoid unnecessary risk"],
                "constraints": ["limited time"],
                "likely_support_reasons": ["clear benefit"],
                "likely_opposition_reasons": ["uncertain rollout"],
                "information_channels": ["internal update"],
                "influence_level": 3,
                "vulnerability_level": 3,
                "compliance_propensity": 3,
                "trust_level": 3,
                "mobilization_capacity": 2,
                "what_changes_their_mind": ["credible evidence"],
            }
        ],
        "assumptions": ["The workflow change is visible."],
        "success_criteria": ["Surface adoption and legitimacy risk."],
        "failure_modes_to_watch": ["low trust"],
        "run": {"ensemble_n": 1, "run_seed": 42},
        "drift_horizon_rounds": 1,
    }


def _make_app_with_tmp_data(monkeypatch, tmp_path):
    return make_app(
        monkeypatch,
        AUTH_ENABLED="false",
        OASIS_SIMULATION_DATA_DIR=str(tmp_path),
    )


def _create_internal_policy_sim(client, scenario: dict) -> str:
    response = client.post(
        "/api/sim/use-cases/internal_policy/simulation/create",
        json={"scenario": scenario},
    )
    assert response.status_code == 201, response.get_data(as_text=True)
    return response.get_json()["data"]["simulation_id"]


# ----------------------------------------------------------------------
# 1. add_episode always passes a non-None reference_time
# ----------------------------------------------------------------------


class _FakeGraphitiClient:
    """Minimal stand-in for the upstream ``graphiti_core.Graphiti``
    client; records the kwargs passed to ``add_episode`` for assertion."""

    def __init__(self) -> None:
        self.add_episode_calls: List[Dict[str, Any]] = []
        self.build_indices_called: bool = False

    async def build_indices_and_constraints(self) -> None:
        self.build_indices_called = True

    async def add_episode(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.add_episode_calls.append(kwargs)


def _store_with_fake_client(monkeypatch) -> tuple:
    import app.seam.graphiti_store as gs_module
    from app.seam.graphiti_store import GraphitiAudienceStore

    store = GraphitiAudienceStore(
        "bolt://localhost:7687",
        "neo4j",
        "test-password",
        llm_base_url="http://localhost:11434/v1",
        llm_api_key="test-key",
    )
    fake = _FakeGraphitiClient()
    # Short-circuit the lazy client init: pre-populate ``_client`` and
    # flip the schema-built flag so ``_ensure_client`` does no I/O.
    store._client = fake  # type: ignore[assignment]
    store._schema_built = True  # type: ignore[assignment]
    # ``_episode_type_for`` imports ``graphiti_core.nodes.EpisodeType``
    # which is not installed in this env. Replace with a stand-in so
    # the add_episode kwargs branch under test is reachable without
    # the live library.
    monkeypatch.setattr(
        gs_module, "_episode_type_for", lambda _src: "TEXT",
    )
    return store, fake


def test_add_episode_passes_default_reference_time_when_none(monkeypatch):
    """``Episode.reference_time=None`` MUST NOT result in the kwarg
    being omitted: upstream Graphiti requires ``reference_time``, and
    the previous behaviour let some versions raise before any memory
    landed. The seam now defaults to ``datetime.now(timezone.utc)``."""
    from app.seam.protocols import Episode

    store, fake = _store_with_fake_client(monkeypatch)
    ep = Episode(
        name="ep-1",
        content="body",
        source="text",
        source_description="prepare:seed_text",
        reference_time=None,
    )
    before = datetime.now(timezone.utc)
    store.add_episode("sim_test", ep)
    after = datetime.now(timezone.utc)

    assert len(fake.add_episode_calls) == 1
    kwargs = fake.add_episode_calls[0]
    assert "reference_time" in kwargs, kwargs
    rt = kwargs["reference_time"]
    assert isinstance(rt, datetime)
    # Sanity: the defaulted timestamp is within the call window.
    assert before <= rt <= after


def test_add_episode_preserves_caller_supplied_reference_time(monkeypatch):
    """When the caller supplies ``reference_time`` it MUST be passed
    through verbatim (ISO-8601 ``Z`` suffix normalised to ``+00:00``)."""
    from app.seam.protocols import Episode

    store, fake = _store_with_fake_client(monkeypatch)
    ep = Episode(
        name="ep-2",
        content="body",
        source="text",
        source_description="prepare:seed_text",
        reference_time="2026-06-28T12:00:00Z",
    )
    store.add_episode("sim_test", ep)
    kwargs = fake.add_episode_calls[0]
    assert kwargs["reference_time"] == datetime(
        2026, 6, 28, 12, 0, 0, tzinfo=timezone.utc,
    )


# ----------------------------------------------------------------------
# 2. _sanitize_uri credential hygiene
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Credential-bearing URIs: userinfo stripped, host:port preserved.
        ("bolt://neo4j:secret@localhost:7687",  "bolt://localhost:7687"),
        ("bolt://user@host.example:7687/db",     "bolt://host.example:7687/db"),
        # Query-string secrets: stripped along with fragment.
        ("https://corp-proxy.example/v1?api_key=SECRET123", "https://corp-proxy.example/v1"),
        ("https://corp-proxy.example/v1?token=ABCDEF#frag",  "https://corp-proxy.example/v1"),
        # Credential-free URIs without query: pass through verbatim.
        ("bolt://localhost:7687",                "bolt://localhost:7687"),
        ("http://localhost:11434/v1",            "http://localhost:11434/v1"),
        # Schemeless or unparseable inputs: pass through verbatim
        # (no userinfo possible without an authority).
        ("api.openai.com/v1",                    "api.openai.com/v1"),
        ("",                                     ""),
    ],
)
def test_sanitize_uri_strips_userinfo_and_query_secrets_but_preserves_clean_uris(
    raw: str, expected: str,
):
    assert _sanitize_uri(raw) == expected


def test_sanitize_uri_query_secret_never_appears_in_constructor_error(monkeypatch):
    """A constructor-level GraphitiInitError on a llm_base_url carrying a
    ``?api_key=SECRET`` query string MUST scrub the secret out of the
    error message."""
    from app.seam.graphiti_store import (
        GraphitiAudienceStore,
        GraphitiInitError,
    )

    # Use a non-OpenAI host so the constructor's cloud refusal does
    # not fire first; force the schema-init step to raise instead so
    # the sanitised URI flows through the second error message.
    store = GraphitiAudienceStore(
        "bolt://neo4j:supersecret@host.example:7687",
        "neo4j",
        "supersecret",
        llm_base_url="https://corp-proxy.example/v1?api_key=SECRET-DO-NOT-LEAK",
        llm_api_key="test-key",
    )
    import app.seam.graphiti_store as gs_module
    def _boom(*_a, **_kw):
        raise ImportError("graphiti_core not installed for this test")
    monkeypatch.setattr(gs_module, "_import_graphiti_symbol", _boom)

    with pytest.raises(GraphitiInitError) as excinfo:
        store._ensure_client()
    msg = str(excinfo.value)
    # The Neo4j URI sanitisation removes the userinfo; the llm_base_url
    # is not quoted by the ensure_client path, only by the constructor's
    # cloud-refusal branch (which did not fire here). Verify that
    # neither secret appears, regardless of which error path quoted it.
    assert "supersecret" not in msg
    assert "SECRET-DO-NOT-LEAK" not in msg


def test_graphiti_init_error_message_does_not_leak_password(monkeypatch):
    """A constructor-level GraphitiInitError raised by ``_ensure_client``
    MUST quote only the sanitised NEO4J_URI; the raw URI containing
    credentials MUST NOT appear in the error message."""
    from app.seam.graphiti_store import (
        GraphitiAudienceStore,
        GraphitiInitError,
    )

    store = GraphitiAudienceStore(
        "bolt://neo4j:supersecret@host.example:7687",
        "neo4j",
        "supersecret",
        llm_base_url="http://localhost:11434/v1",
        llm_api_key="test-key",
    )

    # Force _ensure_client to fail by injecting a raising symbol-importer.
    import app.seam.graphiti_store as gs_module
    def _boom(*_a, **_kw):
        raise ImportError("graphiti_core not installed for this test")
    monkeypatch.setattr(gs_module, "_import_graphiti_symbol", _boom)

    with pytest.raises(GraphitiInitError) as excinfo:
        store._ensure_client()
    msg = str(excinfo.value)
    assert "supersecret" not in msg
    assert "bolt://host.example:7687" in msg
    # The exception class name is preserved (not the raw exception text
    # that might quote the URI back at us via the driver).
    assert "ImportError" in msg


# ----------------------------------------------------------------------
# 3. Missing NEO4J_URI -> 503 with details.reason='neo4j_uri_missing'
# ----------------------------------------------------------------------


def test_prepare_missing_neo4j_uri_returns_503_with_reason_discriminator(
    monkeypatch, tmp_path,
):
    """``ZEP_MODE=graphiti`` with no ``NEO4J_URI`` MUST return 503
    ``ZEP_GRAPHITI_UNAVAILABLE`` carrying
    ``details.reason='neo4j_uri_missing'``. The previous behaviour
    escaped as 500 ``INTERNAL_ERROR`` because the prepare handler
    only caught ``GraphitiInitError`` (not ``RuntimeError`` from
    the factory)."""
    monkeypatch.setenv("ZEP_MODE", "graphiti")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.delenv("NEO4J_URI", raising=False)
    app = _make_app_with_tmp_data(monkeypatch, tmp_path)
    client = app.test_client()
    sim_id = _create_internal_policy_sim(client, _minimal_internal_policy_scenario())
    r = client.post(
        f"/api/sim/use-cases/internal_policy/simulation/{sim_id}/prepare",
        json={},
    )
    assert r.status_code == 503, r.get_json()
    body = r.get_json()
    assert body["error"]["code"] == "ZEP_GRAPHITI_UNAVAILABLE"
    assert body["error"]["details"]["reason"] == "neo4j_uri_missing"


# ----------------------------------------------------------------------
# 4. experimental_cloud emits DeprecationWarning per /prepare call
# ----------------------------------------------------------------------


def test_experimental_cloud_emits_deprecation_warning(monkeypatch, tmp_path):
    """``ZEP_MODE=experimental_cloud`` -> DeprecationWarning whose text
    surfaces the lifecycle (deprecated, scheduled for removal, no
    further implementation, migrate to ZEP_MODE=graphiti)."""
    monkeypatch.setenv("ZEP_MODE", "experimental_cloud")
    monkeypatch.setenv("ZEP_API_KEY", "test-key")
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        app = _make_app_with_tmp_data(monkeypatch, tmp_path)
        client = app.test_client()
        sim_id = _create_internal_policy_sim(
            client,
            _minimal_internal_policy_scenario(),
        )
        client.post(
            f"/api/sim/use-cases/internal_policy/simulation/{sim_id}/prepare",
            json={},
        )
    deprecations = [
        w for w in captured if issubclass(w.category, DeprecationWarning)
    ]
    matching = [
        w for w in deprecations
        if "experimental_cloud is deprecated" in str(w.message)
        and "REMOVED" in str(w.message)
        and "ZEP_MODE=graphiti" in str(w.message)
    ]
    assert matching, (
        "expected DeprecationWarning naming the lifecycle; got: "
        f"{[str(w.message) for w in deprecations]!r}"
    )
