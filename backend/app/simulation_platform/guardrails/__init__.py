"""Platform-level methodological-guardrail surface.

This subpackage owns the canonical home for:

* The closed taxonomies every lane shares when emitting guardrails
  (hypothesis tags, validity-flag kinds, severity / confidence bands,
  evidence ref kinds) -- see :mod:`.taxonomy`.
* The platform-level Pydantic shapes (claim, evidence ref, driver
  hint, validity flag, guardrail finding, report-level aggregator)
  -- see :mod:`.models`.
* The opt-in :class:`GuardrailCapabilities` contract adapters use to
  declare the methodological hooks the generic dispatch should
  invoke on their behalf -- see :mod:`.capabilities`.

Lanes consume these by importing the frozensets and models directly.
"""
from __future__ import annotations

from .capabilities import (
    ApplyTransparencyFlags,
    AssertRenderContract,
    BuildEvidenceCatalog,
    GuardrailCapabilities,
    NO_GUARDRAILS,
    RenderMarkdown,
    RunAdvisoryClaimPass,
)
from .models import (
    ClaimLike,
    DriverHint,
    EvidenceRef,
    GuardrailFinding,
    ReportGuardrailView,
    ValidityFlag,
)
from .taxonomy import (
    CONFIDENCE_BANDS,
    EVIDENCE_REF_KINDS,
    HYPOTHESIS_TAGS,
    SEVERITY_VARIANTS,
    VALIDITY_FLAG_KINDS,
)

__all__ = [
    # Taxonomies
    "HYPOTHESIS_TAGS",
    "VALIDITY_FLAG_KINDS",
    "SEVERITY_VARIANTS",
    "CONFIDENCE_BANDS",
    "EVIDENCE_REF_KINDS",
    # Pydantic shapes
    "ClaimLike",
    "EvidenceRef",
    "DriverHint",
    "ValidityFlag",
    "GuardrailFinding",
    "ReportGuardrailView",
    # Capabilities contract
    "GuardrailCapabilities",
    "NO_GUARDRAILS",
    "AssertRenderContract",
    "ApplyTransparencyFlags",
    "BuildEvidenceCatalog",
    "RenderMarkdown",
    "RunAdvisoryClaimPass",
]
