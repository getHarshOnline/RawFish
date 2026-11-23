"""Standalone creator report generator.

Owns the Pydantic shapes for this use case's :class:`CreatorScenario`
input and :class:`CreatorReport` output, plus the LLM-backed
:class:`CreatorReportGenerator` that composes a stub
or production :class:`app.seam.llm_patches.PolicyLLMClient`-shaped
collaborator with a section-completeness contract identical in spirit
to :class:`app.services.policy.policy_report_generator.PolicyReportGenerator`.

Why a standalone generator (not a wrapper around the policy generator):
  * The policy generator's :class:`app.policy.schema.Report` is typed
    against the policy :class:`app.policy.schema.Persona` enum
    (``creator | video | product | policy``), which does not include
    the personas this use case publishes (``video_creator``,
    ``newsletter_writer``, ``podcaster``). Reusing that report shape
    would require widening the enum at the policy layer, which is the
    wrong direction -- each use case owns its own report schema.
  * The shared infrastructure the platform actually exposes (registry,
    catalog payload, artifact manifest helpers, idempotency cache, run
    state machine) lives under :mod:`app.simulation_platform` and is
    consumed by both adapters. Sharing the generator class would
    re-couple this use case to the policy report shape; sharing the
    platform primitives is what the adapter abstraction is for.

Boundary contract with the LLM collaborator:
  * ``llm.chat_json(messages=..., max_tokens=...) -> Dict[str, Any]``
  * ``llm.policy_max_tokens_report: int``

The two-attribute Protocol mirrors the policy generator's seam so the
production :class:`app.seam.llm_patches.PolicyLLMClient` satisfies it
without modification AND test stubs need to expose the same two
attributes regardless of which adapter is under test.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.utils.atomic_io import atomic_write_text

from app.services.policy.prompt_builder import BANNED_TOKEN_GUIDANCE
from app.simulation_platform.guardrails.taxonomy import HYPOTHESIS_TAGS

from .catalog import CREATOR_SEGMENT_TYPES, CreatorPersona
from .defaults import report_sections_for


# Stable schema-shape marker. Bump when the on-disk
# :class:`CreatorReport` shape changes in a way consumers should
# notice when diffing identifiers across runs. Mirrors the
# ``REPORT_VERSION`` discipline on the policy generator.
REPORT_VERSION: str = "v1"


# Hypothesis-tag taxonomy this use case accepts. Re-exports the
# platform-level :data:`HYPOTHESIS_TAGS` frozenset so the creator lane
# and the policy lane share a SINGLE source of truth (a value added
# on one side automatically lands on the other without an audit).
# The legacy name ``CANONICAL_CLAIM_TAGS`` is preserved as an alias so
# existing imports keep working; new code should import
# :data:`HYPOTHESIS_TAGS` from
# :mod:`app.simulation_platform.guardrails.taxonomy` directly.
CANONICAL_CLAIM_TAGS: frozenset[str] = HYPOTHESIS_TAGS


@runtime_checkable
class _LLMSeam(Protocol):
    """Structural Protocol the generator's ``llm`` collaborator must satisfy.

    Mirrors the :class:`_LLMSeam` on the policy generator so the same
    production seam class satisfies both adapters without modification.
    Marked ``@runtime_checkable`` so an ``isinstance`` check against
    duck-typed stubs passes.
    """

    policy_max_tokens_report: int

    def chat_json(
        self,
        messages: Any,
        *,
        max_tokens: int = ...,
    ) -> Dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Scenario shape -- creator owns its own minimal model
# ---------------------------------------------------------------------------


def _strict_config() -> ConfigDict:
    """Strict Pydantic config used by every model in this module."""
    return ConfigDict(populate_by_name=True, extra="forbid")


class AudienceSegment(BaseModel):
    """One creator cohort participating in the simulation.

    ``type`` MUST be drawn from the persona-keyed allowlist in
    :data:`app.creator.catalog.CREATOR_SEGMENT_TYPES`;
    the parent :class:`CreatorScenario` enforces that allowlist (the
    leaf model cannot validate against the persona without parent
    context, same pattern the policy schema uses).
    """

    model_config = _strict_config()

    segment_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    what_changes_their_mind: List[str] = Field(..., min_length=1)
    notes: Optional[str] = None


class CreatorScenario(BaseModel):
    """Top-level creator scenario."""

    model_config = _strict_config()

    persona: CreatorPersona
    title: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    seed_text: str = Field(..., min_length=1)
    stress_question: str = Field(..., min_length=1)
    stakeholders: List[AudienceSegment] = Field(..., min_length=1)
    assumptions: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)
    failure_modes_to_watch: List[str] = Field(default_factory=list)

    @field_validator("stakeholders")
    @classmethod
    def _segment_ids_unique(
        cls, value: List[AudienceSegment]
    ) -> List[AudienceSegment]:
        """Reject duplicate segment_ids -- a duplicate would silently
        collapse two distinct cohorts in the segment-ref lookup."""
        ids = [s.segment_id for s in value]
        if len(ids) != len(set(ids)):
            raise ValueError(
                f"CreatorScenario.stakeholders has duplicate segment_id "
                f"values: {sorted({i for i in ids if ids.count(i) > 1})!r}"
            )
        return value

    def validate_segment_types(self) -> "CreatorScenario":
        """Persona-aware ``AudienceSegment.type`` allowlist check.

        Called explicitly by :class:`CreatorUseCase`
        after :func:`CreatorScenario.model_validate` because the
        allowlist is keyed by persona and Pydantic field validators
        cannot see siblings cleanly without leaking the catalog import
        into the leaf model.
        """
        allowed = CREATOR_SEGMENT_TYPES[self.persona]
        for idx, seg in enumerate(self.stakeholders):
            if seg.type not in allowed:
                raise ValueError(
                    f"stakeholders[{idx}].type={seg.type!r} not valid for "
                    f"persona={self.persona.value!r}; "
                    f"allowed: {list(allowed)!r}"
                )
        return self


# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------


class Claim(BaseModel):
    """One report claim with strict canonical-tag provenance."""

    model_config = _strict_config()

    claim_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    tag: str = Field(..., min_length=1)
    segment_refs: List[str] = Field(default_factory=list)

    @field_validator("tag")
    @classmethod
    def _tag_is_canonical(cls, value: str) -> str:
        if value not in CANONICAL_CLAIM_TAGS:
            raise ValueError(
                f"Claim.tag={value!r} is not in the canonical taxonomy "
                f"{sorted(CANONICAL_CLAIM_TAGS)!r}."
            )
        return value


class Section(BaseModel):
    """One section of the rendered creator report."""

    model_config = _strict_config()

    section_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    claims: List[Claim] = Field(default_factory=list)


def _normalize_claim_payload(value: Any, fallback_id: str) -> Any:
    """Normalize harmless LLM key drift without inventing claim content."""
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    if "text" not in normalized and isinstance(normalized.get("claim"), str):
        normalized["text"] = normalized["claim"]
    if "tag" not in normalized and normalized.get("provenance") in CANONICAL_CLAIM_TAGS:
        normalized["tag"] = normalized["provenance"]
    if not normalized.get("claim_id"):
        normalized["claim_id"] = fallback_id
    if isinstance(normalized.get("segment_refs"), str):
        normalized["segment_refs"] = [normalized["segment_refs"]]
    allowed = {"claim_id", "text", "tag", "segment_refs"}
    return {k: v for k, v in normalized.items() if k in allowed}


def _normalize_claim_list_payload(value: Any, *, id_prefix: str) -> Any:
    if not isinstance(value, list):
        return value
    return [
        _normalize_claim_payload(claim, f"{id_prefix}_{idx:02d}")
        for idx, claim in enumerate(value, start=1)
    ]


def _normalize_section_payload(value: Any, section_idx: int) -> Any:
    """Normalize section ids and nested claims, leaving titles untouched."""
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    if not normalized.get("section_id"):
        normalized["section_id"] = f"sec_{section_idx:02d}"
    claims = normalized.get("claims")
    if isinstance(claims, list):
        normalized["claims"] = [
            _normalize_claim_payload(
                claim,
                f"c_{section_idx:02d}_{claim_idx:02d}",
            )
            for claim_idx, claim in enumerate(claims, start=1)
        ]
    allowed = {"section_id", "title", "claims"}
    return {k: v for k, v in normalized.items() if k in allowed}


def _normalize_report_draft_payload(
    value: Any,
    *,
    strip_top_level_extras: bool = True,
) -> Any:
    """Normalize real-LLM draft JSON into the strict RawFish report draft.

    This only repairs structural metadata and common key aliases:
    ``claim`` -> ``text``, canonical ``provenance`` -> ``tag``, and
    missing deterministic ids. It deliberately does not rewrite section
    titles, fill absent claim text, or default missing tags; those still
    fail strict validation or the section-completeness check.
    """
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    normalized["executive_summary"] = _normalize_claim_list_payload(
        normalized.get("executive_summary", []),
        id_prefix="exec",
    )
    sections = normalized.get("sections")
    if isinstance(sections, list):
        normalized["sections"] = [
            _normalize_section_payload(section, idx)
            for idx, section in enumerate(sections, start=1)
        ]
    normalized["evidence_gaps"] = _normalize_claim_list_payload(
        normalized.get("evidence_gaps", []),
        id_prefix="gap",
    )
    if not strip_top_level_extras:
        return normalized
    allowed = {
        "executive_summary",
        "sections",
        "evidence_gaps",
        "field_validation_questions",
    }
    return {k: v for k, v in normalized.items() if k in allowed}


def _hedge_unhedged_banned_text(text: str) -> str:
    """Add sentence-local epistemic hedges to generated report prose.

    The shared RawFish language guard remains authoritative: this helper
    asks it which sentences are unsafe, then prepends a real hedge to
    those sentences only. It preserves the model's content while making
    uncertainty explicit before the report can be accepted.
    """
    if not text:
        return text
    from app.services.policy.language_guard import find_unhedged_banned_tokens

    findings = find_unhedged_banned_tokens(text)
    if not findings:
        return text
    out = text
    for sentence in sorted(
        {finding.sentence for finding in findings},
        key=len,
        reverse=True,
    ):
        stripped = sentence.strip()
        if not stripped:
            continue
        hedged = f"Under these assumptions, {stripped[0].lower()}{stripped[1:]}"
        out = out.replace(sentence, hedged)
    return out


def _hedge_claim_dict_text(claim: Any) -> None:
    if isinstance(claim, dict) and isinstance(claim.get("text"), str):
        claim["text"] = _hedge_unhedged_banned_text(claim["text"])


def _hedge_report_language(report: "CreatorReport") -> "CreatorReport":
    """Return a CreatorReport whose generated prose passes the language guard."""
    payload = report.model_dump(mode="python")
    for claim in payload.get("executive_summary", []) or []:
        _hedge_claim_dict_text(claim)
    for section in payload.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        for claim in section.get("claims", []) or []:
            _hedge_claim_dict_text(claim)
    for claim in payload.get("evidence_gaps", []) or []:
        _hedge_claim_dict_text(claim)
    payload["field_validation_questions"] = [
        _hedge_unhedged_banned_text(question)
        if isinstance(question, str)
        else question
        for question in payload.get("field_validation_questions", []) or []
    ]
    return CreatorReport.model_validate(payload)


class CreatorReport(BaseModel):
    """Top-level creator report shape.

    ``simulation_id`` / ``scenario_hash`` / ``seed_hash`` / ``persona``
    on the header are filled by the generator from the caller-supplied
    run-metadata snapshot; the LLM never produces them. ``generated_at``
    is captured by the generator at construction time and round-trips
    through ``CreatorReport.model_validate_json``.
    """

    model_config = _strict_config()

    report_id: str = Field(..., min_length=1)
    simulation_id: str = Field(..., min_length=1)
    persona: CreatorPersona
    scenario_hash: str = Field(..., min_length=1)
    seed_hash: str = Field(..., min_length=1)
    generated_at: datetime
    executive_summary: List[Claim] = Field(default_factory=list)
    sections: List[Section] = Field(..., min_length=1)
    evidence_gaps: List[Claim] = Field(default_factory=list)
    field_validation_questions: List[str] = Field(default_factory=list)


class CreatorReportLLMDraft(BaseModel):
    """LLM-owned subset of :class:`CreatorReport`.

    The generator instructs the LLM not to emit provenance fields
    (``report_id`` / ``simulation_id`` / ``persona`` / ``scenario_hash``
    / ``seed_hash`` / ``generated_at``) -- the caller fills them after
    the response, then the merged dict is validated against
    :class:`CreatorReport`. A strict-JSON-mode LLM client (
    :class:`StructuredOutputClient`) validates the raw response
    BEFORE that merge, so the target schema MUST omit the
    caller-owned fields or strict mode rejects valid drafts.

    Field set: the claim/section/validation lists the LLM is
    responsible for. A draft-only normalizer accepts harmless
    key drift from real local models (``claim`` -> ``text``,
    canonical ``provenance`` -> ``tag``, missing ids) before
    validation; the persisted :class:`CreatorReport` model remains
    strict.
    """

    model_config = _strict_config()

    executive_summary: List[Claim] = Field(default_factory=list)
    sections: List[Section] = Field(..., min_length=1)
    evidence_gaps: List[Claim] = Field(default_factory=list)
    field_validation_questions: List[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_real_llm_draft(cls, value: Any) -> Any:
        return _normalize_report_draft_payload(
            value,
            strip_top_level_extras=False,
        )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ScenarioRunMetadataPersonaMismatch(ValueError):
    """Raised when ``scenario.persona`` disagrees with the run-metadata
    persona supplied to :meth:`CreatorReportGenerator.generate`.
    """


class ReportSectionsMismatch(ValueError):
    """Raised when generated report sections do not match the SSoT
    contract from :func:`app.creator.defaults.report_sections_for`.
    """


# ---------------------------------------------------------------------------
# Provenance / id derivation
# ---------------------------------------------------------------------------


# Keys the generator overwrites on the LLM payload before
# ``CreatorReport.model_validate`` runs. The LLM is instructed never to
# fabricate provenance, but a payload that nevertheless echoes these
# fields must NOT override the caller-supplied snapshot.
_PROVENANCE_KEYS_STRIPPED_FROM_LLM_PAYLOAD: tuple[str, ...] = (
    "report_id",
    "simulation_id",
    "persona",
    "scenario_hash",
    "seed_hash",
    "generated_at",
)


def _derive_report_id(
    simulation_id: str,
    scenario_hash: str,
    seed_hash: str,
) -> str:
    """Return a deterministic 16-char sha256 prefix.

    Inputs hashed (pipe-joined): ``simulation_id``, ``scenario_hash``,
    ``seed_hash``, :data:`REPORT_VERSION`. Re-running the same scenario
    yields the same id; the version string bumps when the report shape
    changes in a way consumers should diff by identifier.
    """
    fingerprint = (
        f"{simulation_id}|{scenario_hash}|{seed_hash}|{REPORT_VERSION}"
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


def _build_messages(
    scenario: CreatorScenario,
    expected_titles: List[str],
) -> List[Dict[str, str]]:
    """Compose the LLM message list.

    Kept local to this module because the prompt content is
    use-case-specific. The system prompt names the required sections
    verbatim so the LLM cannot accidentally drop one; the user prompt
    carries the scenario inputs the generator owns.
    """
    titles_block = "\n".join(f"- {t}" for t in expected_titles)
    sections_skeleton = ",\n    ".join(
        (
            '{"section_id": "sec_%02d", "title": %s, '
            '"claims": [{"claim_id": "c_%02d_01", "text": "...", '
            '"tag": "simulated_observation", "segment_refs": []}]}'
        )
        % (idx, json.dumps(title), idx)
        for idx, title in enumerate(expected_titles, start=1)
    )
    system = (
        "You produce a creator report. Return a single "
        "JSON object matching the requested shape. Use these exact top-level "
        "keys only: executive_summary, sections, evidence_gaps, "
        "field_validation_questions. Every claim object MUST use exactly "
        "claim_id, text, tag, and segment_refs; do not use claim/provenance "
        "aliases. Every claim tag MUST be "
        f"one of: {sorted(CANONICAL_CLAIM_TAGS)!r}. The 'sections' array "
        "MUST contain exactly these titles in this order:\n"
        f"{titles_block}\n"
        "Required JSON skeleton:\n"
        "{\n"
        '  "executive_summary": [{"claim_id": "exec_01", "text": "...", '
        '"tag": "inference", "segment_refs": []}],\n'
        '  "sections": [\n'
        f"    {sections_skeleton}\n"
        "  ],\n"
        '  "evidence_gaps": [{"claim_id": "gap_01", "text": "...", '
        '"tag": "requires_validation", "segment_refs": []}],\n'
        '  "field_validation_questions": ["..."]\n'
        "}\n"
        "Do not invent provenance fields (simulation_id, persona, hashes, "
        "generated_at, report_id) -- the caller fills them.\n"
        f"{BANNED_TOKEN_GUIDANCE}"
    )
    user = (
        f"Title: {scenario.title}\n"
        f"Persona: {scenario.persona.value}\n"
        f"Stress question: {scenario.stress_question}\n"
        f"Seed text:\n{scenario.seed_text}\n"
        f"Stakeholder segments: "
        f"{[(s.segment_id, s.type) for s in scenario.stakeholders]!r}\n"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


class CreatorReportGenerator:
    """Standalone report builder for the creator use case.

    Composition mirror of
    :class:`app.services.policy.policy_report_generator.PolicyReportGenerator`
    but typed against this use case's own :class:`CreatorScenario` /
    :class:`CreatorReport` models. The LLM collaborator is injected so
    tests pass a deterministic stub without monkeypatching SDK
    internals.
    """

    def __init__(
        self, scenario: CreatorScenario, llm: _LLMSeam
    ) -> None:
        self._scenario = scenario
        self._llm = llm

    def generate(
        self,
        *,
        simulation_id: str,
        scenario_hash: str,
        seed_hash: str,
        run_persona: CreatorPersona,
    ) -> CreatorReport:
        """Build and return a validated :class:`CreatorReport`.

        Steps mirror the policy generator's contract: persona preflight,
        SSoT section list resolution, LLM call, provenance overlay,
        ``model_validate``, section-completeness check.
        """
        if self._scenario.persona != run_persona:
            raise ScenarioRunMetadataPersonaMismatch(
                f"scenario.persona={self._scenario.persona.value!r} "
                f"disagrees with run_persona={run_persona.value!r}; the "
                "generator refuses to build prompts for one persona "
                "while persisting a report header that names another."
            )

        sections = report_sections_for(self._scenario.persona)
        if not sections:
            raise ValueError(
                "report_sections_for returned an empty list for "
                f"persona={self._scenario.persona!r}; the SSoT must "
                "supply at least one section per persona."
            )
        expected_titles = [s["title"] for s in sections]

        messages = _build_messages(self._scenario, expected_titles)

        raw_payload: Dict[str, Any] = self._llm.chat_json(
            messages=messages,
            max_tokens=self._llm.policy_max_tokens_report,
        )
        if not isinstance(raw_payload, dict):
            raise TypeError(
                "LLM chat_json returned non-dict payload "
                f"(got {type(raw_payload).__name__}); the LLM must "
                "produce a single JSON object."
            )

        normalized_payload = _normalize_report_draft_payload(raw_payload)
        if not isinstance(normalized_payload, dict):
            raise TypeError(
                "LLM draft normalization returned non-dict payload "
                f"(got {type(normalized_payload).__name__}); the LLM must "
                "produce a single JSON object."
            )

        merged: Dict[str, Any] = {
            k: v
            for k, v in normalized_payload.items()
            if k not in _PROVENANCE_KEYS_STRIPPED_FROM_LLM_PAYLOAD
        }

        merged["report_id"] = _derive_report_id(
            simulation_id=simulation_id,
            scenario_hash=scenario_hash,
            seed_hash=seed_hash,
        )
        merged["simulation_id"] = simulation_id
        merged["persona"] = run_persona
        merged["scenario_hash"] = scenario_hash
        merged["seed_hash"] = seed_hash
        merged["generated_at"] = datetime.now(timezone.utc)

        report = CreatorReport.model_validate(merged)

        actual_titles = [s.title for s in report.sections]
        if actual_titles != expected_titles:
            raise ReportSectionsMismatch(
                "CreatorReport sections do not match the persona SSoT "
                f"contract for persona={self._scenario.persona.value!r}.\n"
                f"  Expected (in order): {expected_titles!r}\n"
                f"  Got (in order):      {actual_titles!r}"
            )
        return _hedge_report_language(report)

    def generate_and_persist(
        self,
        *,
        simulation_id: str,
        scenario_hash: str,
        seed_hash: str,
        run_persona: CreatorPersona,
        run_dir: Path,
    ) -> Path:
        """Generate, then atomically write ``creator_report.json``.

        The on-disk artifact IS consumed by a read path: the generic
        ``GET /api/sim/use-cases/creator/simulation/<id>/report``
        route in :mod:`app.api.use_cases` reads it back and validates
        the bytes against
        :class:`CreatorReport` (per
        :attr:`CreatorUseCase.report_schema`). A torn
        write would surface there as ``REPORT_CORRUPTED``; using
        :func:`app.utils.atomic_io.atomic_write_text` (temp file +
        ``os.replace``) means a crash mid-write leaves the previous
        file -- never a half-written one. Mirrors the same primitive
        the policy generator uses (see
        :meth:`app.services.policy.policy_report_generator.PolicyReportGenerator.generate_and_persist`).
        Returns the written path.
        """
        report = self.generate(
            simulation_id=simulation_id,
            scenario_hash=scenario_hash,
            seed_hash=seed_hash,
            run_persona=run_persona,
        )
        out = Path(run_dir) / "creator_report.json"
        atomic_write_text(str(out), report.model_dump_json(indent=2))
        return out


__all__ = [
    "REPORT_VERSION",
    "CANONICAL_CLAIM_TAGS",
    "AudienceSegment",
    "CreatorScenario",
    "Claim",
    "Section",
    "CreatorReport",
    "ScenarioRunMetadataPersonaMismatch",
    "ReportSectionsMismatch",
    "CreatorReportGenerator",
]
