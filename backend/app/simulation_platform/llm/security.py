"""LLM-input security primitives (delimiter-injection guard).

Single source of truth for the user-role delimiter-injection check
that wards off prompts which try to escape the
``<UNTRUSTED_USER_DOCUMENT> ... </UNTRUSTED_USER_DOCUMENT>`` wrapping
the framework's prompt builders apply to free-form user content.

Two callers consume this:

  * :class:`app.seam.llm_patches.PolicyLLMClient.chat_json` --
    invoked on every LLM call the seam client handles. This is the
    original guard surface; centralising the predicate here lets the
    seam method delegate to a shared check instead of carrying its
    own copy.
  * :class:`app.simulation_platform.llm.structured_output.StructuredOutputClient`
    -- the strict-schema decorator bypasses
    :meth:`PolicyLLMClient.chat_json` and dispatches directly to the
    upstream OpenAI client when the provider advertises
    ``response_format=json_schema`` support. The decorator calls this
    function before that dispatch so a delimiter-injection payload
    smuggled in via the wizard's free-form fields cannot skip the
    guard.

Both callers pre-validate the inbound message list against the same
function so security-critical behaviour cannot drift between the two
code paths. A pin test
(``tests/platform/test_llm_input_security.py``) asserts that any
message rejected here is also rejected by
:meth:`PolicyLLMClient.chat_json` so a future seam-side refactor
that stops calling this guard would be caught at test time.

Pure module: no clock, no env, no I/O, no framework imports.
"""
from __future__ import annotations

from typing import Iterable, Mapping


# The literal payload that signals a delimiter-escape attempt. Mirror
# of the closing tag the prompt builders use to wrap untrusted user
# content; the guard rejects any user-role message that ALSO closes
# that tag because doing so lets the model interpret subsequent text
# as trusted system content.
USER_ROLE_DELIMITER_INJECTION_MARKER: str = "</UNTRUSTED_USER_DOCUMENT>"


class DelimiterInjectionRejected(ValueError):
    """Raised when a user-role message contains the delimiter marker.

    Subclasses ``ValueError`` so callers that have a generic input-
    validation branch already handle it; the concrete class lets
    security-aware callers tell this rejection apart from a generic
    schema violation in their logs.
    """


def assert_no_user_role_delimiter_injection(
    messages: Iterable[Mapping[str, object]],
) -> None:
    """Reject any user-role message containing the injection marker.

    Iterates the message list and raises
    :class:`DelimiterInjectionRejected` on the first violation. Pure
    structural check -- the message list is NOT mutated and no
    network / file I/O happens. Tolerant of non-dict entries
    (skipped) so a malformed message list still surfaces the right
    error class downstream.

    Args:
        messages: an iterable of OpenAI-style chat dicts each with
            ``role`` (a str) and ``content`` (typically a str). Any
            entry that does not expose a mapping interface, or whose
            ``content`` is not a string, is skipped without error --
            the type-mismatch will surface at the actual LLM call,
            not here.

    Raises:
        DelimiterInjectionRejected: the first user-role message whose
            string ``content`` contains the literal
            :data:`USER_ROLE_DELIMITER_INJECTION_MARKER`.
    """
    for m in messages or ():
        try:
            role = m.get("role")
            content = m.get("content", "")
        except AttributeError:
            # Non-mapping entry; skip. The upstream call will surface
            # the type error if the shape is wrong.
            continue
        if role == "user" and isinstance(content, str) and \
                USER_ROLE_DELIMITER_INJECTION_MARKER in content:
            raise DelimiterInjectionRejected(
                "delimiter injection rejected"
            )


__all__ = [
    "USER_ROLE_DELIMITER_INJECTION_MARKER",
    "DelimiterInjectionRejected",
    "assert_no_user_role_delimiter_injection",
]
