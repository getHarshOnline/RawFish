"""StructuredOutputClient: pin json_schema routing + transparent fallback.

Two pins:

  1. When the wrapped client advertises ``supports_json_schema=True``
     and a schema is provided, the request body sent to the underlying
     OpenAI client carries ``response_format={"type": "json_schema", ...}``.
  2. When the wrapped client does NOT advertise the capability, the
     decorator is a verbatim pass-through (the call lands on the wrapped
     client's ``chat_json``, never on the OpenAI client directly).
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from app.simulation_platform.llm import StructuredOutputClient
from app.simulation_platform.llm.protocols import LLMCapabilities, UsageInfo


class _FakeOpenAI:
    """Captures the kwargs of the most recent ``chat.completions.create``."""

    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.last_kwargs: Optional[Dict[str, Any]] = None

        outer = self

        class _Completions:
            def create(self, **kwargs: Any) -> Any:
                outer.last_kwargs = kwargs
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(outer._payload))
                    )],
                    usage=SimpleNamespace(prompt_tokens=7, completion_tokens=9),
                )

        self.chat = SimpleNamespace(completions=_Completions())


class _CapableClient:
    """Wrapped client that advertises strict-schema support."""

    capabilities = LLMCapabilities(supports_json_schema=True)

    def __init__(self) -> None:
        self.model = "schema-model"
        self.json_mode = "strict"
        self.last_usage: Optional[UsageInfo] = None
        self.client = _FakeOpenAI({"answer": "ok"})
        self.chat_json_calls = 0

    def chat_json(self, messages, temperature: float = 0.3, max_tokens: int = 4096) -> Dict[str, Any]:
        self.chat_json_calls += 1
        return {"fallback": True}


class _PlainClient:
    """Wrapped client that does NOT advertise strict-schema support."""

    def __init__(self) -> None:
        self.model = "plain-model"
        self.json_mode = "loose"
        self.last_usage: Optional[UsageInfo] = None
        self.client = _FakeOpenAI({"unused": True})
        self.chat_json_calls = 0
        self.last_messages: Optional[List[Dict[str, str]]] = None

    def chat_json(self, messages, temperature: float = 0.3, max_tokens: int = 4096) -> Dict[str, Any]:
        self.chat_json_calls += 1
        self.last_messages = messages
        return {"plain": True}


def test_structured_output_routes_through_json_schema_when_supported() -> None:
    inner = _CapableClient()
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    wrapper = StructuredOutputClient(inner, json_schema=schema, schema_name="report")

    out = wrapper.chat_json([{"role": "user", "content": "go"}])

    assert out == {"answer": "ok"}
    # chat_json fallback NOT invoked when strict-schema path succeeds.
    assert inner.chat_json_calls == 0
    kwargs = inner.client.last_kwargs
    assert kwargs is not None
    rf = kwargs["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "report"
    assert rf["json_schema"]["schema"] == schema
    # Usage propagated onto the wrapped client.
    assert inner.last_usage == UsageInfo(tokens_in=7, tokens_out=9)


def test_structured_output_is_passthrough_without_capability() -> None:
    inner = _PlainClient()
    schema = {"type": "object"}
    wrapper = StructuredOutputClient(inner, json_schema=schema)

    out = wrapper.chat_json([{"role": "user", "content": "hello"}])

    assert out == {"plain": True}
    # Strict-schema path NOT used.
    assert inner.client.last_kwargs is None
    # Wrapped chat_json was used.
    assert inner.chat_json_calls == 1


def test_structured_output_passthrough_when_no_schema() -> None:
    inner = _CapableClient()
    wrapper = StructuredOutputClient(inner, json_schema=None)

    wrapper.chat_json([{"role": "user", "content": "x"}])

    # Even when the capability is advertised, no schema means no
    # strict-schema routing -- the wrapped chat_json owns the call.
    assert inner.client.last_kwargs is None
    assert inner.chat_json_calls == 1


# ----------------------------------------------------------------------
# last_usage aggregation across retries
# ----------------------------------------------------------------------


class _RetryingFakeOpenAI:
    """Yields validation-failing payloads then a valid one.

    Each call records its own usage so the test can assert that the
    decorator's ``last_usage`` returns the SUM across the retry chain,
    not just the final retry's usage.
    """

    def __init__(self, payloads, usages) -> None:
        assert len(payloads) == len(usages)
        self._payloads = list(payloads)
        self._usages = list(usages)
        self._idx = 0
        self.calls = 0
        outer = self

        class _Completions:
            def create(self, **kwargs: Any) -> Any:
                i = outer._idx
                outer._idx += 1
                outer.calls += 1
                pin, pout = outer._usages[i]
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(
                            content=json.dumps(outer._payloads[i])
                        )
                    )],
                    usage=SimpleNamespace(
                        prompt_tokens=pin,
                        completion_tokens=pout,
                    ),
                )

        self.chat = SimpleNamespace(completions=_Completions())


class _CapableRetryingClient(_CapableClient):
    """Same as :class:`_CapableClient` but with a custom retrying fake."""

    def __init__(self, payloads, usages) -> None:
        super().__init__()
        self.client = _RetryingFakeOpenAI(payloads, usages)


def test_last_usage_returns_sum_across_strict_schema_retries() -> None:
    """The strict-schema retry loop may issue up to ``1 + retries``
    actual provider calls. ``StructuredOutputClient.last_usage`` MUST
    return the SUM of every retry's tokens, not just the final
    retry's tokens, so that an outer :class:`AuditedLLMClient`
    captures the TOTAL cost of the logical call in its single audit
    entry.

    Without the per-call accumulator, the previous behaviour was to
    overwrite the wrapped seam client's ``last_usage`` slot on every
    retry, so an auditor reading ``last_usage`` after a 3-call chain
    saw only the third call's tokens and under-counted spend by
    ~2x.
    """
    from pydantic import BaseModel, Field
    from typing import Annotated

    class _Strict(BaseModel):
        answer: Annotated[str, Field(min_length=1)]

    # First two attempts fail Pydantic validation (empty string is
    # below the min_length=1 floor); the third succeeds. Each call's
    # usage is distinct so the SUM is easy to verify.
    payloads = [{"answer": ""}, {"answer": ""}, {"answer": "ok"}]
    usages = [(10, 20), (15, 25), (5, 7)]
    inner = _CapableRetryingClient(payloads, usages)
    wrapper = StructuredOutputClient(
        inner, target_schema=_Strict, max_validation_retries=2,
    )

    out = wrapper.chat_json([{"role": "user", "content": "go"}])
    assert out == {"answer": "ok"}
    # Provider was called 3 times (initial + 2 retries).
    assert inner.client.calls == 3
    # The per-call usage aggregate sums every retry's tokens.
    assert wrapper.last_usage == UsageInfo(
        tokens_in=10 + 15 + 5,
        tokens_out=20 + 25 + 7,
    )
    # The seam client's slot still carries the LATEST retry's usage
    # (backward-compat for anything that reads from there directly).
    assert inner.last_usage == UsageInfo(tokens_in=5, tokens_out=7)


def test_last_usage_reset_at_top_of_chat_json_even_when_falling_back() -> None:
    """Regression: the per-call usage accumulator MUST reset BEFORE
    the strict-schema branch decision so a follow-on fallback call
    cannot see stale spend from a previous strict call.

    Scenario: a decorator wrapping a CAPABLE client makes one
    successful strict-schema call (accumulator populated), then a
    follow-on call where the decorator was reconfigured to drop the
    schema (forcing the fallback branch). After the second call,
    ``last_usage`` MUST reflect the fallback path (seam's slot),
    NOT the stale strict-call sum.
    """
    from pydantic import BaseModel

    class _Plain(BaseModel):
        answer: str

    inner = _CapableRetryingClient(
        payloads=[{"answer": "first"}], usages=[(20, 30)],
    )
    wrapper = StructuredOutputClient(inner, target_schema=_Plain)
    wrapper.chat_json([{"role": "user", "content": "first"}])
    assert wrapper.last_usage == UsageInfo(tokens_in=20, tokens_out=30)

    # Drop the schema so the next call takes the fallback branch.
    wrapper._schema = None  # type: ignore[attr-defined]
    wrapper._target_schema = None  # type: ignore[attr-defined]
    # Stub the wrapped client's chat_json so the fallback branch
    # produces a deterministic payload. The wrapped client's
    # last_usage slot stays None because chat_json doesn't touch it
    # in this stub.
    inner.chat_json = lambda **_: {"fallback": True}  # type: ignore[assignment]
    inner.last_usage = None

    wrapper.chat_json([{"role": "user", "content": "second"}])
    # The accumulator was reset at the TOP of chat_json so it is None;
    # the property falls through to the seam slot which is also None.
    # The bug being pinned: a stale UsageInfo(20, 30) would leak here
    # if the reset lived only inside the strict-schema branch.
    assert wrapper.last_usage is None


def test_last_usage_reset_between_calls() -> None:
    """The per-call accumulator MUST be reset at the START of every
    logical ``chat_json`` so spend from one call cannot leak into the
    next call's audit entry."""
    from pydantic import BaseModel

    class _Plain(BaseModel):
        answer: str

    inner_a = _CapableRetryingClient(
        payloads=[{"answer": "first"}], usages=[(11, 22)],
    )
    wrapper_a = StructuredOutputClient(inner_a, target_schema=_Plain)
    wrapper_a.chat_json([{"role": "user", "content": "first"}])
    assert wrapper_a.last_usage == UsageInfo(tokens_in=11, tokens_out=22)

    # Second call on the SAME wrapper -- the accumulator must reset,
    # NOT add the second call's tokens onto the first call's sum.
    inner_a.client = _RetryingFakeOpenAI(
        payloads=[{"answer": "second"}], usages=[(3, 5)],
    )
    wrapper_a.chat_json([{"role": "user", "content": "second"}])
    assert wrapper_a.last_usage == UsageInfo(tokens_in=3, tokens_out=5)
