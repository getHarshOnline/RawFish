"""Cloud-Zep shim that satisfies the 7-method ``IAudienceStore`` Protocol.

The bare upstream ``zep_cloud.client.Zep`` class does NOT expose the
reader-style methods the seam contract requires; the existing
:class:`app.services.zep_entity_reader.ZepEntityReader` wrapper covers
only the original 2-method read surface
(``filter_defined_entities`` / ``get_entities_by_type``).

This shim wraps ``ZepEntityReader`` and:

1. Adapts the 2 read-surface methods to the Protocol's keyword-only
   ``enrich_with_edges`` signature (the wrapper uses
   ``enrich_with_edges`` positionally with a different default), and
   re-shapes ``get_all_nodes`` / ``get_all_edges`` returns from
   ``List[Dict[str, Any]]`` into ``List[EntityNode]`` /
   ``List[EntityEdge]`` so the seam returns homogeneous dataclasses
   across all three backends (mock, graphiti, experimental_cloud).
2. Raises :class:`NotImplementedError` for the 3 V3-graph methods
   (``set_ontology`` / ``add_episode`` / ``search``) with a single,
   actionable migration message pointing at ``ZEP_MODE=graphiti``.
   This is the honest contract: the Protocol surface is structurally
   satisfied (every method is callable so ``isinstance(.,
   IAudienceStore)`` returns True), and calls to the un-ported V3
   methods fail loudly at the call site rather than silently casting
   a 2-method object as a 7-method one.

The 5 NotImplementedError methods can be replaced incrementally with
real Zep V3 ``graph.add`` / ``graph.search`` / ``graph.set_entity_types``
delegations later without touching the factory or the Protocol.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

from ..services.zep_entity_reader import (
    EntityEdge,
    EntityNode,
    FilteredEntities,
    ZepEntityReader,
)
from .protocols import Episode, Ontology, SearchResults


class _ReaderLike(Protocol):
    """Structural type for the reader the shim wraps.

    The shim only ever calls these four methods on the wrapped object
    -- typing as a Protocol lets the seam factory pass a real
    ``ZepEntityReader`` while tests inject a lightweight fake without
    inheriting from the upstream class (which would force a real
    ``ZEP_API_KEY`` check in ``__init__``).
    """

    def get_all_nodes(self, graph_id: str) -> List[Dict[str, Any]]: ...

    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]: ...

    def filter_defined_entities(
        self,
        graph_id: str,
        defined_entity_types: Optional[List[str]] = None,
        enrich_with_edges: bool = True,
    ) -> FilteredEntities: ...

    def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str,
        enrich_with_edges: bool = True,
    ) -> List[EntityNode]: ...


_V3_NOT_IMPLEMENTED_MESSAGE = (
    "ZepCloudShim does not implement the V3-graph ingest/query surface "
    "({method!r}); experimental_cloud mode currently exposes only the "
    "read-only ZepEntityReader methods. Use ZEP_MODE=graphiti for the "
    "full 7-method IAudienceStore contract, or extend ZepCloudShim to "
    "delegate to the zep_cloud V3 graph API (graph.add / graph.search / "
    "graph.set_entity_types)."
)


class ZepCloudShim:
    """Wraps :class:`ZepEntityReader` to satisfy :class:`IAudienceStore`.

    Holds a single ``ZepEntityReader`` instance; reader-surface calls
    delegate to it (with signature/return-shape adaptation), and the
    V3-graph methods raise :class:`NotImplementedError` with a clear
    migration message.

    The shim is intentionally thin: it owns no state of its own and
    performs no I/O outside delegating to the wrapped reader. The
    reader itself is the SSoT for the Zep client and API key.
    """

    def __init__(self, reader: Optional[_ReaderLike] = None, *, api_key: Optional[str] = None) -> None:
        """Hold a reader (constructed here if not provided).

        Two construction modes:
        - ``ZepCloudShim(reader=existing_reader)`` -- caller has already
          built the reader (used by tests that inject a fake/mock).
        - ``ZepCloudShim(api_key='...')`` -- shim constructs the reader
          (used by the seam factory).
        """
        if reader is not None:
            self._reader = reader
        else:
            self._reader = ZepEntityReader(api_key=api_key)

    # ------------------------------------------------------------------
    # V3 ingest / query surface (not implemented for experimental_cloud)
    # ------------------------------------------------------------------

    def set_ontology(self, graph_id: str, ontology: Ontology) -> None:
        raise NotImplementedError(
            _V3_NOT_IMPLEMENTED_MESSAGE.format(method="set_ontology")
        )

    def add_episode(self, graph_id: str, episode: Episode) -> None:
        raise NotImplementedError(
            _V3_NOT_IMPLEMENTED_MESSAGE.format(method="add_episode")
        )

    def search(
        self,
        graph_id: str,
        query: str,
        *,
        limit: int = 10,
    ) -> SearchResults:
        raise NotImplementedError(
            _V3_NOT_IMPLEMENTED_MESSAGE.format(method="search")
        )

    # ------------------------------------------------------------------
    # Read surface: re-shape dict returns into dataclasses
    # ------------------------------------------------------------------

    def get_all_nodes(self, graph_id: str) -> List[EntityNode]:
        """Delegate to ``ZepEntityReader.get_all_nodes`` and reshape.

        Upstream returns ``List[Dict[str, Any]]`` (raw API rows); the
        seam contract is ``List[EntityNode]``. The dict keys produced
        by the reader are a known fixed set, so the mapping is total.
        """
        rows = self._reader.get_all_nodes(graph_id)
        return [
            EntityNode(
                uuid=row["uuid"],
                name=row["name"],
                labels=row["labels"],
                summary=row["summary"],
                attributes=row["attributes"],
            )
            for row in rows
        ]

    def get_all_edges(self, graph_id: str) -> List[EntityEdge]:
        """Delegate to ``ZepEntityReader.get_all_edges`` and reshape."""
        rows = self._reader.get_all_edges(graph_id)
        return [
            EntityEdge(
                uuid=row["uuid"],
                name=row["name"],
                fact=row["fact"],
                source_node_uuid=row["source_node_uuid"],
                target_node_uuid=row["target_node_uuid"],
                attributes=row["attributes"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Original 2-method read surface
    # ------------------------------------------------------------------

    def filter_defined_entities(
        self,
        graph_id: str,
        defined_entity_types: List[str],
        *,
        enrich_with_edges: bool = False,
    ) -> FilteredEntities:
        """Delegate to ``ZepEntityReader.filter_defined_entities``.

        Adapts the keyword-only ``enrich_with_edges`` Protocol signature
        onto the wrapper's positional default-True signature.
        """
        return self._reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=defined_entity_types,
            enrich_with_edges=enrich_with_edges,
        )

    def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str,
        *,
        enrich_with_edges: bool = False,
    ) -> List[EntityNode]:
        """Delegate to ``ZepEntityReader.get_entities_by_type``."""
        return self._reader.get_entities_by_type(
            graph_id=graph_id,
            entity_type=entity_type,
            enrich_with_edges=enrich_with_edges,
        )
