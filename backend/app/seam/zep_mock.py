"""Deterministic in-memory stand-in for the cloud Zep client.

Used by prepare and tests to exercise the entity-read
surface with zero network or LLM calls. Contract: same inputs produce
byte-identical outputs (uuid5 + sorted dicts); only the methods that
``simulation_runner`` and ``ZepEntityReader`` actually call are stubbed;
any un-stubbed attribute access raises :class:`MockZepNotImplemented`
(never a silent no-op). The factory selects between the mock,
``graphiti``, and ``experimental_cloud`` (deprecated) backends via
``ZEP_MODE`` (default ``mock``); the valid 3-mode ladder is
``{mock, graphiti, experimental_cloud}``.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

from ..policy.schema import Scenario
from ..services.zep_entity_reader import EntityEdge, EntityNode, FilteredEntities

if TYPE_CHECKING:
    # Forward-ref to keep app.seam.protocols importable without circulars.
    # IAudienceStore lives in app.seam.protocols and imports the same
    # EntityNode/FilteredEntities, so a direct import here would be a cycle.
    from .protocols import IAudienceStore, Episode, Ontology, SearchResults  # noqa: F401


# UUID5 namespace for the mock store. We use a fixed type-5 namespace derived
# from a stable string so two processes (test runner + Flask server) produce
# identical uuids for the same (scenario_hash, segment_id, name) triple. The
# uuid is itself uuid5(NAMESPACE_DNS, "rawfish.mock-zep.v1") so any change to
# the namespace string deliberately invalidates every existing fixture --
# bumping ``v1`` is a breaking change.
_MOCK_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "rawfish.mock-zep.v1")


def scenario_hash(scenario: Scenario) -> str:
    """Hex sha256 of the canonical Scenario JSON dump (``exclude={'run'}``).

    Why exclude ``run``? :class:`RunControls` carries ``run_seed`` /
    ``ensemble_seeds`` / ``persona_salts`` -- knobs that vary across runs of
    the *same* scenario by design. Including them would make every reseed
    yield a different uuid set, defeating determinism for re-prepared sims.

    ``mode='json'`` so enums serialise to their string values, ``by_alias=True``
    so the dump matches the canonical wire shape (seed_text not policy_text).
    ``sort_keys=True`` on json.dumps locks the byte order.
    """
    payload = scenario.model_dump(mode="json", by_alias=True, exclude={"run"})
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resolve_master_seed(scen_hash: str, explicit_seed: Optional[int]) -> int:
    """Resolve the master integer seed.

    Precedence: explicit ``seed`` kwarg > ``RAWFISH_RUN_SEED`` env >
    ``POLICY_RANDOM_SEED`` env > ``int(scen_hash[:16], 16)``. Non-int env
    values fall through rather than raising, so a stray ``=foo`` cannot
    500 a simulation.

    Public so start orchestration can derive ``RunMetadata.seed_hash``
    via the SAME precedence the mock store uses internally -- a single
    SSoT keeps the on-disk provenance string consistent with the seed the
    seam actually applies.
    """
    if explicit_seed is not None:
        return int(explicit_seed)
    for env_key in ("RAWFISH_RUN_SEED", "POLICY_RANDOM_SEED"):
        raw = os.environ.get(env_key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return int(scen_hash[:16], 16)


class MockZepNotImplemented(NotImplementedError):
    """Raised when the simulation reaches for a Zep API surface the mock has
    intentionally not stubbed. Surfacing this loudly is the contract -- see
    the ``__getattr__`` clause on :class:`MockZepStorage`."""

    def __init__(self, name: str) -> None:
        super().__init__(
            f"MockZepStorage has no stub for {name!r}; add an explicit method "
            "or switch to ZEP_MODE=graphiti / ZEP_MODE=experimental_cloud."
        )
        self.name = name


class MockZepStorage:
    """Deterministic in-memory replacement for ``zep_cloud.client.Zep``.

    The store is keyed by ``graph_id`` (which we treat as the simulation id
    during seeding). Entities are derived from ``Scenario.stakeholders`` in
    declaration order; uuids are uuid5-derived so two seedings with the same
    inputs produce identical bytes.
    """

    def __init__(
        self,
        deterministic_seed: Optional[int] = None,
        *,
        seed: Optional[int] = None,
    ) -> None:
        """Construct an empty mock store.

        ``seed`` is the explicit master-seed override; ``deterministic_seed``
        is an alternate constructor name accepted by older tests (passing both
        raises ``TypeError``). When neither is given, the seed is resolved
        lazily at :meth:`seed_from_scenario` time via
        :func:`resolve_master_seed`. Construction is side-effect-free
        because :func:`scenario_hash` needs a :class:`Scenario` that only
        arrives at seeding.
        """
        # Honour explicit ``seed=`` keyword; otherwise accept the legacy
        # positional name. Passing both is a programming error.
        if seed is not None and deterministic_seed is not None:
            raise TypeError(
                "MockZepStorage: pass exactly one of seed=/deterministic_seed"
            )
        self._explicit_seed: Optional[int] = seed if seed is not None else deterministic_seed
        # Populated at seed_from_scenario time; read-only afterwards.
        self.master_seed: Optional[int] = None
        self.seed_zep: Optional[bytes] = None
        self.scenario_hash: Optional[str] = None
        # graph_id -> ordered list[EntityNode]
        self._store: Dict[str, List[EntityNode]] = {}
        # Ingest+query surface state. All keyed by graph_id and seeded
        # off self.master_seed (resolved at seed_from_scenario time) so
        # determinism survives across process boundaries. Initialised here
        # to avoid mutable-default-arg footguns -- the Protocol methods
        # mutate these via setdefault, never via class-level state.
        self._ontologies: Dict[str, "Ontology"] = {}
        self._episodes: Dict[str, List["Episode"]] = {}
        # Entities/edges produced by add_episode (kept separate from the
        # seeded ._store so get_all_nodes can merge+dedup deterministically
        # without mutating the seed surface that ZepEntityReader-parity
        # methods consume).
        self._extracted_nodes: Dict[str, List[EntityNode]] = {}
        self._extracted_edges: Dict[str, List[EntityEdge]] = {}

    # ------------------------------------------------------------------
    # Mock-only seeding helper used by prepare.
    # ------------------------------------------------------------------

    def seed_from_scenario(self, simulation_id: str, scenario: Scenario) -> None:
        """Populate the store from a Scenario (mock-only).

        Idempotent on scenario content: entity uuids are uuid5-keyed on
        ``scenario_hash`` (not ``simulation_id``), so two sims of the same
        scenario share uuids -- the property downstream caches depend on.
        Iteration order matches ``scenario.stakeholders``.
        """
        scen_hash = scenario_hash(scenario)
        self.scenario_hash = scen_hash
        self.master_seed = resolve_master_seed(scen_hash, self._explicit_seed)
        # 8-byte derived sub-seed for the Zep surface specifically; future
        # noise-injecting variants will RNG-seed off this value.
        self.seed_zep = hashlib.sha256(
            f"{self.master_seed}:zep".encode("utf-8")
        ).digest()[:8]

        entities: List[EntityNode] = []
        for stakeholder in scenario.stakeholders:
            # uuid5 keyed on scenario_hash (NOT simulation_id) so identical
            # scenarios produce identical entity uuids across sim runs.
            ent_uuid = str(
                uuid.uuid5(
                    _MOCK_NAMESPACE,
                    f"{scen_hash}:{stakeholder.segment_id}:{stakeholder.name}",
                )
            )

            # Sorted priors dict for stable attribute ordering. Falls back to
            # an empty dict when the stakeholder has no audience_priors.
            if stakeholder.audience_priors is not None:
                priors_dump = stakeholder.audience_priors.model_dump()
                attributes: Dict[str, Any] = {
                    k: priors_dump[k] for k in sorted(priors_dump.keys())
                }
            else:
                attributes = {}

            entities.append(
                EntityNode(
                    uuid=ent_uuid,
                    name=stakeholder.name,
                    # ``segment.type`` is the segment-type label; the field
                    # on AudienceSegment is `.type`.
                    labels=[stakeholder.type],
                    summary=f"Stakeholder segment '{stakeholder.name}' "
                            f"(type={stakeholder.type})",
                    attributes=attributes,
                )
            )

        self._store[simulation_id] = entities

    # ------------------------------------------------------------------
    # Real ZepEntityReader surface (subset actually used downstream)
    # ------------------------------------------------------------------

    def filter_defined_entities(
        self,
        graph_id: str,
        defined_entity_types: List[str],
        enrich_with_edges: bool = False,
    ) -> FilteredEntities:
        """Deterministic stand-in for ``ZepEntityReader.filter_defined_entities``.

        Iteration order matches seeded stakeholder order. ``enrich_with_edges``
        is accepted for parity but the mock has no edges to enrich with — we
        return entities with empty ``related_edges``/``related_nodes``.
        """
        _ = enrich_with_edges  # accepted for ZepEntityReader parity
        all_entities = self._store.get(graph_id, [])
        total_count = len(all_entities)

        wanted = set(defined_entity_types or [])
        matched: List[EntityNode] = []
        types_found = set()

        for ent in all_entities:
            entity_type = ent.get_entity_type()
            if entity_type is None:
                continue
            if wanted and entity_type not in wanted:
                continue
            types_found.add(entity_type)
            matched.append(ent)

        return FilteredEntities(
            entities=matched,
            entity_types=types_found,
            total_count=total_count,
            filtered_count=len(matched),
        )

    def get_entities_by_type(
        self,
        graph_id: str,
        entity_type: str,
        enrich_with_edges: bool = False,
    ) -> List[EntityNode]:
        """Filter the in-memory store by a single label.

        Stable ordering by uuid (uuid5-derived). Empty list — never raises —
        if no entries match; unknown ``entity_type`` is treated as a valid
        zero-match query.
        """
        _ = enrich_with_edges  # accepted for ZepEntityReader parity
        matches = [
            ent
            for ent in self._store.get(graph_id, [])
            if entity_type in ent.labels
        ]
        matches.sort(key=lambda e: e.uuid)
        return matches

    # ------------------------------------------------------------------
    # Ingest + query surface (IAudienceStore widened contract)
    # ------------------------------------------------------------------

    def _seed_for(self, *parts: str) -> int:
        """Derive a deterministic 8-byte int from master_seed + parts.

        Falls back to ``self._explicit_seed or 0`` when ``master_seed`` is
        unset (i.e. ``seed_from_scenario`` was never called). The
        fall-back keeps the seam testable without forcing every caller
        to seed first, while still flagging the un-seeded state through
        a non-random ``0`` constant.
        """
        base = self.master_seed if self.master_seed is not None else (
            self._explicit_seed if self._explicit_seed is not None else 0
        )
        material = ":".join((str(base), *parts)).encode("utf-8")
        return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")

    def set_ontology(
        self,
        graph_id: str,
        ontology: "Ontology",
    ) -> None:
        """Store a per-graph ontology used by :meth:`add_episode` extraction.

        Idempotent overwrite: the last call wins. Stored verbatim (the
        Pydantic model is immutable enough for our purposes) so the
        ``entity_types`` keys remain the deterministic string-match
        vocabulary the deterministic string-match extractor uses.
        """
        self._ontologies[graph_id] = ontology

    def add_episode(
        self,
        graph_id: str,
        episode: "Episode",
    ) -> None:
        """Append an episode and do string-match entity extraction.

        Extraction algorithm:
          * Build the name-bag from (a) any prior ``set_ontology`` call's
            ``entity_types`` keys, plus (b) the seeded
            ``Scenario.stakeholders[].name`` already in ``self._store``.
          * Case-insensitive substring match against ``episode.content``;
            order of matches is alphabetical by name for determinism.
          * Entity uuids are ``uuid5(_MOCK_NAMESPACE, graph_id:name)`` so
            the same name across episodes maps to the same node.
          * If 2+ entities match the same episode, emit co-occurrence
            edges between consecutive pairs (deterministic since the
            extraction order is fixed). Edge uuids are
            ``uuid5(_MOCK_NAMESPACE, graph_id:episode.name:src:dst)`` so
            re-ingesting the same episode is idempotent.
        """
        self._episodes.setdefault(graph_id, []).append(episode)

        # Build the canonical name bag, sorted for determinism.
        name_bag: set[str] = set()
        onto = self._ontologies.get(graph_id)
        if onto is not None:
            name_bag.update(onto.entity_types.keys())
        for ent in self._store.get(graph_id, []):
            name_bag.add(ent.name)

        text_lower = episode.content.lower()
        matched_names = sorted(
            n for n in name_bag if n and n.lower() in text_lower
        )

        nodes_bucket = self._extracted_nodes.setdefault(graph_id, [])
        existing_node_uuids = {n.uuid for n in nodes_bucket}
        # Reuse seeded uuids when a matched name already exists in the
        # seed-surface store -- otherwise get_all_nodes would surface the
        # same logical entity twice (once seeded, once extracted) with
        # different uuids, which breaks the merge+dedup contract.
        seeded_by_name: Dict[str, EntityNode] = {
            n.name: n for n in self._store.get(graph_id, [])
        }
        matched_nodes: List[EntityNode] = []
        for name in matched_names:
            seeded = seeded_by_name.get(name)
            if seeded is not None:
                node_uuid = seeded.uuid
                labels: List[str] = list(seeded.labels)
            else:
                node_uuid = str(
                    uuid.uuid5(_MOCK_NAMESPACE, f"{graph_id}:{name}")
                )
                # Ontology-derived label takes precedence; fall back to
                # the generic 'Entity' tag if no ontology entry exists.
                if onto is not None and name in onto.entity_types:
                    labels = [name]
                else:
                    labels = ["Entity"]
            node = EntityNode(
                uuid=node_uuid,
                name=name,
                labels=labels,
                summary=(
                    f"Extracted from episode {episode.name!r} "
                    f"(source={episode.source})"
                ),
                attributes={"episode": episode.name},
            )
            matched_nodes.append(node)
            if node_uuid not in existing_node_uuids:
                nodes_bucket.append(node)
                existing_node_uuids.add(node_uuid)

        # Co-occurrence edges between consecutive matched pairs.
        edges_bucket = self._extracted_edges.setdefault(graph_id, [])
        existing_edge_uuids = {e.uuid for e in edges_bucket}
        for src, dst in zip(matched_nodes, matched_nodes[1:]):
            edge_uuid = str(
                uuid.uuid5(
                    _MOCK_NAMESPACE,
                    f"{graph_id}:{episode.name}:{src.uuid}:{dst.uuid}",
                )
            )
            if edge_uuid in existing_edge_uuids:
                continue
            edges_bucket.append(
                EntityEdge(
                    uuid=edge_uuid,
                    name="co_occurs_with",
                    fact=(
                        f"{src.name} co-occurs with {dst.name} in "
                        f"episode {episode.name!r}"
                    ),
                    source_node_uuid=src.uuid,
                    target_node_uuid=dst.uuid,
                    attributes={"episode": episode.name},
                )
            )
            existing_edge_uuids.add(edge_uuid)

    def search(
        self,
        graph_id: str,
        query: str,
        *,
        limit: int = 10,
    ) -> "SearchResults":
        """Return canned scored search results, ranked deterministically.

        Score per candidate node is
        ``sha256(master_seed:query:node.uuid)[:8]`` interpreted as a
        big-endian int; ranking is descending. Candidates are the union
        of seeded + extracted nodes for the graph (deduped by uuid).
        Edges are not searched but the matched-node neighbourhood
        edges are surfaced in rank-order of their source node.
        """
        # Local import to dodge the seam<->protocols circular at
        # module-import time. The TYPE_CHECKING import above keeps
        # pyright happy on the annotation.
        from .protocols import SearchResults

        candidates: List[EntityNode] = []
        seen_uuids: set[str] = set()
        for bucket in (
            self._store.get(graph_id, []),
            self._extracted_nodes.get(graph_id, []),
        ):
            for node in bucket:
                if node.uuid in seen_uuids:
                    continue
                seen_uuids.add(node.uuid)
                candidates.append(node)

        scored = sorted(
            candidates,
            key=lambda n: self._seed_for("search", query, n.uuid),
            reverse=True,
        )
        ranked_nodes = scored[: max(0, limit)]
        ranked_uuids = {n.uuid for n in ranked_nodes}
        matched_edges = [
            e
            for e in self._extracted_edges.get(graph_id, [])
            if e.source_node_uuid in ranked_uuids
        ]
        # Stable secondary order by uuid for edges so ties don't drift.
        matched_edges.sort(key=lambda e: e.uuid)
        return SearchResults(
            query=query,
            nodes=ranked_nodes,
            edges=matched_edges,
        )

    def get_all_nodes(
        self,
        graph_id: str,
    ) -> List[EntityNode]:
        """Return all nodes for ``graph_id`` (seeded + extracted), sorted by name.

        Mirrors real Zep semantics: every node ever materialised against
        the graph -- whether via ``seed_from_scenario`` or
        ``add_episode`` -- is returned. Dedup is by ``uuid``; sort is by
        ``(name, uuid)`` so two nodes with identical names still have a
        deterministic order.
        """
        merged: Dict[str, EntityNode] = {}
        for node in self._store.get(graph_id, []):
            merged.setdefault(node.uuid, node)
        for node in self._extracted_nodes.get(graph_id, []):
            merged.setdefault(node.uuid, node)
        return sorted(merged.values(), key=lambda n: (n.name, n.uuid))

    def get_all_edges(
        self,
        graph_id: str,
    ) -> List[EntityEdge]:
        """Return all extracted edges for ``graph_id``, sorted by (src, dst).

        Mock-mode has no seeded edges (``seed_from_scenario`` only
        materialises nodes), so the seeded ``._store`` contributes
        nothing here -- only ``add_episode``-derived co-occurrence
        edges. Sort key is ``(source_node_uuid, target_node_uuid, uuid)``
        for total ordering across ties.
        """
        edges = list(self._extracted_edges.get(graph_id, []))
        edges.sort(
            key=lambda e: (e.source_node_uuid, e.target_node_uuid, e.uuid)
        )
        return edges

    # ------------------------------------------------------------------
    # Loud-failure fallback
    # ------------------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        # ``__getattr__`` only fires for names NOT found via normal lookup,
        # so explicit methods above are unaffected. Dunder names are
        # excluded to keep copy/pickle/repr from blowing up.
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        raise MockZepNotImplemented(name)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def get_factory(cls) -> "IAudienceStore":
        """Return a ``MockZepStorage`` or a cloud-backed ``ZepEntityReader``.

        Selection is driven by ``ZEP_MODE`` (default ``mock``); the
        ``experimental_cloud`` client is imported lazily so mock-mode envs
        need no ``ZEP_API_KEY`` at import. Mode is strict-validated -- any
        value outside ``{mock, graphiti, experimental_cloud}`` (or the
        deprecated ``real`` alias) raises ``RuntimeError`` (no silent
        fallthrough). ``graphiti`` mode is reserved for the
        :class:`GraphitiAudienceStore`; it raises a clear ``RuntimeError``
        here until that store lands so a misconfigured boot surfaces loudly.
        """
        # ZEP_MODE parsing SSoT lives in app.utils.runtime_env.parse_zep_mode.
        # Catch InvalidZepMode and re-raise as RuntimeError so the seam's
        # contract (RuntimeError on bad mode) is preserved bit-for-bit.
        from ..utils.runtime_env import InvalidZepMode, parse_zep_mode
        try:
            mode = parse_zep_mode()
        except InvalidZepMode as exc:
            # Preserve the legacy RuntimeError wording bit-for-bit; pulls
            # the raw value off the exception attribute so we never re-read
            # os.environ outside the SSoT.
            raise RuntimeError(
                f"ZEP_MODE={exc.raw_value!r} is not supported; "
                f"expected one of 'mock', 'graphiti', 'experimental_cloud'."
            ) from exc
        if mode == "experimental_cloud":
            # ZepCloudShim wraps a ZepEntityReader and structurally
            # satisfies the 7-method IAudienceStore Protocol: the 2
            # read-surface methods delegate to the reader; the 3 V3-graph
            # methods (set_ontology / add_episode / search) raise
            # NotImplementedError with a clear migration message. The
            # cast is now legitimate -- isinstance(shim, IAudienceStore)
            # is True at runtime (see tests/seam/test_zep_cloud_shim.py).
            # The shim's __init__ reads ZEP_API_KEY via Config and raises
            # ValueError if missing -- pre-flight the env check here so
            # the contract (RuntimeError on misconfigured boot) holds.
            from .zep_cloud_shim import ZepCloudShim

            api_key = os.environ.get("ZEP_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "ZEP_MODE=experimental_cloud requires ZEP_API_KEY "
                    "in the environment."
                )
            return cast("IAudienceStore", ZepCloudShim(api_key=api_key))
        if mode == "graphiti":
            # GraphitiAudienceStore reads its Neo4j coordinates from
            # NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD and its LLM
            # coordinates from OPENAI_BASE_URL / OPENAI_API_KEY (any
            # OpenAI-compatible endpoint, including local Ollama / LM
            # Studio). Missing
            # NEO4J_URI is a misconfigured boot; raise loudly rather
            # than degrade to mock.
            neo4j_uri = os.environ.get("NEO4J_URI")
            if not neo4j_uri:
                raise RuntimeError(
                    "ZEP_MODE=graphiti requires NEO4J_URI in the "
                    "environment (e.g. bolt://localhost:7687 against the "
                    "neo4j-community service in backend/docker-compose.yml)."
                )
            neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
            neo4j_password = os.environ.get("NEO4J_PASSWORD", "")
            # Local-first failure-closed: refuse to silently route entity
            # extraction through OpenAI cloud. Refusal is hostname-based
            # via llm_endpoint_is_openai_cloud -- any URL whose hostname
            # resolves to api.openai.com is rejected regardless of scheme,
            # port, path (``/v1``, ``/v1/``, schemeless, mixed case all
            # refused), empty string also refused (defaults to OpenAI at
            # the library level). There is intentionally no cloud opt-in
            # through this factory; a user who needs OpenAI cloud must
            # use a corp-proxy URL exposing the OpenAI API under a
            # different hostname. The graphiti_store imports below are
            # local (not module-top) to keep the mock path importable on
            # machines without graphiti-core installed.
            from .graphiti_store import (
                GraphitiAudienceStore,
                GraphitiInitError,
                llm_endpoint_is_openai_cloud,
            )

            llm_base_url = os.environ.get("OPENAI_BASE_URL", "")
            if llm_endpoint_is_openai_cloud(llm_base_url):
                # Single source of truth for the refusal predicate lives in
                # graphiti_store.llm_endpoint_is_openai_cloud so this factory,
                # the constructor, and the test skip helpers cannot drift
                # apart. The constructor enforces the same rule as
                # defense-in-depth.
                raise GraphitiInitError(
                    "ZEP_MODE=graphiti requires an explicit local LLM "
                    "endpoint; set OPENAI_BASE_URL to your Ollama / "
                    "LM Studio / local OpenAI-compatible server (e.g. "
                    "http://localhost:11434/v1). The historical fallback "
                    "to api.openai.com was removed because it silently "
                    "routed entity extraction through OpenAI Cloud when "
                    "the user expected local-only operation."
                )
            llm_api_key = os.environ.get("OPENAI_API_KEY", "")

            # Model-name overrides (env-driven so operators can point
            # at an Ollama-served `nomic-embed-text` or LM Studio's
            # `qwen2.5:7b` without editing code). Unset env vars fall
            # through to the seam's None default, which in turn falls
            # through to upstream Graphiti's OpenAI-cloud defaults --
            # those work only against a cloud endpoint, so a real local
            # deployment MUST set at least OPENAI_EMBEDDING_MODEL and
            # OPENAI_MODEL to model names its local server actually
            # serves. The chat model knob is `OPENAI_MODEL` rather
            # than a Graphiti-specific name so it doubles as the model
            # the PolicyLLMClient consumes for the LLM-side report
            # generation when configured.
            llm_model = os.environ.get("OPENAI_MODEL") or None
            embedding_model = os.environ.get("OPENAI_EMBEDDING_MODEL") or None
            embedding_dim_raw = os.environ.get("OPENAI_EMBEDDING_DIM")
            embedding_dim: Optional[int] = None
            if embedding_dim_raw:
                try:
                    embedding_dim = int(embedding_dim_raw)
                except (TypeError, ValueError):
                    pass
            reranking_model = os.environ.get("OPENAI_RERANKING_MODEL") or None

            return cast(
                "IAudienceStore",
                GraphitiAudienceStore(
                    neo4j_uri,
                    neo4j_user,
                    neo4j_password,
                    llm_base_url=llm_base_url,
                    llm_api_key=llm_api_key,
                    llm_model=llm_model,
                    embedding_model=embedding_model,
                    embedding_dim=embedding_dim,
                    reranking_model=reranking_model,
                ),
            )
        # cast (not pyright-ignore): MockZepStorage will gain the 5 new
        # methods; the Protocol surface widened ahead of the
        # implementation per the gate ordering. Pinned by
        # tests/seam/test_protocols.py once the methods land.
        return cast("IAudienceStore", cls())
