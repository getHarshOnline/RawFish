"""Opt-in guardrail capability contract for :class:`SimulationUseCase` adapters.

The base :class:`app.simulation_platform.use_case.SimulationUseCase`
Protocol covers the minimum surface every lane must expose so the
generic ``/api/sim/use-cases/*`` dispatch layer can route, validate,
and persist. The richer methodological guardrails the policy lane
ships -- evidence binding, language-contract enforcement, validity-
flag synthesis, deterministic markdown rendering, numeric / overclaim
claim demotion -- are NOT part of the base Protocol because they
require lane-specific schema shapes (validity_flags, evidence_refs,
driver_hints) that not every lane has yet.

This module declares the opt-in seam: a lane that wants the generic
report-persist path to apply its guardrails sets
``adapter.guardrail_capabilities`` to an instance of
:class:`GuardrailCapabilities` whose hooks the dispatch layer
discovers and invokes when present. Adapters that do not declare
the attribute (or set it to ``None``) get the current honest
"JSON + read-side schema validation" surface.

Each hook is independently optional. An adapter that ships markdown
rendering but not transparency-flag synthesis sets only the
``render_markdown`` field; the dispatch layer threads through every
hook that is set and skips the ones that are ``None``. This means
the surface is a real ratchet: implementing one capability does not
force the lane to implement them all.

This module is the contract; the generic dispatch
(``backend/app/api/use_cases.py``) and the auto-report watcher
(``backend/app/simulation_platform/auto_report_watcher.py``) BOTH
read ``adapter.guardrail_capabilities`` and invoke the hooks in
this pinned order:

  1. ``build_evidence_catalog(run_dir, scenario)`` (pre-generate)
  2. adapter's own ``generate_report(scenario, run_metadata, catalog)``
  3. ``run_advisory_claim_pass(report, catalog)``
  4. ``apply_transparency_flags(report, run_metadata)``
  5. ``render_markdown(report)``
  6. ``assert_render_contract(rendered_markdown)``

Each hook is independently opt-in: an adapter that sets only
``render_markdown`` gets markdown-only; an adapter that sets
``guardrail_capabilities = None`` gets the JSON-only path.

Adapter-internal-guardrails gate: lanes whose ``generate_report``
already drives advisory / transparency / markdown render /
language-contract passes internally declare
:attr:`SimulationUseCase.adapter_runs_guardrails_internally` as
``True``. Under that contract the dispatch + watcher SKIP steps
3-6 above so the side effects (warnings, demotions,
validity-flag synthesis) do not compound twice. Step 1
(``build_evidence_catalog``) still fires because the adapter
consumes the catalog as an input to step 2.

The platform-level Pydantic shapes (``ClaimLike`` / ``EvidenceRef``
/ ``ValidityFlag`` / ``DriverHint`` / ``GuardrailFinding`` /
``ReportGuardrailView``) live alongside in
:mod:`app.simulation_platform.guardrails.models`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


# Each hook is typed as a plain ``Callable[..., ...]`` (not a Protocol)
# so an adapter can pass a free function, a bound method, or a lambda
# without ceremony. The dispatch layer's responsibility is to call
# each hook in the right order; the adapter's responsibility is to
# return a payload the next hook can consume.


# Hook signature: ``assert_render_contract(rendered_markdown: str) -> None``
#
# Raises an exception if the rendered report violates the lane's
# language contract (banned tokens, missing hedges, etc.). The
# generic dispatch surfaces a raise as 500 ``REPORT_CORRUPTED`` with
# ``details.reason = "render_contract_violation"``. Adapters that
# have no language contract leave this hook as ``None``.
AssertRenderContract = Callable[[str], None]


# Hook signature:
#   ``apply_transparency_flags(report, run_metadata) -> report_with_flags``
#
# Returns a new report with validity-flag synthesis applied; the
# generic dispatch then schema-validates the result (via
# ``report_schema``) before persisting. Adapters that have no
# transparency-flag system leave this hook as ``None``.
ApplyTransparencyFlags = Callable[[Any, Any], Any]


# Hook signature:
#   ``build_evidence_catalog(run_dir, scenario) -> evidence_catalog``
#
# Returns an evidence catalog the adapter's ``generate_report`` call
# can consume via the ``evidence_catalog`` kwarg. Adapters that do
# not bind evidence at generate-time leave this hook as ``None``.
BuildEvidenceCatalog = Callable[[Path, Any], Any]


# Hook signature:
#   ``render_markdown(report) -> str``
#
# Returns the rendered markdown the dispatch persists alongside the
# JSON artifact (when present). Adapters that ship a markdown surface
# set this to their renderer; adapters that don't leave it ``None``
# and the dispatch persists JSON only.
RenderMarkdown = Callable[[Any], str]


# Hook signature:
#   ``run_advisory_claim_pass(report, evidence_catalog) -> report``
#
# Runs adapter-defined numeric / overclaim / contradiction checks
# and returns a possibly-mutated report (e.g. with offending claims
# demoted to ``requires_validation``). Adapters that have no
# advisory checks leave this hook as ``None``.
RunAdvisoryClaimPass = Callable[[Any, Any], Any]


@dataclass(frozen=True)
class GuardrailCapabilities:
    """Opt-in container for the lane's guardrail hooks.

    Every field is :class:`Optional` -- setting one is equivalent
    to declaring "this lane implements this capability"; leaving
    a field at ``None`` is the canonical opt-out signal the generic
    dispatch reads.

    The frozen dataclass makes the contract immutable per-adapter
    (the wiring is hard-coded at module-load time); a future lane
    that needs dynamic capabilities can subclass and override
    ``__post_init__`` to recompute.
    """

    assert_render_contract: Optional[AssertRenderContract] = None
    apply_transparency_flags: Optional[ApplyTransparencyFlags] = None
    build_evidence_catalog: Optional[BuildEvidenceCatalog] = None
    render_markdown: Optional[RenderMarkdown] = None
    run_advisory_claim_pass: Optional[RunAdvisoryClaimPass] = None


# Sentinel: the canonical "no guardrails wired" value. Adapters can
# either set ``guardrail_capabilities = None`` (matches the Protocol
# default) or ``guardrail_capabilities = NO_GUARDRAILS`` (more
# explicit at the call site).
NO_GUARDRAILS: GuardrailCapabilities = GuardrailCapabilities()


__all__ = [
    "GuardrailCapabilities",
    "NO_GUARDRAILS",
    "AssertRenderContract",
    "ApplyTransparencyFlags",
    "BuildEvidenceCatalog",
    "RenderMarkdown",
    "RunAdvisoryClaimPass",
]
