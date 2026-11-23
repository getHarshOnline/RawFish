from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.simulation_platform.statistical import (
    StatisticalConfigError,
    StatisticalManifest,
    build_reseed_lineage_event,
    build_statistical_config_snapshot,
    build_statistical_config_payload,
    load_statistical_manifest,
    statistical_settings_hash,
    validate_statistical_settings,
)
from tests._helpers import make_app


def _creator_scenario() -> dict:
    return {
        "persona": "video_creator",
        "title": "Statistical Workbench Route Pin",
        "summary": "Validate statistical settings persistence.",
        "seed_text": "We are launching a short-form video series.",
        "stress_question": "Who will engage and who will scroll past?",
        "stakeholders": [
            {
                "segment_id": "fans",
                "name": "Loyal subscribers",
                "type": "loyal_subscriber",
                "what_changes_their_mind": ["a format that respects their time"],
            },
            {
                "segment_id": "casuals",
                "name": "Casual viewers",
                "type": "casual_viewer",
                "what_changes_their_mind": ["a hook in the first three seconds"],
            },
        ],
    }


def _internal_policy_scenario() -> dict:
    return {
        "persona": "policy",
        "scope": "internal_org",
        "title": "Policy statistical runtime pin",
        "summary": "Validate workbench settings in the policy runtime.",
        "seed_text": "An internal rollout changes approval workflows.",
        "stress_question": "Which stakeholders lose trust or slow adoption?",
        "stakeholders": [
            {
                "segment_id": "employees",
                "name": "Employees",
                "type": "employee_cohort",
                "interests": ["clarity"],
                "incentives": ["avoid unnecessary risk"],
                "constraints": ["limited time"],
                "likely_support_reasons": ["clear benefit"],
                "likely_opposition_reasons": ["uncertain rollout"],
                "information_channels": ["internal update"],
                "influence_level": 3,
                "vulnerability_level": 3,
                "compliance_propensity": 3,
                "trust_level": 3,
                "mobilization_capacity": 2,
                "what_changes_their_mind": ["credible evidence"],
            }
        ],
        "assumptions": ["The workflow change is materially visible."],
        "success_criteria": ["Surface adoption and legitimacy risk."],
        "failure_modes_to_watch": ["low trust", "unclear accountability"],
        "run": {"ensemble_n": 1, "run_seed": 42},
        "drift_horizon_rounds": 1,
    }


@pytest.fixture
def app(monkeypatch, tmp_path):
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    from app.api import use_cases as use_cases_mod

    monkeypatch.setattr(
        use_cases_mod.Config,
        "OASIS_SIMULATION_DATA_DIR",
        str(tmp_path),
    )
    app.config["OASIS_SIMULATION_DATA_DIR"] = str(tmp_path)
    return app


@pytest.mark.parametrize("lane_id", ["creator", "internal_policy", "public_policy"])
def test_statistical_manifests_publish_canonical_ids(lane_id: str) -> None:
    manifest = load_statistical_manifest(lane_id)

    assert manifest.fields
    assert all("." in field_id for field_id in manifest.field_ids)
    assert all(not field_id.startswith("statistical_workbench_") for field_id in manifest.field_ids)
    assert len(manifest.field_ids) == len(set(manifest.field_ids))
    public_fields = manifest.model_dump_public()["fields"]
    assert all("." in field["id"] for field in public_fields)
    assert all(
        not str(alias).startswith("statistical_workbench_")
        for field in public_fields
        for alias in field.get("aliases", [])
    )


def test_lane_manifests_declare_statistical_workbench_slots() -> None:
    from app.simulation_platform.catalog import build_minimal_lane_manifest

    manifest = build_minimal_lane_manifest(
        lane_id="internal_policy",
        label="Internal Policy",
        summary="test lane",
    )
    slots = set(manifest.cockpit_slots)

    expected = {
        "statistical_workbench_inline_editor",
        "statistical_workbench_override_badge",
        "statistical_workbench_provenance_chip",
        "statistical_workbench_validity_strip",
        "statistical_workbench_variant_strip",
        "statistical_workbench_impact_tooltip",
        "statistical_workbench_preflight_meter",
        "statistical_workbench_drift_subpanel",
        "statistical_workbench_salt_seed_panel",
        "statistical_workbench_priors_attach_panel",
        "statistical_workbench_active_hours_grid",
        "statistical_workbench_manifest_chip",
    }

    assert expected.issubset(slots)


def test_creator_manifest_is_applicable_subset_without_policy_drift_fields() -> None:
    creator = load_statistical_manifest("creator")
    internal = load_statistical_manifest("internal_policy")
    public = load_statistical_manifest("public_policy")

    creator_ids = set(creator.field_ids)
    internal_ids = set(internal.field_ids)
    public_ids = set(public.field_ids)

    assert len(creator_ids) < len(internal_ids)
    assert "run.run_seed" in creator_ids
    assert "variant.audience_metric_focus" in creator_ids
    assert "cohort.drift.axis" not in creator_ids
    assert "variant.counterfactual_axis" not in creator_ids
    assert "manifest.validity_allowlist" not in creator_ids
    assert "cohort.drift.axis" in internal_ids
    assert "cohort.drift.axis" in public_ids
    assert "variant.audience_metric_focus" not in public_ids


def test_statistical_hash_is_deterministic_and_order_invariant() -> None:
    manifest = load_statistical_manifest("public_policy")
    settings_a = validate_statistical_settings(
        manifest,
        {
            "run.run_seed": 99,
            "cohort.active_hours": [18, 9, 9, 12],
            "run.response_temperature": 0.5,
        },
    )
    settings_b = validate_statistical_settings(
        manifest,
        {
            "run.response_temperature": 0.5,
            "cohort.active_hours": [12, 18, 9],
            "run.run_seed": 99,
        },
    )

    assert settings_a == settings_b
    assert settings_a["cohort.active_hours"] == [9, 12, 18]
    assert statistical_settings_hash(
        lane_id=manifest.lane_id,
        version=manifest.version,
        settings=settings_a,
    ) == statistical_settings_hash(
        lane_id=manifest.lane_id,
        version=manifest.version,
        settings=settings_b,
    )


def test_unknown_statistical_setting_is_rejected() -> None:
    manifest = load_statistical_manifest("creator")

    with pytest.raises(StatisticalConfigError):
        validate_statistical_settings(
            manifest,
            {"cohort.drift.axis": "eligibility_boundary"},
        )


def test_statistical_config_payload_shape() -> None:
    manifest = load_statistical_manifest("public_policy")
    payload = build_statistical_config_payload(
        manifest,
        {"run.run_seed": 1001},
    )

    assert payload.lane_id == "public_policy"
    assert payload.version == manifest.version
    assert payload.field_ids == manifest.field_ids
    assert payload.settings["run.run_seed"] == 1001
    assert payload.statistical_settings_hash.startswith("sha256:")


def test_statistical_manifest_accepts_optional_rich_metadata() -> None:
    manifest = StatisticalManifest.model_validate(
        {
            "lane_id": "contract_lane",
            "version": "2026-07-01",
            "impact_model": {"kind": "bounded_delta", "unit": "risk_score"},
            "citation_policy": {"required": True, "style": "evidence_id"},
            "provenance_policy": {"required_sources": ["operator"]},
            "provenance": ["registry"],
            "fields": [
                {
                    "id": "run.run_seed",
                    "label": "Seed",
                    "kind": "integer",
                    "control": "number",
                    "default": 1,
                    "description": "Deterministic run seed.",
                    "why_it_matters": "Keeps repeat runs stable.",
                    "citation_key": "manifest.run_seed",
                    "modalities": ["simulation"],
                    "lane_applicability": ["contract_lane"],
                    "affects": ["run_reproducibility"],
                    "impact_model": {"maps_to": "run.run_seed"},
                    "citation_policy": {"required": False},
                    "provenance_policy": {"source": "operator"},
                    "impact_hint": {
                        "source": "runSurrogateExplainer",
                        "summary": "Changes deterministic sampling.",
                    },
                }
            ],
        }
    )

    assert manifest.impact_model == {"kind": "bounded_delta", "unit": "risk_score"}
    assert manifest.citation_policy == {"required": True, "style": "evidence_id"}
    assert manifest.fields[0].impact_model == {"maps_to": "run.run_seed"}


def test_statistical_snapshot_derives_load_bearing_run_overrides() -> None:
    manifest = load_statistical_manifest("internal_policy")
    snapshot = build_statistical_config_snapshot(
        manifest,
        {
            "run.run_seed": 77,
            "run.ensemble_n": 2,
            "run.drift_horizon_rounds": 4,
            "run.persona_salts": {
                "managers": "m2",
                "employees": "e1",
            },
            "cohort.active_hours": [21, 7, 7],
        },
    )

    overrides = snapshot.run_overrides.model_dump(mode="json", exclude_none=True)
    assert overrides["run_seed"] == 77
    assert overrides["seed"] == 77
    assert overrides["ensemble_n"] == 2
    assert overrides["drift_horizon_rounds"] == 4
    assert overrides["rounds"] == 4
    assert overrides["persona_salts"] == {"employees": "e1", "managers": "m2"}
    assert overrides["active_hours"] == [7, 21]
    assert snapshot.field_ids == manifest.field_ids
    assert snapshot.statistical_settings_hash.startswith("sha256:")


def test_statistical_hash_changes_for_load_bearing_overrides() -> None:
    manifest = load_statistical_manifest("internal_policy")
    baseline = build_statistical_config_snapshot(
        manifest,
        {
            "run.run_seed": 77,
            "run.ensemble_n": 2,
            "run.drift_horizon_rounds": 4,
            "run.persona_salts": {"employees": "e1"},
            "cohort.active_hours": [7, 21],
        },
    )
    changed = build_statistical_config_snapshot(
        manifest,
        {
            "run.run_seed": 78,
            "run.ensemble_n": 3,
            "run.drift_horizon_rounds": 5,
            "run.persona_salts": {"employees": "e2"},
            "cohort.active_hours": [8, 22],
        },
    )

    assert changed.statistical_settings_hash != baseline.statistical_settings_hash
    assert changed.run_overrides.run_seed == 78
    assert changed.run_overrides.ensemble_n == 3
    assert changed.run_overrides.drift_horizon_rounds == 5
    assert changed.run_overrides.persona_salts == {"employees": "e2"}
    assert changed.run_overrides.active_hours == [8, 22]


def test_reseed_lineage_accepts_patch_and_records_previous_next_hashes() -> None:
    manifest = load_statistical_manifest("creator")
    current = build_statistical_config_payload(
        manifest,
        {
            "run.run_seed": 100,
            "cohort.active_hours": [9, 15],
            "run.persona_salts": {"fans": "stable"},
        },
    )

    event = build_reseed_lineage_event(
        manifest=manifest,
        current_settings=current.settings,
        patch={
            "cohort.active_hours": [8, 20, 8],
            "run.persona_salts": {"fans": "variant"},
        },
        reason="adjust_active_window",
        created_at="2026-07-01T00:00:00Z",
    )
    next_config = build_statistical_config_payload(manifest, event.settings)

    assert event.operation == "settings_patch"
    assert event.parent_statistical_settings_hash == current.statistical_settings_hash
    assert event.statistical_settings_hash == next_config.statistical_settings_hash
    assert event.statistical_settings_hash != current.statistical_settings_hash
    assert event.patch == {
        "cohort.active_hours": [8, 20],
        "run.persona_salts": {"fans": "variant"},
    }
    assert event.parent_settings == current.settings
    assert event.settings["run.run_seed"] == 100


def test_create_time_statistical_settings_feed_prepare_artifact(app) -> None:
    client = app.test_client()

    create_res = client.post(
        "/api/sim/use-cases/creator/simulation/create",
        json={
            "scenario": _creator_scenario(),
            "statistical_settings": {
                "run.run_seed": 321,
                "cohort.active_hours": [20, 8, 8],
            },
        },
    )
    assert create_res.status_code == 201, create_res.get_data(as_text=True)
    sim_id = create_res.get_json()["data"]["simulation_id"]
    run_dir = Path(app.config["OASIS_SIMULATION_DATA_DIR"]) / "creator" / sim_id
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))

    assert state["statistical_settings"]["run.run_seed"] == 321
    assert state["statistical_settings"]["cohort.active_hours"] == [8, 20]
    assert state["statistical_settings_hash"].startswith("sha256:")

    prepare_res = client.post(f"/api/sim/use-cases/creator/simulation/{sim_id}/prepare")
    assert prepare_res.status_code == 200, prepare_res.get_data(as_text=True)
    statistical_artifact = json.loads(
        (run_dir / "statistical_settings.json").read_text(encoding="utf-8")
    )

    assert statistical_artifact["settings"] == state["statistical_settings"]
    assert statistical_artifact["statistical_settings_hash"] == state[
        "statistical_settings_hash"
    ]

    start_res = client.post(f"/api/sim/use-cases/creator/simulation/{sim_id}/start")
    assert start_res.status_code == 200, start_res.get_data(as_text=True)
    run_state = json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))
    assert run_state["statistical_settings_hash"] == state[
        "statistical_settings_hash"
    ]
    assert run_state["statistical_seed"] == 321
    assert run_state["active_hours"] == [8, 20]


def test_policy_runtime_consumes_statistical_workbench_overrides(app) -> None:
    from app.simulation_platform.run_metadata import RunMetadata
    from app.simulation_platform.runner_factory import (
        SimulationRunnerHandle,
        reset_runner_factory,
        set_runner_factory,
    )

    class _CapturingFactory:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def start(self, *, simulation_id: str, run_dir: str, config: dict):
            self.calls.append({
                "simulation_id": simulation_id,
                "run_dir": run_dir,
                "config": config,
            })
            return SimulationRunnerHandle(
                task_id=f"runner_task_{simulation_id}",
                terminal_status="completed",
            )

    factory = _CapturingFactory()
    set_runner_factory(factory)
    client = app.test_client()
    try:
        create_res = client.post(
            "/api/sim/use-cases/internal_policy/simulation/create",
            json={
                "scenario": _internal_policy_scenario(),
                "statistical_settings": {
                    "run.run_seed": 77,
                    "run.ensemble_n": 2,
                    "run.drift_horizon_rounds": 4,
                    "cohort.posts_per_hour": 0.4,
                    "cohort.active_hours": [21, 7, 7],
                    "cohorts": {
                        "employees": {
                            "statistical": {
                                "posts_per_hour": 1.7,
                                "active_hours": [15, 8, 8],
                            },
                        },
                    },
                },
            },
        )
        assert create_res.status_code == 201, create_res.get_data(as_text=True)
        sim_id = create_res.get_json()["data"]["simulation_id"]
        run_dir = (
            Path(app.config["OASIS_SIMULATION_DATA_DIR"])
            / "internal_policy"
            / sim_id
        )

        prepare_res = client.post(
            f"/api/sim/use-cases/internal_policy/simulation/{sim_id}/prepare",
        )
        assert prepare_res.status_code == 200, prepare_res.get_data(as_text=True)

        sim_config = json.loads(
            (run_dir / "simulation_config.json").read_text(encoding="utf-8"),
        )
        assert sim_config["statistical_settings_hash"].startswith("sha256:")
        assert {
            tuple(agent.get("active_hours", []))
            for agent in sim_config.get("agent_configs", [])
        } == {(8, 15)}
        assert {
            agent.get("posts_per_hour")
            for agent in sim_config.get("agent_configs", [])
        } == {1.7}

        start_res = client.post(
            f"/api/sim/use-cases/internal_policy/simulation/{sim_id}/start",
        )
        assert start_res.status_code == 200, start_res.get_data(as_text=True)
        assert factory.calls
        assert factory.calls[0]["run_dir"] == str(run_dir)

        metadata = RunMetadata.model_validate_json(
            (run_dir / "run_metadata.json").read_text(encoding="utf-8"),
        )
        assert metadata.runner_task_id == f"runner_task_{sim_id}"
        assert metadata.statistical_settings["run.run_seed"] == 77
        assert metadata.statistical_settings["run.ensemble_n"] == 2
        assert metadata.statistical_settings["run.drift_horizon_rounds"] == 4
        assert metadata.statistical_settings["cohorts"]["employees"][
            "statistical"
        ]["posts_per_hour"] == 1.7
        assert metadata.resolved_statistical_config == metadata.statistical_settings
        assert metadata.statistical_settings_hash
        assert metadata.statistical_settings_version
        assert metadata.statistical_runtime_overrides["seed"] == 77
        assert metadata.statistical_runtime_overrides["rounds"] == 4
        assert metadata.statistical_runtime_overrides["ensemble_n"] == 2
        assert metadata.persona_salts_resolved
        assert metadata.provenance_per_field["run.run_seed"] == "user-set"
        assert "run.run_seed" in metadata.lock_state_per_field
        assert metadata.prior_attachments == {
            "attached_priors_id": "internal_policy_default_priors_v0",
        }
        assert metadata.statistical_variant_lineage == []
        assert metadata.variant_lineage == []
    finally:
        reset_runner_factory()


def test_invalid_create_time_statistical_settings_are_request_errors(app) -> None:
    client = app.test_client()

    create_res = client.post(
        "/api/sim/use-cases/creator/simulation/create",
        json={
            "scenario": _creator_scenario(),
            "statistical_settings": {
                "cohort.drift.axis": "eligibility_boundary",
            },
        },
    )

    assert create_res.status_code == 400, create_res.get_data(as_text=True)
    body = create_res.get_json()
    assert body["error"]["code"] == "INVALID_INPUT"
    assert body["error"]["details"]["reason"] == "statistical_settings_invalid"


def test_create_time_statistical_settings_reject_unknown_run_cohorts(app) -> None:
    client = app.test_client()

    create_res = client.post(
        "/api/sim/use-cases/creator/simulation/create",
        json={
            "scenario": _creator_scenario(),
            "statistical_settings": {
                "cohorts": {
                    "ghost": {
                        "statistical": {
                            "active_hours": [8, 9],
                        },
                    },
                },
            },
        },
    )

    assert create_res.status_code == 422, create_res.get_data(as_text=True)
    body = create_res.get_json()
    assert body["error"]["code"] == "INVALID_INPUT"
    assert body["error"]["details"]["reason"] == "cohort_not_in_run"


def test_statistical_routes_persist_hash_and_reseed_lineage(app) -> None:
    client = app.test_client()

    manifest_res = client.get("/api/sim/use-cases/creator/statistical-manifest")
    assert manifest_res.status_code == 200, manifest_res.get_data(as_text=True)
    manifest_data = manifest_res.get_json()["data"]
    assert manifest_data["manifest"]["lane_id"] == "creator"
    assert manifest_data["manifest"]["fields"]
    assert manifest_data["default_config"]["statistical_settings_hash"].startswith("sha256:")

    create_res = client.post(
        "/api/sim/use-cases/creator/simulation/create",
        json={"scenario": _creator_scenario()},
    )
    assert create_res.status_code == 201, create_res.get_data(as_text=True)
    sim_id = create_res.get_json()["data"]["simulation_id"]

    current_res = client.get(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/statistical-config",
    )
    assert current_res.status_code == 200, current_res.get_data(as_text=True)
    current = current_res.get_json()["data"]
    current_global_hours = current["settings"]["cohort.active_hours"]

    save_res = client.patch(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/statistical-config",
        json={
            "expected_hash": current["statistical_settings_hash"],
            "ops": [
                {"op": "replace", "path": "/run/run_seed", "value": 123},
                {
                    "op": "replace",
                    "path": "/cohorts/fans/statistical/active_hours",
                    "value": [15, 9, 9],
                },
            ],
        },
    )
    assert save_res.status_code == 200, save_res.get_data(as_text=True)
    saved = save_res.get_json()["data"]
    assert saved["settings"]["run.run_seed"] == 123
    assert saved["settings"]["cohort.active_hours"] == current_global_hours
    assert saved["settings"]["cohorts"]["fans"]["statistical"]["active_hours"] == [
        9,
        15,
    ]
    assert saved["statistical_settings_hash"].startswith("sha256:")

    state_path = (
        Path(app.config["OASIS_SIMULATION_DATA_DIR"])
        / "creator"
        / sim_id
        / "state.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["statistical_settings_hash"] == saved["statistical_settings_hash"]
    assert state["statistical_settings"]["run.run_seed"] == 123
    assert state["statistical_runtime_overrides"]["active_hours"] == current_global_hours
    assert state["statistical_runtime_overrides"]["active_hours_by_cohort"] == {
        "fans": [9, 15],
    }

    reseed_res = client.post(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/statistical-config/reseed",
        json={"seed": 456, "reason": "test_variant"},
    )
    assert reseed_res.status_code == 200, reseed_res.get_data(as_text=True)
    reseeded = reseed_res.get_json()["data"]
    assert reseeded["config"]["settings"]["run.run_seed"] == 456
    assert reseeded["variant"]["operation"] == "reseed"
    assert reseeded["variant"]["parent_statistical_settings_hash"] == saved[
        "statistical_settings_hash"
    ]
    assert reseeded["variant"]["variant_id"].startswith("stat_")
    assert reseeded["lineage_count"] == 1

    patch_res = client.post(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/statistical-config/reseed",
        json={
            "ops": [
                {"op": "replace", "path": "/run/ensemble_n", "value": 3},
                {
                    "op": "replace",
                    "path": "/cohorts/fans/statistical/active_hours",
                    "value": [8, 22, 8],
                },
            ],
            "reason": "cohort_patch_variant",
        },
    )
    assert patch_res.status_code == 200, patch_res.get_data(as_text=True)
    patched = patch_res.get_json()["data"]
    assert patched["variant"]["operation"] == "settings_patch"
    assert patched["variant"]["parent_statistical_settings_hash"] == reseeded[
        "config"
    ]["statistical_settings_hash"]
    assert patched["variant"]["patch"] == {
        "cohorts": {
            "fans": {
                "statistical": {
                    "active_hours": [8, 22],
                },
            },
        },
        "run.ensemble_n": 3,
    }
    assert patched["variant"]["applied_ops"][-1]["path"] == (
        "/cohorts/fans/statistical/active_hours"
    )
    assert patched["lineage_count"] == 2

    prepare_res = client.post(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/prepare",
    )
    assert prepare_res.status_code == 200, prepare_res.get_data(as_text=True)
    statistical_artifact = state_path.parent / "statistical_settings.json"
    artifact_payload = json.loads(statistical_artifact.read_text(encoding="utf-8"))
    assert artifact_payload["statistical_settings_hash"] == patched["config"][
        "statistical_settings_hash"
    ]
    assert artifact_payload["settings"]["run.run_seed"] == 456
    assert artifact_payload["settings"]["run.ensemble_n"] == 3
    assert artifact_payload["settings"]["cohort.active_hours"] == current_global_hours
    assert artifact_payload["settings"]["cohorts"]["fans"]["statistical"][
        "active_hours"
    ] == [8, 22]

    lineage_res = client.get(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/statistical-lineage"
    )
    assert lineage_res.status_code == 200, lineage_res.get_data(as_text=True)
    lineage = lineage_res.get_json()["data"]
    assert lineage["statistical_settings_hash"] == patched["config"][
        "statistical_settings_hash"
    ]
    assert len(lineage["lineage"]) == 2
    assert lineage["lineage"][0]["variant_id"] == reseeded["variant"]["variant_id"]
    assert lineage["lineage"][1]["variant_id"] == patched["variant"]["variant_id"]


def test_statistical_patch_route_uses_workbench_paths_and_stale_hash_guard(app) -> None:
    client = app.test_client()
    create_res = client.post(
        "/api/sim/use-cases/creator/simulation/create",
        json={"scenario": _creator_scenario()},
    )
    assert create_res.status_code == 201, create_res.get_data(as_text=True)
    sim_id = create_res.get_json()["data"]["simulation_id"]

    current_res = client.get(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/statistical-config",
    )
    assert current_res.status_code == 200, current_res.get_data(as_text=True)
    current = current_res.get_json()["data"]
    current_global_hours = current["settings"]["cohort.active_hours"]

    patch_res = client.patch(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/statistical-config",
        json={
            "expected_hash": current["statistical_settings_hash"],
            "ops": [
                {"op": "replace", "path": "/run/run_seed", "value": 987},
                {"op": "replace", "path": "/run/ensemble_n", "value": 4},
                {
                    "op": "replace",
                    "path": "/run/persona_salts/fans",
                    "value": "fans-variant",
                },
                {
                    "op": "replace",
                    "path": "/cohorts/fans/statistical/active_hours",
                    "value": [6, 9, 9],
                },
            ],
        },
    )
    assert patch_res.status_code == 200, patch_res.get_data(as_text=True)
    patched = patch_res.get_json()["data"]
    assert patched["settings"]["run.run_seed"] == 987
    assert patched["settings"]["run.ensemble_n"] == 4
    assert patched["settings"]["run.persona_salts"]["fans"] == (
        "fans-variant"
    )
    assert patched["settings"]["cohort.active_hours"] == current_global_hours
    assert patched["settings"]["cohorts"]["fans"]["statistical"]["active_hours"] == [
        6,
        9,
    ]
    assert patched["statistical_settings_hash"] != current["statistical_settings_hash"]
    assert [op["path"] for op in patched["applied_ops"]] == [
        "/run/run_seed",
        "/run/ensemble_n",
        "/run/persona_salts/fans",
        "/cohorts/fans/statistical/active_hours",
    ]
    assert patched["applied_ops"][-1]["cohort_id"] == "fans"
    assert patched["applied_ops"][-1]["field_id"] == "cohort.active_hours"

    state_path = (
        Path(app.config["OASIS_SIMULATION_DATA_DIR"])
        / "creator"
        / sim_id
        / "state.json"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["statistical_runtime_overrides"]["active_hours"] == current_global_hours
    assert state["statistical_runtime_overrides"]["active_hours_by_cohort"] == {
        "fans": [6, 9],
    }

    stale_res = client.patch(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/statistical-config",
        json={
            "expected_hash": current["statistical_settings_hash"],
            "ops": [{"op": "replace", "path": "/run/run_seed", "value": 123}],
        },
    )
    assert stale_res.status_code == 409, stale_res.get_data(as_text=True)
    assert stale_res.get_json()["error"]["details"]["reason"] == "stale_config"

    unknown_cohort_res = client.patch(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/statistical-config",
        json={
            "expected_hash": patched["statistical_settings_hash"],
            "ops": [
                {
                    "op": "replace",
                    "path": "/cohorts/ghost/statistical/active_hours",
                    "value": [7, 8],
                }
            ],
        },
    )
    assert unknown_cohort_res.status_code == 422, (
        unknown_cohort_res.get_data(as_text=True)
    )
    assert unknown_cohort_res.get_json()["error"]["details"]["reason"] == (
        "cohort_not_in_run"
    )

    disallowed_res = client.patch(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/statistical-config",
        json={
            "expected_hash": patched["statistical_settings_hash"],
            "ops": [
                {
                    "op": "replace",
                    "path": "/cohorts/fans/drift/epsilon",
                    "value": 0.2,
                }
            ],
        },
    )
    assert disallowed_res.status_code == 422, disallowed_res.get_data(as_text=True)
    assert disallowed_res.get_json()["error"]["details"]["reason"] == (
        "path_not_in_allowlist"
    )


def test_statistical_variants_and_compare_routes_are_lane_generic(app) -> None:
    client = app.test_client()
    create_res = client.post(
        "/api/sim/use-cases/creator/simulation/create",
        json={"scenario": _creator_scenario()},
    )
    assert create_res.status_code == 201, create_res.get_data(as_text=True)
    sim_id = create_res.get_json()["data"]["simulation_id"]

    reseed_res = client.post(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/statistical-config/reseed",
        json={"seed": 456, "reason": "variant_route_pin"},
    )
    assert reseed_res.status_code == 200, reseed_res.get_data(as_text=True)
    variant_id = reseed_res.get_json()["data"]["variant"]["variant_id"]

    variants_res = client.get(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/variants",
    )
    assert variants_res.status_code == 200, variants_res.get_data(as_text=True)
    variants = variants_res.get_json()["data"]["variants"]
    assert variants[0]["variant_id"] == "base"
    assert {variant["variant_id"] for variant in variants} >= {"base", variant_id}

    compare_res = client.post(
        "/api/sim/use-cases/creator/simulation/compare",
        json={
            "base_run_id": sim_id,
            "variant_run_id": variant_id,
            "axes": ["statistical"],
        },
    )
    assert compare_res.status_code == 200, compare_res.get_data(as_text=True)
    comparison = compare_res.get_json()["data"]
    assert comparison["statistical_settings_hash_pair"][0]
    assert comparison["statistical_settings_hash_pair"][1]
    assert comparison["statistical_diff"]
    assert comparison["findings_diff"]["missing"] == []

    scoped_compare_res = client.post(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/statistical-config/compare",
        json={"variant_run_id": variant_id, "axes": ["statistical"]},
    )
    assert scoped_compare_res.status_code == 200, scoped_compare_res.get_data(
        as_text=True,
    )
    assert scoped_compare_res.get_json()["data"]["base_run_id"] == sim_id


def test_post_prepare_statistical_reseed_records_reprepare_requirement(app) -> None:
    client = app.test_client()

    create_res = client.post(
        "/api/sim/use-cases/creator/simulation/create",
        json={"scenario": _creator_scenario()},
    )
    assert create_res.status_code == 201, create_res.get_data(as_text=True)
    sim_id = create_res.get_json()["data"]["simulation_id"]

    prepare_res = client.post(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/prepare",
    )
    assert prepare_res.status_code == 200, prepare_res.get_data(as_text=True)

    reseed_res = client.post(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/statistical-config/reseed",
        json={"seed": 789, "reason": "post_prepare_variant"},
    )
    assert reseed_res.status_code == 200, reseed_res.get_data(as_text=True)

    start_res = client.post(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/start",
    )
    assert start_res.status_code == 409, start_res.get_data(as_text=True)
    body = start_res.get_json()
    assert body["error"]["details"]["reason"] == (
        "statistical_settings_changed_after_prepare"
    )

    variants_res = client.get(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/variants",
    )
    assert variants_res.status_code == 200, variants_res.get_data(as_text=True)
    assert variants_res.get_json()["data"]["status"] == "requires_reprepare"


def test_creator_prepare_mints_lane_neutral_run_metadata_with_statistical_snapshot(
    app,
) -> None:
    from app.simulation_platform.run_metadata import RunMetadata

    client = app.test_client()
    create_res = client.post(
        "/api/sim/use-cases/creator/simulation/create",
        json={
            "scenario": _creator_scenario(),
            "statistical_config": {"settings": {"run.run_seed": 654}},
        },
    )
    assert create_res.status_code == 201, create_res.get_data(as_text=True)
    sim_id = create_res.get_json()["data"]["simulation_id"]

    prepare_res = client.post(
        f"/api/sim/use-cases/creator/simulation/{sim_id}/prepare",
    )
    assert prepare_res.status_code == 200, prepare_res.get_data(as_text=True)

    run_dir = Path(app.config["OASIS_SIMULATION_DATA_DIR"]) / "creator" / sim_id
    metadata = RunMetadata.model_validate_json(
        (run_dir / "run_metadata.json").read_text(encoding="utf-8"),
    )
    assert metadata.statistical_settings["run.run_seed"] == 654
    assert metadata.statistical_settings_hash
    assert metadata.statistical_runtime_overrides["seed"] == 654
    assert "statistical_settings.json" in metadata.artifact_paths
