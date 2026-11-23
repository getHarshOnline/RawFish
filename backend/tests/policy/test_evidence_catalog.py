"""Tests for the evidence catalog builder.

Pins three contracts:
  * The catalog is built correctly from a minimal run_dir that contains
    only a scenario seed_text (no actions.jsonl, no interviews dir).
  * Each supported artifact kind (actions.jsonl, interviews dir,
    external_sources on the scenario) populates its bucket when present.
  * The catalog tolerates missing / malformed artifacts (skip rather
    than raise) so unit-test ``tmp_path`` fixtures need no scaffolding.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.services.policy.evidence_catalog import (
    EvidenceCatalog,
    build_evidence_catalog,
    render_catalog_prompt_block,
)


def _scenario(
    seed_text: str = "first seed line\nsecond seed line",
    external_sources=None,
) -> SimpleNamespace:
    """Build a duck-typed scenario the catalog reads via getattr.

    Using ``SimpleNamespace`` rather than the full :class:`Scenario`
    keeps these tests focused on the catalog contract (the full
    validator chain is exercised elsewhere).
    """
    return SimpleNamespace(
        seed_text=seed_text,
        external_sources=external_sources,
    )


# ----------------------------------------------------------------------
# Minimal run dir -- only seed_text contributes
# ----------------------------------------------------------------------


def test_catalog_built_from_minimal_run_dir(tmp_path: Path) -> None:
    """An empty run dir + a 2-line seed_text yields exactly 2 keys."""
    catalog = build_evidence_catalog(tmp_path, _scenario())
    assert isinstance(catalog, EvidenceCatalog)
    assert catalog.seed_text_line == ["seed_text:L1", "seed_text:L2"]
    assert catalog.simulation_action == []
    assert catalog.agent_interview == []
    assert catalog.external_source == []
    assert catalog.total() == 2


def test_catalog_skips_blank_seed_lines(tmp_path: Path) -> None:
    """Whitespace-only lines do not produce bindable keys.

    Otherwise a claim could bind against a blank line, which the
    renderer would surface as a broken pointer.
    """
    catalog = build_evidence_catalog(
        tmp_path,
        _scenario(seed_text="line one\n\n   \nline four"),
    )
    # Lines 1 and 4 are non-blank; 2/3 are skipped.
    assert catalog.seed_text_line == ["seed_text:L1", "seed_text:L4"]


# ----------------------------------------------------------------------
# Each artifact kind populates its bucket
# ----------------------------------------------------------------------


def test_catalog_walks_actions_jsonl(tmp_path: Path) -> None:
    """Each well-formed line in ``actions.jsonl`` contributes one key."""
    actions = tmp_path / "actions.jsonl"
    actions.write_text(
        json.dumps({"id": "act-uuid-1", "kind": "post"}) + "\n"
        + json.dumps({"action_id": "act-uuid-2"}) + "\n"
        + json.dumps({"uuid": "act-uuid-3"}) + "\n"
        + "   \n"  # blank line tolerated
        + "{ not json }\n",  # malformed line tolerated
        encoding="utf-8",
    )
    catalog = build_evidence_catalog(tmp_path, _scenario(seed_text="x"))
    assert catalog.simulation_action == [
        "act-uuid-1",
        "act-uuid-2",
        "act-uuid-3",
    ]


def test_catalog_walks_interviews_dir(tmp_path: Path) -> None:
    """Each ``<uuid>.json`` file in ``interviews/`` contributes one key."""
    interviews = tmp_path / "interviews"
    interviews.mkdir()
    (interviews / "interview-a.json").write_text("{}", encoding="utf-8")
    (interviews / "interview-b.json").write_text("{}", encoding="utf-8")
    # A non-json file in the dir must NOT contribute.
    (interviews / "scratch.txt").write_text("ignore me", encoding="utf-8")

    catalog = build_evidence_catalog(tmp_path, _scenario(seed_text="x"))
    assert catalog.agent_interview == ["interview-a", "interview-b"]


def test_catalog_walks_external_sources_strings(tmp_path: Path) -> None:
    """An iterable of URL strings on ``scenario.external_sources`` is used."""
    catalog = build_evidence_catalog(
        tmp_path,
        _scenario(
            seed_text="x",
            external_sources=[
                "https://example.com/policy-text",
                "https://other.example.com/study",
            ],
        ),
    )
    assert catalog.external_source == [
        "https://example.com/policy-text",
        "https://other.example.com/study",
    ]


def test_catalog_walks_external_sources_dict_shape(tmp_path: Path) -> None:
    """Dict-shaped entries are also accepted (url / ref_id / doi keys)."""
    catalog = build_evidence_catalog(
        tmp_path,
        _scenario(
            seed_text="x",
            external_sources=[
                {"url": "https://example.com/a"},
                {"ref_id": "ref-b"},
                {"doi": "10.1234/example"},
                {"unrelated": "skipped"},
                "https://example.com/c",
            ],
        ),
    )
    assert catalog.external_source == [
        "https://example.com/a",
        "ref-b",
        "10.1234/example",
        "https://example.com/c",
    ]


# ----------------------------------------------------------------------
# Contains + dedupe + render helper
# ----------------------------------------------------------------------


def test_contains_resolves_known_ref_and_rejects_unknown(tmp_path: Path) -> None:
    """``contains`` is the binding-time lookup helper."""
    catalog = build_evidence_catalog(tmp_path, _scenario(seed_text="line"))
    assert catalog.contains("seed_text_line", "seed_text:L1")
    assert not catalog.contains("seed_text_line", "seed_text:L99")
    # Unknown ref_type returns False rather than raising.
    assert not catalog.contains("nonexistent_kind", "anything")


def test_catalog_dedupes_within_a_kind(tmp_path: Path) -> None:
    """Duplicate keys within one kind collapse (preserve declared order)."""
    actions = tmp_path / "actions.jsonl"
    actions.write_text(
        json.dumps({"id": "act-1"}) + "\n"
        + json.dumps({"id": "act-1"}) + "\n"  # duplicate
        + json.dumps({"id": "act-2"}) + "\n",
        encoding="utf-8",
    )
    catalog = build_evidence_catalog(tmp_path, _scenario(seed_text="x"))
    assert catalog.simulation_action == ["act-1", "act-2"]


def test_render_catalog_prompt_block_lists_every_kind(tmp_path: Path) -> None:
    """The prompt-block renderer enumerates every kind, ``(none)`` for empty."""
    catalog = build_evidence_catalog(tmp_path, _scenario(seed_text="line"))
    block = render_catalog_prompt_block(catalog)
    assert "seed_text_line:" in block
    assert "seed_text:L1" in block
    # Empty kinds still appear with the (none) sentinel so the LLM
    # cannot infer "evidence not requested" from the absence of a
    # kind heading.
    assert "simulation_action: (none)" in block
    assert "agent_interview: (none)" in block
    assert "external_source: (none)" in block
