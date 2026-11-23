"""``AsyncLLMClientProtocol`` dual-port contract + sync-to-async adapter.

The platform LLM surface grew an async port alongside the sync one
so a future request handler can ``await`` an LLM call instead of
pinning one of the documented 8 Gunicorn request threads for the
full model round-trip.

This pin asserts the additive contract:

  1. The async Protocol declares the same field set + ``chat_json``
     signature as :class:`LLMClientProtocol`, with ``chat_json``
     declared ``async def``.
  2. :class:`SyncToAsyncLLMAdapter` wraps any sync client and
     dispatches the underlying call to a thread worker, returning
     the same shape the sync client returned.
  3. The adapter forwards every field (``model`` / ``json_mode`` /
     ``last_usage``) so an audit decorator on the async surface sees
     the same per-call counters the sync surface sees.
"""
from __future__ import annotations

import asyncio
import inspect

from app.simulation_platform.llm.protocols import (
    AsyncLLMClientProtocol,
    LLMCapabilities,
    SyncToAsyncLLMAdapter,
    UsageInfo,
)


class _FakeSyncLLM:
    model = "fake-model"
    json_mode = "loose"
    last_usage = UsageInfo(tokens_in=10, tokens_out=20)
    capabilities = LLMCapabilities(supports_json_schema=False)

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def chat_json(self, messages, temperature=0.3, max_tokens=4096):
        self.calls.append(("chat_json", messages, temperature, max_tokens))
        return {"reply": "ok"}


def test_async_llm_protocol_declares_chat_json_async():
    member = AsyncLLMClientProtocol.chat_json
    assert inspect.iscoroutinefunction(member), (
        "AsyncLLMClientProtocol.chat_json must be ``async def`` to "
        "release the request thread while the model round-trip is in "
        "flight"
    )


def test_sync_to_async_adapter_satisfies_async_protocol():
    adapter = SyncToAsyncLLMAdapter(_FakeSyncLLM())  # type: ignore[arg-type]  # fake duck-types Protocol for hermetic test
    assert isinstance(adapter, AsyncLLMClientProtocol)


def test_sync_to_async_adapter_returns_underlying_payload():
    sync_client = _FakeSyncLLM()
    adapter = SyncToAsyncLLMAdapter(sync_client)  # type: ignore[arg-type]  # fake duck-types Protocol for hermetic test

    async def _exercise():
        return await adapter.chat_json(
            [{"role": "user", "content": "hi"}],
            temperature=0.7,
            max_tokens=128,
        )

    out = asyncio.run(_exercise())
    assert out == {"reply": "ok"}
    assert sync_client.calls == [
        ("chat_json", [{"role": "user", "content": "hi"}], 0.7, 128),
    ]


def test_sync_to_async_adapter_forwards_fields():
    sync_client = _FakeSyncLLM()
    adapter = SyncToAsyncLLMAdapter(sync_client)  # type: ignore[arg-type]  # fake duck-types Protocol for hermetic test
    assert adapter.model == "fake-model"
    assert adapter.json_mode == "loose"
    assert adapter.last_usage == UsageInfo(tokens_in=10, tokens_out=20)
    assert adapter.capabilities == LLMCapabilities(supports_json_schema=False)


def test_sync_to_async_adapter_chat_json_is_async_def():
    adapter = SyncToAsyncLLMAdapter(_FakeSyncLLM())  # type: ignore[arg-type]  # fake duck-types Protocol for hermetic test
    assert inspect.iscoroutinefunction(adapter.chat_json), (
        "SyncToAsyncLLMAdapter.chat_json must be ``async def`` so a "
        "consumer that awaits it does not get a bare value back"
    )
