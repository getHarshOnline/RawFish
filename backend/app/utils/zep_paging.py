"""Zep Graph paginated reader utilities.

Zep's node/edge list endpoints use UUID cursor pagination.
This module encapsulates the auto-pagination logic (with per-page retry)
and transparently returns the complete list to callers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from zep_cloud import InternalServerError
from zep_cloud.client import Zep

from .logger import get_logger
from .retry_policy import RetryPolicy, run_with_retry

logger = get_logger('rawfish.zep_paging')

_DEFAULT_PAGE_SIZE = 100
_MAX_NODES = 2000
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RETRY_DELAY = 2.0  # seconds, doubles each retry

# Only transient network/IO errors are retried — application-level Zep
# errors (e.g. NotFound, BadRequest) propagate immediately.
_PAGING_RETRY_EXCEPTIONS = (ConnectionError, TimeoutError, OSError, InternalServerError)


def _fetch_page_with_retry(
    api_call: Callable[..., list[Any]],
    *args: Any,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    retry_delay: float = _DEFAULT_RETRY_DELAY,
    page_description: str = "page",
    **kwargs: Any,
) -> list[Any]:
    """Request a single page, retrying with exponential backoff on failure. Only transient network/IO errors are retried."""
    if max_retries < 1:
        raise ValueError("max_retries must be >= 1")

    policy = RetryPolicy(
        max_attempts=max_retries,
        base_delay_s=retry_delay,
        backoff_multiplier=2.0,
        max_delay_s=None,
        jitter=False,
        retry_on=_PAGING_RETRY_EXCEPTIONS,
    )

    def _on_retry(exc: BaseException, attempt: int, delay: float) -> None:
        logger.warning(
            f"Zep {page_description} attempt {attempt} failed: {str(exc)[:100]}, retrying in {delay:.1f}s..."
        )

    try:
        return run_with_retry(
            policy,
            lambda: api_call(*args, **kwargs),
            on_retry=_on_retry,
        )
    except _PAGING_RETRY_EXCEPTIONS as exc:
        logger.error(f"Zep {page_description} failed after {max_retries} attempts: {str(exc)}")
        raise


def fetch_all_nodes(
    client: Zep,
    graph_id: str,
    page_size: int = _DEFAULT_PAGE_SIZE,
    max_items: int = _MAX_NODES,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    retry_delay: float = _DEFAULT_RETRY_DELAY,
) -> list[Any]:
    """Fetch graph nodes with pagination, returning at most max_items entries (default 2000). Each page request has its own retry."""
    all_nodes: list[Any] = []
    cursor: str | None = None
    page_num = 0

    while True:
        kwargs: dict[str, Any] = {"limit": page_size}
        if cursor is not None:
            kwargs["uuid_cursor"] = cursor

        page_num += 1
        batch = _fetch_page_with_retry(
            client.graph.node.get_by_graph_id,
            graph_id,
            max_retries=max_retries,
            retry_delay=retry_delay,
            page_description=f"fetch nodes page {page_num} (graph={graph_id})",
            **kwargs,
        )
        if not batch:
            break

        all_nodes.extend(batch)
        if len(all_nodes) >= max_items:
            all_nodes = all_nodes[:max_items]
            logger.warning(f"Node count reached limit ({max_items}), stopping pagination for graph {graph_id}")
            break
        if len(batch) < page_size:
            break

        cursor = getattr(batch[-1], "uuid_", None) or getattr(batch[-1], "uuid", None)
        if cursor is None:
            logger.warning(f"Node missing uuid field, stopping pagination at {len(all_nodes)} nodes")
            break

    return all_nodes


def fetch_all_edges(
    client: Zep,
    graph_id: str,
    page_size: int = _DEFAULT_PAGE_SIZE,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    retry_delay: float = _DEFAULT_RETRY_DELAY,
) -> list[Any]:
    """Fetch all graph edges with pagination, returning the complete list. Each page request has its own retry."""
    all_edges: list[Any] = []
    cursor: str | None = None
    page_num = 0

    while True:
        kwargs: dict[str, Any] = {"limit": page_size}
        if cursor is not None:
            kwargs["uuid_cursor"] = cursor

        page_num += 1
        batch = _fetch_page_with_retry(
            client.graph.edge.get_by_graph_id,
            graph_id,
            max_retries=max_retries,
            retry_delay=retry_delay,
            page_description=f"fetch edges page {page_num} (graph={graph_id})",
            **kwargs,
        )
        if not batch:
            break

        all_edges.extend(batch)
        if len(batch) < page_size:
            break

        cursor = getattr(batch[-1], "uuid_", None) or getattr(batch[-1], "uuid", None)
        if cursor is None:
            logger.warning(f"Edge missing uuid field, stopping pagination at {len(all_edges)} edges")
            break

    return all_edges
