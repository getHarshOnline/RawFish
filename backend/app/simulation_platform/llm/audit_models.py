"""Platform-level LLM audit record types.

:class:`LLMAuditEntry` describes one persisted LLM call. It lives in
the platform LLM layer because the audit decorator
(:class:`app.simulation_platform.llm.audited.AuditedLLMClient`) and the
on-disk sink (:class:`app.simulation_platform.llm.audit_sink.RunMetadataAuditSink`)
are the canonical producers of the record -- both are platform-level.
``app.policy.run_metadata`` re-exports :class:`LLMAuditEntry` so
callers that import the symbol from that module continue to resolve.

The shape is intentionally narrow: ids + counters + a coarse phase
label. The full request / response payload is NEVER persisted here
(PII risk + bytes), only the counters needed for reproducibility and
cost reconstruction.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LLMAuditEntry(BaseModel):
    """One persisted LLM call record.

    Captured by ``PolicyLLMClient.chat_json`` (or any seam-equivalent)
    on every outbound call.

    Fields:
      * ``call_id`` -- caller-supplied id (uuid4 hex is the convention).
      * ``model`` -- model-name string the call was issued against.
      * ``mode`` -- JSON-mode contract: ``loose`` / ``strict`` / ``off``.
      * ``tokens_in`` / ``tokens_out`` -- billing units from the
        provider's usage response. Both REQUIRED + ``ge=0`` so missing
        usage data (some self-hosted endpoints omit it) hard-fails
        rather than silently records ``None`` and breaks aggregation.
      * ``latency_ms`` -- wall-clock duration measured client-side.
      * ``phase`` -- coarse-grained call site
        (``ontology`` / ``profiles`` / ``simulation`` / ``report`` /
        ``parse`` / ``sensitivity`` / ``other``). Lets the cost summary
        attribute spend per pipeline stage.
      * ``timestamp`` -- tz-aware datetime of the call.
      * ``error`` -- optional short error string when the call failed
        on the seam's retry path; absent on success.
    """

    model_config = ConfigDict(extra="forbid")

    call_id: Annotated[str, Field(min_length=1)]
    model: Annotated[str, Field(min_length=1)]
    mode: Literal["loose", "strict", "off"]
    tokens_in: Annotated[int, Field(ge=0)]
    tokens_out: Annotated[int, Field(ge=0)]
    latency_ms: Annotated[int, Field(ge=0)]
    phase: Literal[
        "ontology", "profiles", "simulation", "report",
        "parse", "sensitivity", "other"
    ]
    timestamp: datetime
    error: Optional[str] = None

    @model_validator(mode="after")
    def _reject_naive_timestamp(self) -> "LLMAuditEntry":
        if self.timestamp.tzinfo is None:
            raise ValueError("LLMAuditEntry.timestamp must be timezone-aware")
        return self


__all__ = ["LLMAuditEntry"]
