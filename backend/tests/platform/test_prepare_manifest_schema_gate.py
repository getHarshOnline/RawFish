"""Generic ``/prepare`` validates ``write_prepare_artifacts`` payload.

When the adapter declares ``prepare_manifest_schema`` (a Pydantic
model class), the generic ``POST .../simulation/<sim_id>/prepare``
route validates the manifest returned by ``write_prepare_artifacts``
against the schema BEFORE the ``state.json`` transition lands. A
schema-drifted manifest surfaces as ``REPORT_CORRUPTED`` 500 with
``details.reason='prepare_manifest_schema_invalid'``.

Adapters that set ``prepare_manifest_schema = None`` skip this gate
honestly (the prepare phase still completes).
"""
from __future__ import annotations

from pydantic import BaseModel

from app.simulation_platform import registry as platform_registry
from app.simulation_platform.tests_helpers import FakeUseCase
from tests._helpers import make_app


class _StrictManifest(BaseModel):
    expected_field: str


def _snapshot_registry():
    return dict(platform_registry._REGISTRY)  # noqa: SLF001


def _restore_registry(snapshot):
    platform_registry._REGISTRY.clear()  # noqa: SLF001
    platform_registry._REGISTRY.update(snapshot)  # noqa: SLF001


def _make_adapter(uid, schema, manifest_payload):
    """Return a FakeUseCase instance with overrides applied.

    ``FakeUseCase`` is a ``@dataclass``; the ``prepare_manifest_schema``
    field carries a default of ``None`` that the dataclass-generated
    ``__init__`` writes into every instance. A class-body
    re-assignment in a subclass would be shadowed by that init,
    so we override the field on the instance directly, plus
    rebind ``write_prepare_artifacts`` to a bound method that
    returns the test's manifest payload.
    """
    instance = FakeUseCase(uid=uid)
    instance.prepare_manifest_schema = schema  # type: ignore[assignment]

    def _wpa(self, scenario, run_dir):
        del self, scenario, run_dir
        return manifest_payload

    # Bind as method on the instance so the route's
    # ``adapter.write_prepare_artifacts(scenario, run_dir)`` call site
    # passes ``self`` correctly.
    import types
    instance.write_prepare_artifacts = types.MethodType(_wpa, instance)
    return instance


def _create_sim(client, lane_id):
    resp = client.post(
        f"/api/sim/use-cases/{lane_id}/simulation/create",
        json={"persona": "fake", "scenario": {"x": 1}},
    )
    assert resp.status_code in (200, 201), resp.get_json()
    return resp.get_json()["data"]["simulation_id"]


def test_prepare_rejects_manifest_that_fails_schema(monkeypatch, tmp_path):
    from app.config import Config as _Cfg
    monkeypatch.setattr(_Cfg, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    from app.config import Config as _Cfg2
    monkeypatch.setattr(_Cfg2, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))

    snapshot = _snapshot_registry()
    try:
        adapter = _make_adapter(
            uid="fake_bad_manifest_lane",
            schema=_StrictManifest,
            manifest_payload={"wrong_field": "boom"},
        )
        platform_registry.register(adapter)
        with app.test_client() as client:
            sim_id = _create_sim(client, "fake_bad_manifest_lane")
            resp = client.post(
                f"/api/sim/use-cases/fake_bad_manifest_lane"
                f"/simulation/{sim_id}/prepare",
                json={},
            )
        assert resp.status_code == 500, resp.get_json()
        body = resp.get_json()
        assert body["success"] is False
        assert body["error"]["code"] == "REPORT_CORRUPTED"
        assert (
            body["error"]["details"]["reason"]
            == "prepare_manifest_schema_invalid"
        )
    finally:
        _restore_registry(snapshot)


def test_prepare_accepts_manifest_that_satisfies_schema(monkeypatch, tmp_path):
    from app.config import Config as _Cfg
    monkeypatch.setattr(_Cfg, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    from app.config import Config as _Cfg2
    monkeypatch.setattr(_Cfg2, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))

    snapshot = _snapshot_registry()
    try:
        adapter = _make_adapter(
            uid="fake_good_manifest_lane",
            schema=_StrictManifest,
            manifest_payload={"expected_field": "ok"},
        )
        platform_registry.register(adapter)
        with app.test_client() as client:
            sim_id = _create_sim(client, "fake_good_manifest_lane")
            resp = client.post(
                f"/api/sim/use-cases/fake_good_manifest_lane"
                f"/simulation/{sim_id}/prepare",
                json={},
            )
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["data"]["phase"] == "prepared"
    finally:
        _restore_registry(snapshot)


def test_prepare_skips_gate_when_manifest_schema_is_none(monkeypatch, tmp_path):
    """An adapter that declares ``prepare_manifest_schema = None``
    gets the prepare phase completed without the schema gate
    firing."""
    from app.config import Config as _Cfg
    monkeypatch.setattr(_Cfg, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))
    app = make_app(monkeypatch, AUTH_ENABLED="false")
    from app.config import Config as _Cfg2
    monkeypatch.setattr(_Cfg2, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))

    snapshot = _snapshot_registry()
    try:
        adapter = _make_adapter(
            uid="fake_noschema_lane",
            schema=None,
            manifest_payload={"anything": "goes"},
        )
        platform_registry.register(adapter)
        with app.test_client() as client:
            sim_id = _create_sim(client, "fake_noschema_lane")
            resp = client.post(
                f"/api/sim/use-cases/fake_noschema_lane"
                f"/simulation/{sim_id}/prepare",
                json={},
            )
        assert resp.status_code == 200, resp.get_json()
    finally:
        _restore_registry(snapshot)
