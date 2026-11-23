"""
Zep entity reading and filtering service
Reads nodes from a Zep graph and filters those matching predefined entity types
"""

import time
from typing import Dict, Any, List, Optional, Set, Callable, TypeVar
from dataclasses import dataclass, field

from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges

logger = get_logger('rawfish.zep_entity_reader')

# Generic return type variable
T = TypeVar('T')


@dataclass
class EntityNode:
    """Data structure for an entity node"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    # Information about related edges
    related_edges: List[Dict[str, Any]] = field(default_factory=list)
    # Information about other related nodes
    related_nodes: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes,
            "related_edges": self.related_edges,
            "related_nodes": self.related_nodes,
        }
    
    def get_entity_type(self) -> Optional[str]:
        """Get the entity type (excluding the default Entity label)"""
        for label in self.labels:
            if label not in ["Entity", "Node"]:
                return label
        return None


@dataclass
class EntityEdge:
    """Data structure for a directed edge between two entity nodes.

    Mirrors the ``EntityNode`` dataclass style so the seam's
    ``get_all_edges`` return type is a homogeneous list with a stable
    shape across backends (``MockZepStorage``, ``GraphitiAudienceStore``,
    and the ``experimental_cloud`` shim). The fields chosen here are the
    intersection of what Graphiti's edge model and Zep Cloud's
    ``RawEdge`` both reliably expose -- richer per-backend fields land in
    ``attributes`` rather than expanding this surface ad hoc.
    """
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "fact": self.fact,
            "source_node_uuid": self.source_node_uuid,
            "target_node_uuid": self.target_node_uuid,
            "attributes": self.attributes,
        }


@dataclass
class FilteredEntities:
    """Collection of filtered entities"""
    entities: List[EntityNode]
    entity_types: Set[str]
    total_count: int
    filtered_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "entity_types": list(self.entity_types),
            "total_count": self.total_count,
            "filtered_count": self.filtered_count,
        }


class ZepEntityReader:
    """
    Zep entity reading and filtering service

    Main features:
    1. Read all nodes from a Zep graph
    2. Filter nodes that match predefined entity types (nodes whose Labels are not only "Entity")
    3. Retrieve related edges and connected node information for each entity
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY is not configured")
        
        self.client = Zep(api_key=self.api_key)
    
    def _call_with_retry(
        self, 
        func: Callable[[], T], 
        operation_name: str,
        max_retries: int = 3,
        initial_delay: float = 2.0
    ) -> T:
        """
        Zep API call with retry logic

        Args:
            func: The function to execute (a no-argument lambda or callable)
            operation_name: Operation name, used in log messages
            max_retries: Maximum number of attempts (default 3, i.e. up to 3 attempts)
            initial_delay: Initial delay in seconds

        Returns:
            The API call result
        """
        last_exception = None
        delay = initial_delay
        
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Zep {operation_name} attempt {attempt + 1} failed: {str(e)[:100]}, "
                        f"retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    delay *= 2  # exponential backoff
                else:
                    logger.error(f"Zep {operation_name} still failed after {max_retries} attempts: {str(e)}")

        assert last_exception is not None, (
            "ZepEntityReader retry loop exhausted without capturing an "
            "exception — reachable only when max_retries == 0."
        )
        raise last_exception
    
    def get_all_nodes(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        Fetch all nodes of a graph (paginated)

        Args:
            graph_id: Graph ID

        Returns:
            List of nodes
        """
        logger.info(f"Fetching all nodes for graph {graph_id}...")

        nodes = fetch_all_nodes(self.client, graph_id)

        nodes_data = []
        for node in nodes:
            nodes_data.append({
                "uuid": getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                "name": node.name or "",
                "labels": node.labels or [],
                "summary": node.summary or "",
                "attributes": node.attributes or {},
            })

        logger.info(f"Fetched {len(nodes_data)} nodes in total")
        return nodes_data

    def get_all_edges(self, graph_id: str) -> List[Dict[str, Any]]:
        """
        Fetch all edges of a graph (paginated)

        Args:
            graph_id: Graph ID

        Returns:
            List of edges
        """
        logger.info(f"Fetching all edges for graph {graph_id}...")

        edges = fetch_all_edges(self.client, graph_id)

        edges_data = []
        for edge in edges:
            edges_data.append({
                "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                "name": edge.name or "",
                "fact": edge.fact or "",
                "source_node_uuid": edge.source_node_uuid,
                "target_node_uuid": edge.target_node_uuid,
                "attributes": edge.attributes or {},
            })

        logger.info(f"Fetched {len(edges_data)} edges in total")
        return edges_data
    
    def get_node_edges(self, node_uuid: str) -> List[Dict[str, Any]]:
        """
        Fetch all related edges for a given node (with retry logic)

        Args:
            node_uuid: Node UUID

        Returns:
            List of edges
        """
        try:
            # Call the Zep API with retry logic
            # The zep_cloud SDK stub omits NodeClient.get_entity_edges from
            # its type signatures, but the method is part of the documented
            # V2 API surface (https://help.getzep.com/api-reference/graph/node).
            # Reach for it via getattr so static analysis sees a single,
            # documented dynamic dispatch instead of a hidden attribute access.
            _node_client = self.client.graph.node
            _get_edges = getattr(_node_client, "get_entity_edges", None)
            if _get_edges is None:  # pragma: no cover — SDK regression
                raise AttributeError(
                    "zep_cloud NodeClient is missing get_entity_edges; the "
                    "installed zep-cloud version may not match the V2 API."
                )
            edges = self._call_with_retry(
                func=lambda: _get_edges(node_uuid=node_uuid),
                operation_name=f"get_node_edges(node={node_uuid[:8]}...)"
            )
            
            edges_data = []
            for edge in edges:
                edges_data.append({
                    "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                    "name": edge.name or "",
                    "fact": edge.fact or "",
                    "source_node_uuid": edge.source_node_uuid,
                    "target_node_uuid": edge.target_node_uuid,
                    "attributes": edge.attributes or {},
                })
            
            return edges_data
        except Exception as e:
            logger.warning(f"Failed to fetch edges for node {node_uuid}: {str(e)}")
            return []
    
    def filter_defined_entities(
        self, 
        graph_id: str,
        defined_entity_types: Optional[List[str]] = None,
        enrich_with_edges: bool = True
    ) -> FilteredEntities:
        """
        Filter nodes that match predefined entity types

        Filtering rules:
        - If a node's Labels only contain "Entity", the entity does not match any
          predefined type and is skipped
        - If a node's Labels contain a label other than "Entity" or "Node", the entity
          matches a predefined type and is kept

        Args:
            graph_id: Graph ID
            defined_entity_types: List of predefined entity types (optional; if provided,
                only these types are kept)
            enrich_with_edges: Whether to fetch related edge information for each entity

        Returns:
            FilteredEntities: The collection of filtered entities
        """
        logger.info(f"Starting entity filtering for graph {graph_id}...")

        # Fetch all nodes
        all_nodes = self.get_all_nodes(graph_id)
        total_count = len(all_nodes)

        # Fetch all edges (used later for relationship lookups)
        all_edges = self.get_all_edges(graph_id) if enrich_with_edges else []

        # Build a mapping from node UUID to node data
        node_map = {n["uuid"]: n for n in all_nodes}

        # Filter matching entities
        filtered_entities = []
        entity_types_found = set()

        for node in all_nodes:
            labels = node.get("labels", [])

            # Filter rule: Labels must include at least one label other than "Entity" or "Node"
            custom_labels = [l for l in labels if l not in ["Entity", "Node"]]

            if not custom_labels:
                # Only default labels, skip
                continue

            # If predefined types were specified, check for a match
            if defined_entity_types:
                matching_labels = [l for l in custom_labels if l in defined_entity_types]
                if not matching_labels:
                    continue
                entity_type = matching_labels[0]
            else:
                entity_type = custom_labels[0]
            
            entity_types_found.add(entity_type)

            # Create the entity node object
            entity = EntityNode(
                uuid=node["uuid"],
                name=node["name"],
                labels=labels,
                summary=node["summary"],
                attributes=node["attributes"],
            )
            
            # Fetch related edges and nodes
            if enrich_with_edges:
                related_edges = []
                related_node_uuids = set()
                
                for edge in all_edges:
                    if edge["source_node_uuid"] == node["uuid"]:
                        related_edges.append({
                            "direction": "outgoing",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "target_node_uuid": edge["target_node_uuid"],
                        })
                        related_node_uuids.add(edge["target_node_uuid"])
                    elif edge["target_node_uuid"] == node["uuid"]:
                        related_edges.append({
                            "direction": "incoming",
                            "edge_name": edge["name"],
                            "fact": edge["fact"],
                            "source_node_uuid": edge["source_node_uuid"],
                        })
                        related_node_uuids.add(edge["source_node_uuid"])
                
                entity.related_edges = related_edges

                # Fetch basic information about related nodes
                related_nodes = []
                for related_uuid in related_node_uuids:
                    if related_uuid in node_map:
                        related_node = node_map[related_uuid]
                        related_nodes.append({
                            "uuid": related_node["uuid"],
                            "name": related_node["name"],
                            "labels": related_node["labels"],
                            "summary": related_node.get("summary", ""),
                        })
                
                entity.related_nodes = related_nodes
            
            filtered_entities.append(entity)
        
        logger.info(f"Filtering complete: total nodes {total_count}, matched {len(filtered_entities)}, "
                   f"entity types: {entity_types_found}")
        
        return FilteredEntities(
            entities=filtered_entities,
            entity_types=entity_types_found,
            total_count=total_count,
            filtered_count=len(filtered_entities),
        )
    
    def get_entity_with_context(
        self, 
        graph_id: str, 
        entity_uuid: str
    ) -> Optional[EntityNode]:
        """
        Fetch a single entity together with its full context (edges and related nodes, with retry logic)

        Args:
            graph_id: Graph ID
            entity_uuid: Entity UUID

        Returns:
            EntityNode or None
        """
        try:
            # Fetch the node with retry logic
            node = self._call_with_retry(
                func=lambda: self.client.graph.node.get(uuid_=entity_uuid),
                operation_name=f"get_node_detail(uuid={entity_uuid[:8]}...)"
            )

            if not node:
                return None

            # Fetch edges for the node
            edges = self.get_node_edges(entity_uuid)

            # Fetch all nodes for relationship lookups
            all_nodes = self.get_all_nodes(graph_id)
            node_map = {n["uuid"]: n for n in all_nodes}

            # Process related edges and nodes
            related_edges = []
            related_node_uuids = set()
            
            for edge in edges:
                if edge["source_node_uuid"] == entity_uuid:
                    related_edges.append({
                        "direction": "outgoing",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "target_node_uuid": edge["target_node_uuid"],
                    })
                    related_node_uuids.add(edge["target_node_uuid"])
                else:
                    related_edges.append({
                        "direction": "incoming",
                        "edge_name": edge["name"],
                        "fact": edge["fact"],
                        "source_node_uuid": edge["source_node_uuid"],
                    })
                    related_node_uuids.add(edge["source_node_uuid"])
            
            # Fetch related node information
            related_nodes = []
            for related_uuid in related_node_uuids:
                if related_uuid in node_map:
                    related_node = node_map[related_uuid]
                    related_nodes.append({
                        "uuid": related_node["uuid"],
                        "name": related_node["name"],
                        "labels": related_node["labels"],
                        "summary": related_node.get("summary", ""),
                    })
            
            return EntityNode(
                uuid=getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {},
                related_edges=related_edges,
                related_nodes=related_nodes,
            )
            
        except Exception as e:
            logger.error(f"Failed to fetch entity {entity_uuid}: {str(e)}")
            return None
    
    def get_entities_by_type(
        self, 
        graph_id: str, 
        entity_type: str,
        enrich_with_edges: bool = True
    ) -> List[EntityNode]:
        """
        Fetch all entities of the specified type

        Args:
            graph_id: Graph ID
            entity_type: Entity type (e.g. "Student", "PublicFigure", etc.)
            enrich_with_edges: Whether to fetch related edge information

        Returns:
            List of entities
        """
        result = self.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=[entity_type],
            enrich_with_edges=enrich_with_edges
        )
        return result.entities


