"""AuditedLLMClient: pin every chat_json call appends one LLMAuditEntry.

Three pins:

  1. Success path: one entry per call, tokens captured from the wrapped
     client's ``last_usage``, error is ``None``.
  2. Error path: the wrapped client's exception propagates AND the
     entry is still appended with ``error`` populated.
  3. Idempotency: wrapping an already-audited client returns the
     existing instance (no double-instrument).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pytest

from app.simulation_platform.run_metadata import LLMAuditEntry
from app.simulation_platform.llm import AuditedLLMClient
from app.simulation_platform.llm.protocols import UsageInfo


class _ListSink:
    def __init__(self) -> None:
        self.entries: List[LLMAuditEntry] = []

    def append(self, entry: LLMAuditEntry) -> None:
        self.entries.append(entry)


class _FakeClient:
    """Minimal :class:`LLMClientProtocol` test double."""

    def __init__(
        self,
        *,
        model: str = "test-model",
        json_mode: str = "loose",
        usage: Optional[UsageInfo] = None,
        raise_exc: Optional[Exception] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.model = model
        self.json_mode = json_mode
        self.last_usage = usage
        self._raise_exc = raise_exc
        self._result = result if result is not None else {"ok": True}
        self.calls: List[Dict[str, Any]] = []

    def chat_json(
        self,
        messages,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        self.calls.append(
            {"messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        )
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._result


def _fixed_clock() -> datetime:
    return datetime(2026, 6, 28, 12, 0, 0, tzinfo=timezone.utc)


def test_audited_appends_one_entry_per_call_with_usage() -> None:
    fake = _FakeClient(usage=UsageInfo(tokens_in=11, tokens_out=22))
    sink = _ListSink()
    audited = AuditedLLMClient(
        fake,
        sink,
        phase="report",
        call_id_factory=lambda: "call-1",
        clock=_fixed_clock,
    )

    out = audited.chat_json([{"role": "user", "content": "hi"}])

    assert out == {"ok": True}
    assert len(sink.entries) == 1
    e = sink.entries[0]
    assert e.call_id == "call-1"
    assert e.model == "test-model"
    assert e.mode == "loose"
    assert e.tokens_in == 11
    assert e.tokens_out == 22
    assert e.phase == "report"
    assert e.error is None
    assert e.timestamp == _fixed_clock()


def test_audited_captures_error_and_reraises() -> None:
    boom = RuntimeError("upstream boom")
    fake = _FakeClient(
        usage=UsageInfo(tokens_in=3, tokens_out=0),
        raise_exc=boom,
    )
    sink = _ListSink()
    audited = AuditedLLMClient(
        fake,
        sink,
        phase="report",
        call_id_factory=lambda: "call-err",
        clock=_fixed_clock,
    )

    with pytest.raises(RuntimeError, match="upstream boom"):
        audited.chat_json([{"role": "user", "content": "x"}])

    assert len(sink.entries) == 1
    e = sink.entries[0]
    assert e.call_id == "call-err"
    assert e.error is not None
    assert "upstream boom" in e.error
    # tokens_in carried from last_usage even on the error path
    assert e.tokens_in == 3
    assert e.tokens_out == 0


def test_audited_is_idempotent_under_double_wrap() -> None:
    fake = _FakeClient(usage=UsageInfo(tokens_in=1, tokens_out=2))
    sink = _ListSink()
    first = AuditedLLMClient(fake, sink, phase="report")
    second = AuditedLLMClient(first, sink, phase="report")

    assert second is first

    first.chat_json([{"role": "user", "content": "y"}])
    # One entry, not two, despite the double-wrap.
    assert len(sink.entries) == 1


def test_audited_rejects_invalid_phase() -> None:
    fake = _FakeClient()
    sink = _ListSink()
    with pytest.raises(ValueError):
        AuditedLLMClient(fake, sink, phase="bogus-phase")
