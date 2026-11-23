"""Graphiti seam smoke gate: cross-boundary read-back + forensic mock pin.

Two complementary tests that close gaps the other Graphiti seam tests
do not cover:

1. ``test_graphiti_prepare_writes_and_reads_local_memory`` — skip-loud
   integration that exercises generic prepare under
   ``ZEP_MODE=graphiti`` and then re-reads the same Neo4j group via a
   freshly-constructed :class:`GraphitiAudienceStore`. This proves the
   writes physically landed in Neo4j and survive the request boundary —
   neither of which the HTTP-shape test pins. Skip predicate also
   requires ``graphiti_core`` to be importable so a missing optional dep
   surfaces as a skip rather than a mid-test ImportError.

2. ``test_mock_path_remains_deterministic_uuid5_pin`` — always-runs
   forensic regression gate. Builds a fixed minimal :class:`Scenario`,
   seeds a :class:`MockZepStorage`, and asserts the resulting
   ``scenario_hash`` and entity uuid match precomputed hex literals
   baked into this source. The literals are NOT recomputed via the same
   ``uuid5(...)`` expression at assertion time — that would tautologise
   the test. They are stable fingerprints that fail loudly if anything
   shifts the canonical Scenario byte-dump or rotates ``_MOCK_NAMESPACE``.

Regenerate protocol (if a future legitimate refactor changes either
literal): recompute via the snippet in the test docstring below, paste
the new hex string into the constant, and justify the byte change in
the same diff message. Do NOT silently update either literal.

Run locally:
    cd backend
    docker compose up -d neo4j-community
    export NEO4J_PASSWORD='choose-something-not-in-this-repo'
    export NEO4J_URI=bolt://localhost:7687
    export NEO4J_USER=neo4j
    export OPENAI_BASE_URL=http://localhost:11434/v1   # Ollama-style local
    export OPENAI_API_KEY=ollama
    uv run pytest tests/seam/test_graphiti_smoke.py -q
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Dict

import pytest

from tests._helpers import make_app


# Skip reason names each missing piece so a half-configured CI env reads
# as "set NEO4J_URI" / "install graphiti-core" rather than "test author
# was lazy". Mirrors the wording in ``test_graphiti_prepare_path.py`` and
# ``test_graphiti_store.py``.
_SKIP_REASON = (
    "Set NEO4J_URI + OPENAI_BASE_URL (non-cloud) and install graphiti-core "
    "to run; missing pieces are why this test skipped. Start the local "
    "stack with `docker compose up -d neo4j-community` from backend/ and "
    "`uv pip install 'graphiti-core>=0.29,<0.30'`."
)


def _smoke_env_ready() -> bool:
    """Skip predicate: Neo4j URI set AND non-cloud LLM endpoint AND
    graphiti-core importable.

    Delegates the LLM-endpoint check to
    :func:`graphiti_store.llm_endpoint_is_openai_cloud` so this predicate
    cannot drift from the constructor's fail-closed contract. The
    ``importlib.util.find_spec`` branch ensures the test SKIPS LOUDLY
    (rather than failing with a wrapped ImportError on first method call)
    when ``graphiti-core`` is intentionally absent from the env.
    """
    from app.seam.graphiti_store import llm_endpoint_is_openai_cloud
    if not os.environ.get("NEO4J_URI"):
        return False
    if llm_endpoint_is_openai_cloud(os.environ.get("OPENAI_BASE_URL", "")):
        return False
    if importlib.util.find_spec("graphiti_core") is None:
        return False
    return True


def _policy_segment() -> Dict[str, Any]:
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


def _creator_segment() -> Dict[str, Any]:
    segment = _policy_segment()
    segment["type"] = "follower_cohort"
    return segment


def _smoke_scenario() -> Dict[str, Any]:
    """Scenario whose seed_text deliberately names the stakeholder so the
    extractor has something concrete to attach."""
    return {
        "persona": "policy",
        "scope": "internal_org",
        "title": "Graphiti smoke read-back",
        "summary": "Minimal policy scenario for cross-boundary smoke.",
        "seed_text": (
            "Post 1/1: Fans cohort trusts the Adidas brand as of March 2026."
        ),
        "stress_question": "Where might this misfire?",
        "stakeholders": [_policy_segment()],
        "assumptions": ["author is in good standing"],
        "success_criteria": ["no factual correction required"],
        "failure_modes_to_watch": ["misread out of context"],
    }


def _make_app_with_tmp_data(monkeypatch, tmp_path: Path, **env_overrides):
    """Same fixture wiring used by ``test_graphiti_prepare_path.py``."""
    env_overrides.setdefault("AUTH_ENABLED", "false")
    app = make_app(monkeypatch, **env_overrides)
    from app.config import Config

    monkeypatch.setattr(Config, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))
    return app


@pytest.mark.skipif(not _smoke_env_ready(), reason=_SKIP_REASON)
def test_graphiti_prepare_writes_and_reads_local_memory(monkeypatch, tmp_path):
    """Prepare under graphiti mode, then re-open the same group via a
    fresh store and assert the writes are observable.

    Differentiator from ``test_prepare_graphiti_mode_round_trip``: that
    test asserts only the HTTP response shape. This test additionally
    constructs a SEPARATE :class:`GraphitiAudienceStore` against the
    same ``NEO4J_URI`` and calls ``get_all_nodes`` to prove the seam's
    Neo4j writes physically persist and survive the request boundary.

    503 ``ZEP_GRAPHITI_UNAVAILABLE`` is treated as a skip (the container
    is set up in env but not actually reachable); any OTHER 503 fails
    loudly so a config-class bug the skip gate missed cannot silently
    mask the failure mode this gate exists to prevent.
    """
    monkeypatch.setenv("ZEP_MODE", "graphiti")
    app = _make_app_with_tmp_data(monkeypatch, tmp_path)
    client = app.test_client()

    r_create = client.post(
        "/api/sim/use-cases/internal_policy/simulation/create",
        json={"scenario": _smoke_scenario()},
    )
    assert r_create.status_code == 201, r_create.get_json()
    sim_id = r_create.get_json()["data"]["simulation_id"]

    r_prep = client.post(
        f"/api/sim/use-cases/internal_policy/simulation/{sim_id}/prepare",
    )
    body = r_prep.get_json()
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

    # Now the read-back: build a SEPARATE store against the same Neo4j
    # coordinates and assert get_all_nodes surfaces persisted state. A
    # fresh instance proves the writes did not just survive in a
    # request-scoped object cache.
    from app.seam.graphiti_store import GraphitiAudienceStore

    neo4j_uri = os.environ["NEO4J_URI"]
    neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
    neo4j_password = os.environ.get("NEO4J_PASSWORD", "")
    llm_base_url = os.environ["OPENAI_BASE_URL"]
    llm_api_key = os.environ.get("OPENAI_API_KEY", "")

    readback_store = GraphitiAudienceStore(
        neo4j_uri,
        neo4j_user,
        neo4j_password,
        llm_base_url=llm_base_url,
        llm_api_key=llm_api_key,
    )
    try:
        nodes = readback_store.get_all_nodes(graph_id=sim_id)
        assert len(nodes) >= 1, (
            f"Expected at least one persisted node for sim_id={sim_id!r} "
            f"after generic prepare returned 200; got 0. The writes "
            f"either never landed or the group_id partition was wrong."
        )
        seed_text = _smoke_scenario()["seed_text"].lower()
        # At least one extracted node's name should appear in the seed
        # text (case-insensitive substring). Extraction quality varies
        # with the LLM endpoint, so we accept any node whose name token
        # is present rather than pinning a specific entity.
        names_in_seed = [n for n in nodes if n.name and n.name.lower() in seed_text]
        assert names_in_seed, (
            f"None of the persisted node names appear in the seed_text. "
            f"Persisted names: {[n.name for n in nodes]!r}; "
            f"seed_text: {seed_text!r}."
        )
    finally:
        # Best-effort cleanup: drop the group so a re-run does not see
        # stale state. Failures here MUST NOT cascade into the assertion
        # contract — cleanup is a courtesy, not part of the gate.
        try:
            readback_store._ensure_client()
            readback_store._run(
                readback_store._cypher(
                    "MATCH (n) WHERE n.group_id = $gid DETACH DELETE n",
                    {"gid": sim_id},
                )
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Forensic mock determinism pin
# ---------------------------------------------------------------------------

# Precomputed via the snippet below (regenerate protocol — if a legitimate
# refactor changes either literal, run this snippet, paste the new hex
# string into the constant, and justify the byte change in the diff
# message; do NOT silently update either literal):
#
#     from app.policy.schema import Scenario
#     from app.seam.zep_mock import MockZepStorage, scenario_hash
#     data = _PIN_SCENARIO_DICT  # the literal dict below
#     scen = Scenario.model_validate(data, context={"allow_expensive": False})
#     print(scenario_hash(scen))
#     s = MockZepStorage()
#     s.seed_from_scenario("sim-fixed", scen)
#     print(s._store["sim-fixed"][0].uuid)
#
# The literals below are NOT recomputed via the same uuid5/sha256
# expression in the assertion — that would tautologise the pin. They are
# stable fingerprints whose change indicates a real byte-level shift in
# either the canonical Scenario dump (sha256) or the namespace + key
# template that derives the seeded uuid.
_EXPECTED_SCENARIO_HASH = (
    "12db2543c2716ac49c3ceb6b9acf93be1cbfa011281c3cdee7fca8f57243d5a5"
)
_EXPECTED_NODE_UUID = "9b4d4add-c561-510a-9eb2-194dcb6cf393"


def _pin_scenario_dict() -> Dict[str, Any]:
    """Fully-specified minimal Scenario used by the forensic pin.

    Kept separate from ``_smoke_scenario`` because the pin's literals
    are tied to THIS exact byte payload — including ``drift_horizon_rounds``
    and the explicit ``run`` block — and any edit here invalidates the
    precomputed constants above. ``_smoke_scenario`` may evolve
    independently for the live-Neo4j test without forcing a literal
    regeneration here.
    """
    return {
        "persona": "creator",
        "title": "Pin scenario",
        "summary": "Pin scenario for forensic uuid5 + scenario_hash regression gate.",
        "seed_text": "Pinned seed text: Adidas brand reaches the Fans cohort.",
        "stress_question": "Where might this misfire?",
        "stakeholders": [_creator_segment()],
        "assumptions": ["author is in good standing"],
        "success_criteria": ["no factual correction required"],
        "failure_modes_to_watch": ["misread out of context"],
        "drift_horizon_rounds": 1,
        "run": {"ensemble_n": 1, "run_seed": 42},
    }


def test_mock_path_remains_deterministic_uuid5_pin():
    """Forensic regression pin: scenario_hash + seeded uuid match literals.

    Fails loudly on:
      * ``_MOCK_NAMESPACE`` rotation (uuid changes)
      * Scenario field-ordering / by_alias / exclude={'run'} drift
        (scenario_hash changes)
      * Seeded extraction key-template drift, e.g. swapping
        ``{scenario_hash}:{segment_id}:{name}`` for any other order
        (uuid changes)

    Always runs (no skip predicate) so the gate is exercised on every
    test invocation regardless of env.
    """
    from app.policy.schema import Scenario
    from app.seam.zep_mock import MockZepStorage, scenario_hash

    scen = Scenario.model_validate(
        _pin_scenario_dict(),
        context={"allow_expensive": False},
    )
    actual_scen_hash = scenario_hash(scen)
    assert actual_scen_hash == _EXPECTED_SCENARIO_HASH, (
        "scenario_hash drifted from precomputed literal. Either the "
        "canonical Scenario byte-dump (model_dump mode/by_alias/exclude) "
        "changed, or a schema field was added/reordered. Regenerate the "
        "literal per the docstring's regenerate protocol and justify the "
        f"byte change in the diff message. Expected "
        f"{_EXPECTED_SCENARIO_HASH!r}, got {actual_scen_hash!r}."
    )

    store = MockZepStorage()
    store.seed_from_scenario("sim-fixed", scen)
    entities = store._store["sim-fixed"]
    assert len(entities) == 1, (
        f"Pin scenario has exactly one stakeholder; expected 1 entity, "
        f"got {len(entities)}."
    )
    assert entities[0].uuid == _EXPECTED_NODE_UUID, (
        "Seeded entity uuid drifted from precomputed literal. Either "
        "_MOCK_NAMESPACE was rotated or the uuid5 key template changed. "
        "Regenerate per the docstring's protocol and justify the byte "
        f"change in the diff message. Expected {_EXPECTED_NODE_UUID!r}, "
        f"got {entities[0].uuid!r}."
    )
