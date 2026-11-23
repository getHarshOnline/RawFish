"""Evidence catalog + claim-evidence binding contract.

Builds an in-memory index of every artifact pointer a :class:`Claim`'s
``evidence_refs`` field can legally resolve against. The catalog is
constructed from a run directory plus the associated :class:`Scenario`
BEFORE the LLM is invoked; the report generator then:

  * surfaces the valid catalog keys to the LLM as part of the prompt
    context (so the LLM knows which ``evidence_refs`` will pass binding);
  * re-validates every claim's ``evidence_refs`` against the catalog
    after the Report is built, demoting claims that fail the binding
    contract (or rejecting the entire report if too many fail).

Sources walked (each tolerated when absent on disk):

  * ``seed_text`` lines  -> ``ref_type='seed_text_line'``, keys
    ``seed_text:L<1-based line number>``.
  * ``actions.jsonl``  -> ``ref_type='simulation_action'``, key per
    record's ``id`` / ``action_id`` / ``uuid`` field (whichever the
    runtime emitted; the loader tries them in declared order).
  * ``interviews/<uuid>.json``  -> ``ref_type='agent_interview'``, key
    is the file stem (the uuid).
  * Scenario-level ``external_sources``  -> ``ref_type='external_source'``,
    key is the URL / DOI verbatim. The Scenario schema does not require
    this field today, so the catalog reads it via getattr and tolerates
    its absence (empty list).

The catalog is purely additive context for the generator. It does NOT
read or write the report itself, never mutates the scenario, and is
safe to call against an empty run dir (the typical unit-test setup).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, List, Set

from pydantic import BaseModel, ConfigDict, Field


class EvidenceCatalog(BaseModel):
    """In-memory index of valid ``EvidenceRef`` keys for a run.

    Each ``ref_type`` -> set-of-valid-``ref_id`` mapping is kept as a
    plain set so a binding check is O(1). The model is Pydantic so it
    serialises cleanly into the prompt-context block the generator
    folds into the user message; a hand-built dict would work too,
    but the typed model documents the shape and lets future readers
    grep for one class name.

    Strict ``extra='forbid'`` so a future loader edit that adds a new
    ``ref_type`` key without first widening :class:`EvidenceRef`'s
    ``Literal`` (see ``app.policy.schema``) trips at validation time
    rather than producing a silently-unbindable evidence kind.

    Sets are serialised as sorted lists for deterministic prompt bytes;
    binding lookups go through :meth:`contains` which rebuilds the
    set on demand. The list field also gives ``model_dump`` a JSON-
    friendly shape without a custom encoder.
    """

    model_config = ConfigDict(extra="forbid")

    seed_text_line: List[str] = Field(default_factory=list)
    simulation_action: List[str] = Field(default_factory=list)
    agent_interview: List[str] = Field(default_factory=list)
    external_source: List[str] = Field(default_factory=list)

    def contains(self, ref_type: str, ref_id: str) -> bool:
        """Return True iff a given ``(ref_type, ref_id)`` resolves.

        Unknown ``ref_type`` returns False -- the schema ``Literal`` on
        :class:`EvidenceRef` already constrains the four valid values,
        so an out-of-band type here is a contract violation; returning
        False keeps the binding loop simple (no exception path) and the
        generator's demotion rule will then flag the claim as
        unbindable.
        """
        bucket = getattr(self, ref_type, None)
        if bucket is None:
            return False
        return ref_id in set(bucket)

    def total(self) -> int:
        """Total number of indexed evidence pointers (across all kinds)."""
        return (
            len(self.seed_text_line)
            + len(self.simulation_action)
            + len(self.agent_interview)
            + len(self.external_source)
        )


# ---------------------------------------------------------------------------
# Loader helpers
# ---------------------------------------------------------------------------


def _seed_text_line_keys(scenario: Any) -> List[str]:
    """Return one ``seed_text:L<n>`` key per non-empty line in seed_text.

    Empty / whitespace-only lines are skipped so a claim cannot bind
    against a blank line. Line numbers are 1-based to match the
    ``seed_text:L<line number>`` convention documented on
    :class:`app.policy.schema.EvidenceRef`.
    """
    seed_text = getattr(scenario, "seed_text", "") or ""
    keys: List[str] = []
    for idx, line in enumerate(seed_text.splitlines(), start=1):
        if line.strip():
            keys.append(f"seed_text:L{idx}")
    return keys


def _simulation_action_keys(run_dir: Path) -> List[str]:
    """Return one key per record in ``actions.jsonl`` (if present).

    Tolerates a missing / empty file: the runtime does not write this
    artifact in every test setup, and the catalog must remain usable
    against an empty run dir. A malformed JSON line is skipped (logged
    via stderr would be louder than helpful here -- the generator's
    binding loop will surface any unbindable claim downstream).

    The id field name is read from the first present of
    ``id`` / ``action_id`` / ``uuid``; matches the few naming
    conventions the runtime / mock emitters use today without forcing
    one of them as canonical here.
    """
    path = run_dir / "actions.jsonl"
    if not path.is_file():
        return []
    # Stream the JSONL file line-by-line so large runs do not pay the
    # cost of loading the whole file into memory before parsing. The
    # earlier ``read_text().splitlines()`` materialised every line
    # twice (once as the whole-file string, once as the list of
    # split lines); on a multi-thousand-action run that is a real
    # memory + time hit. Opening with ``encoding="utf-8"`` iterates
    # one decoded line at a time.
    keys: List[str] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            for id_field in ("id", "action_id", "uuid"):
                value = record.get(id_field)
                if isinstance(value, str) and value:
                    keys.append(value)
                    break
    return keys


def _agent_interview_keys(run_dir: Path) -> List[str]:
    """Return one key per ``interviews/<uuid>.json`` file in ``run_dir``.

    Key is the file stem (typically a uuid). Tolerates the absence of
    the ``interviews`` subdirectory entirely -- the runtime does not
    write interviews in every persona's lane today.
    """
    interviews_dir = run_dir / "interviews"
    if not interviews_dir.is_dir():
        return []
    keys: List[str] = []
    for child in sorted(interviews_dir.iterdir()):
        if child.is_file() and child.suffix == ".json":
            keys.append(child.stem)
    return keys


def _external_source_keys(scenario: Any) -> List[str]:
    """Return one key per scenario-declared external source (if any).

    The current :class:`Scenario` schema does not require this field;
    the catalog reads it via ``getattr`` so a scenario without external
    citations produces an empty list rather than raising. Accepted
    shapes: an iterable of strings (URLs / DOIs) OR an iterable of
    dicts with a ``url`` / ``ref_id`` field. Non-string entries are
    skipped; the binding loop will surface any unbindable claim.
    """
    raw = getattr(scenario, "external_sources", None)
    if not raw:
        return []
    keys: List[str] = []
    try:
        iterator: Iterable[Any] = iter(raw)
    except TypeError:
        return []
    for entry in iterator:
        if isinstance(entry, str) and entry:
            keys.append(entry)
        elif isinstance(entry, dict):
            for field in ("ref_id", "url", "doi"):
                value = entry.get(field)
                if isinstance(value, str) and value:
                    keys.append(value)
                    break
    return keys


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def build_evidence_catalog(
    run_dir: Path,
    scenario: Any,
) -> EvidenceCatalog:
    """Build the in-memory evidence catalog for a (``run_dir``, ``scenario``) pair.

    Pure read: never writes, never mutates the inputs. Tolerates
    every artifact's absence so the catalog is safe to construct in a
    bare unit-test ``tmp_path`` (only ``seed_text`` lines will appear
    in that case).

    Per-kind deduplication: the loader passes raw lists to
    :class:`EvidenceCatalog`; duplicates within one kind would let a
    claim bind against a "weight 2" evidence pointer that does not
    exist on disk. We dedupe here (preserve declared order) so the
    catalog matches the on-disk shape exactly.
    """
    def _dedupe(seq: List[str]) -> List[str]:
        seen: Set[str] = set()
        out: List[str] = []
        for item in seq:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return out

    return EvidenceCatalog(
        seed_text_line=_dedupe(_seed_text_line_keys(scenario)),
        simulation_action=_dedupe(_simulation_action_keys(run_dir)),
        agent_interview=_dedupe(_agent_interview_keys(run_dir)),
        external_source=_dedupe(_external_source_keys(scenario)),
    )


def render_catalog_prompt_block(catalog: EvidenceCatalog) -> str:
    """Render a human-readable prompt block enumerating valid evidence keys.

    Folded into the user message by
    :class:`app.services.policy.policy_report_generator.PolicyReportGenerator`
    so the LLM sees which ``evidence_refs`` will satisfy the binding
    contract. Empty kinds are still listed (with ``(none)``) so the
    LLM cannot infer "evidence not requested" from the absence of a
    kind heading.

    The block is intentionally compact: every key is enumerated, but
    long catalogs are truncated by the caller (it is the generator's
    decision how much of the catalog to surface; the renderer here is
    deterministic over the full list).
    """
    def _bullets(name: str, items: List[str]) -> str:
        if not items:
            return f"  {name}: (none)"
        rendered = "\n".join(f"    - {k}" for k in items)
        return f"  {name}:\n{rendered}"

    return (
        "Valid evidence_refs you may cite on any claim (each entry is "
        "(ref_type, ref_id); any ref_id outside the lists below will "
        "be rejected by the binding contract and the claim demoted):\n"
        f"{_bullets('seed_text_line', catalog.seed_text_line)}\n"
        f"{_bullets('simulation_action', catalog.simulation_action)}\n"
        f"{_bullets('agent_interview', catalog.agent_interview)}\n"
        f"{_bullets('external_source', catalog.external_source)}"
    )


__all__ = [
    "EvidenceCatalog",
    "build_evidence_catalog",
    "render_catalog_prompt_block",
]
