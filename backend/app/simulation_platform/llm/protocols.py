"""Structural protocol every platform-level LLM wrapper accepts.

The shape mirrors what :class:`app.seam.llm_patches.PolicyLLMClient`
exposes today so any existing call site can substitute a wrapped
client without rewriting its arg plumbing:

  * ``chat_json(messages, temperature, max_tokens) -> Dict[str, Any]``
  * ``model: str`` -- the model name the call is issued against.
  * ``json_mode: str`` -- one of ``loose`` / ``strict`` / ``off``;
    drives the JSON-mode label persisted in :class:`LLMAuditEntry`.
  * ``last_usage: Optional[UsageInfo]`` -- per-call billing counters
    populated by the wrapped client AFTER each ``chat_json`` returns.
    Optional on the protocol because the upstream OpenAI client may
    or may not surface a ``usage`` block. Decorators that need real
    token counts (e.g. :class:`AuditedLLMClient`) read this attribute
    immediately after their delegated call and fall back to ``0`` when
    absent rather than guessing.

Capabilities live on a separate :class:`LLMCapabilities` flag bag so
:class:`StructuredOutputClient` can probe whether the underlying
provider supports ``response_format=json_schema`` without sniffing the
concrete class.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class UsageInfo:
    """Per-call billing counters captured from the provider response.

    Both fields are non-negative integer token counts. Wall-clock
    latency is measured by the audit decorator client-side and is NOT
    on this object.
    """

    tokens_in: int
    tokens_out: int


@dataclass(frozen=True)
class LLMCapabilities:
    """Static capability flags advertised by an :class:`LLMClientProtocol`.

    ``supports_json_schema``: provider accepts
    ``response_format={"type": "json_schema", ...}`` with ``strict=True``
    semantics. When ``False``, :class:`StructuredOutputClient` MUST fall
    back to the wrapped client's ``chat_json`` (which uses the broader
    ``response_format={"type": "json_object"}`` plus the repair pipeline).
    """

    supports_json_schema: bool = False


@runtime_checkable
class LLMClientProtocol(Protocol):
    """Structural contract every platform LLM wrapper accepts.

    Conformance is structural (``runtime_checkable``) so test fakes can
    satisfy it without inheriting from a base class.
    """

    model: str
    json_mode: str
    last_usage: Optional[UsageInfo]

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        ...


def get_capabilities(client: object) -> LLMCapabilities:
    """Probe ``client`` for its capability flags.

    Returns the client's ``capabilities`` attribute if present and of
    type :class:`LLMCapabilities`; otherwise returns the conservative
    default (no advertised capabilities). Probing by attribute presence
    rather than by ``isinstance`` keeps the protocol structural.
    """
    cap = getattr(client, "capabilities", None)
    if isinstance(cap, LLMCapabilities):
        return cap
    return LLMCapabilities()


@runtime_checkable
class AsyncLLMClientProtocol(Protocol):
    """Async dual-port mirror of :class:`LLMClientProtocol`.

    Same field set + the SAME ``chat_json`` signature declared
    ``async def``. Backends that prefer the native async transport
    (``AsyncOpenAI``, Anthropic's async client) implement this
    Protocol directly. Sync-only clients are wrapped through
    :class:`SyncToAsyncLLMAdapter` (below) so the async surface is
    satisfied via :func:`asyncio.to_thread`.

    Why the dual port matters depends on the WSGI/ASGI boundary
    the route layer runs under:

      * Under the sync Flask + Gunicorn worker model the app ships
        with today, ``asyncio.run(coro)`` on the request thread
        BLOCKS until the coroutine completes — the async port does
        NOT release the Gunicorn worker. The async surface is still
        useful: a future ASGI route (Quart, FastAPI mount, or an
        async background-job worker that calls ``await client.chat_json``)
        gets real I/O concurrency on the same client.
      * The async surface also lets backends with NATIVE async
        transports (``AsyncOpenAI`` etc.) bypass the threadpool
        bounce a :class:`SyncToAsyncLLMAdapter` introduces; native
        transports return earlier for streaming responses and can
        cancel cleanly mid-flight.

    Conformance is structural; existing seam clients (the protected
    :mod:`app.utils.llm_client` is the upstream-owned sync surface)
    are wrapped through the adapter -- the protected files are NOT
    edited.

    Caveat: ``@runtime_checkable`` Protocols accept any object whose
    method NAMES match -- they do NOT verify the methods are
    ``async def``. A sync ``chat_json`` will satisfy ``isinstance``
    against this Protocol structurally and produce a non-coroutine
    return that crashes at ``await``. Use
    :func:`inspect.iscoroutinefunction` (or the
    :class:`SyncToAsyncLLMAdapter` wrapper) to enforce the async
    contract at runtime when dispatching.
    """

    model: str
    json_mode: str
    last_usage: Optional[UsageInfo]

    async def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        ...


class SyncToAsyncLLMAdapter:
    """Wrap an :class:`LLMClientProtocol` so it satisfies
    :class:`AsyncLLMClientProtocol`.

    Dispatches the wrapped sync ``chat_json`` to a worker thread via
    :func:`asyncio.to_thread`. The event loop is released while the
    LLM call is in flight; a concurrent request handler can advance
    on the same loop.

    Field mirroring (``model`` / ``json_mode`` / ``last_usage``) is
    pass-through. ``capabilities`` (when the wrapped client exposes
    it) is also surfaced so a downstream
    :class:`StructuredOutputClient`-equivalent can probe the same
    flags through the async surface.

    For a backend that has a NATIVE async transport (the real win is
    ``AsyncOpenAI``), implement :class:`AsyncLLMClientProtocol`
    directly; this wrapper is the bridge for the legacy sync seam.
    """

    def __init__(self, sync_client: LLMClientProtocol) -> None:
        self._sync = sync_client

    @property
    def model(self) -> str:
        return self._sync.model

    @property
    def json_mode(self) -> str:
        return self._sync.json_mode

    @property
    def last_usage(self) -> Optional[UsageInfo]:
        return self._sync.last_usage

    @property
    def capabilities(self) -> LLMCapabilities:
        return get_capabilities(self._sync)

    async def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        import asyncio
        return await asyncio.to_thread(
            self._sync.chat_json,
            messages,
            temperature,
            max_tokens,
        )


__all__ = [
    "LLMCapabilities",
    "LLMClientProtocol",
    "AsyncLLMClientProtocol",
    "SyncToAsyncLLMAdapter",
    "UsageInfo",
    "get_capabilities",
]
