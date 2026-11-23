"""StructuredOutputClient: pin Pydantic ``target_schema`` decode + retry.

These tests pin three independent behaviours added by PW2-D:

  1. **Request body shape** when the wrapped client advertises
     ``supports_json_schema=True`` and a ``target_schema`` (Pydantic
     model) is supplied: the request body carries
     ``response_format={"type":"json_schema", "json_schema":{...,
     "strict": True}}`` AND the embedded ``schema`` equals
     ``target_schema.model_json_schema()``. This is the wire-format
     pin -- a regression that drops ``strict`` or sends a different
     schema would change provider behaviour silently.

  2. **Retry on Pydantic validation failure**: when the first response
     fails ``target_schema.model_validate``, the decorator re-calls
     the provider with a tail ``system`` message whose body contains
     the validation error, and returns the corrected payload on the
     retry. This pins the feedback loop -- the model can ONLY learn
     to fix its output if the error is fed back verbatim.

  3. **Max-retry termination**: after ``max_validation_retries``
     additional calls (default 2), the final
     :class:`pydantic.ValidationError` propagates to the caller. This
     pins the upper bound -- without it, a misbehaving provider could
     drive an unbounded retry storm that silently torches the token
     budget.

The fakes here mirror the shape used by the sibling
``test_structured_output_client.py`` so the two suites read together as
one consistent contract pin around the wrapper.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel, ValidationError

from app.simulation_platform.llm import StructuredOutputClient
from app.simulation_platform.llm.protocols import LLMCapabilities, UsageInfo


# ---------------------------------------------------------------------------
# Tiny Pydantic model used as the target_schema across tests
# ---------------------------------------------------------------------------


class _Tiny(BaseModel):
    """Minimal Pydantic model with one required field.

    Kept deliberately small so the generated JSON schema is easy to
    eyeball in the request-body assertion and so failure cases (missing
    ``answer``) trigger a deterministic ValidationError without any
    cross-field validator noise.
    """

    answer: str


# ---------------------------------------------------------------------------
# Programmable fake OpenAI client
# ---------------------------------------------------------------------------


class _ScriptedOpenAI:
    """Fake ``OpenAI`` client that replays a scripted list of payloads.

    Captures the kwargs of every ``chat.completions.create`` call into
    ``self.calls`` so tests can assert on (a) the response_format on
    each call and (b) the message list (notably the synthetic system
    message appended on retries).
    """

    def __init__(self, payloads: List[Dict[str, Any]]) -> None:
        self._payloads = list(payloads)
        self.calls: List[Dict[str, Any]] = []

        outer = self

        class _Completions:
            def create(self, **kwargs: Any) -> Any:
                outer.calls.append(kwargs)
                # Pop the next scripted payload; if the scripted list
                # runs out the test set the script wrong, which we
                # surface as a clear assertion rather than IndexError.
                assert outer._payloads, (
                    "scripted OpenAI fake exhausted -- the test wired "
                    "fewer payloads than the wrapper requested calls"
                )
                payload = outer._payloads.pop(0)
                return SimpleNamespace(
                    choices=[SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(payload))
                    )],
                    usage=SimpleNamespace(prompt_tokens=3, completion_tokens=5),
                )

        self.chat = SimpleNamespace(completions=_Completions())


class _CapableClient:
    """Wrapped client that advertises strict-schema support.

    Carries a ``_ScriptedOpenAI`` so the test can pre-program the
    response payloads driving each strict-schema call.
    """

    capabilities = LLMCapabilities(supports_json_schema=True)

    def __init__(self, payloads: List[Dict[str, Any]]) -> None:
        self.model = "schema-model"
        self.json_mode = "strict"
        self.last_usage: Optional[UsageInfo] = None
        self.client = _ScriptedOpenAI(payloads)
        self.chat_json_calls = 0

    def chat_json(self, messages, temperature: float = 0.3, max_tokens: int = 4096) -> Dict[str, Any]:
        # Sentinel return so a test that expects the strict-schema path
        # NOT to fall through can tell the difference if it does fall
        # through (the fallback dict shows up in the assertion).
        self.chat_json_calls += 1
        return {"fallback": True}


# ===========================================================================
# Test 1: request body shape when capability supported
# ===========================================================================


def test_target_schema_drives_strict_response_format_request_body() -> None:
    """When ``target_schema`` is set, the wire-format ``response_format``:
      * has ``type == "json_schema"``,
      * carries ``json_schema.strict == True``,
      * embeds the EXACT ``model_json_schema()`` of the target model,
      * uses the caller-supplied ``schema_name`` as the ``name`` field.
    """
    inner = _CapableClient(payloads=[{"answer": "ok"}])
    wrapper = StructuredOutputClient(
        inner,
        target_schema=_Tiny,
        schema_name="tiny",
    )

    out = wrapper.chat_json([{"role": "user", "content": "go"}])

    # Sanity: the decoded payload reached the caller untouched.
    assert out == {"answer": "ok"}
    # Strict-schema path was used; the fallback chat_json was NOT.
    assert inner.chat_json_calls == 0
    # Exactly one provider call -- no retry needed because validation
    # passed on the first attempt.
    assert len(inner.client.calls) == 1
    rf = inner.client.calls[0]["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "tiny"
    # The schema sent on the wire MUST be the Pydantic model's
    # canonical json schema -- any divergence means the provider is
    # being asked to enforce the wrong shape.
    assert rf["json_schema"]["schema"] == _Tiny.model_json_schema()
    # Usage propagated onto the wrapped client.
    assert inner.last_usage == UsageInfo(tokens_in=3, tokens_out=5)


# ===========================================================================
# Test 2: retry on Pydantic validation failure feeds the error back
# ===========================================================================


def test_validation_failure_retries_with_error_fed_back_then_succeeds() -> None:
    """First response is missing the required ``answer`` field and
    therefore fails ``_Tiny.model_validate``. The decorator:
      * issues a second provider call,
      * the second call's message list carries a tail ``system``
        message whose body contains the verbatim validation error,
      * the second response validates successfully and is returned.
    """
    inner = _CapableClient(payloads=[
        {"wrong_key": "no answer here"},  # fails validation
        {"answer": "second try"},          # passes validation
    ])
    wrapper = StructuredOutputClient(
        inner,
        target_schema=_Tiny,
        schema_name="tiny",
        max_validation_retries=2,
    )

    out = wrapper.chat_json([{"role": "user", "content": "go"}])

    # Final return is the corrected payload from the retry.
    assert out == {"answer": "second try"}
    # The wrapper made exactly two provider calls (initial + 1 retry)
    # and NEVER fell back to chat_json (a fallback would indicate a
    # provider error was misattributed to validation).
    assert len(inner.client.calls) == 2
    assert inner.chat_json_calls == 0

    # Pin the retry-feedback contract: the second call's message list
    # is the first call's list PLUS exactly one tail ``system`` entry
    # whose content quotes the validation error.
    first_messages = inner.client.calls[0]["messages"]
    retry_messages = inner.client.calls[1]["messages"]
    assert retry_messages[: len(first_messages)] == first_messages
    appended = retry_messages[len(first_messages):]
    assert len(appended) == 1
    assert appended[0]["role"] == "system"
    # The error text MUST appear in the appended content -- without
    # the verbatim error the model cannot learn what to fix.
    assert "failed validation" in appended[0]["content"]
    # The appended message MUST also include the canonical retry
    # instruction so the model knows what to do next.
    assert "corrected JSON" in appended[0]["content"]


# ===========================================================================
# Test 3: max-retry termination re-raises ValidationError
# ===========================================================================


def test_max_retries_exceeded_raises_validation_error() -> None:
    """With ``max_validation_retries=2`` the wrapper allows 3 total
    calls (initial + 2 retries). If ALL three return invalid payloads,
    the third failure surfaces as a :class:`pydantic.ValidationError`
    rather than a fall-back to ``chat_json`` (the task contract pins
    this behaviour because the policy report route maps Pydantic
    ValidationError to ``REPORT_CORRUPTED`` -- a silent fallback would
    swap an actionable 500 for a misleading 200).
    """
    inner = _CapableClient(payloads=[
        {"wrong_key": "attempt 1"},
        {"wrong_key": "attempt 2"},
        {"wrong_key": "attempt 3"},
    ])
    wrapper = StructuredOutputClient(
        inner,
        target_schema=_Tiny,
        schema_name="tiny",
        max_validation_retries=2,
    )

    with pytest.raises(ValidationError):
        wrapper.chat_json([{"role": "user", "content": "go"}])

    # The wrapper exhausted the budget -- exactly 3 provider calls
    # were issued (initial + 2 retries) and no fallback to chat_json.
    assert len(inner.client.calls) == 3
    assert inner.chat_json_calls == 0
