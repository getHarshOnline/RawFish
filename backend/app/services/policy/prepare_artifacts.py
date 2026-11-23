"""Deterministic prepare artifact writer.

Prepare writes five atomic artifacts plus a manifest to
the run directory:

* ``prepare_artifacts.json`` -- the prepare-response envelope frozen as
  the schema we emit today (verbatim copy of the JSON body returned by
  the endpoint, so downstream consumers can replay without re-hitting
  the API).
* ``simulation_config.json`` -- the exact dict shape
  :class:`app.services.simulation_config_generator.SimulationParameters`
  produces via ``to_dict()``, hand-constructed deterministically (no
  LLM call) so re-running on the same :class:`Scenario` is byte-identical.
* ``reddit_profiles.json`` -- OASIS-compatible Reddit profile list
  (from :class:`PolicyPersonaGenerator.generate_reddit_profiles`).
* ``twitter_profiles.csv`` -- OASIS-compatible Twitter profile CSV
  (matches :meth:`OasisProfileGenerator._save_twitter_csv` header order
  + row shape; written via :func:`atomic_write_text`).
* ``manifest.json`` -- ``{scenario_hash, created_at_iso, artifacts: [
  {name, relative_path, sha256, bytes}, ...]}`` with hashes read off
  disk after each artifact is written.

Determinism contract (verified by ``test_prepare_artifacts.py``):

* Every JSON write goes through :func:`atomic_write_json` with the
  ``sort_keys=True`` default.
* The CSV write sorts rows by ``user_id`` (which already matches
  stakeholder declaration order via :meth:`PolicyPersonaGenerator`)
  and is rendered through :class:`io.StringIO` with
  ``lineterminator='\\n'`` so the bytes do not drift across Python
  versions / operating systems.
* ``simulation_config.json``'s wall-clock-ish fields
  (``generated_at`` and ``generation_reasoning``) are replaced with
  scenario-hash-derived sentinels so two runs are byte-identical.
* The manifest's ``created_at_iso`` is derived from ``scenario_hash``
  (NOT wall clock) -- specifically a deterministic ISO-8601 timestamp
  built from the first 8 hex chars of the hash. This keeps the
  manifest itself byte-stable across re-prepares of the same Scenario.
* The ``artifacts`` array is sorted by ``name`` before hashing into
  the manifest so dict-iteration order can never leak in.

Reuses :func:`atomic_write_json` / :func:`atomic_write_text` from
``app.utils.atomic_io``. **No new writer module** -- this file
synthesises payloads and dispatches to the existing helpers.
"""
from __future__ import annotations

import csv
import io
import os
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from ...policy.schema import Scenario
from ...seam.zep_mock import scenario_hash
from ...simulation_platform.artifact_manifest import write_manifest
from ...utils.atomic_io import atomic_write_json, atomic_write_text
from ..simulation_config_generator import (
    EventConfig,
    PlatformConfig,
    SimulationParameters,
    TimeSimulationConfig,
)
from .persona_generator import PolicyPersonaGenerator


# ---------------------------------------------------------------------------
# Deterministic sentinels
# ---------------------------------------------------------------------------


# Manifest / generated_at sentinel: derived from the scenario_hash so two
# prepares of the same scenario produce byte-identical manifests. The format
# is a synthetic ISO-8601 timestamp; downstream consumers only ever read it
# back as a string, so any well-formed ISO literal will do.
def _deterministic_iso(scen_hash: str) -> str:
    """Return a synthetic ISO-8601 timestamp derived from ``scen_hash``.

    Uses the first 8 hex chars of the hash to seed a fixed epoch base
    (2026-01-01T00:00:00Z + N seconds where N = int(hex8, 16) modulo
    one year in seconds). This means two prepares of the same Scenario
    embed the same string, while two distinct Scenarios get distinct
    timestamps -- both properties the byte-idempotency test asserts.
    """
    from datetime import datetime, timedelta, timezone

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    seconds_in_year = 365 * 24 * 60 * 60
    offset_seconds = int(scen_hash[:8], 16) % seconds_in_year
    return (base + timedelta(seconds=offset_seconds)).isoformat()


# Sentinel string for ``SimulationParameters.generation_reasoning``. The
# upstream field is free-form text describing the LLM's reasoning trace; the
# deterministic prepare path does not run the LLM, so a stable sentinel
# keeps the JSON byte-equal.
_GENERATION_REASONING_SENTINEL: str = (
    "deterministic policy prepare (no LLM call)"
)


# ---------------------------------------------------------------------------
# Artifact payload builders
# ---------------------------------------------------------------------------


def _build_simulation_config_payload(
    *,
    simulation_id: str,
    scenario: Scenario,
    scen_hash: str,
    active_hours: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Build the ``simulation_config.json`` payload deterministically.

    Mirrors :meth:`SimulationParameters.to_dict` exactly so downstream
    consumers (the runner) see the same dict shape they would receive
    from the LLM-driven generator. The agent list comes from the
    deterministic persona generator (one config per stakeholder, in
    declaration order); time/event/platform configs use the dataclass
    defaults so no env / LLM input bleeds in.
    """
    # Time/event/platform configs: dataclass defaults are static lists --
    # no wall clock, no env reads. They satisfy the runner's schema
    # expectations without an LLM trip.
    time_cfg = TimeSimulationConfig()
    event_cfg = EventConfig()
    twitter_cfg = PlatformConfig(platform="twitter")
    reddit_cfg = PlatformConfig(platform="reddit")

    # Agent configs: one per stakeholder, derived from the persona
    # generator's profile list (which is itself derived from
    # ``scenario_hash``, so this layer inherits byte-stability).
    persona_gen = PolicyPersonaGenerator()
    reddit_profiles = persona_gen.generate_reddit_profiles(scenario)

    agent_configs: List[Dict[str, Any]] = []
    for agent_id, (profile, stakeholder) in enumerate(
        zip(reddit_profiles, scenario.stakeholders)
    ):
        agent_configs.append({
            "agent_id": agent_id,
            "entity_uuid": profile.get("user_id", agent_id),  # placeholder
            "entity_name": stakeholder.name,
            "entity_type": stakeholder.type,
            "activity_level": 0.5,
            "posts_per_hour": 1.0,
            "comments_per_hour": 2.0,
            "active_hours": list(active_hours or range(8, 23)),
            "response_delay_min": 5,
            "response_delay_max": 60,
            "sentiment_bias": 0.0,
            "stance": "neutral",
            "influence_weight": 1.0,
        })

    # Hand-build the SimulationParameters and dispatch through its
    # canonical ``to_dict``. We override the two wall-clock-ish fields
    # with the deterministic sentinel so the JSON dump is byte-stable.
    params = SimulationParameters(
        simulation_id=simulation_id,
        project_id="",
        graph_id=simulation_id,
        simulation_requirement=scenario.seed_text,
        time_config=time_cfg,
        agent_configs=[],  # we override with our hand-built list below
        event_config=event_cfg,
        twitter_config=twitter_cfg,
        reddit_config=reddit_cfg,
        llm_model="",
        llm_base_url="",
        generated_at=_deterministic_iso(scen_hash),
        generation_reasoning=_GENERATION_REASONING_SENTINEL,
    )
    payload = params.to_dict()
    # SimulationParameters.to_dict expands agent_configs via ``asdict``;
    # we want our hand-built dicts instead so the agent shape stays under
    # our determinism contract regardless of upstream dataclass drift.
    payload["agent_configs"] = agent_configs
    # Ensure deterministic dict shape for nested config blobs by
    # re-dumping via asdict (the upstream to_dict already does this for
    # time/event/platform but we re-affirm for safety).
    payload["time_config"] = asdict(time_cfg)
    payload["event_config"] = asdict(event_cfg)
    payload["twitter_config"] = asdict(twitter_cfg)
    payload["reddit_config"] = asdict(reddit_cfg)
    return payload


def _build_twitter_csv_text(twitter_profiles: List[Dict[str, Any]]) -> str:
    """Render the OASIS-compatible Twitter profile CSV deterministically.

    Header + row shape matches
    :meth:`OasisProfileGenerator._save_twitter_csv` exactly so the OASIS
    Twitter adapter can consume the file without a parser change. Rows
    are sorted by ``user_id`` (which is already stakeholder declaration
    order in :class:`PolicyPersonaGenerator`) and the line terminator is
    pinned to ``\\n`` (instead of csv's default ``\\r\\n``) so the bytes
    do not drift across operating systems.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["user_id", "name", "username", "user_char", "description"])

    sorted_profiles = sorted(
        twitter_profiles, key=lambda p: int(p.get("user_id", 0))
    )
    for idx, profile in enumerate(sorted_profiles):
        bio = str(profile.get("bio", ""))
        persona = str(profile.get("persona", ""))
        if persona and persona != bio:
            user_char = f"{bio} {persona}"
        else:
            user_char = bio
        # Strip newlines so they don't break the CSV row contract.
        user_char = user_char.replace("\n", " ").replace("\r", " ")
        description = bio.replace("\n", " ").replace("\r", " ")
        writer.writerow([
            idx,
            profile.get("name", ""),
            profile.get("username", ""),
            user_char,
            description,
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


def write_prepare_artifacts(
    *,
    run_dir: str,
    simulation_id: str,
    scenario: Scenario,
    prepare_envelope: Dict[str, Any],
    reddit_profiles: List[Dict[str, Any]],
    twitter_profiles: List[Dict[str, Any]],
    statistical_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Write the 5 deterministic artifacts + ``manifest.json`` to ``run_dir``.

    Returns the manifest dict (with ``scenario_hash``, ``created_at_iso``,
    and an ``artifacts`` array of name/relative_path/sha256/bytes entries).
    All writes use :func:`atomic_write_json` / :func:`atomic_write_text`
    -- no other writer is introduced.

    The hashes in the manifest are computed AFTER each artifact is
    written (read-back of the on-disk bytes), so the manifest accurately
    reflects what the consumer will read off disk.
    """
    scen_hash = scenario_hash(scenario)

    os.makedirs(run_dir, exist_ok=True)

    # 1. prepare_artifacts.json: verbatim copy of the response envelope.
    prepare_path = os.path.join(run_dir, "prepare_artifacts.json")
    atomic_write_json(prepare_path, prepare_envelope)

    # 2. simulation_config.json: deterministic SimulationParameters shape.
    active_hours = None
    if statistical_context:
        runtime_overrides = statistical_context.get("runtime_overrides")
        if isinstance(runtime_overrides, dict):
            maybe_hours = runtime_overrides.get("active_hours")
            if isinstance(maybe_hours, list):
                active_hours = [
                    int(hour) for hour in maybe_hours
                    if isinstance(hour, int) and not isinstance(hour, bool)
                ]
        settings = statistical_context.get("settings")
        if active_hours is None and isinstance(settings, dict):
            maybe_hours = settings.get("cohort.active_hours")
            if isinstance(maybe_hours, list):
                active_hours = [
                    int(hour) for hour in maybe_hours
                    if isinstance(hour, int) and not isinstance(hour, bool)
                ]

    sim_config_payload = _build_simulation_config_payload(
        simulation_id=simulation_id,
        scenario=scenario,
        scen_hash=scen_hash,
        active_hours=active_hours,
    )
    sim_config_path = os.path.join(run_dir, "simulation_config.json")
    atomic_write_json(sim_config_path, sim_config_payload)

    statistical_path = None
    if statistical_context:
        statistical_path = os.path.join(run_dir, "statistical_settings.json")
        atomic_write_json(statistical_path, statistical_context)

    # 3. reddit_profiles.json: OASIS Reddit profile list, sorted by user_id
    #    for explicit determinism (PolicyPersonaGenerator already emits in
    #    stakeholder declaration order, but the sort makes the contract
    #    independent of upstream iteration order).
    reddit_sorted = sorted(
        reddit_profiles, key=lambda p: int(p.get("user_id", 0))
    )
    reddit_path = os.path.join(run_dir, "reddit_profiles.json")
    atomic_write_json(reddit_path, reddit_sorted)

    # 4. twitter_profiles.csv: OASIS-compatible CSV, deterministic line
    #    terminator + row sort.
    twitter_csv_text = _build_twitter_csv_text(twitter_profiles)
    twitter_path = os.path.join(run_dir, "twitter_profiles.csv")
    atomic_write_text(twitter_path, twitter_csv_text)

    # 5. manifest.json: hashes of the 4 artifacts written above. The
    #    manifest entries are sorted by name so dict-iteration order can
    #    never affect the bytes. The generic write primitive lives on
    #    the simulation platform; artifact names are passed as inputs
    #    rather than hardcoded inside the primitive.
    artifact_specs = [
        ("prepare_artifacts.json", prepare_path),
        ("simulation_config.json", sim_config_path),
        ("reddit_profiles.json", reddit_path),
        ("twitter_profiles.csv", twitter_path),
    ]
    if statistical_path is not None:
        artifact_specs.append(("statistical_settings.json", statistical_path))
    manifest = write_manifest(
        run_dir=run_dir,
        scenario_hash=scen_hash,
        created_at_iso=_deterministic_iso(scen_hash),
        artifact_specs=artifact_specs,
    )

    return manifest


__all__ = ["write_prepare_artifacts"]
