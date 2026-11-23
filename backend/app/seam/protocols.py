"""Structural protocol for the audience-store seam.

Lives in its own module so the real ``zep_cloud.client.Zep`` is never
pulled through a mock import during type checks.
:class:`IAudienceStore` is the 7-method contract: the original two
read-surface methods (``filter_defined_entities`` /
``get_entities_by_type``) plus the five ingest+query methods
(``set_ontology``, ``add_episode``, ``search``, ``get_all_nodes``,
``get_all_edges``) that ``GraphitiAudienceStore`` and the
``experimental_cloud`` shim both expose.

Both :class:`app.seam.zep_mock.MockZepStorage` and
:class:`app.seam.graphiti_store.GraphitiAudienceStore` satisfy this
Protocol structurally; the bare ``zep_cloud.client.Zep`` does NOT --
``experimental_cloud`` runs through a thin shim that re-uses the
upstream ``ZepEntityReader``. ``@runtime_checkable`` enables
``isinstance`` dispatch in the factory.

Mock-only helpers (``seed_from_scenario``, ``_store``) stay off the
Protocol; ``tests/seam/test_protocols.py`` pins that exclusion.
Keyword-only kwargs match ``ZepEntityReader.filter_defined_entities`` /
``get_entities_by_type`` verbatim and follow the repo convention of
kw-only args on Protocol methods.
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..services.zep_entity_reader import EntityEdge, EntityNode, FilteredEntities


class Ontology(BaseModel):
    """Entity-type ontology supplied to a graph before episode ingest.

    The shape is the intersection of Graphiti's ``entity_types`` argument
    and Zep Cloud's ``set_entity_types`` payload: a mapping from a
    user-defined type name to a free-form JSON schema fragment
    describing that type's attributes. Extra keys are tolerated so the
    Graphiti backend can pass through any backend-specific knobs
    (e.g. ``edge_types``) without expanding this surface.
    """

    model_config = ConfigDict(extra="allow")

    # See Episode for the rationale on ``= Field(...)`` vs Annotated[Field].
    entity_types: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Map from custom type name (e.g. 'Stakeholder') to the "
            "JSON-schema fragment describing its attributes."
        ),
    )


class Episode(BaseModel):
    """A single unit of content ingested into the temporal graph.

    Models the intersection of Graphiti's ``add_episode`` and Zep
    Cloud's ``graph.add`` ingest primitives. ``content`` is the raw
    text/JSON the LLM-extractor processes; ``source`` and
    ``source_description`` mirror Graphiti's ``EpisodeType`` / source
    metadata; ``reference_time`` is the temporal anchor used by both
    backends for "what was true at time T" queries.
    """

    model_config = ConfigDict(extra="allow")

    name: Annotated[str, Field(description="Stable episode identifier.")]
    content: Annotated[
        str,
        Field(description="Raw text or JSON payload to extract entities from."),
    ]
    # Pyright (basic mode) does not recognise ``Annotated[T, Field(default=...)]``
    # as supplying a default at the class-attribute level -- it only inspects
    # PEP-526 assignment defaults. Pydantic v2 accepts both forms; we use the
    # explicit ``= Field(...)`` form here so MockZepStorage tests (and any
    # other Episode() call site) can omit these optional kwargs without a
    # reportCallIssue.  The description still flows through the Field.
    source: str = Field(
        default="text",
        description=(
            "Episode source-type discriminator: 'text', 'json', or "
            "'message'. Matches Graphiti's ``EpisodeType`` strings."
        ),
    )
    source_description: str = Field(
        default="",
        description="Free-form provenance label propagated to nodes/edges.",
    )
    reference_time: Optional[str] = Field(
        default=None,
        description=(
            "ISO-8601 timestamp the backend uses as the episode's "
            "valid-time anchor. ``None`` means 'use ingest time'."
        ),
    )


class SearchResults(BaseModel):
    """Return value of ``IAudienceStore.search``.

    Lightweight envelope: ``nodes`` and ``edges`` are the matched
    entities/edges in rank order; ``query`` echoes the request for
    debuggability. The seam intentionally does NOT surface per-result
    reranker scores -- ``GraphitiAudienceStore`` and the cloud shim
    score on incompatible scales, and downstream consumers treat the
    list as already-ranked.
    """

    model_config = ConfigDict(extra="allow")

    query: Annotated[str, Field(description="The query that produced these results.")]
    nodes: List[EntityNode] = Field(
        default_factory=list, description="Matched entity nodes, rank order."
    )
    edges: List[EntityEdge] = Field(
        default_factory=list, description="Matched edges, rank order."
    )


@runtime_checkable
class IAudienceStore(Protocol):
    """7-method IAudienceStore contract.

    Satisfied by :class:`app.seam.zep_mock.MockZepStorage` and
    :class:`app.seam.graphiti_store.GraphitiAudienceStore`. The
    ``experimental_cloud`` mode wraps ``zep_cloud.client.Zep`` through a
    thin shim because the bare client does not expose the reader-style
    methods directly.
    """

    # --- Ingest / mutation ------------------------------------------------

    def set_ontology(
        self,
        graph_id: str,
        ontology: Ontology,
    ) -> None: ...

    def add_episode(
        self,
        graph_id: str,
        episode: Episode,
    ) -> None: ...

    # --- Query ------------------------------------------------------------

    def search(
        self,
        graph_id: str,
        query: str,
        *,
        limit: int = 10,
    ) -> SearchResults: ...

    def get_all_nodes(
        self,
        graph_id: str,
    ) -> List[EntityNode]: ...

    def get_all_edges(
        self,
        graph_id: str,
    ) -> List[EntityEdge]: ...

    # --- Original 2-method read surface -----------------------------------

    def filter_defined_entities(
        self,
        graph_id: str,
        defined_entity_types: List[str],
        *,
        enrich_with_edges: bool = False,
    ) -> FilteredEntities: ...

    def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str,
        *,
        enrich_with_edges: bool = False,
    ) -> List[EntityNode]: ...


@runtime_checkable
class IAsyncAudienceStore(Protocol):
    """Async dual-port mirror of :class:`IAudienceStore`.

    Same 7-method contract, ``async def`` everywhere. Adapters can
    satisfy ONE or BOTH ports:

      * A Graphiti-native async backend (``GraphitiAudienceStore`` is
        async-first; today its sync methods bridge through ``_run`` /
        fresh event loop) can implement this Protocol directly so a
        future async-capable caller bypasses the per-call event-loop
        spin and uses the native driver.
      * A sync-only store (``MockZepStorage``,
        ``experimental_cloud`` shim) is wrapped through
        :class:`SyncToAsyncAudienceStoreAdapter` (added alongside) so
        the async port is satisfied without rewriting the underlying
        store. The wrapper dispatches sync calls to a threadpool so
        the event loop is not blocked even when the underlying store
        is synchronous.

    Dual-port introduction is an additive seam: every existing
    consumer keeps using :class:`IAudienceStore` unchanged. New
    consumers (the async lane lifecycle Protocol, the interview
    endpoint that streams a long IPC response) opt into the async
    port when they can usefully release the request thread.

    ``@runtime_checkable`` so the audience-store factory can
    ``isinstance``-dispatch when a caller asks for the async port.
    """

    # --- Ingest / mutation ------------------------------------------------

    async def set_ontology(
        self,
        graph_id: str,
        ontology: Ontology,
    ) -> None: ...

    async def add_episode(
        self,
        graph_id: str,
        episode: Episode,
    ) -> None: ...

    # --- Query ------------------------------------------------------------

    async def search(
        self,
        graph_id: str,
        query: str,
        *,
        limit: int = 10,
    ) -> SearchResults: ...

    async def get_all_nodes(
        self,
        graph_id: str,
    ) -> List[EntityNode]: ...

    async def get_all_edges(
        self,
        graph_id: str,
    ) -> List[EntityEdge]: ...

    # --- Original 2-method read surface -----------------------------------

    async def filter_defined_entities(
        self,
        graph_id: str,
        defined_entity_types: List[str],
        *,
        enrich_with_edges: bool = False,
    ) -> FilteredEntities: ...

    async def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str,
        *,
        enrich_with_edges: bool = False,
    ) -> List[EntityNode]: ...


class SyncToAsyncAudienceStoreAdapter:
    """Wrap an :class:`IAudienceStore` so it satisfies
    :class:`IAsyncAudienceStore`.

    Each async method dispatches the wrapped sync call to a
    thread-executor via :func:`asyncio.to_thread`. This is the
    "good enough" bridge for stores that are genuinely synchronous
    underneath (``MockZepStorage`` -- a Python dict; the
    ``experimental_cloud`` shim -- which calls a blocking Zep Cloud
    SDK). The bridge releases the EVENT LOOP while the underlying
    sync work runs, so a coroutine that ``await``s this wrapper can
    yield to other tasks on the same loop. Caveat: in a sync
    Flask/Gunicorn route that drives the wrapper via
    :func:`asyncio.run`, the request thread itself stays blocked
    until the coroutine resolves -- ``asyncio.to_thread`` only buys
    intra-event-loop concurrency, not Gunicorn-worker concurrency.
    The concurrency win materialises under an ASGI route or an
    async background-job worker that calls ``await wrapper.search(...)``
    directly.

    For an async-first store like ``GraphitiAudienceStore``, prefer
    implementing :class:`IAsyncAudienceStore` directly: the native
    driver is async, so the threadpool bounce is unnecessary
    overhead. The wrapper is offered for stores that have no
    natural async path.

    The wrapper is intentionally MINIMAL -- no batching, no caching,
    no fanout. Those are concerns for higher layers (the lane's
    request handler or a future read-aggregation seam).
    """

    def __init__(self, sync_store: IAudienceStore) -> None:
        self._sync = sync_store

    async def set_ontology(
        self,
        graph_id: str,
        ontology: Ontology,
    ) -> None:
        import asyncio
        await asyncio.to_thread(self._sync.set_ontology, graph_id, ontology)

    async def add_episode(
        self,
        graph_id: str,
        episode: Episode,
    ) -> None:
        import asyncio
        await asyncio.to_thread(self._sync.add_episode, graph_id, episode)

    async def search(
        self,
        graph_id: str,
        query: str,
        *,
        limit: int = 10,
    ) -> SearchResults:
        import asyncio
        return await asyncio.to_thread(
            self._sync.search, graph_id, query, limit=limit,
        )

    async def get_all_nodes(
        self,
        graph_id: str,
    ) -> List[EntityNode]:
        import asyncio
        return await asyncio.to_thread(self._sync.get_all_nodes, graph_id)

    async def get_all_edges(
        self,
        graph_id: str,
    ) -> List[EntityEdge]:
        import asyncio
        return await asyncio.to_thread(self._sync.get_all_edges, graph_id)

    async def filter_defined_entities(
        self,
        graph_id: str,
        defined_entity_types: List[str],
        *,
        enrich_with_edges: bool = False,
    ) -> FilteredEntities:
        import asyncio
        return await asyncio.to_thread(
            self._sync.filter_defined_entities,
            graph_id,
            defined_entity_types,
            enrich_with_edges=enrich_with_edges,
        )

    async def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str,
        *,
        enrich_with_edges: bool = False,
    ) -> List[EntityNode]:
        import asyncio
        return await asyncio.to_thread(
            self._sync.get_entities_by_type,
            graph_id,
            entity_type,
            enrich_with_edges=enrich_with_edges,
        )
