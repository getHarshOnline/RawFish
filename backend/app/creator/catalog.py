"""Persona + segment-type catalog for the creator use case.

This module is the single source of truth for the persona ids and
segment-type allowlist published by the creator
adapter. It deliberately does NOT depend on :mod:`app.policy` -- the
policy persona/segment enums are scoped to that use case and reusing
them here would re-couple the two product surfaces at the schema layer.

Personas
--------
``video_creator``
    Long-form video author (YouTube, longform Vimeo, etc.) asking how
    their next upload will land with the subscriber base + drive-by
    audience the recommender brings in.
``newsletter_writer``
    Email / Substack-style author asking how a draft post will land
    with the open-rate cohort and the reply-engager cohort.
``podcaster``
    Audio creator asking how an episode will land with returning
    listeners + the discovery-feed inflow.

Segment types
-------------
Per-persona ordered tuple of segment-type ids. The frontend renders
the segment-type dropdown from this list; the adapter validates each
:class:`scenario.AudienceSegment.type` against the same list at
scenario-validation time so a typo never silently skips into the
prepare phase.

The five canonical segment types -- ``loyal_subscriber``,
``casual_viewer``, ``comment_engager``, ``drift_away``,
``hostile_drive_by`` -- are shared across all three personas; the
keyed dict shape keeps the door open for future persona-specific
extensions without a schema migration.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, Tuple


class CreatorPersona(str, Enum):
    """Closed taxonomy of personas this use case supports.

    Kept private to the creator package. The platform
    boundary uses plain strings (see
    :class:`app.simulation_platform.catalog.PersonaSpec`) so the enum
    never leaks across the seam; it exists internally to give the
    adapter a single grep-friendly definition of valid persona ids.
    """

    VIDEO_CREATOR = "video_creator"
    NEWSLETTER_WRITER = "newsletter_writer"
    PODCASTER = "podcaster"


# Per-persona segment-type allowlist. Tuple (not set) because order is
# significant -- the frontend renders the picker in this order, and a
# shuffled list would silently re-arrange the UI even though the
# allowlist content is identical.
CREATOR_SEGMENT_TYPES: Dict[CreatorPersona, Tuple[str, ...]] = {
    CreatorPersona.VIDEO_CREATOR: (
        "loyal_subscriber",
        "casual_viewer",
        "comment_engager",
        "drift_away",
        "hostile_drive_by",
    ),
    CreatorPersona.NEWSLETTER_WRITER: (
        "loyal_subscriber",
        "casual_viewer",
        "comment_engager",
        "drift_away",
        "hostile_drive_by",
    ),
    CreatorPersona.PODCASTER: (
        "loyal_subscriber",
        "casual_viewer",
        "comment_engager",
        "drift_away",
        "hostile_drive_by",
    ),
}


# Module-init invariant: every persona enum value has a segment-type
# row. Without this guard, adding a new persona without the matching
# segment-type row would let the adapter publish a persona the
# validator could not check. Fail loud at import.
def _assert_segment_type_parity() -> None:
    """Module-init guard -- see block above for rationale."""
    for persona in CreatorPersona:
        assert persona in CREATOR_SEGMENT_TYPES, (
            f"CREATOR_SEGMENT_TYPES missing row for persona={persona!r}"
        )
        assert len(CREATOR_SEGMENT_TYPES[persona]) > 0, (
            f"CREATOR_SEGMENT_TYPES[{persona!r}] is empty; an empty "
            "allowlist would silently reject every scenario."
        )


_assert_segment_type_parity()


__all__ = [
    "CreatorPersona",
    "CREATOR_SEGMENT_TYPES",
]
