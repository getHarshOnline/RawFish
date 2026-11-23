"""Standalone policy report generator (LLM-backed Report constructor).

This module is the I/O-bearing half of the policy report lane. It composes
the pure prompt-builder helpers with a :class:`PolicyLLMClient` to obtain
a structured JSON payload from the LLM, merges that payload with the
provenance fields derived from the caller-supplied
:class:`RunMetadata`, constructs a canonical :class:`Report` via
``Report.model_validate``, and (optionally) persists the validated
report to disk via the atomic-write helper.

Why standalone (not a subclass of an older renderer):
  * The canonical :class:`Report` schema is the contract. Subclassing
    an older renderer with different framing would silently inherit
    prompt material and field-name conventions that disagree with the
    hypothesis-framed contract the schema enforces.
  * Composition over inheritance: the LLM client is INJECTED via the
    constructor so tests pass a deterministic mock and exercise the
    merge / validation / persistence path without a real network call.
  * The import-graph test below pins the allowed dependency set so a
    future edit accidentally pulling in removed renderer code trips at CI
    time, not at runtime.

Boundary with the renderer (kept clean):
  * THIS module reads the clock (``datetime.now(timezone.utc)``) and the
    filesystem (atomic write of ``policy_report.json``). Those are the
    generator's job.
  * The downstream markdown renderer is PURE -- no clock / env / fs /
    network -- and consumes only the persisted :class:`Report` object.

Cross-field invariant enforcement:
  * The :class:`Report` model validates that ``simulation_id``,
    ``scenario_hash``, ``seed_hash``, and ``persona`` on the header
    match the embedded ``run_metadata`` snapshot exactly. The generator
    populates the header fields directly from the same RunMetadata
    instance it embeds, so a mismatch can only occur if an LLM payload
    smuggles its own provenance keys -- those keys are deliberately
    stripped from the LLM payload before merge so the caller-supplied
    RunMetadata is the single source of truth.

Report id derivation:
  * ``report_id`` is a stable sha256 prefix of
    ``"{simulation_id}|{scenario_hash}|{seed_hash}|{REPORT_VERSION}"``.
    The version string starts at ``"v1"`` and is bumped only when the
    Report schema changes shape in a way that should be visible to
    consumers comparing identifiers across runs. Hashing the four
    fingerprints gives a deterministic identifier that changes if any
    upstream provenance changes -- callers re-running the same
    scenario+seed get the same report_id, which is the desired
    fingerprinting behavior.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import ValidationError

from app.policy.persona_defaults import report_sections_for
from app.policy.run_metadata import RunMetadata, refresh_warning
from app.policy.schema import Claim, HypothesisTag, Report, Scenario


@runtime_checkable
class _LLMSeam(Protocol):
    """Structural Protocol the generator's ``llm`` collaborator must
    satisfy. Widening the constructor type from concrete
    :class:`PolicyLLMClient` to this Protocol lets deterministic test
    stubs (e.g. ``_StubLLM``) be injected without `type: ignore`
    suppressions OR a runtime-irrelevant inheritance link.

    The two attributes/methods named here are the ONLY surface the
    generator consumes from the seam:

      * ``chat_json(messages=..., max_tokens=...) -> Dict[str, Any]``
      * ``policy_max_tokens_report: int`` (the report-specific budget)

    Marked ``@runtime_checkable`` so ``isinstance`` checks (if any
    future caller adds them) pass against duck-typed stubs.
    """

    policy_max_tokens_report: int

    def chat_json(
        self,
        messages: Any,
        *,
        max_tokens: int = ...,
    ) -> Dict[str, Any]: ...
from app.services.policy.evidence_catalog import (
    EvidenceCatalog,
    build_evidence_catalog,
    render_catalog_prompt_block,
)
from app.services.policy.language_guard import assert_report_render_contract
from app.services.policy.markdown_renderer import render_report
from app.services.policy.prompt_builder import build_user_prompt
from app.services.policy.transparency_runtime import apply_transparency_flags
from app.utils.atomic_io import atomic_write_text


# ---------------------------------------------------------------------------
# Evidence-binding contract constants
# ---------------------------------------------------------------------------


# Tags whose claims MUST carry at least one resolvable evidence_ref:
# source_grounded restates user-provided seed material, and
# simulated_observation reports something the simulation produced --
# both kinds are ungrounded without a pointer back to the artifact.
# inference claims that speak to aggregate readings (see
# ``_is_aggregate_claim``) join the list because an aggregate
# inference with no per-segment evidence is a hand-wave.
_TAGS_REQUIRING_EVIDENCE: frozenset[str] = frozenset({
    HypothesisTag.SOURCE_GROUNDED.value,
    HypothesisTag.SIMULATED_OBSERVATION.value,
})


# Per-tag whitelist of valid ``EvidenceRef.ref_type`` kinds. Even when
# an evidence_ref resolves against the run catalog (the membership
# check), the SEMANTICS of the binding must also match: a
# ``source_grounded`` claim asserts a real-world citation, so it can
# only be backed by ``seed_text_line`` (text the user provided) or
# ``external_source`` (a URL / DOI); citing a ``simulation_action``
# would conflate simulated output with real source material.
# Symmetrically, ``simulated_observation`` claims report sim output
# and must cite ``simulation_action`` or ``agent_interview`` -- a
# real-world citation here would over-promise what the simulation
# actually saw.
#
# Aggregate ``inference`` claims (executive-summary roll-ups, see
# ``_is_aggregate_claim``) accept ANY of the four kinds because they
# legitimately roll up across real and simulated sources at once.
#
# A claim whose evidence_refs resolve against the catalog but whose
# kinds violate this matrix is demoted to ``requires_validation``
# with a "semantic mismatch" reason -- distinct in the warning text
# from the "no resolvable ref" reason so an operator reading
# ``run_metadata.warnings`` can tell which kind of binding failure
# happened.
_VALID_REF_KINDS_BY_TAG: Dict[str, frozenset[str]] = {
    HypothesisTag.SOURCE_GROUNDED.value: frozenset({
        "seed_text_line",
        "external_source",
    }),
    HypothesisTag.SIMULATED_OBSERVATION.value: frozenset({
        "simulation_action",
        "agent_interview",
    }),
    HypothesisTag.INFERENCE.value: frozenset({
        "seed_text_line",
        "external_source",
        "simulation_action",
        "agent_interview",
    }),
}


# Demotion rejection threshold: if more than this fraction of the
# report's claims would be demoted to ``requires_validation`` the
# report itself is rejected (REPORT_CORRUPTED + reason
# ``evidence_grounding_failed``). 0.5 means a report whose majority
# of claims fail evidence binding is structurally meaningless even
# after demotion, so the generator refuses to persist it.
_MAX_DEMOTION_FRACTION: float = 0.5


class EvidenceGroundingFailed(ValueError):
    """Raised when too many claims fail the evidence-binding contract.

    Distinct from :class:`pydantic.ValidationError` (which fires on
    schema-shape drift) so the route handler can map this specific
    failure to ``REPORT_CORRUPTED`` + ``details.reason='evidence_grounding_failed'``
    without parsing message text. Inherits from ``ValueError`` so the
    existing generator-side ``ValueError`` catch-all still funnels it correctly
    while a tighter ``except EvidenceGroundingFailed`` clause picks it off for
    the structured reason code.
    """


def _is_aggregate_claim(claim: Claim, executive_claim_ids: set[str]) -> bool:
    """Return True iff ``claim`` is the aggregate flavor.

    The :class:`Claim` schema does not carry a dedicated ``claim_class``
    field today; the proxy used here is structural rather than
    heuristic: a claim that lives in ``Report.executive_summary`` is
    aggregate by construction (the executive summary's job is to
    summarise across segments / sections). Section-level claims are
    treated as non-aggregate even when their ``segment_refs`` is empty
    because the absence of a per-segment scope on a section claim does
    not necessarily indicate aggregation -- it can equally mean the
    claim is segment-agnostic context. Pinning the proxy to the
    structural executive_summary location keeps the rule documentable
    and grep-friendly; if a future schema edit introduces an explicit
    ``claim_class`` field, this helper is the one place to update.
    """
    return claim.claim_id in executive_claim_ids


# Stable schema-shape marker folded into the report_id derivation.
# Bump when the on-disk Report shape changes in a way consumers should
# notice when diffing identifiers across runs. v2 added the
# runtime-owned `validity_flags` list to the persisted shape, so the
# same scenario+seed now produces a semantically richer report and
# deserves a distinct id from any v1 artifact lying on disk.
REPORT_VERSION: str = "v2"


# Keys the generator overwrites on the LLM payload before
# Report.model_validate runs. Two categories share this list:
#
#  1. Provenance: simulation_id / persona / scope / scenario_hash /
#     seed_hash / generated_at / run_metadata / report_id come from the
#     caller-supplied RunMetadata; the LLM is instructed (see
#     prompt_builder._NON_PREDICTIVE_PREAMBLE + user-prompt provenance
#     block) NOT to fabricate alternative values, but a payload that
#     nevertheless echoes them must NOT override the snapshot.
#  2. Runtime-owned: validity_flags is populated AFTER schema validation
#     by the transparency post-processor (step 7.5 below). An LLM that
#     emits malformed validity_flags would trip Report.model_validate
#     before the post-processor runs; stripping it up front keeps the
#     LLM payload syntactically constrained to fields the model owns.
_PROVENANCE_KEYS_STRIPPED_FROM_LLM_PAYLOAD: tuple[str, ...] = (
    "report_id",
    "simulation_id",
    "persona",
    "scope",
    "scenario_hash",
    "seed_hash",
    "generated_at",
    "run_metadata",
    "validity_flags",
)


class ScenarioRunMetadataPersonaMismatch(ValueError):
    """Raised when ``scenario.persona`` disagrees with ``run_metadata.persona``.

    Distinct from a generic ``ValueError`` so callers (route handlers,
    upstream pipelines) can branch on the failure mode without parsing
    error message text. Inherits from ``ValueError`` because the failure
    is a caller-input invariant violation, not a server error -- the
    request was internally inconsistent before any LLM call.
    """


class ReportSectionsMismatch(ValueError):
    """Raised when generated Report.sections != the persona SSoT contract.

    The Pydantic Report only enforces ``min_length=1``; the persona
    contract requires exact equality (same titles, same order) with the
    list returned by ``report_sections_for(persona, scope)``. Missing /
    extra / reordered / wrong-titled sections raise this exception
    AFTER schema validation succeeds (the schema accepts the shape;
    the completeness contract rejects the content).
    """


def _derive_report_id(run_metadata: RunMetadata) -> str:
    """Return a 16-char sha256 prefix uniquely identifying the report.

    Inputs hashed (pipe-joined): ``simulation_id``, ``scenario_hash``,
    ``seed_hash``, ``REPORT_VERSION``. Re-running the same scenario+seed
    yields the same id (deterministic), so consumers can diff reports
    across runs by id stability rather than by content scan.
    """
    fingerprint = (
        f"{run_metadata.simulation_id}|{run_metadata.scenario_hash}|"
        f"{run_metadata.seed_hash}|{REPORT_VERSION}"
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]


class PolicyReportGenerator:
    """Standalone Report builder: LLM call + canonical validation + persist.

    NOT a subclass of any other report class. Composes a Scenario (input
    spec) with a :class:`PolicyLLMClient` (network-bearing collaborator)
    and exposes two methods:

      * :meth:`generate` -- build and return a validated Report in-memory.
      * :meth:`generate_and_persist` -- generate, then atomically write
        ``policy_report.json`` to the supplied run directory and return
        the written path.

    The LLM client is injected so tests can substitute a deterministic
    stub (no real OpenAI traffic) without monkeypatching SDK internals.
    """

    def __init__(self, scenario: Scenario, llm: _LLMSeam) -> None:
        # ``llm`` is typed as the structural Protocol :class:`_LLMSeam`
        # rather than the concrete :class:`PolicyLLMClient` so any
        # collaborator with the right shape (production
        # PolicyLLMClient OR a deterministic test stub) type-checks
        # without `type: ignore`. The production seam class still
        # satisfies the Protocol by construction.
        self._scenario = scenario
        self._llm = llm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        run_metadata: RunMetadata,
        evidence_catalog: "EvidenceCatalog | None" = None,
        run_dir: Optional[Path] = None,
    ) -> Report:
        """Build and return a validated :class:`Report`.

        Steps:
          0. Preflight: refuse if ``scenario.persona`` disagrees with
             ``run_metadata.persona``. The post-LLM merge overwrites
             persona with the RunMetadata value (so RunMetadata is the
             single source of truth), which means a silent
             scenario-vs-metadata mismatch would pass cross-field
             validation while producing a report whose SECTIONS were
             built from the wrong persona's SSoT row. The check below
             closes that hole BEFORE the prompt is built so no LLM
             tokens are burned on a misconfigured request.
          1. Resolve the per-persona+scope section list via the SSoT
             (:func:`app.policy.persona_defaults.report_sections_for`).
             Used only to assert the prompt builder saw a non-empty
             list; the prompt builder itself re-resolves internally.
          2. Compose the LLM message list via
             :func:`build_user_prompt` (which internally builds the
             system prompt from the section list).
          3. Call ``self._llm.chat_json(messages=...)`` to obtain the
             structured JSON payload (a ``dict``).
          4. Strip any provenance keys the LLM may have echoed back so
             the caller-supplied RunMetadata is the single source of
             truth (see ``_PROVENANCE_KEYS_STRIPPED_FROM_LLM_PAYLOAD``).
          5. Merge the canonical provenance fields onto the stripped
             payload (header fields from RunMetadata, embedded
             RunMetadata snapshot, wall-clock ``generated_at``, derived
             ``report_id``).
          6. Hand the merged dict to ``Report.model_validate`` -- every
             schema-level invariant (canonical hypothesis tag, expected
             risk tag allowlist, header-vs-embedded provenance
             agreement, scope-iff-policy discriminator) fires here.
             Any drift raises :class:`pydantic.ValidationError`; the
             generator deliberately does NOT swallow that with a try /
             except + silent default fill -- the auditor wants drift to
             surface, not be papered over.

        Returns the validated :class:`Report` instance.
        """
        # Step 0: preflight persona-discriminator check. Raised BEFORE
        # any prompt is built so no tokens burn on a misconfigured
        # request. The Scenario discriminator already enforces
        # scope-iff-policy at scenario-validation time, so we only need
        # to check persona equality here.
        if self._scenario.persona != run_metadata.persona:
            raise ScenarioRunMetadataPersonaMismatch(
                f"scenario.persona={self._scenario.persona.value!r} "
                f"disagrees with run_metadata.persona="
                f"{run_metadata.persona.value!r}; the generator refuses "
                "to build prompts for one persona while persisting a "
                "report header that names another. Re-create the run "
                "with a Scenario whose persona matches the prepare-time "
                "RunMetadata."
            )

        # Step 1: SSoT sanity check (the prompt builder also calls this
        # internally; calling here gives a clearer error if the SSoT is
        # mis-keyed for this persona+scope before the LLM call burns
        # tokens).
        sections = report_sections_for(
            self._scenario.persona, self._scenario.scope,
        )
        if not sections:
            raise ValueError(
                "report_sections_for returned an empty list for "
                f"persona={self._scenario.persona!r} "
                f"scope={self._scenario.scope!r}; the SSoT must supply "
                "at least one section per (persona, scope) row."
            )

        # Step 2: compose messages from the pure prompt-builder helpers.
        # When an evidence catalog is supplied (the production path via
        # ``generate_and_persist``), append a context block listing the
        # valid evidence_ref keys so the LLM can satisfy the binding
        # contract without guessing. Direct callers of ``generate()``
        # may omit the catalog -- the binding step below is then skipped
        # entirely (preserves the existing in-memory-only test surface
        # that does not have a run_dir).
        messages = build_user_prompt(self._scenario, run_metadata)
        if evidence_catalog is not None:
            catalog_block = render_catalog_prompt_block(evidence_catalog)
            # Append the catalog as a tail block on the existing user
            # message. The prompt builder's allowlist would otherwise
            # need widening to depend on this module -- folding the
            # block here keeps the prompt builder pure.
            for entry in messages:
                if entry.get("role") == "user":
                    entry["content"] = (
                        f"{entry['content']}\n\n{catalog_block}"
                    )
                    break

        # Step 3: LLM call. The seam class wraps the upstream LLMClient;
        # this is the ONLY network-bearing call in the generator.
        # ``policy_max_tokens_report`` (env-configurable on PolicyLLMClient)
        # is the report-specific budget -- using the seam default would
        # silently undersize the budget for the multi-section persona
        # rows. Pinned to the seam's report attribute, NOT a magic
        # constant.
        raw_payload: Dict[str, Any] = self._llm.chat_json(
            messages=messages,
            max_tokens=self._llm.policy_max_tokens_report,
        )
        if not isinstance(raw_payload, dict):
            raise TypeError(
                "PolicyLLMClient.chat_json returned non-dict payload "
                f"(got {type(raw_payload).__name__}); the LLM must "
                "produce a single JSON object matching the Report shape."
            )

        # Step 4: drop any provenance keys the LLM echoed -- the
        # caller-supplied RunMetadata is authoritative.
        merged: Dict[str, Any] = {
            k: v
            for k, v in raw_payload.items()
            if k not in _PROVENANCE_KEYS_STRIPPED_FROM_LLM_PAYLOAD
        }

        # Step 5: attach the canonical provenance fields. Persona +
        # scope come from the Scenario discriminator (RunMetadata
        # carries persona but not scope; the Report.scope-iff-policy
        # invariant lives on the Report itself).
        merged["report_id"] = _derive_report_id(run_metadata)
        merged["simulation_id"] = run_metadata.simulation_id
        merged["persona"] = run_metadata.persona
        merged["scope"] = self._scenario.scope
        merged["scenario_hash"] = run_metadata.scenario_hash
        merged["seed_hash"] = run_metadata.seed_hash
        merged["generated_at"] = datetime.now(timezone.utc)
        merged["run_metadata"] = run_metadata

        # Step 6: validate. ValidationError surfaces any schema drift
        # the LLM introduced -- canonical-tag violation, expected-risk
        # allowlist violation, missing required section, etc. We do NOT
        # catch + paper over; the auditor wants the failure visible.
        report = Report.model_validate(merged)

        # Step 7: section-completeness contract. The Pydantic Report
        # only enforces ``min_length=1`` on ``sections``, which is too
        # weak: an LLM could emit the executive summary only and
        # silently drop every other persona-required section. The SSoT
        # at ``report_sections_for(persona, scope)`` defines the
        # canonical section list (titles in order) -- the rendered
        # report MUST carry exactly those titles in exactly that order
        # so the reader sees what the persona contract promises. Any
        # missing / extra / reordered / wrong-titled section raises
        # ReportSectionsMismatch (subclass of ValueError so callers can
        # branch on the failure mode without parsing the message).
        expected_titles = [s["title"] for s in sections]
        actual_titles = [s.title for s in report.sections]
        if actual_titles != expected_titles:
            raise ReportSectionsMismatch(
                "Report sections do not match the persona SSoT contract "
                f"for persona={self._scenario.persona.value!r} "
                f"scope={self._scenario.scope.value if self._scenario.scope else None!r}.\n"
                f"  Expected (in order): {expected_titles!r}\n"
                f"  Got (in order):      {actual_titles!r}"
            )

        # Step 7.5: transparency post-processor. Pure (no clock / env /
        # fs / random / network / LLM). Runs AFTER schema validation
        # and section-completeness so the post-processor sees a
        # fully-validated Report; runs BEFORE the language-guard
        # render contract below so any emitted flag wording is itself
        # gated against the banned-token regex on the final rendered
        # markdown output.
        report = apply_transparency_flags(report, self._scenario)

        # Step 7.6: evidence-binding contract. Re-validates every claim
        # whose tag demands grounding (source_grounded /
        # simulated_observation, plus aggregate-flavor inference)
        # against the supplied catalog. Claims with no resolvable
        # evidence_ref are demoted to ``requires_validation`` and the
        # demotion is logged onto ``run_metadata.warnings``; if the
        # demotion would corrupt the majority of the report (see
        # ``_MAX_DEMOTION_FRACTION``) the report is rejected with
        # :class:`EvidenceGroundingFailed`. Skipped when no catalog
        # is supplied so the in-memory-only test surface (direct
        # ``generate()`` callers) keeps working.
        if evidence_catalog is not None:
            report = self._apply_evidence_binding(
                report, evidence_catalog, run_metadata, run_dir,
            )

        # Step 7.7: advisory semantic pass (numeric / comparative
        # claim verifier + predictive-overclaim paraphrase detector).
        # Demotes claims to ``requires_validation`` on:
        #   * CONTRADICTED numeric verdicts (claim names a percent
        #     that disagrees with the artifact-reported swing -- or
        #     names "top driver" but the actual top factor differs),
        #   * un-hedged un-negated overclaim paraphrases (e.g.
        #     "is set to climb 30% next quarter").
        # UNSUPPORTED numeric verdicts (no claim-linked factor;
        # global match only) are logged as warnings but NOT demoted
        # -- false-positive cost outweighs the silent-overclaim risk
        # at the UNSUPPORTED severity level.
        # Skipped when no catalog (in-memory-only tests) so the
        # behavior aligns with evidence binding's gate.
        if evidence_catalog is not None:
            report = self._apply_advisory_semantic_pass(
                report, run_metadata, run_dir,
            )

        # Step 8: language contract. Render and assert no unhedged
        # banned tokens appear in the OUTPUT (claim text + section
        # titles + driver disclaimers + everything else the renderer
        # surfaces). Funnels through the shared
        # ``assert_report_render_contract`` helper -- the GET routes
        # use the same helper, so generator-side and read-time gates
        # share one code path and cannot drift. Failure raises
        # ``AssertionError`` (the language_guard's canonical exception
        # type for a violation); callers map that to a 500 response.
        assert_report_render_contract(report)

        return report

    def generate_and_persist(
        self,
        run_metadata: RunMetadata,
        run_dir: Path,
    ) -> "PersistedReportArtifacts":
        """Generate, language-guard, atomically persist JSON + markdown.

        Writes two artifacts under ``run_dir``:

        * ``policy_report.json`` -- canonical Report serialised via
          ``Report.model_dump_json(indent=2)`` so it round-trips
          through ``Report.model_validate_json``.
        * ``policy_report.md`` -- deterministic markdown render of the
          same Report via :func:`render_report`. Same byte-sequence the
          ``GET /report.md`` route serves on the read path.

        Both writes use :func:`app.utils.atomic_io.atomic_write_text`
        (temp file + ``os.replace``) so a crash mid-write leaves the
        previous file -- never a half-written one. The markdown is
        rendered BEFORE either disk write so a render failure cannot
        leave the .json behind without a matching .md.

        Returns a :class:`PersistedReportArtifacts` describing both
        output paths + their sha256 digests so the caller can update
        ``run_metadata.json`` (``output_paths`` + ``artifact_hashes``)
        in lockstep with the on-disk state.

        Backward-compat: the previous return type was ``Path``. Callers
        that did ``out = generate_and_persist(...); out.exists()`` now
        get the dataclass, whose ``.report_json_path`` attribute is a
        ``Path`` -- one rename per call site.
        """
        # Build the evidence catalog BEFORE the LLM call so the prompt
        # can surface the valid evidence_ref keys to the model. The
        # catalog is a pure read of the run dir + scenario; it tolerates
        # missing artifacts (the typical /report/generate-immediately-
        # after-prepare path has only ``seed_text`` lines + the prepare
        # manifest's artifacts, not yet any ``actions.jsonl`` /
        # interview files).
        evidence_catalog = build_evidence_catalog(run_dir, self._scenario)

        # generate() already runs the shared language contract check
        # (see step 8 of generate()); the markdown bytes below are
        # produced from the same validated Report so they pass the
        # banned-token guard by construction.
        report = self.generate(
            run_metadata,
            evidence_catalog=evidence_catalog,
            run_dir=run_dir,
        )

        # Compute payloads + hashes IN MEMORY before either disk write
        # so the on-disk side-effects can be ordered atomically and
        # both writes share the same canonical bytes.
        json_payload = report.model_dump_json(indent=2)
        md_payload = render_report(report)

        json_path = run_dir / "policy_report.json"
        md_path = run_dir / "policy_report.md"

        sha256_json = hashlib.sha256(json_payload.encode("utf-8")).hexdigest()
        sha256_md = hashlib.sha256(md_payload.encode("utf-8")).hexdigest()

        # Atomic ordering: ARTIFACT FIRST, METADATA SECOND, with a
        # PARSE-ONLY metadata pre-flight so a known-bad metadata file
        # fails BEFORE either artifact is written.
        #
        # Mirrors the sensitivity write path
        # (``api.policy.run_simulation_sensitivity``) so the two
        # artifact-producing routes agree on a single rule across the
        # codebase: the artifact bytes ARE the source of truth, and
        # ``run_metadata.json`` is a lagging index into those bytes.
        #
        # Sequencing:
        #
        #   1. Pre-flight: if ``run_metadata.json`` exists, READ +
        #      VALIDATE it (no write). A parse / schema failure raises
        #      here -- BEFORE any artifact lands on disk -- preserving
        #      the fail-clean contract (a corrupt metadata file does
        #      not leave fresh orphan artifacts beside it). A missing
        #      file is allowed: ``/prepare`` is what writes it, and a
        #      caller that invokes generate_and_persist against an
        #      empty directory legitimately has no record to update.
        #   2. Artifact write: ``policy_report.json`` then
        #      ``policy_report.md`` via ``atomic_write_text`` (temp
        #      file + ``os.replace``). The .md is rendered IN MEMORY
        #      above so a render failure cannot leave the .json behind
        #      without a matching .md.
        #   3. Metadata refresh: rewrite ``run_metadata.json`` with the
        #      refreshed ``output_paths`` + ``artifact_hashes`` using
        #      the pre-validated snapshot from step 1.
        #
        # Crash-scenario reasoning:
        #
        #   * Crash AFTER artifact write, BEFORE metadata refresh:
        #     ``policy_report.{json,md}`` are on disk with the fresh
        #     bytes; ``run_metadata.json``'s ``artifact_hashes`` still
        #     points to the previous-generation sha (or has no entry
        #     on a first-time generate). The read path
        #     (``GET /simulation/<id>/report``,
        #     ``GET /simulation/<id>/report.md``) opens the artifact
        #     directly and validates it against the Pydantic schema --
        #     it does NOT cross-check ``artifact_hashes`` -- so the
        #     stale index has no visible effect on the read surface.
        #     The next ``POST /report/generate`` reconverges both.
        #
        #   * Reversed ordering (metadata-first, artifact-second) would
        #     leave ``artifact_hashes`` promising a sha that the still-
        #     unwritten artifact does not yet match. A reader who lands
        #     in the window would see either the OLD artifact bytes
        #     (with a fresh-claim index) or a missing artifact (with a
        #     fresh-claim index), both of which break the
        #     "metadata describes what is on disk" invariant.
        #
        # The embedded ``report.run_metadata`` snapshot is intentionally
        # NOT refreshed here. It is the prepare-time provenance bundle
        # the report was generated against, and including the report's
        # own post-generate output-path hashes would recurse (the
        # report bytes depend on the embedded snapshot which would
        # depend on the bytes).
        run_metadata_path = run_dir / "run_metadata.json"
        existing_meta: Optional[RunMetadata] = None
        if run_metadata_path.exists():
            existing_raw = run_metadata_path.read_text(encoding="utf-8")
            # Raises ValidationError on corrupt / schema-drifted file --
            # BEFORE either artifact is written. The route handler maps
            # this to 500 REPORT_CORRUPTED with
            # ``details.reason='run_metadata_corrupt'``.
            existing_meta = RunMetadata.model_validate_json(existing_raw)

        atomic_write_text(json_path, json_payload)
        atomic_write_text(md_path, md_payload)

        if existing_meta is not None:
            output_paths = dict(existing_meta.output_paths)
            artifact_hashes = dict(existing_meta.artifact_hashes)
            output_paths["policy_report.json"] = "policy_report.json"
            output_paths["policy_report.md"] = "policy_report.md"
            artifact_hashes["policy_report.json"] = sha256_json
            artifact_hashes["policy_report.md"] = sha256_md
            updated_meta = existing_meta.model_copy(
                update={
                    "output_paths": output_paths,
                    "artifact_hashes": artifact_hashes,
                }
            )
            atomic_write_text(
                run_metadata_path,
                updated_meta.model_dump_json(indent=2),
            )

        return PersistedReportArtifacts(
            report_json_path=json_path,
            report_md_path=md_path,
            sha256_json=sha256_json,
            sha256_md=sha256_md,
        )

    # ------------------------------------------------------------------
    # Evidence-binding helpers
    # ------------------------------------------------------------------

    def _apply_evidence_binding(
        self,
        report: Report,
        catalog: EvidenceCatalog,
        run_metadata: RunMetadata,
        run_dir: Optional[Path] = None,
    ) -> Report:
        """Re-validate every grounding-required claim against ``catalog``.

        Two-tier binding check per claim that needs evidence:

          1. **Catalog membership** -- at least one of the claim's
             ``evidence_refs`` resolves via
             :meth:`EvidenceCatalog.contains` (the ``(ref_type,
             ref_id)`` pair exists in the run's catalog).
          2. **Semantic match** -- the resolving ref's ``ref_type``
             is in the tag's whitelist
             (:data:`_VALID_REF_KINDS_BY_TAG`). A
             ``source_grounded`` claim citing a
             ``simulation_action`` resolves the membership check but
             over-promises (conflating sim output with real source
             material); a ``simulated_observation`` citing an
             ``external_source`` does the inverse.

        Behavior on failure:

          * The claim's tag is demoted to ``requires_validation``.
            The warning text encodes WHICH check failed so an
            operator reading ``run_metadata.warnings`` can tell
            "no resolvable ref" apart from "semantic mismatch".
          * Each demotion warning is BOTH appended to the in-memory
            ``run_metadata.warnings`` list AND -- when ``run_dir`` is
            supplied -- persisted via :func:`refresh_warning` so a
            crash between the binding loop and the eventual
            ``generate_and_persist`` metadata refresh cannot lose
            the binding-failure record. The dual write is idempotent
            because :func:`refresh_warning` deduplicates on exact
            string match.
          * If the demoted fraction exceeds
            :data:`_MAX_DEMOTION_FRACTION`, the whole report is
            rejected with :class:`EvidenceGroundingFailed`.

        Returns a NEW :class:`Report` (the input is not mutated).
        Uses the same model-dump + model-validate round-trip pattern
        :func:`apply_transparency_flags` uses so the schema's
        after-validators all fire on the demoted shape (e.g. the
        validity-flag hygiene validator catches a dangling claim_ref
        introduced by an out-of-band demotion).
        """
        executive_claim_ids = {c.claim_id for c in report.executive_summary}

        # Walk every claim once; collect demotions + total claim count.
        total_claims = 0
        demotions: List[tuple[str, str]] = []  # (claim_id, reason)

        def _needs_evidence(claim: Claim) -> bool:
            if claim.tag.value in _TAGS_REQUIRING_EVIDENCE:
                return True
            if (
                claim.tag == HypothesisTag.INFERENCE
                and _is_aggregate_claim(claim, executive_claim_ids)
            ):
                return True
            return False

        def _binding_outcome(claim: Claim) -> Optional[str]:
            """Return None if the claim is properly bound, else a
            human-readable reason for the demotion.

            Two failure modes the caller cares to distinguish:
              * ``no_resolvable_ref``: NO supplied ref hits the
                catalog at all. The claim is dangling.
              * ``semantic_mismatch``: at least one ref hits the
                catalog, but NONE of the hitting refs carry a
                ``ref_type`` permitted for the claim's tag. The
                claim points to real artifacts but to the WRONG
                KIND for what it claims to be.
            """
            allowed = _VALID_REF_KINDS_BY_TAG.get(claim.tag.value, frozenset())
            any_resolved = False
            any_semantically_valid = False
            for ref in claim.evidence_refs:
                resolved = catalog.contains(ref.ref_type, ref.ref_id)
                if resolved:
                    any_resolved = True
                    if ref.ref_type in allowed:
                        any_semantically_valid = True
                        # No need to keep scanning -- the claim has
                        # at least one fully valid binding.
                        return None
            if not any_resolved:
                return (
                    f"claim {claim.claim_id!r} (tag={claim.tag.value}) "
                    f"failed evidence binding: "
                    f"{len(claim.evidence_refs)} ref(s) supplied, "
                    "none resolve against the run's evidence catalog "
                    "(no_resolvable_ref)"
                )
            # any_resolved AND not any_semantically_valid: every
            # hitting ref is the wrong KIND for the claim's tag.
            assert not any_semantically_valid
            supplied_kinds = sorted({r.ref_type for r in claim.evidence_refs})
            return (
                f"claim {claim.claim_id!r} (tag={claim.tag.value}) "
                f"failed evidence binding: refs resolved against the "
                f"catalog but every ref_type ({supplied_kinds!r}) is "
                f"outside the tag's whitelist ({sorted(allowed)!r}) "
                "(semantic_mismatch)"
            )

        # Iterate over executive_summary + sections[*].claims + evidence_gaps
        # to cover every Claim instance the schema persists.
        all_claim_buckets: List[List[Claim]] = [
            report.executive_summary,
            *[sec.claims for sec in report.sections],
            report.evidence_gaps,
        ]
        for bucket in all_claim_buckets:
            for claim in bucket:
                total_claims += 1
                if _needs_evidence(claim):
                    reason = _binding_outcome(claim)
                    if reason is not None:
                        demotions.append((claim.claim_id, reason))

        # Reject the whole report if too many demotions would land.
        # total_claims == 0 is impossible because the schema enforces
        # min_length=1 on sections AND a section with claims=[] would
        # not contribute -- but a section with no claims is allowed,
        # so we guard against ZeroDivisionError defensively.
        if total_claims and (
            len(demotions) / total_claims > _MAX_DEMOTION_FRACTION
        ):
            raise EvidenceGroundingFailed(
                f"Evidence-binding contract failed for "
                f"{len(demotions)}/{total_claims} claims "
                f"(> {int(_MAX_DEMOTION_FRACTION * 100)}% threshold); "
                "report would be structurally meaningless after demotion. "
                f"First failing claim: {demotions[0][1] if demotions else 'n/a'}"
            )

        # No demotions -> return the original report unchanged.
        if not demotions:
            return report

        # Demote: dump report to dict, rewrite the tag on each failing
        # claim, re-validate so every Report after-validator (including
        # validity-flag hygiene) sees the demoted shape.
        demoted_ids = {cid for cid, _ in demotions}
        payload = report.model_dump(mode="python")

        def _rewrite_bucket(bucket: List[Dict[str, Any]]) -> None:
            for claim in bucket:
                if claim.get("claim_id") in demoted_ids:
                    claim["tag"] = HypothesisTag.REQUIRES_VALIDATION.value

        _rewrite_bucket(payload.get("executive_summary", []))
        for sec in payload.get("sections", []):
            _rewrite_bucket(sec.get("claims", []))
        _rewrite_bucket(payload.get("evidence_gaps", []))

        # Append a single human-readable warning per demoted claim.
        # Two writes per warning:
        #   1. In-memory append onto ``run_metadata.warnings`` so the
        #      generator's downstream steps (and any caller that
        #      consumes the returned RunMetadata in-process) see the
        #      demotion immediately.
        #   2. ``refresh_warning(run_dir, ...)`` so the warning lands
        #      on disk atomically and survives a crash between the
        #      binding loop and the eventual ``generate_and_persist``
        #      metadata refresh. The on-disk write is idempotent
        #      (duplicate warnings are skipped) so the generator's
        #      end-of-flow ``run_metadata.json`` write does not
        #      double-record the same demotion.
        #
        # ``run_dir`` is optional because the in-memory-only test
        # surface (direct ``generate()`` callers) does not supply a
        # run directory; in that case only the in-memory append fires
        # and the test asserts against the returned report + the
        # mutated run_metadata snapshot.
        for _, reason in demotions:
            warning_text = (
                f"evidence_binding: demoted to requires_validation -- {reason}"
            )
            run_metadata.warnings.append(warning_text)
            if run_dir is not None:
                try:
                    refresh_warning(run_dir, warning_text)
                except (OSError, ValidationError):
                    # Persistent-write failure must NOT abort report
                    # generation -- the in-memory append already
                    # carries the warning forward to the final
                    # ``generate_and_persist`` write, so the operator
                    # still sees the demotion in the eventual on-disk
                    # snapshot. The exception is swallowed but not
                    # silently lost: the underlying refresh helper
                    # logs nothing, but the schema-validation failure
                    # mode here would re-surface at the next
                    # ``run_metadata.json`` read.
                    pass

        return Report.model_validate(payload)

    # ------------------------------------------------------------------
    # Advisory semantic pass (numeric verifier + overclaim paraphrases)
    # ------------------------------------------------------------------

    def _apply_advisory_semantic_pass(
        self,
        report: Report,
        run_metadata: RunMetadata,
        run_dir: Optional[Path],
    ) -> Report:
        """Run the advisory verifiers over every claim and demote on
        contradictions / un-pardoned overclaims.

        Two verifiers fire over the union of claim locations
        (``executive_summary`` + ``sections[*].claims`` +
        ``evidence_gaps``):

          1. :func:`verify_claims` against the run's persisted
             ``sensitivity.json`` (if present). Contradicted percent
             claims and contradicted "top driver" assertions ->
             demote. Unsupported / not-verifiable / not-applicable
             verdicts -> log only, no demotion (false-positive cost
             at those severity levels is higher than the silent-
             overclaim risk).
          2. :func:`find_un_pardoned_paraphrases` on every claim
             text. Findings with ``hedged=False`` AND
             ``negated=False`` -> demote.

        Demotion semantics mirror the evidence-binding step: in-
        memory append onto ``run_metadata.warnings`` plus atomic
        ``refresh_warning`` so a crash between this step and the
        eventual metadata refresh cannot lose the warning. The
        report is round-tripped through ``Report.model_validate``
        after demotion so every Report after-validator runs on the
        demoted shape.

        Returns the (possibly demoted) Report. Pure aside from the
        ``run_dir``-backed ``refresh_warning`` calls.
        """
        from app.services.policy.numeric_claim_verifier import (
            Verdict,
            verify_claims,
        )
        from app.services.policy.overclaim_paraphrases import (
            find_un_pardoned_paraphrases,
        )

        # Load the persisted sensitivity artifact if it exists. The
        # loader distinguishes three states:
        #   * None                     -- sensitivity never run; OK.
        #   * _SensitivityCorrupted    -- file exists but is unreadable.
        #     The advisory pass surfaces an explicit warning so the
        #     "guardrails silently disabled" failure mode the
        #     reviewer flagged cannot happen quietly.
        #   * SensitivityReport        -- usable.
        sensitivity_report = _load_sensitivity_report(run_dir)
        sensitivity_corrupted = isinstance(
            sensitivity_report, _SensitivityCorrupted,
        )
        if sensitivity_corrupted:
            corrupt_reason = sensitivity_report.reason
            # Don't pass the sentinel to the verifier -- give it None
            # so the per-claim verdicts land as NOT_VERIFIABLE
            # cleanly. The loud warning below tells the operator
            # WHY (corrupt artifact, not "never run").
            sensitivity_report = None

        # Collect every claim location once so we walk the report a
        # single time per verifier.
        all_claims: List[Claim] = []
        all_claims.extend(report.executive_summary)
        for sec in report.sections:
            all_claims.extend(sec.claims)
        all_claims.extend(report.evidence_gaps)
        if not all_claims:
            return report

        demoted_ids: set[str] = set()
        warnings: List[str] = []

        if sensitivity_corrupted:
            warnings.append(
                "numeric_claim_verifier: sensitivity_corrupted -- "
                "<run_dir>/sensitivity.json exists but failed to "
                f"parse ({corrupt_reason}); numeric / comparative "
                "claims in this report could NOT be ground-truthed "
                "against the artifact and land as NOT_VERIFIABLE. "
                "Re-run /sensitivity to regenerate the artifact."
            )

        # 1) Numeric / comparative verifier.
        for verdict in verify_claims(all_claims, sensitivity_report):
            if verdict.verdict == Verdict.CONTRADICTED:
                demoted_ids.add(verdict.claim_id)
                warnings.append(
                    f"numeric_claim_verifier: demoted to requires_validation -- "
                    f"claim {verdict.claim_id!r} CONTRADICTED ({verdict.parsed}); "
                    f"{verdict.reason}"
                )
            elif verdict.verdict == Verdict.UNSUPPORTED:
                # Advisory-only: log but do not demote. The
                # UNSUPPORTED verdict means a numeric claim matched
                # a GLOBAL factor swing but no claim-linked factor;
                # demoting at this severity would silently kill
                # paraphrased real findings.
                warnings.append(
                    f"numeric_claim_verifier: advisory -- claim "
                    f"{verdict.claim_id!r} UNSUPPORTED ({verdict.parsed}); "
                    f"{verdict.reason}"
                )

        # 2) Predictive-overclaim paraphrase detector.
        for claim in all_claims:
            text = getattr(claim, "text", "") or ""
            if not text:
                continue
            findings = find_un_pardoned_paraphrases(text)
            if not findings:
                continue
            pattern_ids = sorted({f.pattern_id for f in findings})
            demoted_ids.add(claim.claim_id)
            warnings.append(
                f"overclaim_paraphrases: demoted to requires_validation -- "
                f"claim {claim.claim_id!r} matches un-hedged overclaim "
                f"patterns {pattern_ids!r}"
            )

        if not warnings:
            return report

        # Persist warnings (both in-memory + atomic per-warning to
        # disk) using the same dual-write contract as evidence
        # binding. ``refresh_warning`` is idempotent on duplicate
        # strings so generate_and_persist's eventual metadata write
        # does not double-record.
        for w in warnings:
            run_metadata.warnings.append(w)
            if run_dir is not None:
                try:
                    refresh_warning(run_dir, w)
                except (OSError, ValidationError):
                    pass

        if not demoted_ids:
            return report

        # Demote: rewrite tag on every flagged claim, re-validate
        # the full Report so every after-validator (validity-flag
        # hygiene, scope-iff-policy, header-vs-embedded-metadata)
        # fires on the demoted shape.
        payload = report.model_dump(mode="python")

        def _rewrite_bucket(bucket: List[Dict[str, Any]]) -> None:
            for claim_d in bucket:
                if claim_d.get("claim_id") in demoted_ids:
                    claim_d["tag"] = HypothesisTag.REQUIRES_VALIDATION.value

        _rewrite_bucket(payload.get("executive_summary", []))
        for sec in payload.get("sections", []):
            _rewrite_bucket(sec.get("claims", []))
        _rewrite_bucket(payload.get("evidence_gaps", []))

        return Report.model_validate(payload)


# Sentinel that ``_load_sensitivity_report`` returns when the file
# EXISTS but failed to parse / validate. Distinct from ``None`` so
# the caller can warn loudly + persist a warning instead of
# silently treating corrupt sensitivity as "sensitivity not run".
class _SensitivityCorrupted:
    """Sentinel: ``sensitivity.json`` exists but is unreadable.

    The advisory pass treats this as DIFFERENT from None:

      * ``None`` -> sensitivity was never run. Numeric / comparative
        verdicts land as NOT_VERIFIABLE (no demotion implication;
        an operator's choice not to run sensitivity is not a bug).
      * ``_SensitivityCorrupted`` -> sensitivity was run but the
        artifact is unreadable. The pass appends an explicit
        ``sensitivity_corrupted`` warning to ``run_metadata.warnings``
        AND persists it via refresh_warning so the demotion-aware
        log surface reflects "we cannot ground numeric claims for
        this report -- sensitivity exists on disk but is corrupt".
        Numeric / comparative claims still land as NOT_VERIFIABLE
        but the operator-visible reason is different.
    """

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason


def _load_sensitivity_report(run_dir: Optional[Path]) -> Optional[Any]:
    """Read ``<run_dir>/sensitivity.json`` distinguishing three states.

    Returns one of:

      * ``None``                       -- file does NOT exist (or
        ``run_dir`` is unavailable). Sensitivity was not run; the
        advisory pass treats numeric claims as NOT_VERIFIABLE
        without surfacing a warning (it isn't a bug to skip
        sensitivity).
      * :class:`_SensitivityCorrupted` -- file EXISTS but fails to
        parse / validate. The advisory pass surfaces a loud warning
        AND persists it via ``refresh_warning`` so the demotion log
        records that numeric verification effectively could not run
        because the artifact is corrupt. Distinct from None on
        purpose (silently disabling the guardrail on a corrupt
        artifact is the bug this distinction prevents).
      * :class:`SensitivityReport`     -- file loaded cleanly.
    """
    if run_dir is None:
        return None
    path = Path(run_dir) / "sensitivity.json"
    if not path.is_file():
        return None
    try:
        # Lazy import to keep the generator's import graph slim --
        # the sensitivity module pulls numpy.
        from app.policy.sensitivity.oat_runner import SensitivityReport

        return SensitivityReport.model_validate_json(
            path.read_text(encoding="utf-8"),
        )
    except (OSError, ValidationError, ValueError) as exc:
        return _SensitivityCorrupted(
            reason=f"{type(exc).__name__}: {str(exc)[:200]}"
        )


@dataclass(frozen=True)
class PersistedReportArtifacts:
    """Return shape of :meth:`PolicyReportGenerator.generate_and_persist`.

    Carries the two on-disk paths + their sha256 digests so the route
    handler can echo the digests on the response without re-reading
    the files. The dataclass is frozen so callers cannot mutate the
    record after the fact.
    """

    report_json_path: Path
    report_md_path: Path
    sha256_json: str
    sha256_md: str


__all__: List[str] = [
    "EvidenceGroundingFailed",
    "PersistedReportArtifacts",
    "PolicyReportGenerator",
    "ReportSectionsMismatch",
    "ScenarioRunMetadataPersonaMismatch",
    "REPORT_VERSION",
]
