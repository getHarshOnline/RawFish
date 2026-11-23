"""Per-lane persona/catalog routing for the RawFish generic API.

RawFish does not expose an aggregate personas endpoint. These tests pin the
lane-scoped shape:
each registered lane exposes its own catalog rows under
``/api/sim/use-cases/<lane_id>/personas``.
"""
from __future__ import annotations

import pytest

from tests._helpers import catalog_payload, make_app
from app.simulation_platform import registry as platform_registry
from app.simulation_platform.catalog import CatalogPayload, PersonaSpec
from app.simulation_platform.tests_helpers import build_fake_use_case


@pytest.fixture
def app(monkeypatch):
    return make_app(monkeypatch, AUTH_ENABLED="false")


def _registry_snapshot():
    return dict(platform_registry._REGISTRY)  # noqa: SLF001 -- test isolation only


def _registry_restore(snapshot):
    platform_registry._REGISTRY.clear()  # noqa: SLF001
    platform_registry._REGISTRY.update(snapshot)  # noqa: SLF001


def _get_personas(client, lane_id: str) -> dict:
    res = client.get(f"/api/sim/use-cases/{lane_id}/personas")
    assert res.status_code == 200, res.get_data(as_text=True)
    return res.get_json()["data"]


def test_policy_runtime_lanes_publish_split_policy_catalogs(app):
    client = app.test_client()
    for lane_id in ("internal_policy", "public_policy"):
        data = _get_personas(client, lane_id)
        assert data["use_case_id"] == lane_id
        assert data["lane_manifest"]["lane_id"] == lane_id
        assert [p["persona"] for p in data["personas"]] == ["policy"]
        assert data["personas"][0]["use_case_id"] == lane_id
        assert "policy" in data["segment_types_by_persona"]


def test_creator_runtime_lane_uses_creator_id(app):
    data = _get_personas(app.test_client(), "creator")
    assert data["use_case_id"] == "creator"
    assert data["lane_manifest"]["lane_id"] == "creator"
    assert {p["persona"] for p in data["personas"]} == {
        "video_creator",
        "newsletter_writer",
        "podcaster",
    }
    assert {p["use_case_id"] for p in data["personas"]} == {"creator"}


def test_registered_adapter_rows_appear_under_its_own_lane(app):
    snapshot = _registry_snapshot()
    try:
        fake_uc = build_fake_use_case(
            uid="fake_lane",
            catalog_payload=catalog_payload(
                use_case_id="fake_lane",
                personas=[
                    PersonaSpec(
                        persona_id="researcher",
                        label="Researcher",
                        cohort_question="Who does this study reach?",
                        segment_types_by_scope={None: ["control_arm", "treatment_arm"]},
                    ),
                ],
            ),
        )
        platform_registry.register(fake_uc)

        data = _get_personas(app.test_client(), "fake_lane")
        assert data["use_case_id"] == "fake_lane"
        assert data["personas"] == [{
            "persona": "researcher",
            "use_case_id": "fake_lane",
            "label": "Researcher",
            "cohort_question": "Who does this study reach?",
        }]
        assert set(data["segment_types_by_persona"]["researcher"]) == {
            "control_arm", "treatment_arm",
        }
    finally:
        _registry_restore(snapshot)


def test_broken_catalog_is_scoped_to_the_requested_lane(app):
    class _BrokenCatalogAdapter:
        report_schema = None
        report_draft_schema = None
        prepare_manifest_schema = None
        adapter_runs_guardrails_internally = False
        guardrail_capabilities = None

        def use_case_id(self) -> str:
            return "broken_lane"

        def catalog(self) -> CatalogPayload:  # type: ignore[override]
            raise RuntimeError("catalog deliberately broken")

        def artifact_names(self) -> dict:
            return {}

        def validate_scenario(self, payload):
            return payload

        def rehydrate_scenario(self, state_dict):
            return state_dict.get("scenario", state_dict)

        def parse_seed(self, **kwargs):
            return {}

        def load_presets(self):
            return []

        def build_ontology(self, scenario, run_dir):
            return None

        def write_prepare_artifacts(self, scenario, run_dir):
            return None

        def run_simulation(self, scenario, run_dir):
            return None

        def generate_report(self, **kwargs):
            return None

        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError("not under test")

    snapshot = _registry_snapshot()
    try:
        platform_registry.register(_BrokenCatalogAdapter())  # type: ignore[arg-type]
        client = app.test_client()
        res = client.get("/api/sim/use-cases/broken_lane/personas")
        assert res.status_code == 502
        assert res.get_json()["error"]["code"] == "ADAPTER_CATALOG_FAILED"

        healthy = _get_personas(client, "creator")
        assert healthy["use_case_id"] == "creator"
    finally:
        _registry_restore(snapshot)


def test_same_persona_ids_do_not_collide_across_lane_endpoints(app):
    snapshot = _registry_snapshot()
    try:
        fake_uc = build_fake_use_case(
            uid="creator_clone_test",
            catalog_payload=catalog_payload(
                use_case_id="creator_clone_test",
                personas=[
                    PersonaSpec(
                        persona_id="video_creator",
                        label="Creator clone",
                        cohort_question="How will my next video land?",
                        segment_types_by_scope={None: ["loyal_subscriber", "casual_viewer"]},
                    ),
                ],
            ),
        )
        platform_registry.register(fake_uc)
        client = app.test_client()

        creator_data = _get_personas(client, "creator")
        clone_data = _get_personas(client, "creator_clone_test")

        assert creator_data["personas"][0]["use_case_id"] == "creator"
        assert clone_data["personas"] == [{
            "persona": "video_creator",
            "use_case_id": "creator_clone_test",
            "label": "Creator clone",
            "cohort_question": "How will my next video land?",
        }]
        assert clone_data["segment_types_by_persona"]["video_creator"] == [
            "loyal_subscriber",
            "casual_viewer",
        ]
    finally:
        _registry_restore(snapshot)
