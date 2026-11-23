"""Simulation-platform LLM layer -- composition wrappers around the
unmodified ``app.utils.llm_client.LLMClient`` family.

This package owns the *platform-level* contracts and decorators that
sit IN FRONT of any concrete LLM client (today: ``LLMClient`` and the
seam-hardened ``PolicyLLMClient``). Nothing here mutates or subclasses
the upstream clients -- every capability is added by *wrapping* a
collaborator that satisfies :class:`LLMClientProtocol`.

Re-exports are intentionally narrow: consumers import from the package
root so the internal file layout can move without rippling.

Exposed types:
  * :class:`LLMClientProtocol` -- the structural contract every wrapper
    accepts and every wrapper itself satisfies (decorators stack).
  * :class:`StructuredOutputClient` -- opt-in JSON-Schema strict-mode
    decorator that transparently degrades when the underlying provider
    does not advertise ``response_format=json_schema`` support.
  * :class:`AuditedLLMClient` -- per-call audit decorator that pushes a
    typed :class:`app.policy.run_metadata.LLMAuditEntry` into a
    pluggable :class:`AuditSink` (typically the
    :class:`RunMetadataAuditSink`).
  * :class:`AuditSink` -- the sink protocol the audit decorator writes
    to. Tests substitute a list-backed sink; production wires the
    on-disk :class:`RunMetadataAuditSink`.
  * :class:`RunMetadataAuditSink` -- atomic-write sink that delegates
    to :func:`app.policy.run_metadata.refresh_audit_entry`.
"""
from __future__ import annotations

from .audit_sink import RunMetadataAuditSink
from .audited import AuditedLLMClient, AuditSink
from .protocols import LLMClientProtocol, UsageInfo
from .provider import RunScopedProviderConfigRequired
from .router import (
    PreflightMeterPayload,
    ProviderBudgetState,
    ProviderConfig,
    ProviderReadiness,
    RawFishLLMProviderFactory,
    RawFishLiteLLMClient,
    provider_budget_state,
    provider_config_from_state,
)
from .security import (
    DelimiterInjectionRejected,
    USER_ROLE_DELIMITER_INJECTION_MARKER,
    assert_no_user_role_delimiter_injection,
)
from .structured_output import StructuredOutputClient

__all__ = [
    "AuditSink",
    "AuditedLLMClient",
    "DelimiterInjectionRejected",
    "LLMClientProtocol",
    "PreflightMeterPayload",
    "ProviderBudgetState",
    "ProviderConfig",
    "ProviderReadiness",
    "RawFishLLMProviderFactory",
    "RawFishLiteLLMClient",
    "provider_budget_state",
    "provider_config_from_state",
    "RunMetadataAuditSink",
    "RunScopedProviderConfigRequired",
    "StructuredOutputClient",
    "USER_ROLE_DELIMITER_INJECTION_MARKER",
    "UsageInfo",
    "assert_no_user_role_delimiter_injection",
]
