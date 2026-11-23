"""GraphitiAudienceStore integration smoke.

Single integration round-trip: ``add_episode`` then ``search`` against
the local Neo4j Community service from ``backend/docker-compose.yml``.
This is the ONE acceptable skip in the test suite -- when ``NEO4J_URI``
is not set we cannot reach Neo4j, so the gate's contract is "skip with
a clear reason string" (never a silent ``xfail``/``skip``).

Run locally:
    cd backend && docker compose up -d neo4j-community
    export NEO4J_URI=bolt://localhost:7687
    export NEO4J_USER=neo4j
    export NEO4J_PASSWORD=rawfish-local-dev
    export OPENAI_BASE_URL=http://localhost:11434/v1   # Ollama-style local
    export OPENAI_API_KEY=ollama
    uv run pytest tests/seam/test_graphiti_store.py -q
"""
from __future__ import annotations

import os

import pytest


# Skip-reason string is intentionally descriptive so a CI surface reads it
# as "container not running" rather than "test author was lazy". This is
# the only sanctioned skip in the seam suite. OPENAI_BASE_URL is also
# required so the test never silently routes prompts to api.openai.com --
# the local-first posture demands an explicit local endpoint.
_SKIP_REASON = (
    "NEO4J_URI and OPENAI_BASE_URL both required; GraphitiAudienceStore "
    "integration test requires a running Neo4j Community container (start "
    "it with `docker compose up -d neo4j-community` from backend/) and an "
    "explicit local LLM endpoint (e.g. OPENAI_BASE_URL=http://localhost:11434/v1 "
    "against a local Ollama). Set NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD "
    "/ OPENAI_BASE_URL / OPENAI_API_KEY before running."
)


def _local_first_env_ready() -> bool:
    """Both Neo4j coordinates AND an explicit non-cloud LLM endpoint must
    be present. Delegates to ``graphiti_store.llm_endpoint_is_openai_cloud``
    so the skip predicate cannot drift from the constructor's fail-closed
    contract."""
    from app.seam.graphiti_store import llm_endpoint_is_openai_cloud
    if not os.environ.get("NEO4J_URI"):
        return False
    return not llm_endpoint_is_openai_cloud(os.environ.get("OPENAI_BASE_URL", ""))


@pytest.mark.skipif(not _local_first_env_ready(), reason=_SKIP_REASON)
def test_graphiti_store_round_trip_add_episode_and_search() -> None:
    """One add_episode + one search round-trip against the live container.

    Asserts the store survives lazy-init (schema build), accepts an
    episode, and returns a :class:`SearchResults` envelope for a
    follow-up query. Does NOT pin extracted entities -- extraction
    quality depends on the LLM endpoint and is out of scope for the
    seam smoke.
    """
    from app.seam.graphiti_store import GraphitiAudienceStore
    from app.seam.protocols import Episode, SearchResults

    neo4j_uri = os.environ["NEO4J_URI"]
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "")
    # OPENAI_BASE_URL is required (the skip gate above enforces this) so
    # the constructor's local-first guard never refuses what the skip let
    # through. The test is locked to the user's explicit local endpoint.
    llm_base_url = os.environ["OPENAI_BASE_URL"]
    llm_api_key = os.environ.get("OPENAI_API_KEY", "")

    store = GraphitiAudienceStore(
        neo4j_uri,
        neo4j_user,
        neo4j_password,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
    )

    graph_id = "test-graphiti-smoke"
    store.add_episode(
        graph_id,
        Episode(
            name="smoke-ep-1",
            content="Kendra trusts the Adidas brand as of March 2026.",
            source="text",
            source_description="seam smoke test",
        ),
    )

    results = store.search(graph_id, "What does Kendra trust?", limit=5)
    assert isinstance(results, SearchResults)
    assert results.query == "What does Kendra trust?"
