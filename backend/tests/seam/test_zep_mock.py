"""Ingest+query surface on :class:`MockZepStorage`.

Pins the 5 methods added to satisfy the widened 7-method
``IAudienceStore`` Protocol (7-method contract):
``set_ontology`` / ``add_episode`` (with string-match extraction) /
``search`` (canned scored results) / ``get_all_nodes`` /
``get_all_edges``. Determinism is end-to-end: same scenario + same
episode payloads -> byte-identical extracted node/edge uuid sets and
identical ``search`` rankings.

The existing 2-method read surface
(``filter_defined_entities`` / ``get_entities_by_type``) is covered by
``test_zep_mock_determinism.py``; this file does not duplicate that.
"""
from __future__ import annotations

from typing import Any, Dict

from app.policy.schema import Scenario
from app.seam.protocols import Episode, IAudienceStore, Ontology
from app.seam.zep_mock import MockZepStorage


# ---------------------------------------------------------------------------
# Fixture helpers (kept local; mirrors test_zep_mock_determinism.py shape)
# ---------------------------------------------------------------------------


def _minimal_segment(
    *,
    segment_id: str = "s_fans",
    seg_type: str = "follower_cohort",
    name: str = "Fans cohort",
) -> Dict[str, Any]:
    return {
        "segment_id": segment_id,
        "name": name,
        "type": seg_type,
        "influence_level": 3,
        "vulnerability_level": 3,
        "compliance_propensity": 3,
        "trust_level": 3,
        "mobilization_capacity": 3,
        "what_changes_their_mind": ["new evidence emerges"],
    }


def _build_scenario(**overrides: Any) -> Scenario:
    data: Dict[str, Any] = {
        "persona": "creator",
        "title": "Det scenario",
        "summary": "Deterministic creator scenario for mock-zep tests.",
        "seed_text": "Post 1/1: deterministic seed text.",
        "stress_question": "Where might this misfire?",
        "stakeholders": [
            _minimal_segment(segment_id="s_fans", name="Fans cohort"),
            _minimal_segment(
                segment_id="s_critics",
                name="Critics cohort",
                seg_type="critic",
            ),
        ],
        "assumptions": ["author is in good standing"],
        "success_criteria": ["no factual correction required"],
        "failure_modes_to_watch": ["misread out of context"],
        "drift_horizon_rounds": 1,
        "run": {"ensemble_n": 1, "run_seed": 42},
    }
    data.update(overrides)
    return Scenario.model_validate(data, context={"allow_expensive": False})


# ---------------------------------------------------------------------------
# Protocol structural conformance
# ---------------------------------------------------------------------------


def test_mock_satisfies_iaudience_store_runtime_check():
    """``runtime_checkable`` isinstance must pass once the 5 methods are wired.

    The Protocol is the seam contract; if any of the 7 methods drops off
    the mock, this test catches it before the next caller (factory cast,
    /simulation/prepare) does.
    """
    assert isinstance(MockZepStorage(), IAudienceStore) is True


# ---------------------------------------------------------------------------
# set_ontology
# ---------------------------------------------------------------------------


def test_set_ontology_is_stored_per_graph():
    """Per-graph isolation: setting onto for sim_a must not leak into sim_b."""
    store = MockZepStorage()
    onto_a = Ontology(entity_types={"Stakeholder": {"description": "x"}})
    onto_b = Ontology(entity_types={"Channel": {"description": "y"}})
    store.set_ontology("sim_a", onto_a)
    store.set_ontology("sim_b", onto_b)
    assert store._ontologies["sim_a"] is onto_a
    assert store._ontologies["sim_b"] is onto_b


def test_set_ontology_overwrites_last_wins():
    """Re-setting an ontology for the same graph is idempotent + last-wins."""
    store = MockZepStorage()
    first = Ontology(entity_types={"A": {}})
    second = Ontology(entity_types={"B": {}})
    store.set_ontology("sim_a", first)
    store.set_ontology("sim_a", second)
    assert store._ontologies["sim_a"] is second


# ---------------------------------------------------------------------------
# add_episode -- string-match extraction
# ---------------------------------------------------------------------------


def test_add_episode_extracts_stakeholder_names_from_seeded_store():
    """Stakeholder names in episode text become nodes via string-match.

    Extraction is a string-match against the ontology + Scenario.stakeholders
    name bag. With NO ontology set, the seeded stakeholder names alone
    should still drive extraction.
    """
    store = MockZepStorage()
    store.seed_from_scenario("sim_a", _build_scenario())
    episode = Episode(
        name="ep_001",
        content="Fans cohort showed up today; no Critics cohort in sight.",
    )
    store.add_episode("sim_a", episode)
    names = [n.name for n in store.get_all_nodes("sim_a")]
    # Both seeded stakeholders should appear (seeded + extracted, deduped).
    assert "Fans cohort" in names
    assert "Critics cohort" in names


def test_add_episode_extraction_uses_ontology_entity_type_keys():
    """Ontology entity_types keys augment the name bag (no seeded scenario)."""
    store = MockZepStorage()
    store.set_ontology(
        "sim_a",
        Ontology(entity_types={"Brand": {}, "Channel": {}}),
    )
    store.add_episode(
        "sim_a",
        Episode(name="ep1", content="The Brand launched on a new Channel."),
    )
    names = sorted(n.name for n in store.get_all_nodes("sim_a"))
    assert names == ["Brand", "Channel"]


def test_add_episode_is_deterministic_across_instances():
    """Same scenario + same episode payload -> identical extracted uuids.

    Re-runs the full ingest twice from two fresh stores and compares the
    full (node_uuid, edge_uuid) sets. Determinism here is what lets
    deterministic-mock demo runs reproduce across machines.
    """
    scen = _build_scenario()
    ep = Episode(
        name="ep_det",
        content="Fans cohort and Critics cohort co-appear here.",
    )

    s1 = MockZepStorage()
    s1.seed_from_scenario("sim_a", scen)
    s1.add_episode("sim_a", ep)

    s2 = MockZepStorage()
    s2.seed_from_scenario("sim_a", scen)
    s2.add_episode("sim_a", ep)

    n1 = [n.uuid for n in s1.get_all_nodes("sim_a")]
    n2 = [n.uuid for n in s2.get_all_nodes("sim_a")]
    e1 = [e.uuid for e in s1.get_all_edges("sim_a")]
    e2 = [e.uuid for e in s2.get_all_edges("sim_a")]
    assert n1 == n2
    assert e1 == e2


def test_add_episode_emits_co_occurrence_edges_for_multi_match():
    """2+ matches in one episode -> co-occurrence edges between pairs."""
    store = MockZepStorage()
    store.seed_from_scenario("sim_a", _build_scenario())
    store.add_episode(
        "sim_a",
        Episode(name="ep_pair", content="Fans cohort meets Critics cohort."),
    )
    edges = store.get_all_edges("sim_a")
    assert len(edges) == 1, "two-match episode should yield one consecutive edge"
    assert edges[0].name == "co_occurs_with"


def test_add_episode_with_no_matches_produces_no_nodes_or_edges():
    """Episode whose text mentions no known names is a no-op for extraction."""
    store = MockZepStorage()
    store.seed_from_scenario("sim_a", _build_scenario())
    # The seeded store has 'Fans cohort' / 'Critics cohort'; mention neither.
    store.add_episode(
        "sim_a",
        Episode(name="ep_noise", content="Just unrelated text."),
    )
    # Extracted buckets stay empty; seeded nodes still come back via
    # get_all_nodes (merged surface).
    assert store._extracted_nodes.get("sim_a", []) == []
    assert store.get_all_edges("sim_a") == []


def test_add_episode_idempotent_on_re_ingest():
    """Re-ingesting the same episode does not duplicate nodes or edges."""
    store = MockZepStorage()
    store.seed_from_scenario("sim_a", _build_scenario())
    ep = Episode(
        name="ep_idem",
        content="Fans cohort and Critics cohort appear together.",
    )
    store.add_episode("sim_a", ep)
    store.add_episode("sim_a", ep)
    # Two distinct names -> 2 extracted nodes, 1 edge, regardless of re-ingest.
    assert len(store._extracted_nodes["sim_a"]) == 2
    assert len(store._extracted_edges["sim_a"]) == 1


# ---------------------------------------------------------------------------
# search -- canned scored results, deterministic
# ---------------------------------------------------------------------------


def test_search_is_deterministic_for_same_seed_and_query():
    """Same master_seed + same query -> identical rank order across stores."""
    scen = _build_scenario()
    ep = Episode(
        name="ep_q",
        content="Fans cohort and Critics cohort talk today.",
    )

    s1 = MockZepStorage()
    s1.seed_from_scenario("sim_a", scen)
    s1.add_episode("sim_a", ep)

    s2 = MockZepStorage()
    s2.seed_from_scenario("sim_a", scen)
    s2.add_episode("sim_a", ep)

    r1 = s1.search("sim_a", "who showed up", limit=5)
    r2 = s2.search("sim_a", "who showed up", limit=5)
    assert [n.uuid for n in r1.nodes] == [n.uuid for n in r2.nodes]
    assert r1.query == "who showed up"


def test_search_respects_limit():
    """``limit`` truncates the rank list, never exceeds it."""
    store = MockZepStorage()
    store.seed_from_scenario("sim_a", _build_scenario())
    res = store.search("sim_a", "any query", limit=1)
    assert len(res.nodes) == 1


def test_search_on_empty_graph_returns_empty_envelope():
    """Searching an unseeded graph_id returns a valid empty SearchResults."""
    store = MockZepStorage()
    res = store.search("never_seeded", "anything")
    assert res.query == "anything"
    assert res.nodes == []
    assert res.edges == []


# ---------------------------------------------------------------------------
# get_all_nodes / get_all_edges -- sort contract
# ---------------------------------------------------------------------------


def test_get_all_nodes_sorted_by_name_then_uuid():
    """Stable ordering by (name, uuid) for cross-machine reproducibility."""
    store = MockZepStorage()
    store.seed_from_scenario("sim_a", _build_scenario())
    nodes = store.get_all_nodes("sim_a")
    keys = [(n.name, n.uuid) for n in nodes]
    assert keys == sorted(keys)


def test_get_all_edges_sorted_by_src_dst_uuid():
    """Edges sorted by (source_node_uuid, target_node_uuid, uuid)."""
    store = MockZepStorage()
    store.seed_from_scenario("sim_a", _build_scenario())
    # Two episodes, each contributing one edge, so we have something to sort.
    store.add_episode(
        "sim_a",
        Episode(name="ep_1", content="Fans cohort then Critics cohort."),
    )
    store.add_episode(
        "sim_a",
        Episode(name="ep_2", content="Critics cohort then Fans cohort."),
    )
    edges = store.get_all_edges("sim_a")
    keys = [(e.source_node_uuid, e.target_node_uuid, e.uuid) for e in edges]
    assert keys == sorted(keys)


def test_get_all_nodes_dedupes_seeded_and_extracted_on_uuid():
    """A name matched by extraction reuses the seeded uuid -> no duplicate row.

    Without dedup the same stakeholder would appear twice (once seeded,
    once extracted with a different uuid namespace) -- this asserts the
    merged surface is a true superset.
    """
    store = MockZepStorage()
    store.seed_from_scenario("sim_a", _build_scenario())
    store.add_episode(
        "sim_a",
        Episode(name="ep_dedup", content="Fans cohort returns."),
    )
    fans_rows = [n for n in store.get_all_nodes("sim_a") if n.name == "Fans cohort"]
    assert len(fans_rows) == 1
