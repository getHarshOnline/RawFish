"""Generic prepare end-to-end against a live Neo4j Community instance.

Pins the contract that ``ZEP_MODE=graphiti`` routes the prepare endpoint
through the 7-method seam: ``set_ontology`` -> ``add_episode`` ->
``get_all_nodes``. The same skip pattern as
:mod:`tests.seam.test_graphiti_store` applies -- when ``NEO4J_URI`` is
not set we cannot reach Neo4j, so the gate's contract is "skip with a
clear reason string" (never a silent ``xfail``/``skip``).

A separate test exercises the failure mode: when ``ZEP_MODE=graphiti``
is configured but ``NEO4J_URI`` points at an unreachable host,
the generic prepare route must surface a 503 ``ZEP_GRAPHITI_UNAVAILABLE``
rather than silently degrading to an empty ontology summary.

Run locally:
    cd backend && docker compose up -d neo4j-community
    export NEO4J_URI=bolt://localhost:7687
    export NEO4J_USER=neo4j
    export NEO4J_PASSWORD=rawfish-local-dev
    export OPENAI_BASE_URL=http://localhost:11434/v1   # Ollama-style local
    export OPENAI_API_KEY=ollama
    uv run pytest tests/seam/test_graphiti_prepare_path.py -q
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import pytest

from tests._helpers import make_app


# Same wording as test_graphiti_store.py so CI surfaces it as "container
# not running" rather than "test author was lazy". OPENAI_BASE_URL is
# also required so the factory's local-first guard never refuses what
# the skip let through.
_SKIP_REASON = (
    "NEO4J_URI and OPENAI_BASE_URL both required; generic prepare "
    "graphiti-mode integration test requires a running Neo4j Community "
    "container (start it with `docker compose up -d neo4j-community` "
    "from backend/) and an explicit local LLM endpoint (e.g. "
    "OPENAI_BASE_URL=http://localhost:11434/v1)."
)


def _local_first_env_ready() -> bool:
    """Delegates to ``graphiti_store.llm_endpoint_is_openai_cloud`` so this
    skip predicate cannot drift from the constructor's fail-closed
    contract."""
    from app.seam.graphiti_store import llm_endpoint_is_openai_cloud
    if not os.environ.get("NEO4J_URI"):
        return False
    return not llm_endpoint_is_openai_cloud(os.environ.get("OPENAI_BASE_URL", ""))


def _minimal_segment() -> Dict[str, Any]:
    """Single follower_cohort segment; matches the prepare-artifact test fixture."""
    return {
        "segment_id": "s_fans",
        "name": "Fans cohort",
        "type": "employee_cohort",
        "influence_level": 3,
        "vulnerability_level": 3,
        "compliance_propensity": 3,
        "trust_level": 3,
        "mobilization_capacity": 3,
        "what_changes_their_mind": ["new evidence emerges"],
    }


def _minimal_scenario() -> Dict[str, Any]:
    return {
        "persona": "policy",
        "scope": "internal_org",
        "title": "Graphiti prepare smoke",
        "summary": "Minimal policy scenario for graphiti-mode prepare wiring.",
        "seed_text": (
            "Post 1/1: Fans cohort trusts the Adidas brand as of March 2026."
        ),
        "stress_question": "Where might this misfire?",
        "stakeholders": [_minimal_segment()],
        "assumptions": ["author is in good standing"],
        "success_criteria": ["no factual correction required"],
        "failure_modes_to_watch": ["misread out of context"],
    }


def _make_app_with_tmp_data(monkeypatch, tmp_path: Path, **env_overrides):
    """Wire a Flask app to write artifacts into the test directory.

    Pins ``Config.OASIS_SIMULATION_DATA_DIR`` to ``tmp_path`` AFTER
    ``make_app`` reloads ``app.config`` so the endpoint writes artifacts
    into the per-test directory rather than the package's
    ``backend/uploads`` default.
    """
    env_overrides.setdefault("AUTH_ENABLED", "false")
    app = make_app(monkeypatch, **env_overrides)
    from app.config import Config

    monkeypatch.setattr(Config, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))
    return app


@pytest.mark.skipif(not _local_first_env_ready(), reason=_SKIP_REASON)
def test_prepare_graphiti_mode_round_trip(monkeypatch, tmp_path):
    """ZEP_MODE=graphiti routes through set_ontology + add_episode + get_all_nodes.

    With a reachable Neo4j the endpoint must return a 200 with a
    well-formed ``ontology_summary`` envelope. We do NOT pin a specific
    entity_count: extraction quality depends on the LLM endpoint and is
    out of scope for the seam smoke. We DO assert that the response
    shape is intact (``entity_count``, ``entities``, ``segment_types``
    all present) so a future refactor that drops a field fails loudly.
    """
    monkeypatch.setenv("ZEP_MODE", "graphiti")
    # NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD flow through the seam's
    # GraphitiAudienceStore __init__ via MockZepStorage.get_factory().
    # The skip gate above already pinned a non-cloud OPENAI_BASE_URL, so
    # the factory's local-first guard will accept this env.
    app = _make_app_with_tmp_data(monkeypatch, tmp_path)
    client = app.test_client()

    # Create the simulation first so prepare has a state.json to read.
    r_create = client.post(
        "/api/sim/use-cases/internal_policy/simulation/create",
        json={"scenario": _minimal_scenario()},
    )
    assert r_create.status_code == 201, r_create.get_json()
    sim_id = r_create.get_json()["data"]["simulation_id"]

    r_prep = client.post(
        f"/api/sim/use-cases/internal_policy/simulation/{sim_id}/prepare",
    )
    body = r_prep.get_json()
    # 200 = Neo4j reachable + the 7-method seam executed end-to-end.
    # 503 ZEP_GRAPHITI_UNAVAILABLE = container not actually up despite
    # NEO4J_URI being set; surface that as a skip rather than a
    # false-positive failure so a half-configured CI env does not
    # regress the gate.
    # 503 with any OTHER error_code (e.g. a local-first / config failure
    # the skip gate did not catch) must fail loudly -- a blanket skip on
    # every 503 would mask the very class of bug the gate exists to
    # prevent.
    if r_prep.status_code == 503:
        err = (body or {}).get("error") or {}
        err_code = err.get("code")
        if err_code == "ZEP_GRAPHITI_UNAVAILABLE":
            pytest.skip(
                f"Neo4j unreachable at NEO4J_URI={os.environ.get('NEO4J_URI')!r}: "
                f"{body!r}"
            )
        pytest.fail(
            f"503 with unexpected error_code={err_code!r}; not skipping. "
            f"Body: {body!r}"
        )
    assert r_prep.status_code == 200, body
    assert body["success"] is True
    summary = body["data"]["ontology_summary"]
    assert "entity_count" in summary
    assert "entities" in summary
    assert "segment_types" in summary
    # ``entity_count`` must agree with the list length (cheap sanity invariant
    # so a future bug that off-by-ones the count surfaces here).
    assert summary["entity_count"] == len(summary["entities"])


def test_prepare_graphiti_mode_unreachable_returns_503(monkeypatch, tmp_path):
    """Graphiti init failure surfaces 503 ZEP_GRAPHITI_UNAVAILABLE.

    Pins the failure-mode contract: when the audience-store seam raises
    ``GraphitiInitError``, prepare must return a structured 503 with
    ``error.code == 'ZEP_GRAPHITI_UNAVAILABLE'`` rather than silently
    returning an empty ontology summary as if the seam ran cleanly.
    """
    monkeypatch.setenv("ZEP_MODE", "graphiti")
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "unused-for-failure-path")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "ollama-test-only")

    from app.seam.graphiti_store import GraphitiInitError
    from app.seam.zep_mock import MockZepStorage

    class FailingAudienceStore:
        def set_ontology(self, graph_id, ontology):
            del graph_id, ontology
            raise GraphitiInitError(
                "NEO4J_URI=bolt://127.0.0.1:1 is not reachable"
            )

    monkeypatch.setattr(
        MockZepStorage,
        "get_factory",
        classmethod(lambda cls: FailingAudienceStore()),
    )

    app = _make_app_with_tmp_data(monkeypatch, tmp_path)
    client = app.test_client()

    r_create = client.post(
        "/api/sim/use-cases/internal_policy/simulation/create",
        json={"scenario": _minimal_scenario()},
    )
    assert r_create.status_code == 201, r_create.get_json()
    sim_id = r_create.get_json()["data"]["simulation_id"]

    r_prep = client.post(
        f"/api/sim/use-cases/internal_policy/simulation/{sim_id}/prepare",
    )
    body = r_prep.get_json()
    assert r_prep.status_code == 503, body
    assert body["success"] is False
    assert body["error"]["code"] == "ZEP_GRAPHITI_UNAVAILABLE"
    # The error message must name NEO4J_URI so an operator can fix the
    # misconfigured boot from the response alone.
    assert "NEO4J_URI" in body["error"]["message"]
