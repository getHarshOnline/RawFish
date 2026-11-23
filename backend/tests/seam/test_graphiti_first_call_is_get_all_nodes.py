"""First-call regression: ``get_all_nodes`` on a fresh store must not deadlock.

Previously, ``get_all_nodes`` funnelled through ``_run(self._cypher(...))``
which called ``_ensure_client()`` from inside the awaited coroutine --
itself running on a private event loop opened by ``_run``. The nested
``_run(build_indices_and_constraints())`` inside ``_ensure_client`` then
attempted to drive a NEW event loop while the outer loop was still
running, raising "This event loop is already running" (or, on some
asyncio versions, blocking indefinitely).

The fix moves ``_ensure_client`` to the sync entry points so the
schema-bootstrap completes BEFORE any outer ``_run`` frame is opened.
This test pins that invariant by exercising the formerly-broken path:
``get_all_nodes`` as the very first call against a freshly-constructed
``GraphitiAudienceStore`` (no prior ``set_ontology`` / ``add_episode``
to warm the client).

Skip-gated on ``NEO4J_URI`` for the same reason as
:mod:`tests.seam.test_graphiti_store` -- without a reachable Neo4j the
contract under test is unobservable.

Run locally:
    cd backend && docker compose up -d neo4j-community
    export NEO4J_URI=bolt://localhost:7687
    export NEO4J_USER=neo4j
    export NEO4J_PASSWORD=rawfish-local-dev
    export OPENAI_BASE_URL=http://localhost:11434/v1   # Ollama-style local
    export OPENAI_API_KEY=ollama
    uv run pytest tests/seam/test_graphiti_first_call_is_get_all_nodes.py -q
"""
from __future__ import annotations

import os

import pytest


# Same skip-reason wording as test_graphiti_store.py so CI surfaces it
# as "container not running" rather than "test author was lazy".
# OPENAI_BASE_URL is also required so the constructor's local-first
# guard never refuses what the skip let through.
_SKIP_REASON = (
    "NEO4J_URI and OPENAI_BASE_URL both required; first-call regression "
    "test requires a running Neo4j Community container (start it with "
    "`docker compose up -d neo4j-community` from backend/) and an "
    "explicit local LLM endpoint (e.g. OPENAI_BASE_URL=http://localhost:11434/v1)."
)


def _local_first_env_ready() -> bool:
    """Delegates to ``graphiti_store.llm_endpoint_is_openai_cloud`` so this
    skip predicate cannot drift from the constructor's fail-closed contract."""
    from app.seam.graphiti_store import llm_endpoint_is_openai_cloud
    if not os.environ.get("NEO4J_URI"):
        return False
    return not llm_endpoint_is_openai_cloud(os.environ.get("OPENAI_BASE_URL", ""))


@pytest.mark.skipif(not _local_first_env_ready(), reason=_SKIP_REASON)
def test_first_call_is_get_all_nodes_does_not_deadlock() -> None:
    """``get_all_nodes`` on a cold store must complete (not raise / not hang).

    Returns an empty list on a fresh group_id (no nodes ingested yet),
    but the assertion that matters is the no-exception completion of
    the call itself. Schema bootstrap runs on the sync stack inside
    ``_ensure_client`` before any ``_run(self._cypher(...))`` frame is
    opened, eliminating the loop-re-entry bug.
    """
    from app.seam.graphiti_store import GraphitiAudienceStore

    neo4j_uri = os.environ["NEO4J_URI"]
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "")
    llm_base_url = os.environ["OPENAI_BASE_URL"]
    llm_api_key = os.environ.get("OPENAI_API_KEY", "")

    store = GraphitiAudienceStore(
        neo4j_uri,
        neo4j_user,
        neo4j_password,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
    )

    # FIRST call on the freshly-constructed store. No prior set_ontology
    # / add_episode has populated the client cache or run the schema
    # bootstrap, so this exercises the cold path -- which must not
    # deadlock.
    nodes = store.get_all_nodes("test-graphiti-first-call-cold")
    assert isinstance(nodes, list)
    # Empty list is correct: nothing ingested under this graph_id.
    assert nodes == []
