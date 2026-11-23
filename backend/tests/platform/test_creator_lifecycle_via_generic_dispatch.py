"""Creator lane runs end-to-end through the generic lifecycle
blueprint -- WITHOUT the dispatch importing any ``app.policy.*``.

The lane-isolation contract pinned in
``test_use_cases_blueprint.py:test_use_cases_blueprint_imports_are_lane_agnostic``
covers the blueprint source itself; this file covers the
behavioural side: a non-policy lane completes create -> prepare ->
start -> status -> report -> persisted artifact through the
shared ``/api/sim/use-cases/creator/...`` paths.

The LLM is stubbed at the report-generator boundary so the test
runs in CI without an API key. The persisted report payload comes
from the stub; the generic blueprint serializes via the adapter's
``model_dump_json`` and writes through ``artifact_names`` --
this asserts the wire contract end-to-end without booting the
real LLM stack.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests._helpers import make_app


_CREATOR_BASE = "/api/sim/use-cases/creator"


def _minimal_creator_scenario(title: str = "Lifecycle Pin") -> dict:
    """Build a minimal valid CreatorScenario payload for the
    creator lane's ``video_creator`` persona.

    Matches the strict shape declared in
    the creator report-generator's ``CreatorScenario`` model:
    persona + title + summary + seed_text + stress_question +
    non-empty stakeholders list whose ``type`` values are drawn from
    ``CREATOR_SEGMENT_TYPES[VIDEO_CREATOR]``.
    """
    return {
        "persona": "video_creator",
        "title": title,
        "summary": "lifecycle pin scenario",
        "seed_text": "We are launching a short-form video series.",
        "stress_question": "Who will engage and who will scroll past?",
        "stakeholders": [
            {
                "segment_id": "fans",
                "name": "Loyal subscribers",
                "type": "loyal_subscriber",
                "what_changes_their_mind": ["a new format that respects their time"],
            },
            {
                "segment_id": "casuals",
                "name": "Casual viewers",
                "type": "casual_viewer",
                "what_changes_their_mind": ["a hook in the first three seconds"],
            },
        ],
    }


def _stub_creator_report():
    """Return a minimal valid CreatorReport instance.

    Built fresh per call so the test cannot accidentally mutate a
    shared fixture. Carries the fields the persisted artifact
    contract requires (the generic blueprint serialises via
    ``model_dump_json``).
    """
    from datetime import datetime, timezone

    from app.creator.report_generator import (
        Claim, CreatorReport, Section,
    )
    from app.creator.catalog import CreatorPersona
    from app.simulation_platform.guardrails.taxonomy import HYPOTHESIS_TAGS

    # Sanity-anchor: the literal string here MUST be a member of the
    # platform-level :data:`HYPOTHESIS_TAGS` frozenset (the creator
    # lane validates ``Claim.tag`` against it). Asserting at fixture
    # construction time keeps the test self-describing if the
    # taxonomy ever drifts.
    assert "simulated_observation" in HYPOTHESIS_TAGS

    return CreatorReport(
        report_id="creator_lifecycle_test_001",
        simulation_id="dummy",  # overwritten by adapter from run_metadata
        persona=CreatorPersona.VIDEO_CREATOR,
        scenario_hash="a" * 64,
        seed_hash="b" * 64,
        generated_at=datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc),
        sections=[Section(
            section_id="overview",
            title="Audience response overview",
            claims=[Claim(
                claim_id="c1",
                text="Loyal subscribers may continue engaging with the format.",
                tag="simulated_observation",
            )],
        )],
    )


@pytest.fixture
def app(monkeypatch):
    return make_app(monkeypatch, AUTH_ENABLED="false")


def test_creator_lifecycle_runs_end_to_end_through_generic_dispatch(
    app, monkeypatch, tmp_path: Path,
) -> None:
    """Full lifecycle pin: create -> prepare -> start -> status ->
    report/generate -> report-read -- all through
    ``/api/sim/use-cases/creator/...``.

    Stubs the creator report-generator's ``generate`` so the LLM
    is bypassed; the rest of the lifecycle (adapter persist,
    artifact_names, atomic write, state-machine transitions) is
    exercised verbatim.
    """
    # Stub the LLM-bearing generator so no network call fires.
    from app.creator import report_generator as gen_mod

    monkeypatch.setattr(
        gen_mod.CreatorReportGenerator,
        "generate",
        lambda self, **kwargs: _stub_creator_report(),
    )

    client = app.test_client()

    # 1. Catalog: the per-use-case personas endpoint returns the
    # creator lane's rows.
    catalog_res = client.get(f"{_CREATOR_BASE}/personas")
    assert catalog_res.status_code == 200, catalog_res.get_data(as_text=True)
    data = catalog_res.get_json()["data"]
    assert data["use_case_id"] == "creator"
    persona_ids = {p["persona"] for p in data["personas"]}
    # The creator adapter publishes at least video_creator.
    assert "video_creator" in persona_ids

    # 2. Create. Scenario shape mirrors the creator's CreatorScenario:
    # title / content_format / platform / target_audience are the four
    # required fields the lane's frontend validates against.
    create_res = client.post(
        f"{_CREATOR_BASE}/simulation/create",
        json={"scenario": _minimal_creator_scenario()},
    )
    assert create_res.status_code == 201, create_res.get_data(as_text=True)
    sim_id = create_res.get_json()["data"]["simulation_id"]
    assert sim_id.startswith("creator_sim_")

    # 3. Prepare.
    prep_res = client.post(
        f"{_CREATOR_BASE}/simulation/{sim_id}/prepare",
    )
    assert prep_res.status_code == 200, prep_res.get_data(as_text=True)
    assert prep_res.get_json()["data"]["phase"] == "prepared"
    # Persisted prepare artifact lands on disk under the adapter's
    # documented filename.
    sim_dir = Path(client.application.config["OASIS_SIMULATION_DATA_DIR"]) / (
        f"creator/{sim_id}"
    )
    assert (sim_dir / "creator_prepare.json").exists()

    # 4. Start (deterministic mock).
    start_res = client.post(f"{_CREATOR_BASE}/simulation/{sim_id}/start")
    assert start_res.status_code == 200
    assert start_res.get_json()["data"]["phase"] == "completed"

    # 5. Status.
    status_res = client.get(f"{_CREATOR_BASE}/simulation/{sim_id}/status")
    assert status_res.status_code == 200
    assert status_res.get_json()["data"]["phase"] == "completed"

    # 6. Report/generate.
    gen_res = client.post(f"{_CREATOR_BASE}/simulation/{sim_id}/report/generate")
    assert gen_res.status_code == 200, gen_res.get_data(as_text=True)
    gen_payload = gen_res.get_json()["data"]
    assert gen_payload["phase"] == "reported"
    assert gen_payload["report_path"] == "creator_report.json"
    assert gen_payload["sha256_json"].startswith("sha256:")

    # 7. Report read returns the persisted artifact.
    read_res = client.get(f"{_CREATOR_BASE}/simulation/{sim_id}/report")
    assert read_res.status_code == 200, read_res.get_data(as_text=True)
    report = read_res.get_json()["data"]
    assert report["report_id"] == "creator_lifecycle_test_001"
    # The adapter's generate_report returns a CreatorReport
    # which the blueprint persists via model_dump_json; the round-trip
    # preserves the structured shape so a frontend client gets the
    # lane-specific Pydantic model JSON verbatim.


def test_creator_sensitivity_through_generic_dispatch_is_501(
    app, monkeypatch,
) -> None:
    """The creator adapter explicitly raises NotImplementedError for
    sensitivity. The generic blueprint MUST surface this as a typed
    501 CAPABILITY_UNSUPPORTED envelope -- never a 500 or bare
    exception."""
    client = app.test_client()
    create_res = client.post(
        f"{_CREATOR_BASE}/simulation/create",
        json={"scenario": _minimal_creator_scenario(title="Sensitivity 501")},
    )
    assert create_res.status_code == 201, create_res.get_data(as_text=True)
    sim_id = create_res.get_json()["data"]["simulation_id"]

    sens_res = client.post(
        f"{_CREATOR_BASE}/simulation/{sim_id}/sensitivity",
        json={},
    )
    assert sens_res.status_code == 501
    err = sens_res.get_json()["error"]
    assert err["code"] == "CAPABILITY_UNSUPPORTED"
    # Wire vocabulary: any lane that does not run real ensemble re-runs
    # opts out via NotImplementedError; the generic envelope names the
    # capability in vendor-neutral terms ("real_sensitivity"), not the
    # method name. The creator lane raises in run_sensitivity today;
    # other lanes that want a surrogate surface expose it through a
    # separately-named adapter method (e.g. ``run_surrogate_explainer``).
    assert err["details"]["capability"] == "real_sensitivity"
    assert err["details"]["reason"] == "real_sensitivity_unsupported"
