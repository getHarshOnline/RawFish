"""Generic lifecycle blueprint contract pins.

The ``/api/sim/use-cases/<use_case_id>/...`` surface is the generic
dispatch path every registered :class:`SimulationUseCase` adapter
exposes. Pinned guarantees:

  * The 8 lifecycle routes are mounted and reachable.
  * An unknown ``use_case_id`` returns a structured 404
    ``USE_CASE_NOT_REGISTERED`` envelope.
  * ``GET .../personas`` returns the catalog rows belonging to the
    requested use case (filtered to that ``use_case_id``).
  * ``run_sensitivity`` on an adapter that raises
    :class:`NotImplementedError` surfaces a structured 501
    ``CAPABILITY_UNSUPPORTED`` envelope with the capability name
    so clients can branch on the reason without parsing the
    message text.
  * The full create -> prepare -> start -> status -> report
    pipeline lands the expected phase transitions through a
    fake adapter; no policy imports involved.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.simulation_platform import registry as platform_registry
from app.simulation_platform.catalog import PersonaSpec
from app.simulation_platform.tests_helpers import FakeUseCase, build_fake_use_case
from tests._helpers import catalog_payload, make_app


@pytest.fixture
def app(monkeypatch):
    return make_app(monkeypatch, AUTH_ENABLED="false")


def _registry_snapshot():
    return dict(platform_registry._REGISTRY)  # noqa: SLF001


def _registry_restore(snapshot):
    platform_registry._REGISTRY.clear()  # noqa: SLF001
    platform_registry._REGISTRY.update(snapshot)  # noqa: SLF001


# ----------------------------------------------------------------------
# Route mount + 404 / 501 envelopes
# ----------------------------------------------------------------------


def test_use_case_routes_are_exactly_the_rawfish_product_surface(app):
    """The generic lane API exposes the RawFish product surface exactly."""
    expected = frozenset({
        "/api/sim/use-cases",
        "/api/sim/use-cases/",
        "/api/sim/use-cases/<use_case_id>/catalog",
        "/api/sim/use-cases/<use_case_id>/evals",
        "/api/sim/use-cases/<use_case_id>/evals/preflight_meter",
        "/api/sim/use-cases/<use_case_id>/personas",
        "/api/sim/use-cases/<use_case_id>/scenario/parse",
        "/api/sim/use-cases/<use_case_id>/statistical-manifest",
        "/api/sim/use-cases/<use_case_id>/simulation/create",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/prepare",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/start",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/status",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/graph",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/report/generate",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/report/jobs/<job_id>",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/report",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/report/chat",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/sensitivity",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/statistical-config",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/statistical-config/reseed",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/statistical-config/compare",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/statistical-lineage",
        "/api/sim/use-cases/<use_case_id>/simulation/<sim_id>/variants",
        "/api/sim/use-cases/<use_case_id>/simulation/compare",
    })
    actual = frozenset(
        str(r) for r in app.url_map.iter_rules()
        if str(r).startswith("/api/sim/use-cases")
    )
    assert actual == expected


def test_use_case_index_lists_registered_lane_manifests(app):
    client = app.test_client()
    res = client.get("/api/sim/use-cases")
    assert res.status_code == 200, res.get_data(as_text=True)
    data = res.get_json()["data"]
    assert set(data["lane_ids"]) >= {"creator", "internal_policy", "public_policy"}
    manifests = {
        row["use_case_id"]: row["lane_manifest"]
        for row in data["use_cases"]
    }
    for lane_id in ("creator", "internal_policy", "public_policy"):
        assert manifests[lane_id]["lane_id"] == lane_id
        assert manifests[lane_id]["report_sections"]


def test_unknown_use_case_returns_structured_404(app):
    client = app.test_client()
    res = client.get("/api/sim/use-cases/not_a_lane/personas")
    assert res.status_code == 404
    err = res.get_json()["error"]
    assert err["code"] == "USE_CASE_NOT_REGISTERED"
    assert err["details"]["use_case_id"] == "not_a_lane"


def test_parse_seed_endpoint_uses_lane_adapter(app):
    client = app.test_client()
    res = client.post(
        "/api/sim/use-cases/creator/scenario/parse",
        json={
            "seed_text": "Stress-test an apology video.",
            "stress_question": "Will loyal fans forgive it?",
            "persona": "video_creator",
        },
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    data = res.get_json()["data"]
    assert data["use_case_id"] == "creator"
    assert data["parse_source"] == "fallback"
    assert data["parser"] == "adapter.parse_seed"
    assert data["scenario_draft"]["seed_text"] == "Stress-test an apology video."
    assert data["scenario_draft"]["stress_question"] == "Will loyal fans forgive it?"


def test_parse_seed_endpoint_rejects_empty_seed(app):
    client = app.test_client()
    res = client.post(
        "/api/sim/use-cases/creator/scenario/parse",
        json={"seed_text": ""},
    )
    assert res.status_code == 400
    err = res.get_json()["error"]
    assert err["code"] == "INVALID_INPUT"
    assert err["details"]["reason"] == "seed_text_required"


def test_parse_seed_endpoint_preserves_adapter_parse_metadata(app):
    snapshot = _registry_snapshot()

    class _MetadataParseLane(FakeUseCase):
        def parse_seed(self, seed_text, stress_question, persona, scope):
            return {
                "_parse_meta": {
                    "parse_source": "llm",
                    "parser": "lane.smart_parser_v2",
                },
                "seed_text": seed_text,
                "stress_question": stress_question,
                "persona": persona,
                "scope": scope,
            }

    try:
        fake = _MetadataParseLane(
            uid="parse_meta_lane",
            catalog_payload=catalog_payload(
                use_case_id="parse_meta_lane",
                personas=[PersonaSpec(
                    persona_id="researcher",
                    label="Researcher",
                    cohort_question="Who reacts?",
                    segment_types_by_scope={None: ["reader"]},
                )],
            ),
        )
        platform_registry.register(fake)
        client = app.test_client()
        res = client.post(
            "/api/sim/use-cases/parse_meta_lane/scenario/parse",
            json={"seed_text": "seed", "stress_question": "stress"},
        )
        assert res.status_code == 200, res.get_data(as_text=True)
        data = res.get_json()["data"]
        assert data["parse_source"] == "llm"
        assert data["parser"] == "lane.smart_parser_v2"
        assert "_parse_meta" not in data["scenario_draft"]
    finally:
        _registry_restore(snapshot)


def test_preflight_preserves_parse_provenance_in_source_context(app):
    client = app.test_client()
    res = client.post(
        "/api/sim/use-cases/creator/evals/preflight_meter",
        json={
            "source_context": {
                "mode": "paste",
                "parse_provenance": {
                    "source": "fallback",
                    "parser": "adapter.parse_seed",
                },
            },
        },
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    source_context = res.get_json()["data"]["source_context"]
    assert source_context["parse_provenance"] == {
        "source": "fallback",
        "parser": "adapter.parse_seed",
    }


# ----------------------------------------------------------------------
# Catalog pass-through (per-use-case filter)
# ----------------------------------------------------------------------


def test_personas_returns_only_rows_for_requested_use_case(app):
    snapshot = _registry_snapshot()
    try:
        fake = build_fake_use_case(
            uid="lifecycle_lane_personas_test",
            catalog_payload=catalog_payload(
                use_case_id="lifecycle_lane_personas_test",
                personas=[
                    PersonaSpec(
                        persona_id="researcher",
                        label="Researcher",
                        cohort_question="Who reads the preprint?",
                        segment_types_by_scope={None: ["peer_reviewer"]},
                    ),
                ],
            ),
        )
        platform_registry.register(fake)
        client = app.test_client()
        res = client.get(
            "/api/sim/use-cases/lifecycle_lane_personas_test/personas"
        )
        assert res.status_code == 200, res.get_data(as_text=True)
        data = res.get_json()["data"]
        assert data["use_case_id"] == "lifecycle_lane_personas_test"
        personas = data["personas"]
        assert len(personas) == 1
        assert personas[0]["persona"] == "researcher"
        assert personas[0]["use_case_id"] == "lifecycle_lane_personas_test"
    finally:
        _registry_restore(snapshot)


# ----------------------------------------------------------------------
# CAPABILITY_UNSUPPORTED 501 envelope
# ----------------------------------------------------------------------


def test_start_on_adapter_that_opts_out_of_run_simulation_returns_501(app):
    """An adapter that opts out of ``run_simulation`` by raising
    :class:`NotImplementedError` MUST surface a structured 501
    envelope so the lane author knows the generic-dispatch lifecycle
    cannot run until the adapter supplies a real runner.
    """
    snapshot = _registry_snapshot()

    class _OptOutRunnerAdapter:
        # SimulationUseCase Protocol declares ``report_schema`` -- set
        # to None for the raw-parse fallback (this pin does not exercise
        # the read-side schema-validation path).
        report_schema = None
        report_draft_schema = None
        prepare_manifest_schema = None
        guardrail_capabilities = None
        adapter_runs_guardrails_internally = False

        def use_case_id(self):
            return "lifecycle_lane_optout_runner"

        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(),
                personas=[PersonaSpec(
                    persona_id="x", label="X", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            )

        def artifact_names(self):
            return {"report_json": "report.json"}

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
            return {}

        def generate_report(self, **kwargs):
            return {}

        def run_simulation(self, scenario, run_dir):
            # Canonical opt-out signal: the generic blueprint surfaces
            # this as a structured 501 CAPABILITY_UNSUPPORTED envelope.
            raise NotImplementedError(
                "this lane's lifecycle is not driven through generic dispatch"
            )

        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError("not under test")

    try:
        platform_registry.register(_OptOutRunnerAdapter())  # type: ignore[arg-type]  # test stub probes runtime conformance gate
        client = app.test_client()
        created = client.post(
            "/api/sim/use-cases/lifecycle_lane_optout_runner/simulation/create",
            json={"scenario": {}},
        )
        assert created.status_code == 201
        sim_id = created.get_json()["data"]["simulation_id"]
        # Bypass prepare so start sees phase=prepared. The
        # generic prepare path does its own thing; for this pin
        # we go directly to start which has the runner-capability
        # check.
        client.post(
            f"/api/sim/use-cases/lifecycle_lane_optout_runner/simulation/{sim_id}/prepare",
        )
        res = client.post(
            f"/api/sim/use-cases/lifecycle_lane_optout_runner/simulation/{sim_id}/start",
        )
        assert res.status_code == 501, res.get_data(as_text=True)
        err = res.get_json()["error"]
        assert err["code"] == "CAPABILITY_UNSUPPORTED"
        assert err["details"]["capability"] == "run_simulation"
    finally:
        _registry_restore(snapshot)


def test_sensitivity_on_adapter_that_raises_not_implemented_is_501(app):
    """``run_sensitivity`` on an adapter that raises
    :class:`NotImplementedError` MUST surface a structured 501 with
    ``details.reason = "real_sensitivity_unsupported"`` -- not a 500
    or a bare exception. The "real_sensitivity" wording is deliberate:
    any lane that does not run actual ensemble re-runs opts out via
    NotImplementedError, and the wire contract names the capability
    in vendor-neutral terms.
    """
    snapshot = _registry_snapshot()

    class _NoSensitivityAdapter:
        # SimulationUseCase Protocol declares report_schema; this fake
        # opts out of schema validation.
        report_schema = None
        report_draft_schema = None
        prepare_manifest_schema = None
        guardrail_capabilities = None
        adapter_runs_guardrails_internally = False

        def use_case_id(self) -> str:
            return "lifecycle_lane_no_sensitivity"

        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(),
                personas=[PersonaSpec(
                    persona_id="x", label="X", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            )

        def artifact_names(self):
            return {"report_json": "report.json"}

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
            return {}

        def generate_report(self, **kwargs):
            return {"report": "ok"}

        def run_simulation(self, scenario, run_dir):
            pass

        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError("this lane does not support sensitivity")

    try:
        platform_registry.register(_NoSensitivityAdapter())  # type: ignore[arg-type]  # test stub probes runtime conformance gate
        # Need at least a simulation row for the sensitivity route to
        # not 404 first. Create one through the generic create path
        # so the test covers the create+sensitivity surface jointly.
        client = app.test_client()
        created = client.post(
            "/api/sim/use-cases/lifecycle_lane_no_sensitivity/simulation/create",
            json={"scenario": {"persona": "x"}},
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        sim_id = created.get_json()["data"]["simulation_id"]

        res = client.post(
            f"/api/sim/use-cases/lifecycle_lane_no_sensitivity/"
            f"simulation/{sim_id}/sensitivity",
            json={},
        )
        assert res.status_code == 501, res.get_data(as_text=True)
        err = res.get_json()["error"]
        assert err["code"] == "CAPABILITY_UNSUPPORTED"
        assert err["details"]["reason"] == "real_sensitivity_unsupported"
        assert err["details"]["capability"] == "real_sensitivity"
    finally:
        _registry_restore(snapshot)


# ----------------------------------------------------------------------
# Full lifecycle: create -> prepare -> start -> status -> report
# ----------------------------------------------------------------------


def test_full_generic_lifecycle_through_fake_adapter(app, tmp_path):
    """End-to-end pin: a registered fake adapter's create / prepare /
    start / status / report transitions land correctly through the
    generic blueprint without importing any policy code.
    """
    snapshot = _registry_snapshot()

    class _CapturedAdapter:
        """Records the side-effects so the test can assert phase transitions."""
        # SimulationUseCase Protocol declares report_schema; this fake
        # opts out of schema validation (raw-parse fallback).
        report_schema = None
        report_draft_schema = None
        prepare_manifest_schema = None
        guardrail_capabilities = None
        adapter_runs_guardrails_internally = False

        def __init__(self):
            self.prepared = []
            self.ran = []
            self.reported = []

        def use_case_id(self):
            return "lifecycle_lane_full"

        def run_simulation(self, scenario, run_dir):
            self.ran.append(("run_simulation", str(run_dir)))

        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(),
                personas=[PersonaSpec(
                    persona_id="p", label="P", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            )

        def artifact_names(self):
            return {"report_json": "lifecycle_report.json"}

        def validate_scenario(self, payload):
            return payload

        def rehydrate_scenario(self, state_dict):
            return state_dict.get("scenario", state_dict)

        def parse_seed(self, **kwargs):
            return {}

        def load_presets(self):
            return []

        def build_ontology(self, scenario, run_dir):
            self.prepared.append(("build_ontology", str(run_dir)))

        def write_prepare_artifacts(self, scenario, run_dir):
            self.prepared.append(("write_artifacts", str(run_dir)))
            return {"manifest": "ok"}

        def generate_report(self, scenario, run_metadata, evidence_catalog, llm_client=None):
            self.reported.append("generate_report")
            return {
                "report_id": "fake_001",
                "use_case_id": self.use_case_id(),
                "summary": "lifecycle test",
            }

        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError("not under test")

    adapter = _CapturedAdapter()

    try:
        platform_registry.register(adapter)  # type: ignore[arg-type]  # test stub probes runtime conformance gate
        client = app.test_client()

        # 1. create
        created = client.post(
            "/api/sim/use-cases/lifecycle_lane_full/simulation/create",
            json={"scenario": {"persona": "p"}},
        )
        assert created.status_code == 201
        sim_id = created.get_json()["data"]["simulation_id"]
        assert sim_id.startswith("lifecycle_lane_full_sim_")

        # 2. prepare
        prepared = client.post(
            f"/api/sim/use-cases/lifecycle_lane_full/simulation/{sim_id}/prepare",
        )
        assert prepared.status_code == 200
        assert prepared.get_json()["data"]["phase"] == "prepared"
        # Adapter side-effects fired in the documented order.
        assert [op for op, _ in adapter.prepared] == [
            "build_ontology", "write_artifacts",
        ]

        # 3. start (deterministic mock: phase -> completed)
        started = client.post(
            f"/api/sim/use-cases/lifecycle_lane_full/simulation/{sim_id}/start",
        )
        assert started.status_code == 200
        assert started.get_json()["data"]["phase"] == "completed"

        # 4. status reports completed
        status = client.get(
            f"/api/sim/use-cases/lifecycle_lane_full/simulation/{sim_id}/status",
        )
        assert status.status_code == 200
        assert status.get_json()["data"]["phase"] == "completed"

        # 5. report/generate
        reported = client.post(
            f"/api/sim/use-cases/lifecycle_lane_full/simulation/{sim_id}"
            f"/report/generate",
        )
        assert reported.status_code == 200, reported.get_data(as_text=True)
        assert reported.get_json()["data"]["phase"] == "reported"
        assert "sha256:" in reported.get_json()["data"]["sha256_json"]
        assert adapter.reported == ["generate_report"]

        # 6. report read returns the persisted artifact
        read = client.get(
            f"/api/sim/use-cases/lifecycle_lane_full/simulation/{sim_id}/report",
        )
        assert read.status_code == 200
        report = read.get_json()["data"]
        assert report["report_id"] == "fake_001"
        assert report["use_case_id"] == "lifecycle_lane_full"
    finally:
        _registry_restore(snapshot)


# ----------------------------------------------------------------------
# Lane isolation: the dispatch never imports app.policy.*
# ----------------------------------------------------------------------


def test_generic_create_replays_response_under_same_idempotency_key(app):
    """Two same-key same-body POSTs to the generic create route MUST
    return the same simulation_id (a same-key replay is cached
    through the platform idempotency helper). Without the wrapper,
    the second call would mint a new simulation_id and execute the
    handler again -- duplicating the side effect Idempotency-Key
    exists to prevent."""
    snapshot = _registry_snapshot()
    try:
        fake = build_fake_use_case(
            uid="idempotency_replay_lane",
            catalog_payload=catalog_payload(
                use_case_id="idempotency_replay_lane",
                personas=[PersonaSpec(
                    persona_id="x", label="X", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            ),
        )
        platform_registry.register(fake)
        client = app.test_client()
        body = {"scenario": {"persona": "x"}}
        headers = {"Idempotency-Key": "generic-replay-key-001"}

        a = client.post(
            "/api/sim/use-cases/idempotency_replay_lane/simulation/create",
            json=body, headers=headers,
        )
        b = client.post(
            "/api/sim/use-cases/idempotency_replay_lane/simulation/create",
            json=body, headers=headers,
        )
        assert a.status_code == 201
        assert b.status_code == 201
        # Same simulation_id on the replay = the handler did NOT
        # execute twice; the cached response was returned.
        assert (
            a.get_json()["data"]["simulation_id"]
            == b.get_json()["data"]["simulation_id"]
        )
    finally:
        _registry_restore(snapshot)


def test_generic_create_different_body_same_key_returns_422(app):
    """Same Idempotency-Key + DIFFERENT body must return 422
    IDEMPOTENCY_KEY_MISMATCH, NOT execute the handler with the new
    body (that would be exactly the duplicate-execution race
    Idempotency-Key exists to prevent)."""
    snapshot = _registry_snapshot()
    try:
        fake = build_fake_use_case(
            uid="idempotency_mismatch_lane",
            catalog_payload=catalog_payload(
                use_case_id="idempotency_mismatch_lane",
                personas=[PersonaSpec(
                    persona_id="x", label="X", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            ),
        )
        platform_registry.register(fake)
        client = app.test_client()
        headers = {"Idempotency-Key": "generic-mismatch-key-001"}

        first = client.post(
            "/api/sim/use-cases/idempotency_mismatch_lane/simulation/create",
            json={"scenario": {"persona": "x", "title": "A"}},
            headers=headers,
        )
        assert first.status_code == 201
        retry = client.post(
            "/api/sim/use-cases/idempotency_mismatch_lane/simulation/create",
            json={"scenario": {"persona": "x", "title": "B"}},
            headers=headers,
        )
        assert retry.status_code == 422
        err = retry.get_json()["error"]
        assert err["code"] == "IDEMPOTENCY_KEY_MISMATCH"
    finally:
        _registry_restore(snapshot)


def test_generic_create_rejects_adapter_artifact_name_with_path_separator(app):
    """A maliciously-crafted adapter whose ``artifact_names`` returns
    a path-traversal-shaped report filename MUST fail closed with
    INVALID_ADAPTER_ARTIFACT_NAME. Otherwise the route would happily
    write outside the run dir or to an absolute path."""
    snapshot = _registry_snapshot()

    class _BadArtifactAdapter:
        report_schema = None
        report_draft_schema = None
        prepare_manifest_schema = None
        guardrail_capabilities = None
        adapter_runs_guardrails_internally = False
        def use_case_id(self): return "bad_artifact_lane"
        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(),
                personas=[PersonaSpec(
                    persona_id="x", label="X", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            )
        def artifact_names(self): return {"report_json": "../../escape.json"}
        def validate_scenario(self, payload): return payload
        def rehydrate_scenario(self, state_dict): return state_dict.get("scenario", state_dict)
        def parse_seed(self, **kwargs): return {}
        def load_presets(self): return []
        def build_ontology(self, scenario, run_dir): return None
        def write_prepare_artifacts(self, scenario, run_dir): return {}
        def generate_report(self, **kwargs): return {"r": True}
        def run_simulation(self, scenario, run_dir): pass
        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError("not under test")

    try:
        platform_registry.register(_BadArtifactAdapter())  # type: ignore[arg-type]  # test stub probes runtime conformance gate
        client = app.test_client()
        created = client.post(
            "/api/sim/use-cases/bad_artifact_lane/simulation/create",
            json={"scenario": {"persona": "x"}},
        )
        sim_id = created.get_json()["data"]["simulation_id"]
        client.post(
            f"/api/sim/use-cases/bad_artifact_lane/simulation/{sim_id}/prepare",
        )
        client.post(
            f"/api/sim/use-cases/bad_artifact_lane/simulation/{sim_id}/start",
        )
        res = client.post(
            f"/api/sim/use-cases/bad_artifact_lane/simulation/{sim_id}/report/generate",
        )
        assert res.status_code == 500
        err = res.get_json()["error"]
        assert err["code"] == "INVALID_ADAPTER_ARTIFACT_NAME"
        assert "../../escape.json" in err["details"]["value"]
    finally:
        _registry_restore(snapshot)


def test_generic_status_distinguishes_corrupt_state_from_missing(app, tmp_path):
    """Corrupt ``state.json`` MUST surface as 500 STATE_CORRUPTED
    with a ``details.reason`` discriminator, not collapsed into a
    404 SIMULATION_NOT_FOUND. Collapsing the two makes "corrupt run"
    indistinguishable from "nonexistent run"."""
    snapshot = _registry_snapshot()
    try:
        fake = build_fake_use_case(
            uid="corrupt_state_lane",
            catalog_payload=catalog_payload(
                use_case_id="corrupt_state_lane",
                personas=[PersonaSpec(
                    persona_id="x", label="X", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            ),
        )
        platform_registry.register(fake)
        client = app.test_client()
        created = client.post(
            "/api/sim/use-cases/corrupt_state_lane/simulation/create",
            json={"scenario": {"persona": "x"}},
        )
        sim_id = created.get_json()["data"]["simulation_id"]
        # Corrupt the persisted state.json -- valid file path,
        # invalid JSON.
        sim_dir = Path(client.application.config["OASIS_SIMULATION_DATA_DIR"]) / (
            f"corrupt_state_lane/{sim_id}"
        )
        (sim_dir / "state.json").write_text("not valid json", encoding="utf-8")
        res = client.get(
            f"/api/sim/use-cases/corrupt_state_lane/simulation/{sim_id}/status"
        )
        assert res.status_code == 500
        err = res.get_json()["error"]
        assert err["code"] == "STATE_CORRUPTED"
        assert err["details"]["reason"] == "state_json_parse_failed"
    finally:
        _registry_restore(snapshot)


def test_generic_catalog_includes_report_sections_by_scope(app):
    """The catalog payload MUST carry ``report_sections_by_persona``
    + per-row ``report_sections`` so a frontend TOC renderer can
    read the persona's section list without consulting a separate
    endpoint."""
    snapshot = _registry_snapshot()
    try:
        fake = build_fake_use_case(
            uid="catalog_sections_lane",
            catalog_payload=catalog_payload(
                use_case_id="catalog_sections_lane",
                personas=[PersonaSpec(
                    persona_id="x", label="X", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                    report_sections_by_scope={
                        None: ["overview", "findings", "limitations"],
                    },
                )],
            ),
        )
        platform_registry.register(fake)
        client = app.test_client()
        res = client.get("/api/sim/use-cases/catalog_sections_lane/personas")
        assert res.status_code == 200
        data = res.get_json()["data"]
        # Per-row report_sections present.
        assert data["personas"][0]["report_sections"] == [
            "overview", "findings", "limitations",
        ]
        # Denormalised index present too.
        assert data["report_sections_by_persona"]["x"] == [
            "overview", "findings", "limitations",
        ]
    finally:
        _registry_restore(snapshot)


def test_use_cases_blueprint_imports_are_lane_agnostic():
    """The generic dispatch must not pull in any policy-specific
    module. A future contributor that needs to read a policy helper
    has to either lift it into a platform module or duplicate it --
    this test fails before the smell ships."""
    import ast
    src = Path(__file__).resolve().parents[2].joinpath(
        "app", "api", "use_cases.py",
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # Reject any policy-namespaced import.
            if module.startswith("app.policy") or module.startswith(
                "app.services.policy"
            ):
                offenders.append(module)
            # Also reject deleted direct-route blueprints.
            if module.startswith("app.api." + "policy"):
                offenders.append(module)
    assert not offenders, (
        f"generic lifecycle blueprint MUST be lane-agnostic; found: "
        f"{offenders!r}"
    )


# ----------------------------------------------------------------------
# Write-time schema gate on /report/generate (fail-clean contract)
# ----------------------------------------------------------------------


def test_generate_report_rejects_payload_that_fails_report_schema(app, tmp_path):
    """When ``adapter.report_schema`` is declared, a malformed
    ``generate_report`` return MUST surface as 500 REPORT_CORRUPTED
    BEFORE the artifact lands on disk. Mirrors the read-path schema
    gate so a corrupt-but-JSON payload never makes it to the GET
    surface in the first place.
    """
    from pydantic import BaseModel

    class _StrictReport(BaseModel):
        report_id: str
        persona: str

    class _BadAdapter:
        report_schema = _StrictReport
        report_draft_schema = None
        prepare_manifest_schema = None
        guardrail_capabilities = None
        adapter_runs_guardrails_internally = False

        def use_case_id(self): return "g14_bad_report_lane"
        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(),
                personas=[PersonaSpec(
                    persona_id="p", label="P", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            )
        def artifact_names(self): return {"report_json": "g14_report.json"}
        def validate_scenario(self, payload): return payload
        def rehydrate_scenario(self, state_dict): return state_dict.get("scenario", state_dict)
        def parse_seed(self, **kwargs): return {}
        def load_presets(self): return []
        def build_ontology(self, scenario, run_dir): return None
        def write_prepare_artifacts(self, scenario, run_dir): return {}
        def run_simulation(self, scenario, run_dir): pass
        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError("not under test")
        def generate_report(self, scenario, run_metadata, evidence_catalog, llm_client=None):
            # Schema-invalid payload: missing required ``persona`` field.
            return {"report_id": "rpt_001"}

    snapshot = _registry_snapshot()
    try:
        platform_registry.register(_BadAdapter())  # type: ignore[arg-type]  # test stub probes runtime conformance gate
        client = app.test_client()
        created = client.post(
            "/api/sim/use-cases/g14_bad_report_lane/simulation/create",
            json={"scenario": {"persona": "p"}},
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        sim_id = created.get_json()["data"]["simulation_id"]
        # Walk to completed via prepare + start.
        client.post(
            f"/api/sim/use-cases/g14_bad_report_lane/simulation/{sim_id}/prepare",
        )
        client.post(
            f"/api/sim/use-cases/g14_bad_report_lane/simulation/{sim_id}/start",
        )
        # Now generate -- should be 500 REPORT_CORRUPTED with the
        # generated_report_schema_invalid reason.
        res = client.post(
            f"/api/sim/use-cases/g14_bad_report_lane/simulation/{sim_id}/report/generate",
        )
        assert res.status_code == 500, res.get_data(as_text=True)
        err = res.get_json()["error"]
        assert err["code"] == "REPORT_CORRUPTED"
        assert err["details"]["reason"] == "generated_report_schema_invalid"

        # And critically: the corrupt payload MUST NOT have landed on disk.
        from pathlib import Path as _Path
        from app.config import Config as _Config
        run_dir = _Path(_Config.OASIS_SIMULATION_DATA_DIR) / "g14_bad_report_lane" / sim_id
        assert not (run_dir / "g14_report.json").exists(), (
            "Write-time schema gate failed: corrupt report landed on disk"
        )
    finally:
        _registry_restore(snapshot)


# ----------------------------------------------------------------------
# state.json validation on GET /report + POST /sensitivity
# ----------------------------------------------------------------------


def test_get_report_surfaces_simulation_not_found_when_state_missing(app):
    """``GET /report`` on a sim_id whose run dir has no ``state.json``
    MUST return 404 SIMULATION_NOT_FOUND -- not REPORT_NOT_READY.
    Without the state validation, the route would skip straight to
    the artifact lookup and emit a misleading "report not yet
    generated" envelope for a run that does not exist at all.
    """
    snapshot = _registry_snapshot()
    try:
        fake = build_fake_use_case(
            uid="a3_report_missing_state_lane",
            catalog_payload=catalog_payload(
                use_case_id="a3_report_missing_state_lane",
                personas=[PersonaSpec(
                    persona_id="x", label="X", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            ),
        )
        platform_registry.register(fake)
        client = app.test_client()
        # Never create the sim; just GET /report against a freshly
        # minted (but never-persisted) id.
        res = client.get(
            "/api/sim/use-cases/a3_report_missing_state_lane/simulation/"
            "a3_report_missing_state_lane_sim_deadbeef/report"
        )
        assert res.status_code == 404, res.get_data(as_text=True)
        err = res.get_json()["error"]
        assert err["code"] == "SIMULATION_NOT_FOUND"
    finally:
        _registry_restore(snapshot)


def test_get_report_surfaces_state_corrupted_when_state_unparseable(app):
    """``GET /report`` on a sim whose ``state.json`` is corrupt MUST
    return 500 STATE_CORRUPTED with the parse-failed reason -- not
    REPORT_NOT_READY or an orphan artifact read.
    """
    snapshot = _registry_snapshot()
    try:
        fake = build_fake_use_case(
            uid="a3_report_corrupt_state_lane",
            catalog_payload=catalog_payload(
                use_case_id="a3_report_corrupt_state_lane",
                personas=[PersonaSpec(
                    persona_id="x", label="X", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            ),
        )
        platform_registry.register(fake)
        client = app.test_client()
        created = client.post(
            "/api/sim/use-cases/a3_report_corrupt_state_lane/simulation/create",
            json={"scenario": {"persona": "x"}},
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        sim_id = created.get_json()["data"]["simulation_id"]
        # Corrupt the just-persisted state.json (valid file path,
        # invalid JSON body).
        sim_dir = Path(client.application.config["OASIS_SIMULATION_DATA_DIR"]) / (
            f"a3_report_corrupt_state_lane/{sim_id}"
        )
        (sim_dir / "state.json").write_text("not valid json", encoding="utf-8")
        res = client.get(
            f"/api/sim/use-cases/a3_report_corrupt_state_lane/"
            f"simulation/{sim_id}/report"
        )
        assert res.status_code == 500, res.get_data(as_text=True)
        err = res.get_json()["error"]
        assert err["code"] == "STATE_CORRUPTED"
        assert err["details"]["reason"] == "state_json_parse_failed"
    finally:
        _registry_restore(snapshot)


def test_sensitivity_surfaces_simulation_not_found_when_state_missing(app):
    """``POST /sensitivity`` on a sim_id whose run dir has no
    ``state.json`` MUST return 404 SIMULATION_NOT_FOUND BEFORE the
    adapter is invoked. Without the state-existence check, the
    adapter would either be invoked against a non-existent run or
    the corrupt/missing-state condition would be masked by a
    downstream capability response.
    """
    snapshot = _registry_snapshot()
    try:
        fake = build_fake_use_case(
            uid="a3_sensitivity_missing_state_lane",
            catalog_payload=catalog_payload(
                use_case_id="a3_sensitivity_missing_state_lane",
                personas=[PersonaSpec(
                    persona_id="x", label="X", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            ),
        )
        platform_registry.register(fake)
        client = app.test_client()
        # Never create the sim; just POST /sensitivity against a
        # freshly minted (but never-persisted) id.
        res = client.post(
            "/api/sim/use-cases/a3_sensitivity_missing_state_lane/simulation/"
            "a3_sensitivity_missing_state_lane_sim_deadbeef/sensitivity",
            json={},
        )
        assert res.status_code == 404, res.get_data(as_text=True)
        err = res.get_json()["error"]
        assert err["code"] == "SIMULATION_NOT_FOUND"
    finally:
        _registry_restore(snapshot)


def test_sensitivity_surfaces_state_corrupted_before_capability_check(app):
    """When ``state.json`` is corrupt, ``POST /sensitivity`` MUST
    surface STATE_CORRUPTED (500) -- NOT CAPABILITY_UNSUPPORTED
    (501). The state-integrity check runs BEFORE the adapter is
    invoked, so even an adapter that would otherwise raise
    NotImplementedError gets pre-empted by the corrupt-state
    response.
    """
    snapshot = _registry_snapshot()

    class _OptOutSensitivityAdapter:
        report_schema = None
        report_draft_schema = None
        prepare_manifest_schema = None
        guardrail_capabilities = None
        adapter_runs_guardrails_internally = False

        def use_case_id(self): return "a3_sensitivity_corrupt_state_lane"

        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(),
                personas=[PersonaSpec(
                    persona_id="x", label="X", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            )

        def artifact_names(self): return {"report_json": "report.json"}
        def validate_scenario(self, payload): return payload
        def rehydrate_scenario(self, state_dict): return state_dict.get("scenario", state_dict)
        def parse_seed(self, **kwargs): return {}
        def load_presets(self): return []
        def build_ontology(self, scenario, run_dir): return None
        def write_prepare_artifacts(self, scenario, run_dir): return {}
        def generate_report(self, **kwargs): return {}
        def run_simulation(self, scenario, run_dir): pass

        def run_sensitivity(self, simulation_id, body):
            # Would otherwise produce 501 CAPABILITY_UNSUPPORTED --
            # the state-corrupt response must pre-empt this.
            raise NotImplementedError("would-be capability_unsupported")

    try:
        platform_registry.register(_OptOutSensitivityAdapter())  # type: ignore[arg-type]  # test stub probes runtime conformance gate
        client = app.test_client()
        created = client.post(
            "/api/sim/use-cases/a3_sensitivity_corrupt_state_lane/simulation/create",
            json={"scenario": {"persona": "x"}},
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        sim_id = created.get_json()["data"]["simulation_id"]
        sim_dir = Path(client.application.config["OASIS_SIMULATION_DATA_DIR"]) / (
            f"a3_sensitivity_corrupt_state_lane/{sim_id}"
        )
        (sim_dir / "state.json").write_text("not valid json", encoding="utf-8")
        res = client.post(
            f"/api/sim/use-cases/a3_sensitivity_corrupt_state_lane/"
            f"simulation/{sim_id}/sensitivity",
            json={},
        )
        assert res.status_code == 500, res.get_data(as_text=True)
        err = res.get_json()["error"]
        assert err["code"] == "STATE_CORRUPTED"
        assert err["details"]["reason"] == "state_json_parse_failed"
    finally:
        _registry_restore(snapshot)


# ----------------------------------------------------------------------
# generic create persists the canonical (model_dump) scenario
# ----------------------------------------------------------------------


def test_create_persists_canonical_scenario_via_model_dump(app):
    """When an adapter's ``validate_scenario`` returns a Pydantic
    model, the persisted ``state.json``'s ``scenario`` MUST be the
    canonical ``model_dump(mode='json')`` form -- NOT the raw
    request body. This guarantees that semantically-identical
    scenarios (e.g. ones using extra-tolerant aliases, ignored
    extras, or differing key orders) collapse to the same on-disk
    payload, so the deterministic ``_build_run_metadata``
    scenario_hash is byte-identical across canonical-equivalent
    requests.
    """
    from pydantic import BaseModel, ConfigDict

    class _CanonicalScenario(BaseModel):
        # Allow extras in the request body but drop them from the
        # canonical model_dump output so the test can assert that
        # the raw extra was excluded from the persisted form.
        model_config = ConfigDict(extra="ignore")
        persona: str
        title: str

    class _CanonicalAdapter:
        report_schema = None
        report_draft_schema = None
        prepare_manifest_schema = None
        guardrail_capabilities = None
        adapter_runs_guardrails_internally = False

        def use_case_id(self): return "a4_canonical_scenario_lane"

        def catalog(self):
            return catalog_payload(
                use_case_id=self.use_case_id(),
                personas=[PersonaSpec(
                    persona_id="x", label="X", cohort_question="q?",
                    segment_types_by_scope={None: ["t"]},
                )],
            )

        def artifact_names(self): return {"report_json": "report.json"}

        def validate_scenario(self, payload):
            # Returning a Pydantic model: the route MUST persist the
            # model_dump form, not the raw payload.
            return _CanonicalScenario.model_validate(payload)

        def rehydrate_scenario(self, state_dict):
            return _CanonicalScenario.model_validate(state_dict["scenario"])

        def parse_seed(self, **kwargs): return {}
        def load_presets(self): return []
        def build_ontology(self, scenario, run_dir): return None
        def write_prepare_artifacts(self, scenario, run_dir): return {}
        def generate_report(self, **kwargs): return {}
        def run_simulation(self, scenario, run_dir): pass
        def run_sensitivity(self, simulation_id, body):
            raise NotImplementedError("not under test")

    snapshot = _registry_snapshot()
    try:
        platform_registry.register(_CanonicalAdapter())  # type: ignore[arg-type]  # test stub probes runtime conformance gate
        client = app.test_client()
        # Request body carries an extra field; the canonical
        # ``model_dump`` MUST drop it.
        raw_body = {
            "scenario": {
                "persona": "x",
                "title": "T",
                "raw_extra_to_drop": "should not appear on disk",
            },
        }
        created = client.post(
            "/api/sim/use-cases/a4_canonical_scenario_lane/simulation/create",
            json=raw_body,
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        sim_id = created.get_json()["data"]["simulation_id"]
        sim_dir = Path(client.application.config["OASIS_SIMULATION_DATA_DIR"]) / (
            f"a4_canonical_scenario_lane/{sim_id}"
        )
        persisted = json.loads(
            (sim_dir / "state.json").read_text(encoding="utf-8")
        )
        # Canonical match: persisted scenario equals model_dump output.
        expected = _CanonicalScenario.model_validate(
            raw_body["scenario"]
        ).model_dump(mode="json")
        assert persisted["scenario"] == expected
        # Raw extra was excluded from the persisted canonical form.
        assert "raw_extra_to_drop" not in persisted["scenario"]
    finally:
        _registry_restore(snapshot)
