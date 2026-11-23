"""ZepCloudShim Protocol conformance + delegation contract.

Three properties under test:

1. ``isinstance(ZepCloudShim(...), IAudienceStore)`` is True at
   runtime -- the cast in the experimental_cloud factory branch is
   honest because the shim structurally implements all 7 methods.
2. The 3 V3-graph methods (``set_ontology`` / ``add_episode`` /
   ``search``) raise :class:`NotImplementedError` with a message that
   points users at ``ZEP_MODE=graphiti``. This is the documented
   contract: experimental_cloud exposes only the read-only reader
   surface; ingest/query needs either the V3 wiring or graphiti mode.
3. The 4 read-surface methods (``get_all_nodes`` / ``get_all_edges`` /
   ``filter_defined_entities`` / ``get_entities_by_type``) delegate
   to the wrapped ``ZepEntityReader`` and reshape dict rows into
   ``EntityNode`` / ``EntityEdge`` dataclasses.

A lightweight fake reader (no real ``ZepEntityReader.__init__`` call,
so no ``ZEP_API_KEY`` env requirement) is injected via the shim's
``reader=`` constructor kwarg.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from app.seam.protocols import Episode, IAudienceStore, Ontology
from app.seam.zep_cloud_shim import ZepCloudShim
from app.services.zep_entity_reader import (
    EntityEdge,
    EntityNode,
    FilteredEntities,
)


# ---------------------------------------------------------------------------
# Test double
# ---------------------------------------------------------------------------


class _FakeReader:
    """Records call args; returns canned dict/dataclass rows.

    Mirrors the public shape of ``ZepEntityReader`` that the shim
    touches; it deliberately does NOT subclass ``ZepEntityReader``
    so we sidestep the upstream ``__init__``'s ``ZEP_API_KEY`` check.
    """

    def __init__(self) -> None:
        self.calls: List[tuple[str, tuple[Any, ...], Dict[str, Any]]] = []

    def get_all_nodes(self, graph_id: str) -> List[Dict[str, Any]]:
        self.calls.append(("get_all_nodes", (graph_id,), {}))
        return [
            {
                "uuid": "uuid-1",
                "name": "Alice",
                "labels": ["Entity", "Stakeholder"],
                "summary": "summary-1",
                "attributes": {"role": "lead"},
            }
        ]

    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]:
        self.calls.append(("get_all_edges", (graph_id,), {}))
        return [
            {
                "uuid": "edge-1",
                "name": "MENTIONS",
                "fact": "Alice mentions Bob",
                "source_node_uuid": "uuid-1",
                "target_node_uuid": "uuid-2",
                "attributes": {"weight": 1.0},
            }
        ]

    def filter_defined_entities(
        self,
        graph_id: str,
        defined_entity_types: Optional[List[str]] = None,
        enrich_with_edges: bool = True,
    ) -> FilteredEntities:
        self.calls.append(
            (
                "filter_defined_entities",
                (graph_id,),
                {
                    "defined_entity_types": defined_entity_types,
                    "enrich_with_edges": enrich_with_edges,
                },
            )
        )
        return FilteredEntities(
            entities=[],
            entity_types=set(),
            total_count=0,
            filtered_count=0,
        )

    def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str,
        enrich_with_edges: bool = True,
    ) -> List[EntityNode]:
        self.calls.append(
            (
                "get_entities_by_type",
                (graph_id, entity_type),
                {"enrich_with_edges": enrich_with_edges},
            )
        )
        return []


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_shim_is_runtime_instance_of_iaudiencestore() -> None:
    """``isinstance`` must return True so the factory cast is honest."""
    shim = ZepCloudShim(reader=_FakeReader())
    assert isinstance(shim, IAudienceStore), (
        "ZepCloudShim does not structurally satisfy IAudienceStore; "
        "the factory cast in zep_mock.get_factory() would lie."
    )


# ---------------------------------------------------------------------------
# Not-implemented contract for the 3 V3-graph methods
# ---------------------------------------------------------------------------


def _expected_message_fragment() -> str:
    return "ZEP_MODE=graphiti"


def test_set_ontology_raises_not_implemented_with_migration_hint() -> None:
    shim = ZepCloudShim(reader=_FakeReader())
    with pytest.raises(NotImplementedError) as excinfo:
        shim.set_ontology("graph-a", Ontology())
    assert "set_ontology" in str(excinfo.value)
    assert _expected_message_fragment() in str(excinfo.value)


def test_add_episode_raises_not_implemented_with_migration_hint() -> None:
    shim = ZepCloudShim(reader=_FakeReader())
    with pytest.raises(NotImplementedError) as excinfo:
        shim.add_episode("graph-a", Episode(name="ep-1", content="hello"))
    assert "add_episode" in str(excinfo.value)
    assert _expected_message_fragment() in str(excinfo.value)


def test_search_raises_not_implemented_with_migration_hint() -> None:
    shim = ZepCloudShim(reader=_FakeReader())
    with pytest.raises(NotImplementedError) as excinfo:
        shim.search("graph-a", "find Alice", limit=5)
    assert "search" in str(excinfo.value)
    assert _expected_message_fragment() in str(excinfo.value)


# ---------------------------------------------------------------------------
# Read-surface delegation
# ---------------------------------------------------------------------------


def test_get_all_nodes_reshapes_dicts_into_entity_nodes() -> None:
    fake = _FakeReader()
    shim = ZepCloudShim(reader=fake)
    out = shim.get_all_nodes("graph-a")
    assert len(out) == 1
    node = out[0]
    assert isinstance(node, EntityNode)
    assert node.uuid == "uuid-1"
    assert node.name == "Alice"
    assert node.labels == ["Entity", "Stakeholder"]
    assert node.attributes == {"role": "lead"}
    assert fake.calls == [("get_all_nodes", ("graph-a",), {})]


def test_get_all_edges_reshapes_dicts_into_entity_edges() -> None:
    fake = _FakeReader()
    shim = ZepCloudShim(reader=fake)
    out = shim.get_all_edges("graph-a")
    assert len(out) == 1
    edge = out[0]
    assert isinstance(edge, EntityEdge)
    assert edge.uuid == "edge-1"
    assert edge.source_node_uuid == "uuid-1"
    assert edge.target_node_uuid == "uuid-2"
    assert edge.attributes == {"weight": 1.0}
    assert fake.calls == [("get_all_edges", ("graph-a",), {})]


def test_filter_defined_entities_delegates_with_kw_only_signature() -> None:
    fake = _FakeReader()
    shim = ZepCloudShim(reader=fake)
    result = shim.filter_defined_entities(
        "graph-a", ["Stakeholder"], enrich_with_edges=True
    )
    assert isinstance(result, FilteredEntities)
    assert fake.calls == [
        (
            "filter_defined_entities",
            ("graph-a",),
            {
                "defined_entity_types": ["Stakeholder"],
                "enrich_with_edges": True,
            },
        )
    ]


def test_get_entities_by_type_delegates_with_kw_only_signature() -> None:
    fake = _FakeReader()
    shim = ZepCloudShim(reader=fake)
    result = shim.get_entities_by_type(
        "graph-a", "Stakeholder", enrich_with_edges=False
    )
    assert result == []
    assert fake.calls == [
        (
            "get_entities_by_type",
            ("graph-a", "Stakeholder"),
            {"enrich_with_edges": False},
        )
    ]
