"""Per-persona defaults for the creator use case.

Analogous to :mod:`app.policy.persona_defaults` but scoped to the
three creator personas. Carries the human-readable label, the lead
cohort question rendered by the persona picker, the soft capability
hint list surfaced in the catalog payload, and the canonical ordered
report section list each persona's generator produces.

Every resolver helper (``label_for``, ``cohort_question_for``,
``capabilities_for``, ``report_sections_for``) raises ``KeyError`` on
an unknown persona so a typo at the call site surfaces immediately
rather than silently substituting a stale default.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from .catalog import CreatorPersona


# Human-readable persona labels rendered in the wizard's persona picker.
PERSONA_LABELS: Dict[CreatorPersona, str] = {
    CreatorPersona.VIDEO_CREATOR: "Video Creator",
    CreatorPersona.NEWSLETTER_WRITER: "Newsletter Writer",
    CreatorPersona.PODCASTER: "Podcaster",
}


# Lead cohort question surfaced under the persona label in the wizard.
PERSONA_COHORT_QUESTIONS: Dict[CreatorPersona, str] = {
    CreatorPersona.VIDEO_CREATOR: (
        "How will my next video land with my subscriber cohorts?"
    ),
    CreatorPersona.NEWSLETTER_WRITER: (
        "How will my next post land with my reader cohorts?"
    ),
    CreatorPersona.PODCASTER: (
        "How will my next episode land with my listener cohorts?"
    ),
}


# Soft capability hint list surfaced in the catalog defaults bag. These
# are short string labels the frontend may render as chips next to the
# persona; the canonical per-persona contract is the report-sections
# list below.
PERSONA_CAPABILITIES: Dict[CreatorPersona, List[str]] = {
    CreatorPersona.VIDEO_CREATOR: [
        "audience_simulation",
        "draft_stress_test",
        "comment_cluster_surfacing",
    ],
    CreatorPersona.NEWSLETTER_WRITER: [
        "audience_simulation",
        "draft_stress_test",
        "reply_cluster_surfacing",
    ],
    CreatorPersona.PODCASTER: [
        "audience_simulation",
        "draft_stress_test",
        "review_cluster_surfacing",
    ],
}


# Per-persona ordered report section list. Same shape contract as the
# policy persona defaults: each entry is a (title, claim_guidance) pair.
# The generator's section-completeness check enforces exact equality +
# ordering of titles between the LLM payload and this list.
_VIDEO_CREATOR_SECTIONS: Tuple[Tuple[str, str], ...] = (
    (
        "Executive summary",
        "3-5 claims summarising the most material reactions",
    ),
    (
        "Loyal-subscriber reception",
        "How the returning audience receives the upload -- "
        "`simulated_observation` heavy",
    ),
    (
        "Casual-viewer drop-off risks",
        "Where the casual cohort tunes out -- `inference`",
    ),
    (
        "Comment-cluster themes",
        "Likely comment themes -- `inference` + `simulated_observation`",
    ),
    (
        "Drive-by hostile risks",
        "External pile-on signals -- `inference` + `requires_validation`",
    ),
    (
        "Recommender-feed amplification",
        "What the algorithm is likely to surface -- `inference`",
    ),
)


_NEWSLETTER_WRITER_SECTIONS: Tuple[Tuple[str, str], ...] = (
    (
        "Executive summary",
        "3-5 claims summarising the most material reactions",
    ),
    (
        "Open-rate cohort reception",
        "How the returning reader cohort engages -- `simulated_observation`",
    ),
    (
        "Reply-cluster themes",
        "Likely reply themes -- `inference` + `simulated_observation`",
    ),
    (
        "Unsubscribe risks",
        "What might drive churn -- `inference` + `requires_validation`",
    ),
    (
        "Forwarding triggers",
        "What might drive forwarding to a friend -- `inference`",
    ),
)


_PODCASTER_SECTIONS: Tuple[Tuple[str, str], ...] = (
    (
        "Executive summary",
        "3-5 claims summarising the most material reactions",
    ),
    (
        "Returning-listener reception",
        "How the loyal cohort receives the episode -- "
        "`simulated_observation`",
    ),
    (
        "Discovery-feed inflow signals",
        "Likely first-time-listener reception -- `inference`",
    ),
    (
        "Review-cluster themes",
        "Likely review themes -- `inference` + `simulated_observation`",
    ),
    (
        "Drop-off risks",
        "Where listeners stop the episode -- `inference`",
    ),
)


PERSONA_REPORT_SECTIONS: Dict[
    CreatorPersona, Tuple[Tuple[str, str], ...]
] = {
    CreatorPersona.VIDEO_CREATOR: _VIDEO_CREATOR_SECTIONS,
    CreatorPersona.NEWSLETTER_WRITER: _NEWSLETTER_WRITER_SECTIONS,
    CreatorPersona.PODCASTER: _PODCASTER_SECTIONS,
}


# Module-init invariant: every persona has at least one section. An
# empty section list would let the generator publish a structurally
# empty report whose section-completeness check vacuously passes.
def _assert_sections_invariant() -> None:
    """Module-init guard -- see block above for rationale."""
    for persona in CreatorPersona:
        assert persona in PERSONA_REPORT_SECTIONS, (
            f"PERSONA_REPORT_SECTIONS missing row for persona={persona!r}"
        )
        sections = PERSONA_REPORT_SECTIONS[persona]
        assert len(sections) > 0, (
            f"PERSONA_REPORT_SECTIONS[{persona!r}] is empty; the "
            "generator contract requires at least one section per persona."
        )
        titles = [t for t, _ in sections]
        assert len(titles) == len(set(titles)), (
            f"PERSONA_REPORT_SECTIONS[{persona!r}] has duplicate titles "
            f"{titles!r}; the renderer keys anchors on section titles."
        )


_assert_sections_invariant()


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def label_for(persona: CreatorPersona) -> str:
    """Return the human-readable persona label."""
    if persona not in PERSONA_LABELS:
        raise KeyError(f"No label entry for persona={persona!r}.")
    return PERSONA_LABELS[persona]


def cohort_question_for(persona: CreatorPersona) -> str:
    """Return the lead cohort question for the persona picker."""
    if persona not in PERSONA_COHORT_QUESTIONS:
        raise KeyError(f"No cohort_question entry for persona={persona!r}.")
    return PERSONA_COHORT_QUESTIONS[persona]


def capabilities_for(persona: CreatorPersona) -> List[str]:
    """Return the soft capability hint list for a persona."""
    if persona not in PERSONA_CAPABILITIES:
        raise KeyError(f"No capabilities entry for persona={persona!r}.")
    return list(PERSONA_CAPABILITIES[persona])


def report_sections_for(persona: CreatorPersona) -> List[Dict[str, str]]:
    """Return the ordered report section list for a persona.

    Each entry is a dict ``{"title": ..., "claim_guidance": ...}`` --
    same shape :mod:`app.policy.persona_defaults` uses so a future
    cross-use-case helper can consume both surfaces uniformly.
    """
    if persona not in PERSONA_REPORT_SECTIONS:
        raise KeyError(f"No report sections for persona={persona!r}.")
    return [
        {"title": title, "claim_guidance": guidance}
        for title, guidance in PERSONA_REPORT_SECTIONS[persona]
    ]


__all__ = [
    "PERSONA_LABELS",
    "PERSONA_COHORT_QUESTIONS",
    "PERSONA_CAPABILITIES",
    "PERSONA_REPORT_SECTIONS",
    "label_for",
    "cohort_question_for",
    "capabilities_for",
    "report_sections_for",
]
