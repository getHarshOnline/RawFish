"""``IAsyncAudienceStore`` dual-port contract + sync-to-async adapter.

The audience-store seam grew an async port alongside the sync one
so:

  * a Graphiti-native async backend can implement
    :class:`IAsyncAudienceStore` directly and bypass the per-call
    event-loop spin its sync methods do via ``_run``;
  * a sync-only store (``MockZepStorage``, ``experimental_cloud``
    shim) can be wrapped through
    :class:`SyncToAsyncAudienceStoreAdapter` so callers that need an
    async surface receive one even when the underlying store is
    blocking.

This pin asserts the additive contract:

  1. The async Protocol exists, ``@runtime_checkable``, with all 7
     methods declared ``async def`` (mirrors :class:`IAudienceStore`).
  2. The :class:`SyncToAsyncAudienceStoreAdapter` is structurally
     compatible (each method is ``async def``); wrapping any sync
     :class:`IAudienceStore` produces a value that satisfies the
     async Protocol.
  3. Calling a wrapped method awaits the underlying sync call and
     returns the same result. Without the adapter the underlying
     store would block the event loop; the wrapper's behavior is
     correct in either case because ``asyncio.to_thread`` runs the
     sync call on a worker thread.
"""
from __future__ import annotations

import asyncio
import inspect

from app.seam.protocols import (
    IAsyncAudienceStore,
    SearchResults,
    SyncToAsyncAudienceStoreAdapter,
)


class _FakeSyncStore:
    """Minimal sync store that records calls for assertion."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def set_ontology(self, graph_id, ontology):
        self.calls.append(("set_ontology", graph_id, ontology))

    def add_episode(self, graph_id, episode):
        self.calls.append(("add_episode", graph_id, episode))

    def search(self, graph_id, query, *, limit=10):
        self.calls.append(("search", graph_id, query, limit))
        return SearchResults(query=query, nodes=[], edges=[])

    def get_all_nodes(self, graph_id):
        self.calls.append(("get_all_nodes", graph_id))
        return []

    def get_all_edges(self, graph_id):
        self.calls.append(("get_all_edges", graph_id))
        return []

    def filter_defined_entities(
        self, graph_id, defined_entity_types, *, enrich_with_edges=False,
    ):
        self.calls.append((
            "filter_defined_entities", graph_id, defined_entity_types,
            enrich_with_edges,
        ))
        return {"entities_by_type": {}, "entity_counts": {}}

    def get_entities_by_type(
        self, graph_id, entity_type, *, enrich_with_edges=False,
    ):
        self.calls.append((
            "get_entities_by_type", graph_id, entity_type, enrich_with_edges,
        ))
        return []


def test_async_audience_store_protocol_declares_all_seven_methods_async():
    methods = {
        "set_ontology",
        "add_episode",
        "search",
        "get_all_nodes",
        "get_all_edges",
        "filter_defined_entities",
        "get_entities_by_type",
    }
    for name in methods:
        member = getattr(IAsyncAudienceStore, name)
        assert inspect.iscoroutinefunction(member), (
            f"IAsyncAudienceStore.{name} must be ``async def`` "
            "(the dual-port contract mirrors IAudienceStore with "
            "every method awaitable)"
        )


def test_async_protocol_is_runtime_checkable():
    """``isinstance`` dispatch must be available so the audience-store
    factory can ``isinstance``-branch on which port a backend
    supports. Python's ``@runtime_checkable`` is structural by method
    NAME ONLY (it does not distinguish sync vs async), so a sync
    store's name-set matches the async port -- this pin only
    asserts that the Protocol IS runtime-checkable (i.e. an
    ``isinstance`` call does not raise ``TypeError``). The actual
    sync/async distinction is enforced by the
    :func:`inspect.iscoroutinefunction` per-method check landing
    in the test above."""
    sync_store = _FakeSyncStore()
    # No raise -> Protocol is runtime-checkable. Whether the sync
    # store satisfies it structurally is a Python-level convention
    # we cannot tighten without abandoning ``@runtime_checkable``.
    isinstance(sync_store, IAsyncAudienceStore)  # type: ignore[arg-type]


def test_sync_to_async_adapter_satisfies_async_protocol():
    adapter = SyncToAsyncAudienceStoreAdapter(_FakeSyncStore())  # type: ignore[arg-type]  # fake duck-types Protocol for hermetic test
    assert isinstance(adapter, IAsyncAudienceStore)


def test_sync_to_async_adapter_dispatches_search_to_underlying_store():
    sync_store = _FakeSyncStore()
    adapter = SyncToAsyncAudienceStoreAdapter(sync_store)  # type: ignore[arg-type]  # fake duck-types Protocol for hermetic test

    async def _exercise():
        return await adapter.search("g", "q", limit=3)

    result = asyncio.run(_exercise())
    assert isinstance(result, SearchResults)
    assert sync_store.calls == [("search", "g", "q", 3)]


def test_sync_to_async_adapter_passes_kwargs_through():
    sync_store = _FakeSyncStore()
    adapter = SyncToAsyncAudienceStoreAdapter(sync_store)  # type: ignore[arg-type]  # fake duck-types Protocol for hermetic test

    async def _exercise():
        return await adapter.filter_defined_entities(
            "g", ["Persona"], enrich_with_edges=True,
        )

    asyncio.run(_exercise())
    assert sync_store.calls == [
        ("filter_defined_entities", "g", ["Persona"], True),
    ]


def test_sync_to_async_adapter_methods_return_coroutines():
    """Every method on the adapter returns a coroutine (i.e. the
    wrapper is awaitable end-to-end). This is the stronger contract
    than a name-set ``isinstance`` check (Python's
    ``@runtime_checkable`` does not distinguish sync/async); a
    caller that forgets ``await`` will see ``RuntimeWarning:
    coroutine was never awaited`` at runtime.

    Note that ``isinstance(adapter, IAudienceStore)`` would *also*
    return True under Python's name-set semantics (Python does not
    check return-type-vs-coroutine), so the type system is NOT the
    boundary here -- the convention is: the adapter is documented as
    async-port-only, and the test above pins that every method is
    a coroutine function.
    """
    adapter = SyncToAsyncAudienceStoreAdapter(_FakeSyncStore())  # type: ignore[arg-type]  # fake duck-types Protocol for hermetic test
    methods = (
        "set_ontology", "add_episode", "search", "get_all_nodes",
        "get_all_edges", "filter_defined_entities", "get_entities_by_type",
    )
    for name in methods:
        member = getattr(adapter, name)
        assert inspect.iscoroutinefunction(member), (
            f"SyncToAsyncAudienceStoreAdapter.{name} must be ``async def``"
        )
