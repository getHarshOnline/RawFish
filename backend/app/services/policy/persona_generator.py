"""Deterministic policy-aware persona generator.

Ships deterministic OASIS-compatible Reddit + Twitter profile records
built from ``Scenario.stakeholders[]`` plus
:mod:`app.policy.persona_defaults` vocabulary -- **with zero LLM calls**.
LLM-driven enrichment is an explicit out-of-scope path; an
LLM-enriched subclass is the documented extension point.

Why a standalone class (not a subclass of upstream
:class:`OasisProfileGenerator`)?
The upstream ``__init__`` requires ``LLM_API_KEY`` and instantiates an
``OpenAI`` client. This generator must run with no LLM key configured, so
subclassing would fail at construction time. To keep the OASIS-record
shape identical, we still construct
:class:`app.services.oasis_profile_generator.OasisAgentProfile`
instances and reuse its ``to_reddit_format`` / ``to_twitter_format``
methods -- they're the single source of truth for the dict shape that
OASIS consumes (see ``backend/app/services/oasis_profile_generator.py``
L61-L117).

Determinism contract (verified by ``test_persona_generator.py``):

* ``profile_id = sha256(f"{scen_hash}:{segment_id}:{segment.name}")[:16]``.
* Every trait pick (MBTI, country, age, karma, follower counts) is
  derived from ``profile_id`` via modular arithmetic -- **no
  ``random.*``** anywhere. Two independent calls on the same
  :class:`Scenario` produce byte-identical JSON dumps.
* ``created_at`` is pinned to a fixed sentinel string
  (``_DETERMINISTIC_CREATED_AT``) to avoid leaking wall-clock time into
  the artifact. A future LLM-enriched generator may refresh this from
  the simulation's metadata clock.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Tuple

from ...policy.persona_defaults import (
    PERSONA_VOCABULARY,
    vocabulary_for,
)
from ...policy.schema import (
    AudienceSegment,
    Scenario,
)
from ...policy_core.scope_context import vocabulary_scope
from ...seam.zep_mock import scenario_hash
from ..oasis_profile_generator import OasisAgentProfile


# Deterministic sentinel for the OasisAgentProfile.created_at field. The
# upstream default uses ``datetime.now()`` which would defeat byte-stable
# round-tripping. The string is intentionally a valid YYYY-MM-DD literal so
# OASIS' downstream parsers do not choke. Treat this constant as the
# "schema-version" of the persona-generator's wire output.
_DETERMINISTIC_CREATED_AT: str = "2026-01-01"


# Numeric range bounds for deterministic derivation. Kept tight enough to
# look like a populated profile but bounded so the hashing-derived index
# does not produce wildly skewed cohorts.
_AGE_MIN: int = 22
_AGE_MAX: int = 65
_KARMA_MIN: int = 500
_KARMA_MAX: int = 5_000
_FOLLOWER_MIN: int = 100
_FOLLOWER_MAX: int = 2_000
_FRIEND_MIN: int = 50
_FRIEND_MAX: int = 500
_STATUSES_MIN: int = 100
_STATUSES_MAX: int = 3_000


# Closed catalogues lifted from the upstream :class:`OasisProfileGenerator`
# class-level constants so a deterministic round-trip keeps the same
# fingerprint surface OASIS expects (see L156-L167 of
# ``oasis_profile_generator.py``).
_MBTI_TYPES: Tuple[str, ...] = (
    "INTJ", "INTP", "ENTJ", "ENTP",
    "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISFJ", "ESTJ", "ESFJ",
    "ISTP", "ISFP", "ESTP", "ESFP",
)
_COUNTRIES: Tuple[str, ...] = (
    "China", "US", "UK", "Japan", "Germany", "France",
    "Canada", "Australia", "Brazil", "India", "South Korea",
)
# Institutional-account placeholder used when the segment type maps to an
# organisation rather than a single individual. Mirrors the upstream
# ``other`` placeholder gender for institutional accounts.
_INSTITUTIONAL_TYPES: frozenset = frozenset({
    "platform",
    "press_outlet",
    "media_actor",
    "implementing_agency",
    "regulator",
    "legal_actor",
    "service_provider",
    "comms_team",
    "hr_ops",
    "legal_counsel",
    "executive_team",
    "glassdoor_channel",
    "slack_channel",
})


def _profile_id(scen_hash: str, segment: AudienceSegment) -> str:
    """sha256 of ``f"{scen_hash}:{segment_id}:{segment.name}"`` truncated to 16 hex."""
    digest = hashlib.sha256(
        f"{scen_hash}:{segment.segment_id}:{segment.name}".encode("utf-8")
    ).hexdigest()
    return digest[:16]


def _hash_int(profile_id: str, salt: str) -> int:
    """Deterministic integer derived from ``profile_id`` + a discriminator salt.

    Each profile derives multiple independent ints (one per trait) by
    salting with a per-field label, then hashing again. This keeps the
    age pick independent of the mbti pick while staying purely functional.
    """
    return int(
        hashlib.sha256(f"{salt}:{profile_id}".encode("utf-8")).hexdigest()[:16],
        16,
    )


def _pick_from(profile_id: str, salt: str, choices: Tuple[str, ...]) -> str:
    """Modular pick from a closed tuple. Stable across processes."""
    return choices[_hash_int(profile_id, salt) % len(choices)]


def _pick_int(
    profile_id: str, salt: str, lo: int, hi: int
) -> int:
    """Inclusive-bounded modular pick."""
    span = hi - lo + 1
    return lo + (_hash_int(profile_id, salt) % span)


def _is_institutional(seg_type: str) -> bool:
    return seg_type.lower() in _INSTITUTIONAL_TYPES


def _build_profile(
    scenario: Scenario,
    segment: AudienceSegment,
    user_id: int,
    scen_hash: str,
) -> OasisAgentProfile:
    """Build a single deterministic :class:`OasisAgentProfile`.

    All variable fields are derived from ``profile_id`` so the dict is
    byte-stable for a given (Scenario, segment) pair. The bio/persona
    strings stay short and templated -- a downstream LLM-driven rewrite
    will replace this body while keeping the field shape identical.
    """
    pid = _profile_id(scen_hash, segment)
    vocab = vocabulary_for(scenario.persona, vocabulary_scope(scenario))
    cohort_label = vocab["stakeholders"]

    institutional = _is_institutional(segment.type)

    # username: stable, segment-id-derived, no random suffix (determinism).
    username = f"{segment.segment_id}_{pid[:6]}"

    # bio: short, persona+segment aware -- baseline placeholder that a
    # downstream LLM enrichment pass will replace.
    bio = (
        f"{cohort_label}: {segment.name}. "
        f"Type={segment.type}, influence={segment.influence_level}/5."
    )

    # persona: deterministic template summarising the segment's structured
    # fields. No free-text generation; everything is sourced from the
    # validated :class:`AudienceSegment` shape.
    persona_lines = [
        f"{segment.name} represents a {cohort_label.lower()} "
        f"of type '{segment.type}'.",
    ]
    if segment.interests:
        persona_lines.append(f"Interests: {', '.join(segment.interests)}.")
    if segment.incentives:
        persona_lines.append(f"Incentives: {', '.join(segment.incentives)}.")
    if segment.what_changes_their_mind:
        persona_lines.append(
            "Stance updates when: "
            + "; ".join(segment.what_changes_their_mind)
            + "."
        )
    persona_text = " ".join(persona_lines)

    if institutional:
        age = 30
        gender = "other"
        mbti = "ISTJ"
    else:
        age = _pick_int(pid, "age", _AGE_MIN, _AGE_MAX)
        gender = "male" if _hash_int(pid, "gender") % 2 == 0 else "female"
        mbti = _pick_from(pid, "mbti", _MBTI_TYPES)

    return OasisAgentProfile(
        user_id=user_id,
        user_name=username,
        name=segment.name,
        bio=bio,
        persona=persona_text,
        karma=_pick_int(pid, "karma", _KARMA_MIN, _KARMA_MAX),
        friend_count=_pick_int(pid, "friend", _FRIEND_MIN, _FRIEND_MAX),
        follower_count=_pick_int(pid, "follower", _FOLLOWER_MIN, _FOLLOWER_MAX),
        statuses_count=_pick_int(pid, "statuses", _STATUSES_MIN, _STATUSES_MAX),
        age=age,
        gender=gender,
        mbti=mbti,
        country=_pick_from(pid, "country", _COUNTRIES),
        profession=segment.type,
        interested_topics=list(segment.interests),
        source_entity_uuid=pid,
        source_entity_type=segment.type,
        created_at=_DETERMINISTIC_CREATED_AT,
    )


class PolicyPersonaGenerator:
    """Deterministic OASIS-compatible profile factory.

    Stateless by design: every public method takes a :class:`Scenario`
    and returns a fresh ``list[dict]``. The class form is preserved
    because the planned LLM-enriched subclass will inherit from this
    one and override the profile-building hook, not the entry-point
    methods.
    """

    def _profiles(self, scenario: Scenario) -> List[OasisAgentProfile]:
        """Internal: realise the full ``OasisAgentProfile`` list once.

        Both Reddit and Twitter exports start from the same profile
        list; only the dict-shape adapter differs.
        """
        scen_hash = scenario_hash(scenario)
        return [
            _build_profile(
                scenario=scenario,
                segment=segment,
                user_id=user_id,
                scen_hash=scen_hash,
            )
            for user_id, segment in enumerate(scenario.stakeholders)
        ]

    def generate_reddit_profiles(self, scenario: Scenario) -> List[Dict[str, Any]]:
        """Return OASIS-compatible Reddit profile dicts (one per stakeholder).

        The output dict shape comes from
        :meth:`OasisAgentProfile.to_reddit_format`, the single SSoT for
        the JSON OASIS' Reddit adapter consumes.
        """
        return [p.to_reddit_format() for p in self._profiles(scenario)]

    def generate_twitter_profiles(self, scenario: Scenario) -> List[Dict[str, Any]]:
        """Return OASIS-compatible Twitter profile dicts (one per stakeholder).

        Shape source: :meth:`OasisAgentProfile.to_twitter_format`. The
        eventual CSV export is the responsibility of
        :meth:`OasisProfileGenerator._save_twitter_csv`; this method
        returns the dict the CSV writer would itself iterate.
        """
        return [p.to_twitter_format() for p in self._profiles(scenario)]


# Module-init sanity: the constants we lifted from the upstream class are
# expected to match upstream's class-level attributes exactly. If upstream
# ever changes its catalogue the determinism guarantee silently drifts;
# fail loud at import.
from ..oasis_profile_generator import OasisProfileGenerator as _UpstreamOPG

_assert_mbti = tuple(_UpstreamOPG.MBTI_TYPES)
_assert_countries = tuple(_UpstreamOPG.COUNTRIES)
if _assert_mbti != _MBTI_TYPES:
    raise AssertionError(
        f"SSoT drift: upstream OasisProfileGenerator.MBTI_TYPES = "
        f"{_assert_mbti!r} but persona_generator._MBTI_TYPES = "
        f"{_MBTI_TYPES!r}. Re-align before shipping."
    )
if _assert_countries != _COUNTRIES:
    raise AssertionError(
        f"SSoT drift: upstream OasisProfileGenerator.COUNTRIES = "
        f"{_assert_countries!r} but persona_generator._COUNTRIES = "
        f"{_COUNTRIES!r}. Re-align before shipping."
    )
del _assert_mbti, _assert_countries

# Smoke-check the persona defaults vocab is still importable in the shape
# we depend on; scope resolution now lives in policy_core.scope_context.
assert callable(vocabulary_scope)
assert isinstance(PERSONA_VOCABULARY, dict)


__all__ = ["PolicyPersonaGenerator"]
